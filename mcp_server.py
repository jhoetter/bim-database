"""bim-database MCP server (FastMCP, stdio).

Wraps the REST API in api/main.py so an LLM agent can drive the full
annotation workflow. v1 covers Phase A of the agentic-labeling tracker:
discovery + workflow state + the two grid-image tools. Phase B fills in
the remaining 18 tools.

Run manually (rarely needed — Claude Code launches it via ~/.claude.json):

    BIM_DATABASE_API_BASE=http://127.0.0.1:12500 \
        ~/repos/bim-database/.venv/bin/python ~/repos/bim-database/mcp_server.py

Or via `make mcp` once the Makefile target lands.

The server defaults to :12500 (the user's habitual `make dev-forwarded`).
Local-only `make dev` users set BIM_DATABASE_API_BASE=http://127.0.0.1:2500.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

from mcp_context_summary import (
    compact_label,
    compact_plan_status,
    compact_scene_row,
    label_counts,
)

# Server identity — version is read by the skill at startup to verify
# compatibility (tracker §6.3). Bump MAJOR on any tool signature break.
SERVER_VERSION = "0.1.1"

API_BASE = os.environ.get("BIM_DATABASE_API_BASE", "http://127.0.0.1:12500").rstrip("/")
HEALTH_PROBE_TIMEOUT_S = float(os.environ.get("BIM_MCP_HEALTH_TIMEOUT_S", "10"))
HEALTH_PROBE_INTERVAL_S = 2.0

LOG_PATH = Path(__file__).parent / "tmp" / "mcp-server.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
IMAGE_HANDLE_DIR = Path(__file__).parent / "tmp" / "mcp-image-handles"
IMAGE_HANDLE_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_HANDLE_INLINE_THRESHOLD = int(os.environ.get("BIM_MCP_IMAGE_INLINE_THRESHOLD", "250000"))
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bim-db-mcp")
log.info("startup: API_BASE=%s version=%s", API_BASE, SERVER_VERSION)

mcp = FastMCP("bim-database")


# ── H3: universal transport-error contract ─────────────────────────────────
# Historically only ~30 of the tools wrapped their backend calls in a
# retry-once guard that returns the uniform `api_unreachable` envelope; the
# rest called `_api_*` bare and would raise a raw httpx exception out of the
# tool on a transport blip — breaking the contract the agent relies on.
#
# Rather than hand-patch every tool, we wrap `mcp.tool` once so EVERY tool
# (and every future tool) is guarded at registration: any httpx transport
# error that escapes the tool body is converted to `api_unreachable`. Tools
# that already retry internally handle the error before it reaches here, so
# their behavior is unchanged; this is purely a safety net that makes the
# contract impossible to regress.


def _transport_guard(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        started = time.time()
        try:
            return await fn(*args, **kwargs)
        except (httpx.HTTPError, httpx.RequestError):
            return _api_unreachable_error(started)

    return wrapper


_register_tool = mcp.tool


def _guarded_tool(*dargs, **dkwargs):
    """Drop-in replacement for ``mcp.tool`` that applies ``_transport_guard``
    to the handler before FastMCP registers it."""
    register = _register_tool(*dargs, **dkwargs)

    def deco(fn):
        return register(_transport_guard(fn))

    return deco


mcp.tool = _guarded_tool


# Shared HTTP client — keep-alive across tool calls.
_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(30.0))
    return _http


# ── envelope ───────────────────────────────────────────────────────────────
# Every tool returns this shape (tracker §5.0) so the agent post-processes
# uniformly. The MCP runtime serialises dicts to JSON for the model.


def _ok(data: Any, *, next_tool: dict | None = None, started_at: float | None = None, status_code: int | None = None) -> dict:
    return {
        "ok": True,
        "data": data,
        "next_recommended_tool": next_tool,
        "_meta": _meta(started_at, status_code),
    }


def _err(code: str, message: str, *, hint: str = "", retry: bool = False, details: dict | None = None, started_at: float | None = None, status_code: int | None = None) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
            "retry_advisable": retry,
            "details": details or {},
        },
        "_meta": _meta(started_at, status_code),
    }


def _meta(started_at: float | None, status_code: int | None) -> dict:
    return {
        "tool_call_id": f"tc-{int(time.time() * 1000):x}",
        "api_status_code": status_code,
        "latency_ms": int((time.time() - started_at) * 1000) if started_at else None,
        "server_version": SERVER_VERSION,
    }


async def _api_get(path: str, params: dict | None = None) -> tuple[int, Any]:
    """GET wrapper that surfaces httpx errors as transport_error envelopes
    when called from a tool. Returns (status_code, body) on HTTP success
    (including 4xx). Raises httpx exceptions on transport failure."""
    r = await _client().get(path, params=params)
    try:
        body = r.json() if r.content else None
    except json.JSONDecodeError:
        body = r.text
    return r.status_code, body


async def _api_get_bytes(path: str, params: dict | None = None) -> tuple[int, bytes, str]:
    """GET wrapper for binary endpoints — returns (status, bytes, content_type)."""
    r = await _client().get(path, params=params)
    return r.status_code, r.content, r.headers.get("content-type", "application/octet-stream")


async def _api_post(path: str, json_body: Any = None, params: dict | None = None) -> tuple[int, Any]:
    r = await _client().post(path, json=json_body, params=params)
    try:
        body = r.json() if r.content else None
    except json.JSONDecodeError:
        body = r.text
    return r.status_code, body


async def _api_put(path: str, json_body: Any) -> tuple[int, Any]:
    r = await _client().put(path, json=json_body)
    try:
        body = r.json() if r.content else None
    except json.JSONDecodeError:
        body = r.text
    return r.status_code, body


async def _api_delete(path: str) -> tuple[int, Any]:
    r = await _client().delete(path)
    try:
        body = r.json() if r.content else None
    except json.JSONDecodeError:
        body = r.text
    return r.status_code, body


async def _api_patch(path: str, json_body: dict, started: float) -> dict | None:
    """PATCH wrapper that returns an MCP error envelope on failure, or None
    on success. Use in tools where the response body isn't needed and the
    caller just wants to know if the change landed."""
    try:
        r = await _client().patch(path, json=json_body)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        try:
            r = await _client().patch(path, json=json_body)
        except (httpx.HTTPError, httpx.RequestError):
            return _api_unreachable_error(started)
    if r.status_code >= 400:
        try:
            body = r.json()
        except (json.JSONDecodeError, ValueError):
            body = r.text
        return _http_status_to_error(r.status_code, body, started)
    return None


async def _wait_for_api(timeout_s: float = HEALTH_PROBE_TIMEOUT_S) -> bool:
    """Poll the API's healthish root for up to timeout_s. Used by the
    transport-error handler in tool wrappers."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = await _client().get("/datasets", timeout=httpx.Timeout(2.0))
            if r.status_code < 500:
                return True
        except (httpx.HTTPError, httpx.RequestError):
            pass
        await asyncio.sleep(HEALTH_PROBE_INTERVAL_S)
    return False


def _api_unreachable_error(started_at: float) -> dict:
    return _err(
        "api_unreachable",
        f"bim-database FastAPI is not responding at {API_BASE}.",
        hint=(
            f"Run `make dev-forwarded` in ~/repos/bim-database in another shell. "
            f"(Or `make dev` + override BIM_DATABASE_API_BASE=http://127.0.0.1:2500 "
            f"if you're on a local-only setup.)"
        ),
        retry=True,
        started_at=started_at,
    )


# Helper: an API 4xx becomes an MCP error envelope so the agent can read
# the underlying detail without parsing HTTP semantics.
def _http_status_to_error(status: int, body: Any, started_at: float) -> dict:
    detail = body
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
    if status == 404:
        return _err("not_found", str(detail), retry=False, started_at=started_at, status_code=status)
    if status == 409:
        return _err("conflict", str(detail), hint="re-fetch state and retry", retry=False, started_at=started_at, status_code=status)
    if status == 422 or status == 400:
        return _err("schema_invalid", str(detail), hint="fix the payload", retry=False, started_at=started_at, status_code=status)
    if 400 <= status < 500:
        return _err(f"http_{status}", str(detail), retry=False, started_at=started_at, status_code=status)
    return _err("api_5xx", str(detail), retry=True, started_at=started_at, status_code=status)


# ── §5.1 Discovery ────────────────────────────────────────────────────────


@mcp.tool()
async def list_houses() -> dict:
    """List every house in the corpus with a compact workflow summary.

    USE when:
      - The agent doesn't know which houses exist yet.
      - It needs to pick the next unlabeled house (`--next` flow).

    DON'T USE when:
      - You already have the key — call `get_house` instead for full
        detail.

    Returns: `data.houses` is a list of compact records:
      {key, intake_only, page_count, scenes_count, workflow_phase,
       exportable, has_labels}

    Example:
      list_houses() → {"ok": true, "data": {"houses": [...]}, ...}
    """
    started = time.time()
    try:
        status, body = await _api_get("/datasets")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get("/datasets")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    rows = []
    for h in body or []:
        drawings = h.get("drawings") or []
        labeled = sum(1 for d in drawings if d.get("labeled"))
        rows.append({
            "key": h.get("key"),
            "intake_only": bool(h.get("intake_only")),
            "page_count": h.get("intake_page_count") or h.get("page_count"),
            "scenes_count": len(drawings),
            "labeled_scenes": labeled,
            "has_labels": labeled > 0,
            "model": h.get("model"),
        })
    return _ok({"houses": rows}, started_at=started, status_code=status)


@mcp.tool()
async def get_house(key: str) -> dict:
    """Full dataset manifest for one house, with house_facts merged in.

    USE when:
      - You need the per-scene list with current labeled / label_count.
      - You're about to call `set_scene_tag` / `upsert_label` / etc.

    DON'T USE when:
      - You only need workflow status — call `get_workflow_state`.

    Args:
      key: house key, e.g. "house-22".

    Returns:
      `data` contains the dataset manifest plus a `house_facts` field
      (null if not yet populated).
    """
    started = time.time()
    try:
        status, body = await _api_get(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    facts_status, facts_body = await _api_get(f"/datasets/{key}/house_facts")
    body["house_facts"] = facts_body if facts_status == 200 else None
    return _ok(body, started_at=started, status_code=status)


async def _load_facts_and_scene_meta(key: str, dataset: dict) -> tuple[dict, dict[str, dict]]:
    """Load house_facts + per-scene workflow-vocabulary meta for `key`.

    Shared by `get_workflow_state` and `validate_export_readiness` so both
    derive phase status from exactly the same inputs. The workflow
    vocabulary (scene_tag / scene_orientation / scene_level) lives in each
    scene's labels JSON, NOT on the manifest's extraction-time
    kind/view/floor fields — so we read it from there.

    Returns: (facts, {file: {scene_tag, scene_orientation, scene_level}}).
    """
    facts_status, facts = await _api_get(f"/datasets/{key}/house_facts")
    facts = facts if facts_status == 200 else {}
    scene_meta_by_file: dict[str, dict] = {}
    for d in (dataset.get("drawings") or []):
        f = d.get("file")
        if not f:
            continue
        lbl_status, lbl = await _api_get(f"/labels/dataset/{key}/{f}")
        if lbl_status == 200 and isinstance(lbl, dict):
            labels = lbl.get("labels") or []
            label_types = {
                la.get("type") for la in labels if isinstance(la, dict)
            }
            label_type_counts = {}
            for la in labels:
                if isinstance(la, dict) and la.get("type"):
                    label_type_counts[la["type"]] = label_type_counts.get(la["type"], 0) + 1
            scene_meta_by_file[f] = {
                "scene_tag": lbl.get("scene_tag"),
                "scene_orientation": lbl.get("scene_orientation"),
                "scene_level": lbl.get("scene_level"),
                "title": d.get("title"),
                "manifest_floor": d.get("floor"),
                "manifest_kind": d.get("kind"),
                "label_count": len(labels),
                "label_counts": label_type_counts,
                # Issue #23: W1 may also be satisfied by the presence of a
                # height_mark label, not only by heights facts on disk.
                "has_height_mark": any(
                    isinstance(la, dict) and la.get("type") == "height_mark"
                    for la in labels
                ),
                # V5.1: the geometry label types present on this scene, so
                # the workflow gate can require real polygons (walls, roof,
                # openings) — not just facts — before a scene is "done".
                "label_types": sorted(t for t in label_types if t),
            }
            plan_status, plan = await _api_get(f"/datasets/{key}/{f}/plan-state/status")
            plan_data = (plan.get("data") if isinstance(plan, dict) else None) or {}
            scene_meta_by_file[f]["plan_state_exists"] = (
                plan_status == 200
                and bool(plan_data.get("exists"))
            )
            scene_meta_by_file[f]["plan_required_complete"] = (
                plan_status == 200
                and bool(plan_data.get("exists"))
                and bool(plan_data.get("required_complete"))
            )
            scene_meta_by_file[f]["plan_status"] = plan_data.get("status")
            scene_meta_by_file[f]["plan_next_action_available"] = bool(plan_data.get("next_action_available"))
            scene_meta_by_file[f]["plan_next_action"] = plan_data.get("next_action")
            scene_meta_by_file[f]["plan_terminality_reasons"] = plan_data.get("terminality_reasons") or []
        else:
            scene_meta_by_file[f] = {"scene_tag": None}
    return facts or {}, scene_meta_by_file


@mcp.tool()
async def get_workflow_state(key: str) -> dict:
    """Per-phase status (W0–W5) derived from on-disk facts.

    USE when:
      - At the start of a labeling run to see where the agent picks up.
      - After every phase to confirm the predicate flipped to `done`.
      - When deciding whether to call `export_house`.

    DON'T USE when:
      - You're just listing scenes — `get_house` gives that.

    Returns:
      `data` = {phase: {status: "done"|"in_progress"|"pending",
                        predicate_value: ...,
                        blockers: [...]},
                next_phase: "W4",
                exportable: bool,
                blockers: [...]}

    Implementation note (v0.1): workflow predicates live in the
    frontend's `ui/src/lib/workflow.ts`. This tool computes a
    server-side approximation from the dataset manifest + house_facts.
    The skill should still consult the SPA for ground truth when the
    agent's behavior diverges from expected. A future task moves the
    predicate set into a shared schema both consume.
    """
    started = time.time()
    try:
        status, body = await _api_get(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    facts, scene_meta_by_file = await _load_facts_and_scene_meta(key, body or {})
    state = _derive_workflow_state(body or {}, facts, scene_meta_by_file)
    next_tool = None
    if not state.get("exportable") and state.get("next_phase"):
        next_tool = {
            "name": "get_recommended_next_action",
            "args": {"key": key},
            "reason": f"phase {state['next_phase']} is the next to advance",
        }
    return _ok(state, next_tool=next_tool, started_at=started, status_code=status)


# V5.1 — geometry the honest gate requires, per scene type. A scene is
# "geometry-complete" when it carries at least one label of EACH required
# kind. This is what stops a facts-only scene (tagged + an assumed
# orientation, ZERO polygons) from passing as export-ready.
_REQUIRED_GEOMETRY: dict[str, list[str]] = {
    # Grundriss: walls define the footprint; openings are windows/doors.
    "grundriss": ["wall", "floorplan_opening"],
    # Schnitt: component lines carry roof planes / slabs / storey lines.
    "schnitt": ["component_line"],
    # Ansicht: façade openings (windows/doors/dormers) + roof/outline lines.
    "ansicht": ["view_opening"],
}


def _missing_geometry(scene_tag: str | None, label_types) -> list[str]:
    """Return the required geometry kinds this scene is MISSING (empty list
    = geometry-complete). Pure helper for the V5.1 gate."""
    req = _REQUIRED_GEOMETRY.get(scene_tag or "", [])
    have = set(label_types or [])
    return [k for k in req if k not in have]


def _is_groundfloor_scene(file: str | None, meta: dict[str, Any]) -> bool:
    """True for EG/Ground-floor floorplan scenes.

    Prefer explicit scene_level, but fall back to manifest/file/title
    hints because fresh extraction and reset flows can temporarily have
    one source populated before the others.
    """
    if meta.get("scene_tag") != "grundriss":
        return False
    explicit_level = str(meta.get("scene_level") or "").strip().lower()
    if explicit_level:
        return explicit_level in {"eg", "erdgeschoss", "ground", "groundfloor", "ground_floor"}
    manifest_floor = str(meta.get("manifest_floor") or "").strip().lower()
    if manifest_floor:
        return manifest_floor in {"eg", "erdgeschoss", "ground", "groundfloor", "ground_floor"}
    values = [
        meta.get("title"),
        file,
    ]
    text = " ".join(str(v).lower() for v in values if v)
    tokens = set(re.split(r"[^a-z0-9]+", text))
    return bool(tokens & {"eg", "erdgeschoss", "ground", "groundfloor"})


def _derive_workflow_state(dataset: dict, facts: dict, scene_meta: dict[str, dict]) -> dict:
    """Server-side approximation of ui/src/lib/workflow.ts predicates.

    Keep deliberately conservative: when in doubt, return `pending` and
    let the skill's actual labeling behavior drive the SPA to fill in
    the gaps. The status flips only on clear, observable conditions.

    Args:
      dataset: dataset manifest (drawings list).
      facts: HouseFacts (heights, extent, wall_thickness, orientation,
             calibration_per_scene, workflow).
      scene_meta: per-file labels-JSON projection {file: {scene_tag,
                  scene_orientation, scene_level}}. Reading the
                  workflow-vocabulary values from labels JSON, not from
                  the manifest's extraction-time kind/view/floor.
    """
    drawings = dataset.get("drawings") or []
    scenes_by_file = {d.get("file"): d for d in drawings}

    # W0: every scene has a non-null scene_tag + Ansicht/Schnitt have
    # scene_orientation + Grundriss have scene_level.
    w0_blockers: list[str] = []
    if not drawings:
        w0_blockers.append("no scenes extracted yet")
    # H3 (followups-2 tracker): orientation is OPTIONAL on ansicht/
    # schnitt. Missing orientation surfaces in list_anomalies as a
    # warning, not a W0 blocker.
    for d in drawings:
        f = d.get("file")
        meta = scene_meta.get(f, {})
        tag = meta.get("scene_tag")
        if tag in (None, "nicht_klassifiziert"):
            w0_blockers.append(f"{f}: untagged")
            continue
        if tag == "grundriss" and not meta.get("scene_level"):
            w0_blockers.append(f"{f}: missing level")
    w0_status = "done" if drawings and not w0_blockers else "pending"

    # Issue #20 + #23: no scenes means no labels, so any heights/extent/
    # orientation facts on disk are orphaned/stale (e.g. left behind by a
    # reset, or a workflow-only stub on a brand-new house). The
    # substantive phases W1–W4 (and W5) can only be `done` when scenes
    # actually exist — otherwise a house with `scenes_total: 0` (and
    # possibly null house_facts) falsely reports them complete, the SPA
    # progress bar lights up green before any work is done, and a labeling
    # agent reading get_workflow_state SKIPS the height anchor (W1) and
    # orientation (W3). The `has_scenes` short-circuit below is applied
    # UNIFORMLY to W1–W4 (and W5), not just W2.
    has_scenes = bool(drawings)
    has_height_mark = any(m.get("has_height_mark") for m in scene_meta.values())

    # W1: heights anchored — bezug_mm == 0 AND first_mm set, OR (issue #23)
    # at least one height_mark label has been placed on a scene.
    heights = (facts.get("heights") or {})
    w1_status = "done" if has_scenes and (
        (heights.get("bezug_mm") == 0 and heights.get("first_mm") not in (None, ""))
        or has_height_mark
    ) else "pending"

    # W2: extent.width_mm + depth_mm + wall_thickness.outer_mm
    extent = facts.get("extent") or {}
    wt = facts.get("wall_thickness") or {}
    w2_status = "done" if (
        has_scenes and extent.get("width_mm") and extent.get("depth_mm")
        and wt.get("outer_mm")
    ) else "pending"

    # W3: orientation set (either north_edge_label_id or north_angle_deg)
    orient = facts.get("orientation") or {}
    w3_status = "done" if (
        has_scenes and (
            orient.get("north_edge_label_id")
            or orient.get("north_angle_deg") is not None
        )
    ) else "pending"

    # W4: every Ansicht/Schnitt has facts.calibration_per_scene[file].
    # Issue #27: a single axis-aligned ref dim DOES calibrate the scene
    # under the square-pixel (isotropic) assumption (#26) — the persisted
    # calibration carries single_ref_assumed_isotropic. Such a scene counts
    # as calibrated, but we SURFACE the assumption so reviewers see W4
    # rests on it (vs a measured two-axis M1-both calibration).
    cps = facts.get("calibration_per_scene") or {}
    w4_blockers: list[str] = []
    w4_assumed_isotropic: list[str] = []
    has_calibration_targets = False
    for d in drawings:
        f = d.get("file")
        tag = scene_meta.get(f, {}).get("scene_tag")
        if tag in ("ansicht", "schnitt"):
            has_calibration_targets = True
            calib = cps.get(f)
            if not calib:
                w4_blockers.append(f"{f}: not calibrated")
            elif isinstance(calib, dict) and calib.get("single_ref_assumed_isotropic"):
                w4_assumed_isotropic.append(f)
    w4_status = "done" if has_calibration_targets and not w4_blockers else "pending"

    # Wgeo (V5.1): every scene of a geometry-bearing type carries the
    # required polygons (walls/openings/component lines), not just facts.
    # This is the honest-gate fix — a facts-only scene with zero geometry
    # is NOT complete. Only scenes whose tag is in _REQUIRED_GEOMETRY are
    # checked; sonstiges/detail/nicht_klassifiziert are exempt.
    wgeo_blockers: list[str] = []
    wgeo_groundfloor_blockers: list[str] = []
    has_geometry_targets = False
    for d in drawings:
        f = d.get("file")
        meta = scene_meta.get(f, {})
        tag = meta.get("scene_tag")
        if tag in _REQUIRED_GEOMETRY:
            has_geometry_targets = True
            scene_blockers: list[str] = []
            if not meta.get("plan_state_exists"):
                scene_blockers.append(f"{f}: missing scene plan state")
            elif not meta.get("plan_required_complete"):
                reasons = meta.get("plan_terminality_reasons") or []
                reason = f"; {'; '.join(str(r) for r in reasons[:3])}" if reasons else ""
                scene_blockers.append(f"{f}: scene plan incomplete{reason}")
            missing = _missing_geometry(tag, meta.get("label_types"))
            if missing:
                scene_blockers.append(f"{f}: missing geometry {missing}")
            wgeo_blockers.extend(scene_blockers)
            if _is_groundfloor_scene(f, meta):
                wgeo_groundfloor_blockers.extend(scene_blockers)
    wgeo_status = "done" if has_geometry_targets and not wgeo_blockers else (
        "pending" if has_scenes else "pending"
    )

    # W5: manual; user_skipped or phase_completed_at.detail
    wf = (facts.get("workflow") or {})
    w5_status = "done" if has_scenes and (
        (wf.get("phase_completed_at") or {}).get("detail")
        or (wf.get("user_skipped") or {}).get("detail")
    ) else "pending"

    # Issue #20: when there are no scenes, the substantive phases are
    # blocked on that, not on a missing field — say so plainly.
    no_scenes_blocker = "no scenes extracted yet"
    phases = {
        "W0": {"status": w0_status, "blockers": w0_blockers},
        "W1": {"status": w1_status, "blockers": [] if w1_status == "done"
               else ([no_scenes_blocker] if not has_scenes else ["heights.bezug_mm or first_mm missing"])},
        "W2": {"status": w2_status, "blockers": [] if w2_status == "done"
               else ([no_scenes_blocker] if not has_scenes else ["extent or wall_thickness missing"])},
        "W3": {"status": w3_status, "blockers": [] if w3_status == "done"
               else ([no_scenes_blocker] if not has_scenes else ["orientation not set"])},
        "W4": {"status": w4_status, "blockers": w4_blockers,
               "assumed_isotropic_scenes": w4_assumed_isotropic},
        "Wgeo": {"status": wgeo_status, "blockers": [] if wgeo_status == "done"
                 else ([no_scenes_blocker] if not has_scenes else wgeo_blockers),
                 "groundfloor_blockers": wgeo_groundfloor_blockers},
        "W5": {"status": w5_status, "blockers": ["W5 not marked complete"] if w5_status != "done" else []},
    }
    next_phase = None
    if phases["W0"]["status"] != "done":
        next_phase = "W0"
    elif wgeo_groundfloor_blockers:
        next_phase = "Wgeo"
    else:
        for p in ("W1", "W2", "W3", "W4", "Wgeo"):
            if phases[p]["status"] != "done":
                next_phase = p
                break
    # Export gating: ≥1 drawing with labels (mirrors api/main._sanity_check_house).
    labeled_count = sum(1 for d in scenes_by_file.values() if d.get("labeled"))
    exportable = bool(drawings) and labeled_count > 0
    return {
        "phases": phases,
        "next_phase": next_phase,
        "exportable": exportable,
        "blockers_total": sum(len(p["blockers"]) for p in phases.values()),
        "scenes_total": len(drawings),
        "labeled_scenes": labeled_count,
    }


# ── §5.3 Scene inspection (image tools — A5) ──────────────────────────────


@mcp.tool()
async def get_scene_view(
    key: str,
    file: str,
    region: str | None = None,
    tiers: str = "broad,finer",
    max_dim: int = 1600,
    enhance: str | None = None,
    format: str = "png8",
    style: str = "standard",
    target: str | None = None,
    target_line: str = "none",
    background_opacity: float | None = None,
    image_delivery: str = "inline",
) -> list[ImageContent | TextContent]:
    """Scene image with the three-tier coordinate grid overlay.

    USE when:
      - Labeling a scene — every coordinate-setting decision should
        consult a fresh grid view first.
      - Identifying scene_tag at W0 (without region; full image).
      - Reading a faint freehand/pencil scan — pass enhance="auto" (or
        "threshold" for the faintest) to lift contrast before you read.

    DON'T USE when:
      - You only need scene metadata — call `get_scene_meta`.

    Args:
      key:     house key, e.g. "house-22".
      file:    scene filename, e.g. "house-22-ansicht-sued.jpg".
      region:  optional 'x0,y0,x1,y1' (source-pixel coords) — agent zoom.
      tiers:   comma list of {broad, finer, detail}; default broad+finer.
               Pass detail only on small, intentional coordinate crops; it is
               too dense for overview or label QA views.
      max_dim: cap on the longer side of the output PNG; default 1600.
      enhance: contrast lift for faint scans (issue #2): one of
               none|auto|clahe|threshold (default none). "auto"/"clahe"
               apply CLAHE; "threshold" additionally binarizes. This is
               preprocessing for the vision-LLM reader, not OCR. Pixel
               positions are unchanged — coordinates stay SOURCE-pixel,
               so readings still map 1:1 to the un-cropped scene.
      format:  png|png8 (issue #3). Default png8 — a 256-colour palette
               PNG, typically 2-4x fewer bytes (and tokens) than RGBA at
               near-identical legibility. The verify-after-place loop
               reads one image per write, so this multiplies how much of
               a drive fits in context. Pass format="png" only when you
               need full-fidelity colour.
      style:   standard|coordinate_multicolor|coordinate_audit|coordinate_pair.
               Use coordinate_multicolor for hard coordinate reads: every
               tier's grid lines cycle through distinct colours and the
               coordinate labels are colour-matched to their lines, making
               it easier to trace a point back to its x/y labels.
      background_opacity:
               Optional source drawing opacity in (0,1]. Use about 0.5 for
               labeling/placement and 0.2 for QA when you want labels and
               grid to dominate. If omitted, enhanced views keep the legacy
               contrast-preserving fade.

    Per H4 (followups-2 tracker): when `region` is given, the output
    keeps 1:1 native resolution up to `max_dim`. A 400×400 crop comes
    back as 400×400, NOT scaled up. Small rotated dim text stays
    readable. Full-image renders (no region) still cap at `max_dim`.

    Returns: one ImageContent (PNG) and one TextContent with the image
    metadata (source dimensions, region applied, tier step sizes,
    image_bytes so you can see the payload cost). Grid labels show
    SOURCE pixels — use them directly in `upsert_label` against the
    un-cropped scene.
    """
    started = time.time()
    params: dict[str, Any] = {
        "tiers": tiers, "max_dim": max_dim, "format": format,
        "style": style, "target_line": target_line,
    }
    if region:
        params["region"] = region
    if enhance:
        params["enhance"] = enhance
    if target:
        params["target"] = target
    if background_opacity is not None:
        params["background_opacity"] = background_opacity
    try:
        status, content, ctype = await _api_get_bytes(f"/datasets/{key}/{file}/grid", params=params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _wrap_text(_api_unreachable_error(started))
        status, content, ctype = await _api_get_bytes(f"/datasets/{key}/{file}/grid", params=params)
    if status >= 400:
        try:
            err_body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            err_body = {}
        return _wrap_text(_http_status_to_error(status, err_body, started))
    # Also fetch meta so the agent gets dimensions + cache key context.
    # NOTE: `extraction_kind` here is the dataset manifest's `kind`
    # (floorplan/elevation/section/detail) — a SEPARATE vocabulary
    # from the workflow's `scene_tag` (grundriss/ansicht/schnitt/
    # sonstiges/nicht_klassifiziert). Field renamed in G6 to stop
    # tripping the agent (and me) into thinking they're the same.
    meta_status, meta_body = await _api_get(f"/datasets/{key}")
    scene_meta = {}
    if meta_status == 200:
        for d in (meta_body or {}).get("drawings") or []:
            if d.get("file") == file:
                scene_meta = {
                    "file": file,
                    "extraction_kind": d.get("kind"),
                    "view": d.get("view"),
                    "floor": d.get("floor"),
                    "labeled": d.get("labeled"),
                    "label_count": d.get("label_count"),
                }
                break
    return _image_delivery_payload(
        content=content,
        ctype=ctype,
        metadata={
            "image_format": "PNG",
            "scene_meta": scene_meta,
            "region": region,
            "tiers": tiers.split(","),
            "max_dim": max_dim,
            "enhance": enhance or "none",
            "format": format,
            "style": style,
            "target": target,
            "target_line": target_line,
            "background_opacity": background_opacity,
        },
        started_at=started,
        status_code=status,
        image_delivery=image_delivery,
    )


@mcp.tool()
async def get_scene_view_with_labels(
    key: str,
    file: str,
    region: str | None = None,
    tiers: str = "broad",
    max_dim: int = 900,
    enhance: str | None = None,
    format: str = "png8",
    style: str = "qa",
    target: str | None = None,
    target_line: str = "none",
    background_opacity: float | None = None,
    clean: bool = True,
    contrast: str = "high",
    show_relations: str = "required",
    show_height_guides: str = "auto",
    show_openings: str = "full",
    include_hidden: bool = False,
    image_delivery: str = "inline",
) -> list[ImageContent | TextContent]:
    """Scene image + grid overlay + EVERY LABEL CURRENTLY SAVED rendered
    on top. This is the agent's verify view — call it after every
    geometry-bearing label write to confirm the label landed on the
    intended feature.

    USE when:
      - You need global or multi-label QA after several edits, topology
        review, or a wider relation check.
      - You're suspicious of an earlier label and want to spot-check
        without opening the SPA in a browser.

    DON'T USE when:
      - You haven't placed any labels yet — use `get_scene_view` for
        a clean image.
      - You just wrote or updated ONE label — prefer
        `verify_label_placement`, which auto-crops tightly around that
        label and keeps context smaller.

    Args:
      key:     house key.
      file:    scene filename.
      region:  optional 'x0,y0,x1,y1' (source-pixel coords) — zoom
               around the just-placed label for the closest look.
      tiers:   comma list of {broad, finer, detail}. Default 'broad'.
               Use denser tiers only for coordinate-reading views, not QA.
      max_dim: cap on the longer side of the output PNG; default 1600.
               Per H4, small region crops keep 1:1 native resolution.
      enhance: contrast lift for faint scans (issue #2):
               none|auto|clahe|threshold (default none). Coordinates stay
               SOURCE-pixel; labels still render at their saved positions.
      format:  png|png8 (issue #3). Default png8 — the cheaper palette
               PNG. Use it for the verify-after-place loop to keep each
               read affordable; pass format="png" for full-fidelity RGBA.
      style:   qa|ink_compare|semantic|standard|coordinate_multicolor|
               coordinate_audit|coordinate_pair.
               qa/ink_compare render labels lightly so source ink remains
               visible; semantic renders full wall/opening bodies.
               coordinate_multicolor is the preferred coordinate-audit
               style when verifying exact placement because line colours
               repeat as landmarks and labels match their line colour.
      background_opacity:
               Optional source drawing opacity in (0,1]. Use about 0.5 for
               normal labeling and 0.2 for visual QA so saved labels stand
               out strongly against faint source ink.
      clean:   When true, render semantic labels without the coordinate grid.
               Defaults true because verification/QA must distinguish source
               ink from saved labels without grid noise.
      contrast:
               normal|high. High contrast keeps the same semantics but makes
               labels/chips stronger for agent QA.
      show_relations:
               required|all|none. Required shows correctness-critical links
               such as opening→wall and dimension number→distance.
      show_height_guides:
               auto|always|never. Auto shows datum guide lines in Agent View /
               clean QA contexts and keeps normal editor views quieter.
      show_openings:
               full|outline|hide. Use outline/hide when opening quads obscure
               wall ink during detail QA.
      include_hidden:
               When false, respect display.hidden_label_ids like the UI.

    Returns: one ImageContent (PNG) + one TextContent envelope.

    Render vocabulary:
      wall body band + axis    — wall; qa/ink_compare use light bands
      opening body + internals — opening; qa/ink_compare use cut outlines
      polyline/region          — component_line
      datum marker + line      — height_mark (Bezug is visually distinct)
      dimension + caps + value — dimensioned_distance
      text chip / bbox         — dimension_number
      warning chips/rings      — uncertain/missing/not_readable

    Per the context-bloat policy, use this full labeled view deliberately
    for global QA. For routine verify-after-write, call
    `verify_label_placement` first. If the rendered geometry doesn't land
    on the intended feature, `update_label_attrs` or `delete_label` +
    re-place. Budget 3 attempts per label; flag `status: uncertain` on
    the closest if it still misses.
    """
    started = time.time()
    effective_background_opacity = background_opacity
    if clean and effective_background_opacity is None:
        effective_background_opacity = 0.2
    params: dict[str, Any] = {
        "tiers": tiers, "max_dim": max_dim, "format": format,
        "style": style, "target_line": target_line,
        "clean": clean, "contrast": contrast,
        "show_relations": show_relations,
        "show_height_guides": show_height_guides,
        "show_openings": show_openings,
        "include_hidden": include_hidden,
    }
    if region:
        params["region"] = region
    if enhance:
        params["enhance"] = enhance
    if target:
        params["target"] = target
    if effective_background_opacity is not None:
        params["background_opacity"] = effective_background_opacity
    try:
        status, content, ctype = await _api_get_bytes(
            f"/datasets/{key}/{file}/grid-with-labels", params=params,
        )
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _wrap_text(_api_unreachable_error(started))
        status, content, ctype = await _api_get_bytes(
            f"/datasets/{key}/{file}/grid-with-labels", params=params,
        )
    if status >= 400:
        try:
            err_body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            err_body = {}
        return _wrap_text(_http_status_to_error(status, err_body, started))
    # Also fetch the scene's labels list so the agent has a textual
    # accompaniment to the image (label_id ↔ what's drawn).
    lbl_status, lbl_body = await _api_get(f"/labels/dataset/{key}/{file}")
    label_summaries: list[dict] = []
    if lbl_status == 200 and isinstance(lbl_body, dict):
        for lab in (lbl_body.get("labels") or []):
            attrs = lab.get("attributes") or {}
            label_summaries.append({
                "id": lab.get("id"),
                "type": lab.get("type"),
                "status": lab.get("status"),
                "is_reference": attrs.get("is_reference") if lab.get("type") == "dimensioned_distance" else None,
                "value_mm": attrs.get("value_mm"),
                "summary": _label_summary(lab),
            })
    return _image_delivery_payload(
        content=content,
        ctype=ctype,
        metadata={
            "image_format": "PNG",
            "format": format,
            "labels_in_view": label_summaries,
            "region": region,
            "tiers": tiers.split(","),
            "style": style,
            "target": target,
            "target_line": target_line,
            "background_opacity": effective_background_opacity,
            "clean": clean,
            "contrast": contrast,
            "show_relations": show_relations,
            "show_height_guides": show_height_guides,
            "show_openings": show_openings,
            "include_hidden": include_hidden,
            "render_contract_version": "labeling-render-contract/2026-05-31",
            "hint": (
                "Verify the rendered geometry lands on the intended feature. "
                "If a label is off, update_label_attrs (preferred for small "
                "shifts) or delete_label + re-place. Budget 3 attempts per "
                "label, then set status='uncertain' on the closest miss."
            ),
        },
        started_at=started,
        status_code=status,
        image_delivery=image_delivery,
    )


@mcp.tool()
async def verify_label_placement(
    key: str,
    file: str,
    label_id: str,
    pad_px: int = 80,
    tiers: str = "finer,detail",
    max_dim: int = 1600,
    enhance: str | None = None,
    format: str = "png8",
    snap_radius_px: int = 18,
    background_opacity: float | None = None,
    contrast: str = "high",
    show_relations: str = "required",
    show_height_guides: str = "auto",
    show_openings: str = "full",
    include_hidden: bool = False,
    image_delivery: str = "inline",
) -> list[ImageContent | TextContent]:
    """H5-7 — sugar over `get_scene_view_with_labels`: auto-crop around
    a single label so the agent doesn't have to compute the region.

    Reads the label's geometry, computes a tight bbox around all its
    points + `pad_px` margin, clamps to image bounds, and returns the
    verify view of that crop. Useful as the single tool call right
    after `upsert_label` / `add_reference_dim` / `update_label_attrs`.

    USE when:
      - You just placed or updated a label and want one tool call to
        confirm the placement. Pair with the 3-attempt verify budget
        from operating principle #9.

    DON'T USE when:
      - You're verifying multiple labels at once — call
        `get_scene_view_with_labels` directly with a wider region.

    Args:
      key, file: scene identifier.
      label_id:  the label to zoom into.
      pad_px:    margin around the label's bbox (source pixels).
      tiers:     grid tiers to draw; defaults to 'finer,detail' for
                 the closest-possible look.
      max_dim:   max output dim; default 900 keeps normal verification
                 crops compact while preserving local detail. Per H4 small
                 crops stay 1:1.
      enhance:   contrast lift for faint scans (issue #2):
                 none|auto|clahe|threshold (default none).
      format:    png|png8 (issue #3). Default png8 — the cheaper palette
                 PNG; ideal for the verify-after-place loop.
      background_opacity:
                 Optional source drawing opacity in (0,1]. For placement
                 verification use 0.2 to make saved geometry dominate while
                 retaining enough source ink to spot misses.
      contrast:  normal|high. Defaults high for QA.
      show_relations:
                 required|all|none relation cues. Defaults required.
      show_height_guides:
                 auto|always|never datum guide lines for height marks.
      show_openings:
                 full|outline|hide opening rendering for this verification crop.
      include_hidden:
                 Include labels hidden in the UI display preferences.
      snap_radius_px: search radius for the numeric offset check (issue
                 #10). The envelope reports `offset_px` — the vector from
                 the label's anchor to the nearest drawn feature — so you
                 correct by a precise delta instead of eyeballing.

    Returns: image + envelope with the same shape as
    `get_scene_view_with_labels`, PLUS (issue #10) `offset_px`,
    `nearest_feature_px`, `nearest_feature_distance_px`, and an
    `offset_hint`. The envelope's `labels_in_view` will typically contain
    just this one label (plus any neighbours in the padded crop).
    """
    started = time.time()
    # Look up the label to read its geometry.
    label_resp = await get_label(key=key, file=file, label_id=label_id)
    if not label_resp.get("ok"):
        return _wrap_text(label_resp)
    lab = label_resp["data"]
    geom = lab.get("geometry") or {}
    pts: list[tuple[float, float]] = []
    for k in ("start", "end", "anchor"):
        v = geom.get(k)
        if isinstance(v, list) and len(v) >= 2:
            pts.append((float(v[0]), float(v[1])))
    for k in ("points", "polygon", "quad", "top_edge", "bottom_edge"):
        seq = geom.get(k)
        if isinstance(seq, list):
            for p in seq:
                if isinstance(p, list) and len(p) >= 2:
                    pts.append((float(p[0]), float(p[1])))
    if "circle" in geom:
        c = geom["circle"]
        center = c.get("center") or [0, 0]
        r = float(c.get("radius_px") or 0)
        pts.append((center[0] - r, center[1] - r))
        pts.append((center[0] + r, center[1] + r))
    if not pts:
        return _wrap_text(_err(
            "label_has_no_geometry",
            f"label {label_id!r} carries no positional geometry — nothing to verify",
            started_at=started,
        ))
    # Clamp the crop to image bounds.
    meta = await get_scene_meta(key=key, file=file)
    if not meta.get("ok"):
        return _wrap_text(meta)
    img_w, img_h = meta["data"].get("image_size_px") or [None, None]
    if img_w is None:
        return _wrap_text(_err(
            "scene_missing_image_size",
            "scene_meta has no image_size_px — cannot clamp crop",
            started_at=started,
        ))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0 = max(0, int(min(xs)) - pad_px)
    y0 = max(0, int(min(ys)) - pad_px)
    x1 = min(int(img_w), int(max(xs)) + pad_px)
    y1 = min(int(img_h), int(max(ys)) + pad_px)
    # Ensure non-degenerate region — pad if the label is a point.
    if x1 - x0 < 20:
        x1 = min(int(img_w), x0 + 20)
    if y1 - y0 < 20:
        y1 = min(int(img_h), y0 + 20)
    region = f"{x0},{y0},{x1},{y1}"
    view = await get_scene_view_with_labels(
        key=key, file=file, region=region, tiers=tiers, max_dim=max_dim,
        enhance=enhance,
        format=format,
        style="ink_compare",
        background_opacity=background_opacity,
        clean=True,
        contrast=contrast,
        show_relations=show_relations,
        show_height_guides=show_height_guides,
        show_openings=show_openings,
        include_hidden=include_hidden,
        image_delivery=image_delivery,
    )

    # Issue #10: numeric offset feedback. How far is the label's anchor
    # from the nearest drawn feature? The agent can then correct by a
    # precise delta instead of eyeballing the visual crop.
    if isinstance(geom.get("anchor"), list) and len(geom["anchor"]) >= 2:
        anchor = [float(geom["anchor"][0]), float(geom["anchor"][1])]
    elif isinstance(geom.get("start"), list) and len(geom["start"]) >= 2:
        anchor = [float(geom["start"][0]), float(geom["start"][1])]
    else:
        anchor = [sum(xs) / len(xs), sum(ys) / len(ys)]
    try:
        rp_status, rp_body = await _api_get(
            f"/datasets/{key}/{file}/resolve-point",
            params={
                "point": f"{anchor[0]},{anchor[1]}",
                "frame": "source",
                "snap": "true",
                "snap_radius_px": snap_radius_px,
            },
        )
    except (httpx.HTTPError, httpx.RequestError):
        rp_status, rp_body = 0, None
    if (
        rp_status == 200 and isinstance(rp_body, dict)
        and view and isinstance(view[-1], TextContent)
    ):
        try:
            env = json.loads(view[-1].text)
            data = env.get("data") or {}
            data["anchor_checked"] = anchor
            if rp_body.get("snapped"):
                data["offset_px"] = rp_body.get("offset_px")
                data["nearest_feature_px"] = rp_body.get("feature_point")
                data["nearest_feature_distance_px"] = rp_body.get("distance_px")
                data["offset_hint"] = (
                    "offset_px is the vector FROM the label's anchor TO the "
                    "nearest drawn feature. To center the anchor on that "
                    "feature, shift it by offset_px (update_label_attrs for a "
                    f"small move). Searched within {snap_radius_px}px."
                )
            else:
                data["offset_px"] = None
                data["offset_hint"] = (
                    f"No drawn feature within {snap_radius_px}px of the anchor — "
                    "the anchor may already be clear of ink, or widen "
                    "snap_radius_px and re-check."
                )
            env["data"] = data
            view[-1].text = json.dumps(env, indent=2)
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    return view


@mcp.tool()
async def resolve_scene_point(
    key: str,
    file: str,
    point: list[float],
    region: str | None = None,
    max_dim: int = 1600,
    frame: str = "source",
    snap: bool = True,
    snap_radius_px: int = 14,
) -> dict:
    """Issue #10 — turn a roughly-placed point into a precise SOURCE-pixel
    coordinate, so you DON'T have to read absolute coords off a dense grid.

    Two inversions of the hard part:
      1. Local-crop coordinates. If you called `get_scene_view(region=...)`
         and want to point at something in that crop, pass frame="crop"
         with `point` in the CROP's local pixel frame (0..w, 0..h) and the
         same `region`/`max_dim` you used. The server maps it back to
         source pixels — short tracing distance in a small crop, low error.
      2. Snap-to-feature. With snap=true (default) the mapped point is
         snapped to the nearest drawn feature (Höhenkote tick-triangle,
         line, dim arrow) within `snap_radius_px`. Place approximately;
         the server lands you on the real mark.

    USE when:
      - About to `upsert_label` / `add_reference_dim`: resolve each
        endpoint/anchor here first, then pass the returned `source_point`.
      - You read a feature in a zoom crop and want its source coordinate
        without interpolating across gridlines.

    Args:
      key, file:      scene identifier.
      point:          [x, y]. Source pixels when frame='source'; the crop's
                      local pixel frame when frame='crop'.
      region:         'x0,y0,x1,y1' source-pixel crop (required for
                      frame='crop') — the same rect you passed to
                      get_scene_view.
      max_dim:        the same max_dim you used for the crop (so a
                      downscaled crop maps back correctly).
      frame:          'source' | 'crop'.
      snap:           snap the mapped point to the nearest feature.
      snap_radius_px: snap search radius (source pixels). Use a small
                      radius near dense content so it doesn't grab a
                      neighbour.

    Returns: `data` = {source_point:[x,y], mapped_point:[x,y],
      snapped:bool, offset_px:[dx,dy], distance_px, feature_point, frame}.
      Feed `source_point` straight into the write tools.
    """
    started = time.time()
    if frame not in ("source", "crop"):
        return _err("bad_frame", "frame must be 'source' or 'crop'", started_at=started)
    if not (isinstance(point, list) and len(point) == 2):
        return _err("bad_point", "point must be [x, y]", started_at=started)
    params: dict[str, Any] = {
        "point": f"{point[0]},{point[1]}",
        "max_dim": max_dim,
        "frame": frame,
        "snap": "true" if snap else "false",
        "snap_radius_px": snap_radius_px,
    }
    if region:
        params["region"] = region
    try:
        status, body = await _api_get(f"/datasets/{key}/{file}/resolve-point", params=params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(f"/datasets/{key}/{file}/resolve-point", params=params)
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def get_pdf_page_view(
    key: str,
    page: int,
    dpi: int = 144,
    region: str | None = None,
    tiers: str = "broad,finer,detail",
    max_dim: int = 1600,
    image_delivery: str = "inline",
) -> list[ImageContent | TextContent]:
    """PDF page render with grid overlay — used for scene identification.

    USE when:
      - Identifying scenes at W0 / extract-time: render each page,
        emit bboxes, call `extract_scenes`.
      - Debugging a misextracted scene by viewing the source PDF page.

    Args:
      key:     house key.
      page:    1-indexed page number in the consolidated PDF.
      dpi:     render DPI; default 144. The `extract_scenes` tool needs
               to know the DPI the agent saw to convert bbox pixels →
               PDF units. PASS THIS SAME DPI THROUGH.
      region:  optional 'x0,y0,x1,y1' to zoom (pixel coords at `dpi`).
      tiers:   comma list of {broad, finer, detail}.
      max_dim: cap on longer side; default 1600.

    Returns image + metadata text. The text envelope includes the
    rendered DPI so the agent can store it for the matching
    `extract_scenes` call.
    """
    started = time.time()
    params: dict[str, Any] = {"dpi": dpi, "tiers": tiers, "max_dim": max_dim}
    if region:
        params["region"] = region
    try:
        status, content, ctype = await _api_get_bytes(f"/pdfs/{key}/page/{page}/grid", params=params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _wrap_text(_api_unreachable_error(started))
        status, content, ctype = await _api_get_bytes(f"/pdfs/{key}/page/{page}/grid", params=params)
    if status >= 400:
        try:
            err_body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            err_body = {}
        return _wrap_text(_http_status_to_error(status, err_body, started))
    pdf_status, pdf_body = await _api_get(f"/pdfs/{key}/info")
    page_meta = {}
    if pdf_status == 200:
        for p in (pdf_body or {}).get("pages") or []:
            if p.get("page") == page:
                page_meta = p
                break
    return _image_delivery_payload(
        content=content,
        ctype=ctype,
        metadata={
            "image_format": "PNG",
            "page": page,
            "dpi": dpi,
            "page_pdf_size": page_meta,
            "region": region,
            "tiers": tiers.split(","),
            "hint": "If you emit a bbox from this view, remember to pass the same dpi to extract_scenes so pixel→PDF conversion is correct.",
        },
        started_at=started,
        status_code=status,
        image_delivery=image_delivery,
    )


def _wrap_text(envelope: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(envelope, indent=2))]


def _image_delivery_payload(
    *,
    content: bytes,
    ctype: str,
    metadata: dict[str, Any],
    started_at: float,
    status_code: int,
    image_delivery: str,
) -> list[ImageContent | TextContent]:
    delivery = (image_delivery or "inline").lower()
    if delivery not in {"inline", "handle", "auto"}:
        return _wrap_text(_err(
            "bad_image_delivery",
            "image_delivery must be one of inline, handle, auto",
            started_at=started_at,
            status_code=400,
        ))
    should_inline = delivery == "inline" or (
        delivery == "auto" and len(content) <= IMAGE_HANDLE_INLINE_THRESHOLD
    )
    payload = {
        **metadata,
        "image_bytes": len(content),
        "image_delivery": "inline" if should_inline else "handle",
        "inline_threshold_bytes": IMAGE_HANDLE_INLINE_THRESHOLD,
    }
    if should_inline:
        image = ImageContent(
            type="image",
            data=base64.b64encode(content).decode("ascii"),
            mimeType=ctype or "image/png",
        )
        return [
            image,
            TextContent(type="text", text=json.dumps(_ok(payload, started_at=started_at, status_code=status_code), indent=2)),
        ]

    suffix = ".jpg" if "jpeg" in (ctype or "").lower() else ".png"
    digest = hashlib.sha256(content).hexdigest()[:24]
    out = IMAGE_HANDLE_DIR / f"{digest}{suffix}"
    if not out.exists():
        out.write_bytes(content)
    payload["image_handle"] = {
        "id": digest,
        "path": str(out),
        "uri": out.resolve().as_uri(),
        "mime_type": ctype or "image/png",
        "bytes": len(content),
        "garbage_collectable": True,
    }
    payload["visual_access_note"] = (
        "Handle mode omits inline base64 to reduce context. Request "
        "image_delivery='inline' when the current model turn must inspect pixels."
    )
    return [TextContent(type="text", text=json.dumps(_ok(payload, started_at=started_at, status_code=status_code), indent=2))]


@mcp.tool()
async def cleanup_image_handles(max_age_seconds: int = 86_400) -> dict:
    """Garbage-collect rendered image handles by age.

    USE when:
      - A long labeling run used `image_delivery="handle"` and you want to
        remove old handle files from `tmp/mcp-image-handles`.

    DON'T USE when:
      - A current worker may still need recently returned handle paths.
        Increase `max_age_seconds` or wait until the run handoff is written.
    """
    started = time.time()
    cutoff = time.time() - max(0, int(max_age_seconds))
    removed = []
    kept = 0
    for path in IMAGE_HANDLE_DIR.glob("*"):
        if not path.is_file():
            continue
        if path.stat().st_mtime < cutoff:
            size = path.stat().st_size
            path.unlink()
            removed.append({"path": str(path), "bytes": size})
        else:
            kept += 1
    return _ok({
        "directory": str(IMAGE_HANDLE_DIR),
        "max_age_seconds": max_age_seconds,
        "removed_count": len(removed),
        "removed_bytes": sum(item["bytes"] for item in removed),
        "kept_count": kept,
        "removed": removed[:20],
        "truncated": len(removed) > 20,
    }, started_at=started)


# ── §5.1 Discovery (cont.) ────────────────────────────────────────────────


def _level_or_orientation_for_plan(meta: dict[str, Any]) -> str | None:
    tag = meta.get("scene_tag")
    if tag == "grundriss":
        return meta.get("scene_level") or meta.get("manifest_floor")
    if tag in ("ansicht", "schnitt"):
        return meta.get("scene_orientation")
    return None


def _scene_plan_priority(file: str, meta: dict[str, Any]) -> tuple[int, str]:
    tag = meta.get("scene_tag")
    if _is_groundfloor_scene(file, meta):
        return (0, file)
    if tag == "grundriss":
        return (1, file)
    if tag == "schnitt":
        return (2, file)
    if tag == "ansicht":
        return (3, file)
    return (9, file)


def _scene_needs_plan_work(file: str, meta: dict[str, Any]) -> bool:
    tag = meta.get("scene_tag")
    if tag not in _REQUIRED_GEOMETRY:
        return False
    if not meta.get("plan_state_exists"):
        return True
    if not meta.get("plan_required_complete"):
        return True
    if _missing_geometry(tag, meta.get("label_types")):
        return True
    return False


async def _recommended_scene_plan_action(key: str, *, groundfloor_only: bool = False) -> dict | None:
    status, ds = await _api_get(f"/datasets/{key}")
    if status >= 400 or not isinstance(ds, dict):
        return None
    _facts, scene_meta = await _load_facts_and_scene_meta(key, ds)
    candidates: list[tuple[tuple[int, str], str, dict[str, Any]]] = []
    for drawing in ds.get("drawings") or []:
        file = drawing.get("file")
        if not file:
            continue
        meta = scene_meta.get(file) or {}
        if groundfloor_only and not _is_groundfloor_scene(file, meta):
            continue
        if _scene_needs_plan_work(file, meta):
            candidates.append((_scene_plan_priority(file, meta), file, meta))
    if not candidates:
        return None
    _priority, file, meta = sorted(candidates, key=lambda item: item[0])[0]
    if not meta.get("plan_state_exists"):
        return {
            "phase": "Wgeo",
            "suggested_tool": "create_scene_plan_state_from_template",
            "suggested_args": {
                "key": key,
                "file": file,
                "scene_tag": meta.get("scene_tag") or "nicht_klassifiziert",
                "level_or_orientation": _level_or_orientation_for_plan(meta),
            },
            "reason": (
                f"{file} is the next geometry scene and has no scene plan. "
                "Create the plan before placing labels."
            ),
            "scene_priority": "groundfloor-first" if _is_groundfloor_scene(file, meta) else "scene-order",
            "blockers_in_phase": [f"{file}: missing scene plan state"],
        }
    next_action = meta.get("plan_next_action")
    return {
        "phase": "Wgeo",
        "suggested_tool": "get_scene_plan_next_action",
        "suggested_args": {"key": key, "file": file},
        "reason": (
            f"{file} has an incomplete required scene plan. Work exactly one "
            "plan action through start/attempt/evidence/finish/evaluate before moving on."
        ),
        "scene_priority": "groundfloor-first" if _is_groundfloor_scene(file, meta) else "scene-order",
        "scene_plan_status": meta.get("plan_status"),
        "next_action": next_action,
        "blockers_in_phase": [
            f"{file}: scene plan incomplete",
            *[str(r) for r in (meta.get("plan_terminality_reasons") or [])[:3]],
        ],
    }


async def _scene_order_guard(key: str, file: str) -> dict | None:
    """Return a compact redirect if global scene priority blocks `file`."""
    status, ds = await _api_get(f"/datasets/{key}")
    if status >= 400 or not isinstance(ds, dict):
        return None
    _facts, scene_meta = await _load_facts_and_scene_meta(key, ds)
    requested_meta = scene_meta.get(file) or {}
    if _is_groundfloor_scene(file, requested_meta):
        return None

    recommended = await _recommended_scene_plan_action(key, groundfloor_only=True)
    recommended_args = (recommended or {}).get("suggested_args") or {}
    recommended_file = recommended_args.get("file")
    if not recommended_file or recommended_file == file:
        return None
    state_env = await get_workflow_state(key=key)
    state = state_env.get("data") if state_env.get("ok") else {}
    wgeo = (state.get("phases") or {}).get("Wgeo") if isinstance(state, dict) else {}
    return {
        "code": "groundfloor_first_blocked",
        "requested_file": file,
        "recommended_file": recommended_file,
        "recommended_tool": (recommended or {}).get("suggested_tool") or "get_scene_plan_next_action",
        "recommended_args": recommended_args,
        "recommended_action": (recommended or {}).get("next_action"),
        "scene_priority": "groundfloor-first",
        "scene_order_blocked": True,
        "reason": (
            "Groundfloor required geometry is still incomplete. Work the "
            "recommended EG scene-plan action before requesting non-EG "
            "floorplans, sections, or elevations."
        ),
        "blockers_in_phase": list((wgeo or {}).get("groundfloor_blockers") or (recommended or {}).get("blockers_in_phase") or [])[:5],
    }


async def _is_file_groundfloor(key: str, file: str) -> bool:
    status, ds = await _api_get(f"/datasets/{key}")
    if status >= 400 or not isinstance(ds, dict):
        return False
    _facts, scene_meta = await _load_facts_and_scene_meta(key, ds)
    return _is_groundfloor_scene(file, scene_meta.get(file) or {})


@mcp.tool()
async def get_recommended_next_action(key: str) -> dict:
    """Convenience wrapper: derives the next thing the agent should do
    from the workflow state, returning a tool call template.

    USE when:
      - You're starting an iteration loop and want a single source of
        truth for "what now?".

    DON'T USE when:
      - You're already mid-phase — re-call your own playbook step. This
        tool is for orientation, not constant lookup.

    Returns: `data` = {phase, suggested_tool, suggested_args, reason}
    or {done: true} when the house exports cleanly.
    """
    started = time.time()
    state_env = await get_workflow_state(key=key)
    if not state_env.get("ok"):
        return state_env
    state = state_env["data"]
    if state.get("exportable") and not state.get("blockers_total"):
        return _ok({"done": True, "reason": "all phases done; ready to export"}, started_at=started)
    phase = state.get("next_phase") or "W0"
    if phase == "Wgeo":
        groundfloor_only = bool((state["phases"].get("Wgeo") or {}).get("groundfloor_blockers"))
        plan_action = await _recommended_scene_plan_action(key, groundfloor_only=groundfloor_only)
        if plan_action:
            return _ok(plan_action, started_at=started)
    suggestions = {
        "W0": ("get_house", {"key": key}, "list scenes + their current tags; then set_scene_tag for each untagged"),
        "W1": ("get_house", {"key": key}, "pick an Ansicht with visible bezug + ridge; label height_marks; set_house_facts heights"),
        "W2": ("get_house", {"key": key}, "pick EG-Grundriss; add_reference_dim horizontal + vertical; set_house_facts extent + wall_thickness"),
        "W3": ("get_house", {"key": key}, "pick EG-Grundriss; identify north wall; set_house_facts orientation"),
        "W4": ("get_house", {"key": key}, "for each uncalibrated Ansicht/Schnitt: add_reference_dim h+v, recompute_homography"),
        "Wgeo": ("get_house_context_summary", {"key": key, "include_plan_status": True}, "create missing scene plans; then drive each geometry scene via get_scene_plan_next_action"),
        "W5": ("get_workflow_state", {"key": key}, "W5 is opt-in; if --with-detail, label view_openings + component_lines"),
    }
    tool_name, tool_args, reason = suggestions[phase]
    return _ok({
        "phase": phase,
        "suggested_tool": tool_name,
        "suggested_args": tool_args,
        "reason": reason,
        "blockers_in_phase": state["phases"][phase].get("blockers", []),
    }, started_at=started)


# ── §5.2 Intake ──────────────────────────────────────────────────────────


@mcp.tool()
async def list_pdfs() -> dict:
    """Every incoming PDF bundle (data/pdfs/incoming/<key>/).

    USE when:
      - About to call extract_scenes — need to know which house has a
        consolidated PDF ready.

    Returns: `data.pdfs` = [{key, consolidated_pdf, source_filenames,
                             page_count, state, user_notes}]
    """
    started = time.time()
    try:
        status, body = await _api_get("/pdfs/incoming")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get("/pdfs/incoming")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok({"pdfs": body or []}, started_at=started, status_code=status)


@mcp.tool()
async def get_pdf_info(key: str) -> dict:
    """Page count + per-page width_pt/height_pt for the consolidated PDF.

    USE when:
      - You're about to render PDF pages for scene identification — the
        page count tells you how many `get_pdf_page_view` calls to make.
      - Sanity-checking a `bbox_pixels` is within the page.

    Args:
      key: house key.

    Returns: `data` = {key, page_count, pages: [{page, width_pt, height_pt}]}
    """
    started = time.time()
    try:
        status, body = await _api_get(f"/pdfs/{key}/info")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(f"/pdfs/{key}/info")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def extract_scenes(
    key: str,
    items: list[dict],
    idempotency_key: str | None = None,
) -> dict:
    """Crop one or more scenes out of the consolidated PDF.

    USE when:
      - The agent has identified scene bboxes from `get_pdf_page_view`
        renders (W0/extract phase).
      - Re-extracting after adjusting a bbox (idempotent on (page, slug);
        re-extract overwrites the scene image and updates the manifest entry but
        preserves any existing labels.json).

    DON'T USE when:
      - The bundle has no consolidated PDF — `extract_scenes` returns
        409. Check via `get_pdf_info` first.

    Args:
      key: house key.
      items: list of crop specs. Each item:
        {
          "page": 1,                 // 1-indexed page in the PDF
          "bbox_pixels": [x0,y0,x1,y1],  // pixel coords AT THE DPI YOU SAW
          "dpi": 144,                 // view DPI used ONLY for bbox_pixels -> PDF units
          "bbox_pdf_units": [x0,y0,x1,y1], // alternative: PDF coords, no conversion
          "crop_dpi": 600,            // output raster DPI; default 600
          "format": "png",            // png|jpg; default png for lossless agent work
          "kind": "floorplan",        // floorplan|elevation|section|detail
          "view": "north",            // optional — for elevations/sections
          "floor": "eg",              // optional — for floorplans
          "title": "EG-Grundriss",    // optional human title
          "slug_override": null,      // optional slug
          "allow_blank": false,       // optional; bypass the blank-render guard
          "no_clip_expand": false,    // optional; bypass clip-detection bbox
                                      //   auto-expansion (issue #25)
          "bbox_is_authoritative": false // optional (V1.1); YOUR chosen bbox
                                      //   is final — never auto-expand it
        }
      idempotency_key: optional driver-supplied key for crash-replay safety.

    Issue #12: if a crop renders blank (a failed rasterization — e.g. a
    corrupt content stream in the merged PDF), extraction returns an error
    instead of writing an empty scene that would still report as
    `labeled`. Fix the merge / bbox, or pass `allow_blank: true` to force.

    Issue #25: the segmentation bbox can under-shoot a tall drawing
    (cutting the roof apex so the ridge/Firsthöhe is never captured). The
    API auto-expands the bbox toward any border the drawing's ink touches
    and re-crops until the drawing no longer hits an edge. To re-capture a
    clipped scene, re-extract it (idempotent on slug) — pass a wider bbox or
    just let the auto-expansion grow it. Set `no_clip_expand: true` to off.

    V1.1: when YOU chose the bbox deliberately (the vision-LLM-picks-the-
    extent flow — building + all dim chains + Nordpfeil + datum), pass
    `bbox_is_authoritative: true` so the auto-expansion never overrides your
    chosen crop. The recorded crop_from bbox then equals your input exactly.

    Returns: `data` = {extracted: [...new manifest entries...], intake_state: ...}

    Pixel→PDF conversion is handled here: the API takes bbox_pdf_units,
    so this tool multiplies bbox_pixels by (72 / dpi) before posting. Do not
    pass dpi=600 merely to request a 600 dpi crop when bbox_pixels came from a
    144 dpi page view; pass crop_dpi=600 instead. If bbox_pdf_units are used,
    dpi is accepted as a backwards-compatible output-DPI alias when crop_dpi is
    omitted.
    """
    started = time.time()
    if not items:
        return _err("schema_invalid", "items must be a non-empty list",
                    hint="pass at least one crop spec", started_at=started)
    api_items: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            return _err("schema_invalid", f"items must be objects, got {type(raw).__name__}",
                        started_at=started)
        bbox_px = raw.get("bbox_pixels") or raw.get("bbox_pdf_units")
        if not (isinstance(bbox_px, (list, tuple)) and len(bbox_px) == 4):
            return _err("bbox_zero_area", "bbox_pixels must be [x0,y0,x1,y1]",
                        started_at=started)
        has_bbox_pixels = "bbox_pixels" in raw
        view_dpi = int(raw.get("dpi", 144))
        if view_dpi <= 0:
            return _err("schema_invalid", "dpi must be > 0", started_at=started)
        crop_dpi_raw = raw.get("crop_dpi", raw.get("output_dpi"))
        crop_dpi = int(crop_dpi_raw) if crop_dpi_raw is not None else (
            600 if has_bbox_pixels else int(raw.get("dpi", 600))
        )
        if crop_dpi <= 0 or crop_dpi > 1200:
            return _err("schema_invalid", "crop_dpi must be in 1..1200", started_at=started)
        fmt = str(raw.get("format", "png")).strip().lower()
        if fmt == "jpeg":
            fmt = "jpg"
        if fmt not in {"png", "jpg"}:
            return _err("schema_invalid", "format must be 'png' or 'jpg'", started_at=started)
        x0, y0, x1, y1 = (float(v) for v in bbox_px)
        if not (x1 > x0 and y1 > y0):
            return _err("bbox_zero_area", f"bbox has non-positive area: {bbox_px}",
                        started_at=started)
        factor = 72.0 / view_dpi if has_bbox_pixels else 1.0
        api_items.append({
            "page": int(raw.get("page", 0)),
            "bbox_pdf_units": [x0 * factor, y0 * factor, x1 * factor, y1 * factor],
            "kind": raw.get("kind", "detail"),
            "view": raw.get("view"),
            "floor": raw.get("floor"),
            "title": raw.get("title"),
            "slug_override": raw.get("slug_override"),
            "dpi": crop_dpi,
            "format": fmt,
            "allow_blank": bool(raw.get("allow_blank", False)),
            "no_clip_expand": bool(raw.get("no_clip_expand", False)),
            # V1.1: when YOU (the vision-LLM) have chosen the crop extent
            # deliberately — building + all dim chains + Nordpfeil + datum —
            # set this so the #25 auto-expansion never overrides it.
            "bbox_is_authoritative": bool(raw.get("bbox_is_authoritative", False)),
        })
    try:
        status, body = await _api_post(f"/pdfs/{key}/extract", json_body={"items": api_items})
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_post(f"/pdfs/{key}/extract", json_body={"items": api_items})
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status,
               next_tool={
                   "name": "get_workflow_state",
                   "args": {"key": key},
                   "reason": "see what W0 needs next now that scenes exist",
               })


@mcp.tool()
async def split_scene(
    key: str,
    file: str,
    regions: list[dict],
    retire_parent: bool = True,
) -> dict:
    """Split an over-broad scene (a full-page lump holding several
    drawings) into one scene PER drawing (issue #11).

    A scene must be ONE drawing — lumping multiple drawings into a single
    scene makes scene_tag meaningless, breaks best-source routing, and
    makes calibration impossible (multiple coordinate frames in one image).

    Flow (region detection is YOUR job — the vision-LLM is the detector):
      1. View the lump with `get_scene_view(key, file)`.
      2. Identify each constituent drawing's bbox in the scene's own pixel
         frame (the SOURCE pixels the grid labels show).
      3. Call this tool with one region per drawing. Each region is
         re-cropped from the parent PDF page as a standalone scene, and the
         parent lump is retired (recycle-bin; restorable) unless
         retire_parent=false.

    USE when:
      - A just-extracted scene visibly contains 2+ distinct drawings
        (e.g. "4 facades on one sheet", or "EG+DG+Schnitt combined").
        Split BEFORE tagging — never tag a multi-drawing lump.

    DON'T USE when:
      - The scene is a single drawing — nothing to split.
      - The parent scene wasn't cropped from a PDF (no crop_from).

    Args:
      key:  house key.
      file: the over-broad parent scene file to split.
      regions: list of child specs, each:
        {
          "bbox_pixels": [x0,y0,x1,y1],  // in the PARENT scene's source px
          "kind": "elevation",           // floorplan|elevation|section|detail
          "view": "north",               // optional
          "floor": "eg",                 // optional
          "title": "Nordansicht",        // optional
          "slug_override": null           // optional
        }
      retire_parent: recycle the parent lump after the children are
        created (default true; restorable via the SPA / undo).

    Returns: `data` = {created: [...child manifest entries...],
      retired: <parent file or null>, parent_dims_px: [w,h]}.
    Blank child regions are rejected by the extract guard (issue #12), in
    which case nothing is retired.
    """
    started = time.time()
    from api.segment import scene_px_dims, scene_px_to_pdf, validate_region_px

    if not regions:
        return _err("schema_invalid", "regions must be a non-empty list",
                    hint="pass one region per constituent drawing", started_at=started)

    # Look up the parent scene's crop provenance.
    try:
        ds_status, ds = await _api_get(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        ds_status, ds = await _api_get(f"/datasets/{key}")
    if ds_status >= 400:
        return _http_status_to_error(ds_status, ds, started)
    parent = next((d for d in (ds or {}).get("drawings") or [] if d.get("file") == file), None)
    if parent is None:
        return _err("not_found", f"scene {file!r} not in dataset manifest", started_at=started)
    crop = parent.get("crop_from") or {}
    bbox = crop.get("bbox_pdf_units")
    page = crop.get("page")
    pdf_dpi = int(crop.get("dpi") or 0)
    if not (isinstance(bbox, list) and len(bbox) == 4 and page and pdf_dpi > 0):
        return _err(
            "not_splittable",
            f"scene {file!r} has no PDF crop_from (page/bbox/dpi) — cannot split",
            hint="split only applies to scenes extracted from a PDF page",
            started_at=started,
        )
    parent_dims = scene_px_dims(bbox, pdf_dpi)

    # Build one extract item per region, mapping parent-scene px -> PDF units.
    items: list[dict] = []
    for i, reg in enumerate(regions):
        if not isinstance(reg, dict):
            return _err("schema_invalid", f"regions[{i}] must be an object", started_at=started)
        err = validate_region_px(reg.get("bbox_pixels"), parent_dims)
        if err:
            return _err("bad_region", f"regions[{i}]: {err}", started_at=started)
        pdf_box = scene_px_to_pdf(reg["bbox_pixels"], bbox, pdf_dpi)
        items.append({
            "page": int(page),
            "bbox_pdf_units": pdf_box,
            "kind": reg.get("kind", "detail"),
            "view": reg.get("view"),
            "floor": reg.get("floor"),
            "title": reg.get("title"),
            "slug_override": reg.get("slug_override"),
            "dpi": pdf_dpi,
            "allow_blank": bool(reg.get("allow_blank", False)),
        })

    ex_status, ex_body = await _api_post(f"/pdfs/{key}/extract", json_body={"items": items})
    if ex_status >= 400:
        # e.g. a child region rendered blank (issue #12 guard) — leave the
        # parent intact so nothing is lost.
        return _http_status_to_error(ex_status, ex_body, started)
    created = (ex_body or {}).get("extracted") or []

    retired = None
    if retire_parent:
        del_status, del_body = await _api_delete(f"/pdfs/{key}/extract/{file}")
        if del_status >= 400:
            # Children exist; surface the retire failure but don't fail hard.
            return _ok(
                {"created": created, "retired": None, "parent_dims_px": list(parent_dims),
                 "warning": f"children created but parent not retired (status {del_status})"},
                started_at=started, status_code=ex_status,
            )
        retired = file

    return _ok(
        {"created": created, "retired": retired, "parent_dims_px": list(parent_dims)},
        started_at=started, status_code=ex_status,
    )


# ── §5.3 Scene inspection (cont.) ────────────────────────────────────────


@mcp.tool()
async def get_scene_meta(key: str, file: str) -> dict:
    """Compact metadata for one scene.

    USE when:
      - Checking the current scene_tag / view / floor / labeled status
        without pulling the whole house manifest.

    Returns: `data` = {file, scene_tag, view, floor, title, image_size_px,
                       labeled, label_count, calibration_status}
    """
    started = time.time()
    try:
        status, ds = await _api_get(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, ds = await _api_get(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, ds, started)
    target = next((d for d in (ds.get("drawings") or []) if d.get("file") == file), None)
    if target is None:
        return _err("scene_not_found", f"no scene {file!r} in {key!r}", started_at=started)
    # Labels JSON carries the workflow-time scene_tag + orientation + level +
    # image_size_px. The manifest carries the extraction-time `kind` (a
    # separate vocabulary: floorplan/elevation/section/detail).
    lbl_status, lbl = await _api_get(f"/labels/dataset/{key}/{file}")
    if lbl_status == 200 and isinstance(lbl, dict):
        scene_tag = lbl.get("scene_tag")
        scene_orientation = lbl.get("scene_orientation")
        scene_level = lbl.get("scene_level")
        image_size = lbl.get("image_size_px")
    else:
        scene_tag = scene_orientation = scene_level = image_size = None
    facts_status, facts = await _api_get(f"/datasets/{key}/house_facts")
    calibration = (facts.get("calibration_per_scene") or {}).get(file) if facts_status == 200 else None
    return _ok({
        "file": file,
        "scene_tag": scene_tag,                # workflow discriminator
        "extraction_kind": target.get("kind"), # extraction-time category
        "view": target.get("view"),
        "floor": target.get("floor"),
        "scene_orientation": scene_orientation,
        "scene_level": scene_level,
        "title": target.get("title"),
        "image_size_px": image_size,
        "labeled": bool(target.get("labeled")),
        "label_count": target.get("label_count", 0),
        "calibration_status": "calibrated" if calibration else "not_calibrated",
    }, started_at=started, status_code=status)


@mcp.tool()
async def list_scene_labels(key: str, file: str, max_labels: int = 100) -> dict:
    """Compact list of labels on one scene — id, type, status, summary.

    USE when:
      - You want to see what's already on a scene without the full
        geometry payload. Cheap; ≤ 200 bytes per label.

    DON'T USE when:
      - You need the actual coordinates — use `get_label`.

    Returns: `data.labels` = [{id, type, status, summary}] plus
      labels_total / labels_truncated. Pass a larger max_labels only when
      you really need the whole compact list.
    """
    started = time.time()
    try:
        status, body = await _api_get(f"/labels/dataset/{key}/{file}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(f"/labels/dataset/{key}/{file}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    labels = body.get("labels") or []
    max_labels = max(0, int(max_labels))
    summaries = [compact_label(lab) for lab in labels[:max_labels]]
    return _ok({
        "scene_tag": body.get("scene_tag"),
        "scene_orientation": body.get("scene_orientation"),
        "scene_level": body.get("scene_level"),
        "image_size_px": body.get("image_size_px"),
        "label_counts": label_counts(labels),
        "labels_total": len(labels),
        "labels_truncated": len(labels) > max_labels,
        "labels": summaries,
    }, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_context_summary(
    key: str,
    file: str,
    include_label_summaries: bool = True,
    include_plan_status: bool = True,
    max_labels: int = 20,
    max_blockers: int = 3,
) -> dict:
    """Compact routing summary for one scene.

    USE for normal scene routing before deciding whether you need full
    labels, full plan state, or fresh pixels. This intentionally omits
    geometry arrays and full Markdown by default.
    """
    started = time.time()
    ds_status, ds = await _api_get(f"/datasets/{key}")
    if ds_status >= 400:
        return _http_status_to_error(ds_status, ds, started)
    drawing = next((d for d in (ds.get("drawings") or []) if d.get("file") == file), None)
    if drawing is None:
        return _err("scene_not_found", f"no scene {file!r} in {key!r}", started_at=started)
    lbl_status, lbl = await _api_get(f"/labels/dataset/{key}/{file}")
    if lbl_status >= 400:
        return _http_status_to_error(lbl_status, lbl, started)
    labels = lbl.get("labels") or []
    meta = {
        "scene_tag": lbl.get("scene_tag"),
        "scene_level": lbl.get("scene_level"),
        "scene_orientation": lbl.get("scene_orientation"),
        "label_count": len(labels),
        "label_types": sorted(label_counts(labels)),
    }
    plan = None
    if include_plan_status:
        plan_status, plan_body = await _api_get(f"/datasets/{key}/{file}/plan-state/status")
        if plan_status == 200:
            plan = compact_plan_status(plan_body, max_blockers=max_blockers)
        elif plan_status == 404:
            plan = compact_plan_status(None, max_blockers=max_blockers)
        else:
            return _http_status_to_error(plan_status, plan_body, started)
    max_labels = max(0, int(max_labels))
    data = {
        "summary_contract": "mcp-context-bloat/scene-context-summary-v1",
        "scene": compact_scene_row(drawing, meta),
        "image_size_px": lbl.get("image_size_px"),
        "label_counts": label_counts(labels),
        "labels_total": len(labels),
        "labels_truncated": include_label_summaries and len(labels) > max_labels,
        "plan": plan,
    }
    if include_label_summaries:
        data["labels"] = [compact_label(lab) for lab in labels[:max_labels]]
    return _ok(data, started_at=started, status_code=ds_status)


@mcp.tool()
async def get_house_context_summary(
    key: str,
    include_plan_status: bool = False,
    max_blockers_per_scene: int = 3,
) -> dict:
    """Compact house dashboard for routing.

    Prefer this over fetching the full house, every labels file, house
    facts, and every plan state when deciding the next scene/phase.
    """
    started = time.time()
    status, ds = await _api_get(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, ds, started)
    facts, scene_meta = await _load_facts_and_scene_meta(key, ds or {})
    workflow = _derive_workflow_state(ds or {}, facts, scene_meta)
    scenes = []
    total_labels = 0
    for drawing in ds.get("drawings") or []:
        file_name = drawing.get("file")
        if not file_name:
            continue
        meta = dict(scene_meta.get(file_name) or {})
        total_labels += int(meta.get("label_count") or 0)
        row = compact_scene_row(drawing, meta)
        row["label_counts"] = meta.get("label_counts") or {}
        if include_plan_status:
            plan_status, plan_body = await _api_get(f"/datasets/{key}/{file_name}/plan-state/status")
            row["plan"] = (
                compact_plan_status(plan_body, max_blockers=max_blockers_per_scene)
                if plan_status == 200
                else compact_plan_status(None, max_blockers=max_blockers_per_scene)
            )
        scenes.append(row)
    return _ok({
        "summary_contract": "mcp-context-bloat/house-context-summary-v1",
        "key": key,
        "scene_count": len(scenes),
        "total_labels": total_labels,
        "workflow": _compact_workflow_for_summary(workflow, max_blockers=max_blockers_per_scene),
        "scenes": scenes,
    }, started_at=started, status_code=status)


# ── §5.3b Scene plan workflow ───────────────────────────────────────────


@mcp.tool()
async def create_scene_plan_state_from_template(
    key: str,
    file: str,
    scene_tag: str = "nicht_klassifiziert",
    level_or_orientation: str | None = None,
    created_by: str | None = "bim-agent",
    overwrite: bool = False,
) -> dict:
    """Create authoritative plan-state JSON plus rendered Markdown.

    USE when:
      - Inventory/extraction has produced a classified scene and a
        subagent is about to place geometry labels.
      - A legacy scene has labels but no plan state, and you need to
        bring it back under the analyze → edit → verify workflow.
      This writes the structured `*.plan.json` sidecar and syncs the
      human-readable `plan.md`.

    DON'T USE to overwrite a plan with human edits unless the user
    explicitly asked for a reset; leave `overwrite=false` for normal runs.

    Args:
      key/file: scene identifier.
      scene_tag: current workflow scene tag (`grundriss`, `ansicht`,
                 `schnitt`, `sonstiges`, or `nicht_klassifiziert`).
      level_or_orientation: EG/DG/etc for Grundriss or cardinal face for
                            Ansicht/Schnitt when known.
      created_by: provenance marker stored in the plan state.
      overwrite: replace an existing plan state when true.
    """
    started = time.time()
    body = {
        "scene_tag": scene_tag,
        "level_or_orientation": level_or_orientation,
        "created_by": created_by,
        "overwrite": overwrite,
    }
    try:
        status, res = await _api_post(f"/datasets/{key}/{file}/plan-state/template", body)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, res = await _api_post(f"/datasets/{key}/{file}/plan-state/template", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    return _ok(res.get("data") if isinstance(res, dict) else res, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_plan_state(key: str, file: str) -> dict:
    """Read the structured per-scene plan state sidecar.

    USE when:
      - Starting or resuming a scene subagent and you need the full
        task/defect/evidence state.

    DON'T USE when:
      - You only need routing status; prefer `get_scene_plan_status` or
        `get_scene_context_summary` because they are much smaller.
    """
    started = time.time()
    status, body = await _api_get(f"/datasets/{key}/{file}/plan-state")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_plan_status(key: str, file: str) -> dict:
    """Return concise terminality/progress status for a scene plan.

    USE when:
      - Before spawning or resuming a scene subagent.
      - Checking whether a legacy labeled scene is missing its plan.

    DON'T USE when:
      - You need the full task/evidence body; call `get_scene_plan_state`.
      If `exists=false`, create the plan before placing geometry labels.
    """
    started = time.time()
    status, body = await _api_get(f"/datasets/{key}/{file}/plan-state/status")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_plan_next_action(key: str, file: str) -> dict:
    """Return exactly one blocker-first, subagent-ready next action.

    USE when:
      - Inside the scene analyze → edit → verify loop and ready to work
        on one focused blocker or task.
      - You accept global scene priority. If EG required geometry is still
        open, non-EG requests return `code='groundfloor_first_blocked'`.

    DON'T USE when:
      - The scene plan is missing; create it first. Claim the returned
        action with `start_scene_plan_action`, record attempts/evidence,
        close it, and re-run `evaluate_scene_plan_gates`.
      - You are trying to skip the global recommender. Resume long runs
        from `get_recommended_next_action`.
    """
    started = time.time()
    guard = await _scene_order_guard(key, file)
    if guard:
        return _ok(guard, started_at=started)
    status, body = await _api_get(f"/datasets/{key}/{file}/plan-state/next-action")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    data = body.get("data") if isinstance(body, dict) else body
    if isinstance(data, dict):
        data.setdefault("scene_order_blocked", False)
        data.setdefault("global_recommended_file", file)
        data.setdefault("scene_priority", "groundfloor-first" if await _is_file_groundfloor(key, file) else "scene-order")
    return _ok(data, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_plan_next_actions(key: str, file: str, limit: int = 3) -> dict:
    """Return blocker-first, subagent-ready next actions for one scene.

    USE when:
      - A parent/orchestrator needs a compact queue preview before
        assigning scene work.

    DON'T USE when:
      - A worker is already editing a scene; use the singular
        `get_scene_plan_next_action` to stay focused.
    """
    started = time.time()
    guard = await _scene_order_guard(key, file)
    if guard:
        return _ok({"exists": True, "actions": [], **guard}, started_at=started)
    status, body = await _api_get(
        f"/datasets/{key}/{file}/plan-state/next-actions",
        params={"limit": int(limit)},
    )
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def start_scene_plan_action(
    key: str,
    file: str,
    action_id: str,
    agent_id: str | None = "bim-agent",
    expected_version: str | None = None,
) -> dict:
    """Claim a scene-plan action and mark its task/defect in progress.

    USE when:
      - Before making edits for a plan action.

    DON'T USE when:
      - You have not fetched a next action from the current plan state.
      This prevents a resumed worker from silently working stale state
      and gives reviewers a clear current action in the plan.
    """
    started = time.time()
    guard = await _scene_order_guard(key, file)
    if guard:
        return _err(
            "groundfloor_first_blocked",
            guard["reason"],
            hint="Call get_recommended_next_action and work the recommended EG scene first.",
            retry=False,
            details=guard,
            started_at=started,
        )
    body = {"agent_id": agent_id, "expected_version": expected_version}
    status, res = await _api_post(f"/datasets/{key}/{file}/plan-state/actions/{action_id}/start", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    return _ok(_compact_plan_mutation_response(data, action="start_action"), started_at=started, status_code=status)


@mcp.tool()
async def record_scene_plan_attempt(
    key: str,
    file: str,
    action_id: str,
    hypothesis: str,
    edits: list[dict] | None = None,
    evidence_ids: list[str] | None = None,
    attempt_id: str | None = None,
    expected_version: str | None = None,
) -> dict:
    """Record one coherent edit/review attempt for the current action.

    USE when:
      - After an edit or review pass, before finishing the action.

    DON'T USE when:
      - You only have a vague transcript note; add concrete evidence
        first. Keep the hypothesis short and cite evidence IDs rather
        than pasting visual transcript details.
    """
    started = time.time()
    body = {
        "hypothesis": hypothesis,
        "edits": edits or [],
        "evidence_ids": evidence_ids or [],
        "attempt_id": attempt_id,
        "expected_version": expected_version,
    }
    status, res = await _api_post(f"/datasets/{key}/{file}/plan-state/actions/{action_id}/attempts", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    return _ok(_compact_plan_mutation_response(data, action="record_attempt"), started_at=started, status_code=status)


@mcp.tool()
async def finish_scene_plan_action(
    key: str,
    file: str,
    action_id: str,
    outcome: str,
    reason: str | None = None,
    evidence_ids: list[str] | None = None,
    attempt_id: str | None = None,
    expected_version: str | None = None,
) -> dict:
    """Finish one plan action after verification.

    USE when:
      - The attempted edit has been verified, rejected, or clearly
        blocked, with evidence or a blocker reason.

    DON'T USE when:
      - The edit has not been visually/algorithmically checked yet.
      Valid outcomes are `fixed`, `still_open`, `rejected`,
      `accepted_uncertain`, `regressed`, and `blocked_external`.
      For task actions, `accepted_uncertain` is rejected by the API:
      keep working, finish as `blocked_external`, or verify with passing
      gates. Required tasks are complete only when `verified`.
    """
    started = time.time()
    body = {
        "outcome": outcome,
        "reason": reason,
        "evidence_ids": evidence_ids or [],
        "attempt_id": attempt_id,
        "expected_version": expected_version,
    }
    status, res = await _api_post(f"/datasets/{key}/{file}/plan-state/actions/{action_id}/finish", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    return _ok(_compact_plan_mutation_response(data, action="finish_action"), started_at=started, status_code=status)


@mcp.tool()
async def get_scene_repair_candidates(key: str, file: str, limit: int = 20) -> dict:
    """Return clustered current topology findings with deterministic repair candidates.

    USE when:
      - A scene has many topology/wall warning defects.
      - You need the ranked accept/reject queue instead of manually inspecting
        duplicate dangling endpoint cards.

    The response is bounded and includes candidate ids for
    `get_scene_view_with_repair_candidate` and `apply_repair_candidate`.
    """
    started = time.time()
    status, body = await _api_get(
        f"/datasets/{key}/{file}/plan-state/repair-candidates",
        params={"limit": int(limit)},
    )
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_view_with_repair_candidate(
    key: str,
    file: str,
    candidate_id: str,
    max_dim: int = 1600,
    clean: bool = True,
    style: str = "ink_compare",
    image_delivery: str = "inline",
) -> list[ImageContent | TextContent]:
    """Render current labels plus one proposed repair candidate.

    USE before accepting/rejecting a candidate from
    `get_scene_repair_candidates`. The overlay is visual evidence only; it does
    not mutate labels.
    """
    started = time.time()
    status, content, ctype = await _api_get_bytes(
        f"/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/overlay",
        params={"max_dim": int(max_dim), "clean": bool(clean), "style": style},
    )
    if status >= 400:
        try:
            body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            body = {}
        return _wrap_text(_http_status_to_error(status, body, started))
    return _image_delivery_payload(
        content=content,
        ctype=ctype,
        metadata={
            "key": key,
            "file": file,
            "candidate_id": candidate_id,
            "image_format": "PNG",
            "max_dim": max_dim,
            "clean": clean,
            "style": style,
        },
        started_at=started,
        status_code=status,
        image_delivery=image_delivery,
    )


@mcp.tool()
async def apply_repair_candidate(
    key: str,
    file: str,
    candidate_id: str,
    expected_candidate_op: str | None = None,
    evidence_ids: list[str] | None = None,
    expected_version: str | None = None,
    note: str | None = None,
) -> dict:
    """Apply one precomputed deterministic repair candidate.

    USE only after visually inspecting the candidate overlay. No-edit
    classification candidates return `persisted=false`; geometry candidates
    persist labels through the same server validation path as normal label
    writes.
    """
    started = time.time()
    body = {
        "expected_candidate_op": expected_candidate_op,
        "evidence_ids": evidence_ids or [],
        "expected_version": expected_version,
        "note": note,
    }
    status, res = await _api_post(
        f"/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/apply",
        body,
    )
    if status >= 400:
        return _http_status_to_error(status, res, started)
    return _ok(res.get("data") if isinstance(res, dict) else res, started_at=started, status_code=status)


@mcp.tool()
async def decide_repair_candidate(
    key: str,
    file: str,
    candidate_id: str,
    outcome: str,
    expected_candidate_op: str | None = None,
    evidence_ids: list[str] | None = None,
    expected_version: str | None = None,
    note: str | None = None,
) -> dict:
    """Record an accept/reject/manual decision for a repair candidate.

    USE after visually inspecting a candidate overlay when the correct outcome
    is not a geometry application. Allowed outcomes are accepted_applied,
    rejected_false_positive, rejected_intentional_opening,
    rejected_would_hurt_score, accepted_uncertain, and needs_manual_geometry.
    """
    started = time.time()
    body = {
        "outcome": outcome,
        "expected_candidate_op": expected_candidate_op,
        "evidence_ids": evidence_ids or [],
        "expected_version": expected_version,
        "note": note,
    }
    status, res = await _api_post(
        f"/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/decision",
        body,
    )
    if status >= 400:
        return _http_status_to_error(status, res, started)
    return _ok(res.get("data") if isinstance(res, dict) else res, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_plan_quality_report(key: str, file: str) -> dict:
    """Return the compact current topology quality report for a scene plan.

    USE before handoff/export claims. This reports current findings, candidate
    decisions, superseded historical defects, accepted repairs, rejected
    candidates, and unresolved high-confidence warning clusters.
    """
    started = time.time()
    status, body = await _api_get(f"/datasets/{key}/{file}/plan-state/quality-report")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_topology_snapshot(key: str, file: str) -> dict:
    """Return the deterministic non-binary topology regression snapshot.

    USE for handoff/CI-style quality checks. The snapshot contains topology
    counts, cluster count, candidate count/types, reviewed clusters, decision
    outcomes, and unresolved high-confidence warnings.
    """
    started = time.time()
    status, body = await _api_get(f"/datasets/{key}/{file}/plan-state/topology-snapshot")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def add_scene_plan_evidence(
    key: str,
    file: str,
    kind: str,
    summary: str,
    mode: str = "analysis",
    tool: str | None = None,
    params: dict | None = None,
    result: dict | None = None,
    image_url: str | None = None,
    task_ids: list[str] | None = None,
    observation_id: str | None = None,
    expected_version: str | None = None,
) -> dict:
    """Add evidence to the structured scene plan.

    USE when:
      - Recording the observation, crop, score, or verification result
        that justifies a task/defect state change.

    DON'T USE when:
      - You are trying to store large image payloads or full transcripts.
      Store compact observations and tool result summaries instead.
    """
    started = time.time()
    body = {
        "kind": kind,
        "summary": summary,
        "mode": mode,
        "tool": tool,
        "params": params or {},
        "result": result or {},
        "image_url": image_url,
        "task_ids": task_ids or [],
        "observation_id": observation_id,
        "expected_version": expected_version,
    }
    status, res = await _api_post(f"/datasets/{key}/{file}/plan-state/evidence", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    return _ok(_compact_plan_mutation_response(data, action="add_evidence"), started_at=started, status_code=status)


@mcp.tool()
async def set_scene_plan_task_state(
    key: str,
    file: str,
    task_id: str,
    status: str,
    evidence_ids: list[str] | None = None,
    blocked_by: list[str] | None = None,
    gate_updates: list[dict] | None = None,
    note: str | None = None,
    expected_version: str | None = None,
) -> dict:
    """Set one structured scene-plan task state.

    USE when:
      - A plan action's allowed tools require marking a task verified,
        accepted incomplete, blocked, rejected, or back to in progress.
      - You have evidence IDs and gate updates that justify the state.

    DON'T USE when:
      - You are trying to bypass scene work. `accepted_incomplete` is
        only an honest blocker marker and does NOT satisfy readiness or
        export for required tasks. Normal progress requires `verified`
        with passed gates plus evidence; required tasks cannot use waived
        gates as a completion shortcut.

    Valid statuses: `todo`, `in_progress`, `blocked`, `rejected`,
    `verified`, `accepted_incomplete`. Required scene-plan tasks must
    end as `verified` for `required_complete=true`.
    """
    started = time.time()
    body = {
        "status": status,
        "evidence_ids": evidence_ids or [],
        "blocked_by": blocked_by or [],
        "gate_updates": gate_updates or [],
        "note": note,
        "expected_version": expected_version,
    }
    patch_error = await _api_patch(f"/datasets/{key}/{file}/plan-state/tasks/{task_id}", body, started)
    if patch_error is not None:
        return patch_error
    status_code, res = await _api_get(f"/datasets/{key}/{file}/plan-state")
    if status_code >= 400:
        return _http_status_to_error(status_code, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    return _ok(_compact_plan_mutation_response(data, action="set_task_state"), started_at=started, status_code=status_code)


@mcp.tool()
async def evaluate_scene_plan_gates(
    key: str,
    file: str,
    run_score_walls: bool = True,
    run_score_measurements: bool = True,
    run_topology_qa: bool = True,
    visual_evidence: bool = False,
    run_continuity_check: bool = False,
    expected_version: str | None = None,
) -> dict:
    """Evaluate deterministic plan gates and update defects/tasks/status.

    USE when:
      - After edits and before declaring a scene action complete.

    DON'T USE when:
      - You have not yet placed or inspected the relevant labels. This is
        the verification half of the scene loop: it runs server-side QA
        and records resulting blockers in plan state.
    """
    started = time.time()
    body = {
        "run_score_walls": run_score_walls,
        "run_score_measurements": run_score_measurements,
        "run_topology_qa": run_topology_qa,
        "visual_evidence": visual_evidence,
        "run_continuity_check": run_continuity_check,
        "expected_version": expected_version,
    }
    status, res = await _api_post(f"/datasets/{key}/{file}/plan-state/evaluate-gates", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    return _ok(_compact_plan_mutation_response(data, action="evaluate_gates"), started_at=started, status_code=status)


@mcp.tool()
async def detect_wall_corners(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 8,
    thresh: int | None = None,
) -> dict:
    """Detect candidate WALL-corner coordinates (classic-CV positional prior).

    Hand-drawn floorplans have THICK wall strokes (~10-18px) and THIN
    annotation lines (dimensions/furniture/hatching, ~1-3px). A
    morphological open (kernel ~min_wall_px) erases the thin lines so only
    thick walls survive; contour-polygon vertices of the wall mask are
    returned as candidate corners in FULL-image SOURCE pixels. YOU remain
    the judge of which corners are real and how to connect them — snap wall
    endpoints to these instead of guessing off a faint downscaled image.

    key:         house key (e.g. 'house-22').
    file:        scene image filename.
    region:      optional 'x0,y0,x1,y1' source px to restrict detection
                 (recommended — keeps title-block / dimension-frame ink out).
    min_wall_px: wall stroke thickness in px. Tune up (e.g. 18 on house-22)
                 to suppress thin-line noise; down to catch thinner walls.
    thresh:      optional dark-ink cutoff 0-255 (default = Otsu).

    Returns {corners:[[x,y],...], count, params}.
    """
    started = time.time()
    params: dict[str, Any] = {"min_wall_px": min_wall_px}
    if region is not None:
        params["region"] = region
    if thresh is not None:
        params["thresh"] = thresh
    try:
        status, body = await _api_get(
            f"/datasets/{key}/{file}/wall-corners", params
        )
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(
            f"/datasets/{key}/{file}/wall-corners", params
        )
    if status >= 400:
        return _http_status_to_error(status, body, started)
    data = body.get("data", body) if isinstance(body, dict) else body
    return _ok(data, started_at=started, status_code=status)


@mcp.tool()
async def check_corner(
    key: str,
    file: str,
    x: int,
    y: int,
    search_px: int = 40,
    min_wall_px: int = 8,
) -> dict:
    """Snap-check a candidate wall endpoint against the nearest detected
    wall corner. Use iteratively to pull an endpoint onto real ink.

    Returns {found, nearest:[cx,cy], dx, dy, distance, move_hint}.
    dx>0 => the true corner is to the RIGHT of (x,y);
    dy>0 => the true corner is BELOW (image y grows downward).
    move_hint reads e.g. 'left 5, up 8' or 'on-corner'.

    key/file:    scene id.
    x, y:        candidate endpoint in source px.
    search_px:   max snap radius.
    min_wall_px: wall stroke thickness in px (match detect_wall_corners).
    """
    started = time.time()
    params = {
        "x": x, "y": y, "search_px": search_px, "min_wall_px": min_wall_px,
    }
    try:
        status, body = await _api_get(
            f"/datasets/{key}/{file}/check-corner", params
        )
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(
            f"/datasets/{key}/{file}/check-corner", params
        )
    if status >= 400:
        return _http_status_to_error(status, body, started)
    data = body.get("data", body) if isinstance(body, dict) else body
    return _ok(data, started_at=started, status_code=status)


async def _cv_get(path: str, params: dict, started: float) -> dict:
    """Shared GET->envelope helper for the CV tools (retry once on transport
    error; surface HTTP errors; unwrap + wrap success in _ok)."""
    try:
        status, body = await _api_get(path, params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(path, params)
    if status >= 400:
        return _http_status_to_error(status, body, started)
    data = body.get("data", body) if isinstance(body, dict) else body
    return _ok(data, started_at=started, status_code=status)


async def _cv_post(path: str, json_body: dict, started: float) -> dict:
    """Shared POST->envelope helper for the CV tools."""
    try:
        status, body = await _api_post(path, json_body)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_post(path, json_body)
    if status >= 400:
        return _http_status_to_error(status, body, started)
    data = body.get("data", body) if isinstance(body, dict) else body
    return _ok(data, started_at=started, status_code=status)


def _truncate_lists(data: Any, limits: dict[str, int]) -> Any:
    if not isinstance(data, dict):
        return data
    out = dict(data)
    truncation: dict[str, dict[str, int]] = {}
    for key, limit in limits.items():
        value = out.get(key)
        if isinstance(value, list) and limit >= 0 and len(value) > limit:
            out[key] = value[:limit]
            truncation[key] = {
                "returned": limit,
                "total": len(value),
                "omitted": len(value) - limit,
            }
    if truncation:
        out["truncated"] = True
        out["truncation"] = truncation
    else:
        out.setdefault("truncated", False)
    return out


def _compact_plan_mutation_response(data: Any, *, action: str | None = None, max_items: int = 8) -> Any:
    """Shrink verbose plan-state mutation responses for LLM context hygiene.

    The HTTP API keeps full responses for the UI/tests. MCP write tools use
    this summary so a one-line evidence write does not echo hundreds of lines
    of old tasks, defects, markdown, and evidence back into the next prompt.
    """
    if not isinstance(data, dict):
        return data
    state = data.get("state") if isinstance(data.get("state"), dict) else data
    current = state.get("current_state") or {}
    term = current.get("terminality") or {}
    defects = state.get("defects") or []
    open_defects = [
        d for d in defects
        if isinstance(d, dict) and d.get("status") in {"open", "in_progress"}
    ]
    blockers = [d for d in open_defects if d.get("severity") == "blocker"]
    warnings = [d for d in open_defects if d.get("severity") == "warning"]
    tasks = state.get("tasks") or []
    incomplete = [
        {
            "id": t.get("id"),
            "status": t.get("status"),
            "blocked_by": t.get("blocked_by") or [],
        }
        for t in tasks
        if isinstance(t, dict) and t.get("required") and t.get("status") != "verified"
    ]
    actionable = data.get("actionable_tasks")
    if not isinstance(actionable, list):
        actionable = []
    summary = {
        "summary_contract": "plan-mutation-summary/v1",
        "action": action,
        "key": state.get("key"),
        "file": state.get("file"),
        "version": data.get("version") or state.get("version"),
        "status": data.get("status") or state.get("status"),
        "terminal": term.get("terminal"),
        "percent_complete": term.get("percent_complete"),
        "required_complete": term.get("required_complete"),
        "summary": term.get("summary") or current.get("summary"),
        "label_counts": current.get("label_counts") or {},
        "open_blocker_count": len(blockers),
        "open_warning_count": len(warnings),
        "open_blockers": [
            {
                "id": d.get("id"),
                "category": d.get("category"),
                "title": d.get("title"),
                "region": d.get("region"),
            }
            for d in blockers[:max_items]
        ],
        "incomplete_required_tasks": incomplete[:max_items],
        "next_action": actionable[0] if actionable else term.get("next_action"),
        "truncated": len(blockers) > max_items or len(incomplete) > max_items,
    }
    evidence = state.get("evidence") or []
    if isinstance(evidence, list) and evidence:
        latest = evidence[-1]
        if isinstance(latest, dict):
            summary["latest_evidence_id"] = latest.get("id")
            summary["evidence_count"] = len(evidence)
    if data.get("open_defects") and isinstance(data["open_defects"], list):
        summary["open_defect_count"] = len(data["open_defects"])
    if state.get("current_state", {}).get("findings"):
        findings = state["current_state"]["findings"]
        summary["current_findings"] = {
            "count": findings.get("count"),
            "blockers": findings.get("blockers"),
            "warnings": findings.get("warnings"),
        }
    return summary


def _compact_workflow_for_summary(workflow: dict[str, Any], max_blockers: int = 2) -> dict[str, Any]:
    phases = {}
    for phase, data in (workflow.get("phases") or {}).items():
        blockers = data.get("blockers") or []
        if not isinstance(blockers, list):
            blockers = []
        phases[phase] = {
            "status": data.get("status"),
            "blocker_count": len(blockers),
            "blockers": blockers[:max_blockers],
            "truncated": len(blockers) > max_blockers,
        }
        if data.get("assumed_isotropic_scenes"):
            phases[phase]["assumed_isotropic_scene_count"] = len(data["assumed_isotropic_scenes"])
    return {
        "next_phase": workflow.get("next_phase"),
        "exportable": workflow.get("exportable"),
        "blockers_total": workflow.get("blockers_total"),
        "scenes_total": workflow.get("scenes_total"),
        "labeled_scenes": workflow.get("labeled_scenes"),
        "phases": phases,
    }


@mcp.tool()
async def wall_outline(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 8,
    thresh: int | None = None,
    n_outlines: int = 2,
    epsilon_px: float = 8.0,
) -> dict:
    """Ordered outer-boundary polygon(s) of the thick-wall ink (TOPOLOGY/shape).
    Each consecutive vertex pair is a wall; disjoint structures (house vs garage)
    return as separate polygons. Use a small min_wall_px (6-10) so faint outer
    walls survive. For the cleaned, step-snapped silhouette use building_silhouette."""
    started = time.time()
    params: dict = {"min_wall_px": min_wall_px, "n_outlines": n_outlines,
                    "epsilon_px": epsilon_px}
    if region is not None:
        params["region"] = region
    if thresh is not None:
        params["thresh"] = thresh
    return await _cv_get(f"/datasets/{key}/{file}/wall-outline", params, started)


@mcp.tool()
async def building_silhouette(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 16,
    thresh: int | None = None,
    angle_tol_deg: float = 18.0,
    min_area_frac: float = 0.02,
) -> dict:
    """Shape-first decomposition (do this BEFORE placing coordinates): outer
    silhouette as ORDERED stepped polygon(s), one per connected mass (house vs
    detached garage auto-separate), edges snapped to axis-aligned steps, specks
    dropped. Returns {masses:[{polygon,area,bbox}], count}."""
    started = time.time()
    params: dict = {"min_wall_px": min_wall_px, "angle_tol_deg": angle_tol_deg,
                    "min_area_frac": min_area_frac}
    if region is not None:
        params["region"] = region
    if thresh is not None:
        params["thresh"] = thresh
    return await _cv_get(f"/datasets/{key}/{file}/building-silhouette", params, started)


@mcp.tool()
async def refine_wall(
    key: str,
    file: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    search_px: int = 22,
    n_samples: int = 25,
    thresh: int | None = None,
) -> dict:
    """Sub-pixel refine a candidate wall segment onto the measured ink BAND:
    returns corrected endpoints, thickness_px, angle_deg (TRUE tilt), and
    confidence (frac of slices on ink). Accept an edge only at confidence>=~0.85.
    Intersect adjacent refined walls (connect_corners) for exact shared corners."""
    started = time.time()
    params: dict = {"x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "search_px": search_px, "n_samples": n_samples}
    if thresh is not None:
        params["thresh"] = thresh
    return await _cv_get(f"/datasets/{key}/{file}/refine-wall", params, started)


@mcp.tool()
async def score_walls(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 16,
    tol_px: int = 18,
    thresh: int | None = None,
    thin_aware: bool = False,
    close_px: int = 82,
    max_regions: int = 20,
) -> dict:
    """THE self-QA signal. Scores the CURRENTLY SAVED wall labels vs the ink:
    precision, recall, f1, plus missing_regions ('add a wall here') and
    off_ink_segments ('this one is wrong'). Converge until recall>=~0.9,
    precision>=~0.85, both lists empty. Defaults are the canonical 600dpi params
    (min_wall_px=16, tol_px=18, close_px=82); scale down for lower-DPI scenes."""
    started = time.time()
    params: dict = {"min_wall_px": min_wall_px, "tol_px": tol_px,
                    "thin_aware": thin_aware, "close_px": close_px}
    if region is not None:
        params["region"] = region
    if thresh is not None:
        params["thresh"] = thresh
    result = await _cv_get(f"/datasets/{key}/{file}/score-walls", params, started)
    if result.get("ok"):
        result["data"] = _truncate_lists(result["data"], {
            "missing_regions": max_regions,
            "off_ink_segments": max_regions,
        })
    return result


@mcp.tool()
async def score_measurements(
    key: str,
    file: str,
    tol_px: int = 8,
    axis_tol_px: int = 14,
    max_ticks: int = 20,
    max_chains: int = 20,
) -> dict:
    """Metric-correctness QA over score-walls: checks each dimension tick is the
    projection of a wall face (unmatched_ticks = misplaced/missing wall + nearest
    + delta) and per-chain collinearity + part-sum vs the printed overall."""
    started = time.time()
    params: dict = {"tol_px": tol_px, "axis_tol_px": axis_tol_px}
    result = await _cv_get(f"/datasets/{key}/{file}/score-measurements", params, started)
    if result.get("ok"):
        result["data"] = _truncate_lists(result["data"], {
            "unmatched_ticks": max_ticks,
            "chains": max_chains,
            "chain_checks": max_chains,
        })
    return result


@mcp.tool()
async def wall_topology_qa(
    key: str,
    file: str,
    endpoint_tol_px: float = 18.0,
    near_miss_px: float = 60.0,
    collinear_tol_deg: float = 8.0,
    collinear_gap_px: float = 140.0,
    short_stub_px: float = 80.0,
    max_items: int = 20,
) -> dict:
    """Whole-wall-system verification after wall placement.

    Flags dangling endpoints, near-miss corners, mergeable collinear
    fragments, suspicious short stubs, and connected components. Large
    lists are truncated with explicit omitted counts.
    """
    started = time.time()
    params = {
        "endpoint_tol_px": endpoint_tol_px,
        "near_miss_px": near_miss_px,
        "collinear_tol_deg": collinear_tol_deg,
        "collinear_gap_px": collinear_gap_px,
        "short_stub_px": short_stub_px,
    }
    result = await _cv_get(f"/datasets/{key}/{file}/wall-topology-qa", params, started)
    if result.get("ok"):
        result["data"] = _truncate_lists(result["data"], {
            "dangling_endpoints": max_items,
            "near_miss_corners": max_items,
            "collinear_fragments": max_items,
            "short_stubs": max_items,
            "components": max_items,
        })
    return result


@mcp.tool()
async def wall_continuity_check(
    key: str,
    file: str,
    collinear_tol_deg: float = 8.0,
    gap_px: float = 180.0,
    line_tol_px: float = 24.0,
    opening_near_px: float = 80.0,
    max_items: int = 20,
) -> dict:
    """Detect likely walls split at openings.

    Returns collinear wall fragments separated by short gaps, with nearby
    opening symbols when present. Candidate lists are bounded by max_items
    with truncation metadata.
    """
    started = time.time()
    params = {
        "collinear_tol_deg": collinear_tol_deg,
        "gap_px": gap_px,
        "line_tol_px": line_tol_px,
        "opening_near_px": opening_near_px,
    }
    result = await _cv_get(f"/datasets/{key}/{file}/wall-continuity-check", params, started)
    if result.get("ok"):
        result["data"] = _truncate_lists(result["data"], {"candidates": max_items})
    return result


@mcp.tool()
async def ambiguous_line_context(
    key: str,
    file: str,
    bbox: str | None = None,
    line: str | None = None,
    pad_px: float = 120.0,
    max_nearby_labels: int = 20,
) -> dict:
    """Context checklist for suspicious line continuations.

    Use before treating a questionable stroke as a wall. The result names
    non-wall classes to consider and returns a bounded nearby-label list.
    """
    started = time.time()
    params: dict[str, Any] = {"pad_px": pad_px}
    if bbox:
        params["bbox"] = bbox
    if line:
        params["line"] = line
    result = await _cv_get(f"/datasets/{key}/{file}/ambiguous-line-context", params, started)
    if result.get("ok"):
        result["data"] = _truncate_lists(result["data"], {"nearby_labels": max_nearby_labels})
    return result


@mcp.tool()
async def dimension_chain_candidates(
    key: str,
    file: str,
    region: str | None = None,
    orientation: str | None = None,
    thresh: int = 205,
    min_line_frac: float = 0.25,
    min_tick_px: int = 12,
    tick_search_px: int = 45,
    pad_px: int = 80,
) -> dict:
    """Dimension-chain context-gatherer for measurement-first labeling.

    USE before wall placement on Grundriss scenes:
      - Pass a region around a visible dimension chain.
      - The tool returns a likely running line, tick positions, and a tight
        crop_region for the harness vision model to read the printed values.

    IMPORTANT: this is CV-as-prior only. It never reads text/OCR and is not
    authoritative. The agent reads values from the returned crop, then writes
    dimensioned_distance + dimension_number labels via upsert_label or
    add_reference_dim.
    """
    started = time.time()
    params: dict = {
        "thresh": thresh,
        "min_line_frac": min_line_frac,
        "min_tick_px": min_tick_px,
        "tick_search_px": tick_search_px,
        "pad_px": pad_px,
    }
    if region is not None:
        params["region"] = region
    if orientation is not None:
        params["orientation"] = orientation
    return await _cv_get(f"/datasets/{key}/{file}/dimension-chain-candidates", params, started)


@mcp.tool()
async def dimension_station_graph(
    key: str,
    file: str,
    region: str | None = None,
    orientation: str | None = None,
    thresh: int = 205,
    min_line_frac: float = 0.25,
    min_tick_px: int = 12,
    tick_search_px: int = 45,
    pad_px: int = 80,
    wall_anchor_tol_px: float = 28.0,
    max_stations: int = 30,
    max_spans: int = 30,
) -> dict:
    """Return a no-OCR dimension station graph tied to saved wall labels.

    USE when:
      - Dimension ticks are visible but the agent risks guessing endpoints.
      - You need stable station/span ids before writing
        dimensioned_distance + dimension_number labels.

    The model still reads printed values from `crop_region`. This tool only
    provides tick station geometry, adjacent spans, and nearest-wall context.
    """
    started = time.time()
    params: dict[str, Any] = {
        "thresh": thresh,
        "min_line_frac": min_line_frac,
        "min_tick_px": min_tick_px,
        "tick_search_px": tick_search_px,
        "pad_px": pad_px,
        "wall_anchor_tol_px": wall_anchor_tol_px,
    }
    if region is not None:
        params["region"] = region
    if orientation is not None:
        params["orientation"] = orientation
    result = await _cv_get(f"/datasets/{key}/{file}/dimension-station-graph", params, started)
    if result.get("ok"):
        result["data"] = _truncate_lists(result["data"], {
            "stations": max_stations,
            "spans": max_spans,
        })
    return result


@mcp.tool()
async def opening_candidates(
    key: str,
    file: str,
    strip_half_width_px: float = 18.0,
    step_px: float = 4.0,
    min_gap_px: float = 28.0,
    max_gap_px: float = 260.0,
    endpoint_margin_px: float = 18.0,
    thresh: int = 180,
    limit: int = 20,
) -> dict:
    """Return reviewable floorplan opening candidates from wall-gap evidence.

    USE when:
      - Wall topology is stable and the next focused task is placing or
        checking doors, windows, passages, or garage doors.
      - Existing opening defects are noisy and you need a deterministic
        parent-wall candidate queue.

    Each candidate has a parent wall, proposed quad, local region, and a
    suggested `floorplan_opening` label skeleton. Inspect with
    `get_scene_view_with_opening_candidate` before writing or rejecting.
    """
    started = time.time()
    params = {
        "strip_half_width_px": strip_half_width_px,
        "step_px": step_px,
        "min_gap_px": min_gap_px,
        "max_gap_px": max_gap_px,
        "endpoint_margin_px": endpoint_margin_px,
        "thresh": thresh,
        "limit": limit,
    }
    return await _cv_get(f"/datasets/{key}/{file}/opening-candidates", params, started)


@mcp.tool()
async def view_geometry_candidates(
    key: str,
    file: str,
    region: str | None = None,
    thresh: int = 185,
    min_line_px: int = 80,
    min_rect_px: int = 18,
    max_candidates: int = 30,
) -> dict:
    """Return section/elevation component-line and opening candidates.

    USE when:
      - Working on an `ansicht` or `schnitt` scene after height/datum review.
      - You need deterministic priors for component_line roof/slab/terrain
        edges or likely view_opening rectangles before placing labels.

    This is CV-as-prior only. Inspect a crop before applying labels; text,
    hatching, furniture, and title-block lines can produce false positives.
    """
    started = time.time()
    params: dict[str, Any] = {
        "thresh": thresh,
        "min_line_px": min_line_px,
        "min_rect_px": min_rect_px,
        "max_candidates": max_candidates,
    }
    if region is not None:
        params["region"] = region
    return await _cv_get(f"/datasets/{key}/{file}/view-geometry-candidates", params, started)


@mcp.tool()
async def get_scene_view_with_opening_candidate(
    key: str,
    file: str,
    candidate_id: str,
    max_dim: int = 1600,
    clean: bool = True,
    image_delivery: str = "inline",
) -> list[ImageContent | TextContent]:
    """Render current labels plus one proposed opening candidate.

    USE when:
      - You selected one candidate from `opening_candidates` and need a
        tight visual accept/reject crop.
      - You want to compare the proposed opening quad and centerline against
        the source ink while keeping the current label overlay visible.

    DON'T USE when:
      - You have not listed candidates yet; call `opening_candidates` first.
      - You intend to mutate labels. This overlay is visual evidence only and
        never writes geometry.
    """
    started = time.time()
    status, content, ctype = await _api_get_bytes(
        f"/datasets/{key}/{file}/opening-candidates/{candidate_id}/overlay",
        params={"max_dim": int(max_dim), "clean": bool(clean)},
    )
    if status >= 400:
        try:
            body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            body = {}
        return _wrap_text(_http_status_to_error(status, body, started))
    return _image_delivery_payload(
        content=content,
        ctype=ctype,
        metadata={
            "key": key,
            "file": file,
            "candidate_id": candidate_id,
            "image_format": "PNG",
            "max_dim": max_dim,
            "clean": clean,
            "render_contract_version": "opening-candidate-overlay/v1",
        },
        started_at=started,
        status_code=status,
        image_delivery=image_delivery,
    )


@mcp.tool()
async def apply_opening_candidate(
    key: str,
    file: str,
    candidate_id: str,
    expected_candidate_kind: str | None = None,
    opening_kind: str | None = None,
    width_mm: float | None = None,
    swing: str | None = None,
    swing_side: str | None = None,
    evidence_ids: list[str] | None = None,
    expected_version: str | None = None,
    note: str | None = None,
) -> dict:
    """Apply one reviewed deterministic floorplan opening candidate.

    USE when:
      - You inspected `get_scene_view_with_opening_candidate` and the quad is
        a true door/window/passage/garage-door opening on the suggested parent
        wall.
      - You want the server to persist the opening through the same validation
        path as normal labels and record a scene-plan candidate decision.

    DON'T USE when:
      - The candidate is false, ambiguous, or has the wrong parent wall. Use
        `decide_opening_candidate` with the appropriate outcome instead.
    """
    started = time.time()
    body = {
        "expected_candidate_kind": expected_candidate_kind,
        "opening_kind": opening_kind,
        "width_mm": width_mm,
        "swing": swing,
        "swing_side": swing_side,
        "evidence_ids": evidence_ids or [],
        "expected_version": expected_version,
        "note": note,
    }
    status, res = await _api_post(f"/datasets/{key}/{file}/opening-candidates/{candidate_id}/apply", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    return _ok(res.get("data") if isinstance(res, dict) else res, started_at=started, status_code=status)


@mcp.tool()
async def decide_opening_candidate(
    key: str,
    file: str,
    candidate_id: str,
    outcome: str,
    expected_candidate_kind: str | None = None,
    evidence_ids: list[str] | None = None,
    expected_version: str | None = None,
    note: str | None = None,
) -> dict:
    """Record an accept/reject/manual decision for an opening candidate.

    USE when:
      - A candidate has been visually inspected and should be rejected,
        accepted as uncertain, or routed to manual geometry instead of being
        applied automatically.

    Allowed outcomes: `rejected_false_positive`, `rejected_not_an_opening`,
    `rejected_bad_parent_wall`, `accepted_uncertain`, `needs_manual_geometry`.
    This writes plan-state audit data but does not mutate labels.
    """
    started = time.time()
    body = {
        "outcome": outcome,
        "expected_candidate_kind": expected_candidate_kind,
        "evidence_ids": evidence_ids or [],
        "expected_version": expected_version,
        "note": note,
    }
    status, res = await _api_post(f"/datasets/{key}/{file}/opening-candidates/{candidate_id}/decision", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    return _ok(_compact_plan_mutation_response(data, action="decide_opening_candidate"), started_at=started, status_code=status)


@mcp.tool()
async def dimension_chain_context(
    key: str,
    file: str,
    region: str | None = None,
    orientation: str | None = None,
    thresh: int = 205,
    min_line_frac: float = 0.25,
    min_tick_px: int = 12,
    tick_search_px: int = 45,
    pad_px: int = 80,
    tiers: str = "finer,detail",
    max_dim: int = 1600,
    enhance: str | None = "auto",
    format: str = "png8",
    image_delivery: str = "inline",
) -> list[ImageContent | TextContent]:
    """Find a dimension chain and return the tight crop image + tick metadata.

    This is the one-call measurement-first context gatherer:
      MCP prepares a crop and tick priors; the harness vision-LLM reads the
      printed values from the returned image; then the agent writes
      dimensioned_distance + dimension_number labels.

    No OCR/text reading happens here.
    """
    started = time.time()
    params: dict = {
        "thresh": thresh,
        "min_line_frac": min_line_frac,
        "min_tick_px": min_tick_px,
        "tick_search_px": tick_search_px,
        "pad_px": pad_px,
    }
    if region is not None:
        params["region"] = region
    if orientation is not None:
        params["orientation"] = orientation
    status, body = await _api_get(f"/datasets/{key}/{file}/dimension-chain-candidates", params)
    if status >= 400 or not (body or {}).get("ok"):
        return _wrap_text(_http_status_to_error(status, body or {}, started))
    data = body.get("data") or {}
    crop = data.get("crop_region")
    if not data.get("found") or not crop:
        return _wrap_text(_ok(data, started_at=started, status_code=status))

    crop_region = ",".join(str(int(v)) for v in crop)
    grid_params: dict[str, Any] = {
        "region": crop_region,
        "tiers": tiers,
        "max_dim": max_dim,
        "format": format,
    }
    if enhance:
        grid_params["enhance"] = enhance
    img_status, content, ctype = await _api_get_bytes(f"/datasets/{key}/{file}/grid", params=grid_params)
    if img_status >= 400:
        try:
            err_body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            err_body = {}
        return _wrap_text(_http_status_to_error(img_status, err_body, started))

    return _image_delivery_payload(
        content=content,
        ctype=ctype,
        metadata={
            **data,
            "image_format": "PNG",
            "region": crop_region,
            "tiers": tiers.split(","),
            "max_dim": max_dim,
            "enhance": enhance or "none",
            "format": format,
        },
        started_at=started,
        status_code=img_status,
        image_delivery=image_delivery,
    )


@mcp.tool()
async def propose_wall_edit(
    key: str,
    file: str,
    candidate: dict,
    params: dict | None = None,
    region: str | None = None,
    apply: bool = False,
) -> dict:
    """Atomic test-and-apply for ONE wall edit. candidate is
    {"op":"add","wall":[[x0,y0],[x1,y1]]} | {"op":"move","index":i,"wall":[...]}
    | {"op":"delete","index":i}. Scores current vs edited walls with the canonical
    params; returns {applied, gain, before, after, walls_after, persisted}. With
    apply=true it persists walls_after ONLY if f1 improved (a delete that lowers
    recall is rejected). Removes the test-vs-apply desync."""
    started = time.time()
    payload: dict = {"candidate": candidate, "apply": apply}
    if params is not None:
        payload["params"] = params
    if region is not None:
        payload["region"] = region
    return await _cv_post(f"/datasets/{key}/{file}/propose-wall-edit", payload, started)


@mcp.tool()
async def connect_corners(edges: list, closed: bool = True) -> dict:
    """Pure geometry: given ORDERED fitted edges [[[x0,y0],[x1,y1]], ...] (each a
    refine-wall band centerline), return walls whose shared corners are the
    INTERSECTIONS of adjacent edges' lines, so the shell is closed by construction
    (honors tilt). Returns {walls, count, closed}."""
    started = time.time()
    return await _cv_post("/geometry/connect-corners",
                          {"edges": edges, "closed": closed}, started)


def _label_summary(label: dict) -> str:
    """One-line human description for the summary view."""
    t = label.get("type")
    attrs = label.get("attributes") or {}
    geom = label.get("geometry") or {}
    if t == "wall":
        return f"thickness={attrs.get('thickness_mm')}mm"
    if t in ("floorplan_opening", "view_opening"):
        kind = attrs.get("opening_kind")
        return f"{kind} width={attrs.get('width_mm', '?')}mm"
    if t == "component_line":
        n = len(geom.get("points") or [])
        return f"{attrs.get('line_kind', 'unknown')} ({n} pts)"
    if t == "height_mark":
        return f"value={attrs.get('value_mm')}mm datum={attrs.get('datum')}"
    if t == "dimensioned_distance":
        ref = " (REF)" if attrs.get("is_reference") else ""
        return f"value={attrs.get('value_mm')}mm{ref}"
    if t == "dimension_number":
        return f"text={attrs.get('text')!r}"
    return ""


@mcp.tool()
async def get_label(key: str, file: str, label_id: str) -> dict:
    """Full label object — geometry + attributes + relations + notes.

    USE when:
      - About to delete or update a label — confirm the id refers to
        what you think.

    Returns: `data` = the full Label per scene_labels.schema.json.
    """
    started = time.time()
    try:
        status, body = await _api_get(f"/labels/dataset/{key}/{file}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(f"/labels/dataset/{key}/{file}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    target = next((l for l in (body.get("labels") or []) if l.get("id") == label_id), None)
    if target is None:
        return _err("label_not_found", f"no label {label_id!r} on {file!r}", started_at=started)
    return _ok(target, started_at=started, status_code=status)


# ── §5.4 Tagging ──────────────────────────────────────────────────────────

_VALID_TAGS = {"grundriss", "ansicht", "schnitt", "sonstiges", "nicht_klassifiziert"}
_VALID_ORIENTATIONS = {"north", "south", "east", "west", None}
_VALID_LEVELS = {"kg", "ug", "eg", "og", "dg", "spitzboden", None}


@mcp.tool()
async def set_scene_tag(
    key: str,
    file: str,
    tag: str,
    idempotency_key: str | None = None,
) -> dict:
    """Set the scene discriminator tag for one scene.

    USE when:
      - The scene's tag is still 'nicht_klassifiziert' after extraction.
      - Earlier tagging was wrong and the human hasn't touched labels.

    DON'T USE when:
      - The scene has labels of types the new tag can't render — call
        `delete_label` for those first.

    Args:
      key: house key.
      file: scene filename.
      tag: one of 'grundriss', 'ansicht', 'schnitt', 'sonstiges',
           'nicht_klassifiziert'.
      idempotency_key: optional driver-supplied key.

    Returns: `data` = {file, scene_tag} from the labels-JSON update.

    Writes ONLY to data/dataset/<key>/labels/<file>.json `scene_tag` —
    that is the workflow predicate's source of truth. The manifest's
    separate `kind` field (floorplan/elevation/section/detail; set by
    extraction) is left alone; use the SPA's edit-attrs popover to
    change it when needed.
    """
    started = time.time()
    if tag not in _VALID_TAGS:
        return _err("schema_invalid", f"unknown tag {tag!r}",
                    hint=f"use one of {sorted(_VALID_TAGS)}", started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    payload["scene_tag"] = tag
    put_status, put_body = await _api_put(f"/labels/dataset/{key}/{file}", payload)
    if put_status >= 400:
        return _http_status_to_error(put_status, put_body, started)
    return _ok({"file": file, "scene_tag": tag},
               started_at=started, status_code=put_status)


@mcp.tool()
async def set_scene_orientation(
    key: str,
    file: str,
    orientation: str | None,
    idempotency_key: str | None = None,
) -> dict:
    """Set scene_orientation on one scene's labels JSON.

    USE when:
      - The scene_tag is 'ansicht' or 'schnitt' and you can determine
        the cardinal direction.
      - Pass null to clear.

    Args:
      orientation: 'north' | 'south' | 'east' | 'west' | null
    """
    started = time.time()
    if orientation not in _VALID_ORIENTATIONS:
        return _err("schema_invalid", f"unknown orientation {orientation!r}",
                    hint="use 'north', 'south', 'east', 'west', or null", started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    payload["scene_orientation"] = orientation
    put_status, put_body = await _api_put(f"/labels/dataset/{key}/{file}", payload)
    if put_status >= 400:
        return _http_status_to_error(put_status, put_body, started)
    return _ok({"file": file, "scene_orientation": orientation},
               started_at=started, status_code=put_status)


@mcp.tool()
async def set_scene_level(
    key: str,
    file: str,
    level: str | None,
    idempotency_key: str | None = None,
) -> dict:
    """Set scene_level on a Grundriss scene.

    USE when:
      - scene_tag is 'grundriss' — determine which floor.
      - Pass null to clear.

    Args:
      level: 'kg' | 'ug' | 'eg' | 'og' | 'dg' | 'spitzboden' | null
    """
    started = time.time()
    if level not in _VALID_LEVELS:
        return _err("schema_invalid", f"unknown level {level!r}",
                    hint=f"use one of {sorted({lv for lv in _VALID_LEVELS if lv})} or null",
                    started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    payload["scene_level"] = level
    put_status, put_body = await _api_put(f"/labels/dataset/{key}/{file}", payload)
    if put_status >= 400:
        return _http_status_to_error(put_status, put_body, started)
    return _ok({"file": file, "scene_level": level},
               started_at=started, status_code=put_status)


@mcp.tool()
async def reset_scene_labels(
    key: str,
    file: str,
    idempotency_key: str | None = None,
) -> dict:
    """Reset ONE scene's labels and scene metadata, keeping the scene image.

    USE when:
      - You want to restart labeling for a single extracted scene.
      - A prior agent run produced bad labels and the extraction itself is OK.

    EFFECT:
      - Writes a clean labels skeleton for the scene.
      - Sets scene_tag='nicht_klassifiziert', clears scene_orientation/level.
      - Removes every saved label for that scene.
      - Rebuilds house_facts from scratch so stale calibration/extent facts
        from deleted labels do not leak into the next run.

    DON'T USE when:
      - You want to remove extracted scenes and return to PDF extraction;
        call `reset_house_dataset` instead.
    """
    started = time.time()
    try:
        status, body = await _api_delete(f"/labels/dataset/{key}/{file}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_delete(f"/labels/dataset/{key}/{file}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def reset_house_labeling(
    key: str,
    idempotency_key: str | None = None,
) -> dict:
    """Reset ALL labels for a house while keeping extracted scenes.

    USE when:
      - You want a fresh labeling run on the existing scene crops.
      - The extraction/cropping is good, but the annotations should be purged.

    EFFECT:
      - Replaces every scene labels JSON with an empty skeleton.
      - Clears scene tags/orientations/levels back to unclassified metadata.
      - Rebuilds house_facts from scratch so required phases become pending.
      - Keeps data/dataset/<key> images and manifest intact.

    DON'T USE when:
      - You need to re-extract scenes from the incoming PDF. Use
        `reset_house_dataset` for that stronger reset.
    """
    started = time.time()
    try:
        status, body = await _api_delete(f"/datasets/{key}/labels")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_delete(f"/datasets/{key}/labels")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def reset_house_dataset(
    key: str,
    idempotency_key: str | None = None,
) -> dict:
    """Destructive house reset: remove extracted scenes and labels.

    USE when:
      - The scene extraction/cropping itself is bad.
      - You want to return the incoming PDF bundle to the "ready to extract"
        state and start over from W0 extraction.

    EFFECT:
      - Deletes data/dataset/<key>/ entirely.
      - Resets the incoming PDF manifest's extracted_scenes list/state.
      - Keeps data/pdfs/incoming/<key>/ source PDFs.

    This is stronger than `reset_house_labeling` and cannot be undone.
    """
    started = time.time()
    try:
        status, body = await _api_delete(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_delete(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok({"key": key, "mode": "dataset_removed_keep_incoming_pdf"},
               started_at=started, status_code=status)


# ── §5.5 Label CRUD ──────────────────────────────────────────────────────


def _new_label_id() -> str:
    return f"lab-{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:10]}"


async def _read_labels(key: str, file: str, started: float) -> tuple[dict | None, dict | None]:
    """Helper: fetch labels payload; return (payload, error_envelope) tuple
    where exactly one is None."""
    try:
        status, body = await _api_get(f"/labels/dataset/{key}/{file}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return None, _api_unreachable_error(started)
        status, body = await _api_get(f"/labels/dataset/{key}/{file}")
    if status >= 400:
        return None, _http_status_to_error(status, body, started)
    return body, None


async def _write_labels(key: str, file: str, payload: dict, started: float) -> dict:
    """Helper: PUT labels payload; return envelope (ok or err)."""
    put_status, put_body = await _api_put(f"/labels/dataset/{key}/{file}", payload)
    if put_status >= 400:
        return _http_status_to_error(put_status, put_body, started)
    return _ok({"file": file, "label_count": len(payload.get("labels") or [])},
               started_at=started, status_code=put_status)


async def _current_action_write_warning(key: str, file: str, label_type: str) -> dict | None:
    try:
        status, body = await _api_get(f"/datasets/{key}/{file}/plan-state")
    except Exception:  # noqa: BLE001
        return None
    if status >= 400 or not isinstance(body, dict):
        return None
    state = ((body.get("data") or {}).get("state") or {})
    current = ((state.get("current_state") or {}).get("current_action_id") or "")
    if not current:
        return None
    actions = state.get("actions") or []
    action = next((a for a in actions if a.get("action_id") == current), None)
    allowed = set((action or {}).get("allowed_label_types") or [])
    forbidden = set((action or {}).get("forbidden_label_types") or [])
    task_id = str((action or {}).get("task_id") or (action or {}).get("id") or current.replace("ACT-", ""))
    warnings: list[str] = []
    if task_id == "CLASSIFY_SCENE":
        warnings.append("CLASSIFY_SCENE is classification-only; geometry writes belong under wall/opening/dimension tasks.")
    if label_type in forbidden:
        warnings.append(f"label type {label_type!r} is forbidden for current action {current}.")
    if allowed and label_type not in allowed:
        warnings.append(f"label type {label_type!r} is outside current action allowed_label_types={sorted(allowed)}.")
    wall_anchoring = (state.get("current_state") or {}).get("wall_anchoring") or {}
    if label_type in {"floorplan_opening", "dimensioned_distance", "dimension_number"} and wall_anchoring.get("status") == "failed":
        warnings.append("wall ink anchoring is failed; do not write openings/dimensions until wall blockers are repaired.")
    if not warnings:
        return None
    return {
        "code": "action_scope_warning",
        "current_action_id": current,
        "task_id": task_id,
        "warnings": warnings,
    }


@mcp.tool()
async def upsert_label(
    key: str,
    file: str,
    label: dict,
    idempotency_key: str | None = None,
) -> dict:
    """Create or replace a label by id.

    USE when:
      - Adding a new label (omit `label.id` — server allocates one).
      - Replacing an existing label by its id.

    DON'T USE when:
      - You only want to change attributes — use `update_label_attrs`
        (avoids re-sending geometry; less error-prone).

    Args:
      key: house key.
      file: scene filename.
      label: a Label dict per scene_labels.schema.json. Required:
             `type`, `geometry`. The tool defaults `status='readable'`
             and `attributes={}` if absent.

             Scene-category palette is enforced by the API:
               grundriss: wall, floorplan_opening, dimensioned_distance,
                          dimension_number
               ansicht/schnitt: view_opening, component_line, height_mark,
                                dimensioned_distance, dimension_number
               sonstiges: all label types
             Example: `height_mark` on a `grundriss` is rejected.

             Geometry uses [x, y] ARRAYS, not {x, y} objects:
               wall:                 {start: [x,y], end: [x,y]}
               floorplan_opening:    {quad: [[x,y],[x,y],[x,y],[x,y]]}
               view_opening:         one of
                                       {top_edge: [[x,y],...], bottom_edge: [[x,y],...]}
                                       {circle: {center: [x,y], radius_px: N}}
                                       {polygon: [[x,y],...]}
               component_line:       {points: [[x,y],...]}
               height_mark:          {anchor: [x,y]}
               dimensioned_distance: {start: [x,y], end: [x,y]}
               dimension_number:     {anchor: [x,y]} XOR {bbox: [[x,y]*4]}
      idempotency_key: optional driver-supplied key.

    Returns: `data.label_id` = the (new or existing) label id.
    """
    started = time.time()
    if not isinstance(label, dict) or "type" not in label:
        return _err("schema_invalid", "label must be an object with at least 'type'",
                    hint="see bim-db://schema/scene_labels resource", started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    labels = payload.setdefault("labels", [])
    label_id = label.get("id") or _new_label_id()
    label["id"] = label_id
    # Default required schema fields the agent often forgets.
    label.setdefault("status", "readable")
    label.setdefault("attributes", {})
    existing_idx = next((i for i, l in enumerate(labels) if l.get("id") == label_id), None)
    if existing_idx is not None:
        labels[existing_idx] = label
        action = "replaced"
    else:
        labels.append(label)
        action = "created"
    result = await _write_labels(key, file, payload, started)
    if not result.get("ok"):
        return result
    result["data"]["label_id"] = label_id
    result["data"]["action"] = action
    warning = await _current_action_write_warning(key, file, str(label.get("type") or ""))
    if warning:
        result["data"]["action_scope_warning"] = warning
    if label.get("type") == "wall":
        try:
            status, body = await _api_post(
                f"/datasets/{key}/{file}/wall-labels/anchoring-check",
                {"label": label},
            )
            if status < 400 and isinstance(body, dict):
                check = body.get("data") or {}
                result["data"].update({
                    "anchoring_status": check.get("anchoring_status") or check.get("status") or "unchecked",
                    "ink_overlap": check.get("ink_overlap"),
                    "recommended_tool": "upsert_wall_anchored",
                    "must_verify_before_downstream": bool(check.get("must_verify_before_downstream")),
                    "anchoring_check_region": check.get("region"),
                })
        except Exception:  # noqa: BLE001
            result["data"].update({
                "anchoring_status": "unchecked",
                "recommended_tool": "upsert_wall_anchored",
                "must_verify_before_downstream": True,
            })
    return result


@mcp.tool()
async def upsert_wall_anchored(
    key: str,
    file: str,
    candidate: dict,
    anchor: dict | None = None,
    status_if_unanchored: str = "reject",
    evidence_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Create or replace a floorplan wall after snapping/refining it to ink.

    USE instead of raw `upsert_label(type='wall')` when placing Grundriss
    walls. The tool treats `candidate.start/end` as a draft, refines to the
    measured wall band, checks local ink overlap, and persists a readable wall
    only when confidence + overlap pass.

    Args:
      candidate: {"start":[x,y], "end":[x,y], "thickness_mm":300, "id": optional}
      anchor: optional {"search_px":40, "min_confidence":0.82,
              "min_overlap":0.6, "snap_corners":true}
      status_if_unanchored: "reject" (default) or "uncertain". Uncertain
              persistence requires evidence_id so the dataset stays honest.
    """
    started = time.time()
    if not isinstance(candidate, dict):
        return _err("schema_invalid", "candidate must be an object",
                    started_at=started)
    body = {
        "candidate": candidate,
        "anchor": anchor or {},
        "status_if_unanchored": status_if_unanchored,
        "evidence_id": evidence_id,
    }
    try:
        status, resp = await _api_post(f"/datasets/{key}/{file}/wall-labels/anchored", body)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, resp = await _api_post(f"/datasets/{key}/{file}/wall-labels/anchored", body)
    if status >= 400:
        return _http_status_to_error(status, resp, started)
    data = (resp or {}).get("data") or resp
    warning = await _current_action_write_warning(key, file, "wall")
    if warning and isinstance(data, dict):
        data["action_scope_warning"] = warning
    return _ok(data, started_at=started, status_code=status)


@mcp.tool()
async def delete_label(
    key: str,
    file: str,
    label_id: str,
    idempotency_key: str | None = None,
) -> dict:
    """Delete a label by id.

    USE when:
      - The agent decided a label was wrong and wants a clean slate.
      - You're about to re-tag a scene and the existing labels would
        violate the new tag's tool palette.
    """
    started = time.time()
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    labels = payload.get("labels") or []
    before = len(labels)
    payload["labels"] = [l for l in labels if l.get("id") != label_id]
    if len(payload["labels"]) == before:
        return _err("label_not_found", f"no label {label_id!r} on {file!r}",
                    started_at=started)
    return await _write_labels(key, file, payload, started)


@mcp.tool()
async def update_label_attrs(
    key: str,
    file: str,
    label_id: str,
    attrs_patch: dict,
    idempotency_key: str | None = None,
) -> dict:
    """Partial update on a label's `attributes` dict.

    USE when:
      - Changing a `dimensioned_distance.attributes.value_mm` after
        re-reading the dim text.
      - Flipping `is_reference` after deciding a stroke is/isn't an
        anchor.
      - Tightening `attributes.opening_kind` from default 'window' to
        e.g. 'door'.

    Args:
      attrs_patch: dict of attributes to merge in. Existing attributes
                   not mentioned are preserved.
    """
    started = time.time()
    if not isinstance(attrs_patch, dict) or not attrs_patch:
        return _err("schema_invalid", "attrs_patch must be a non-empty dict",
                    started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    target = next((l for l in (payload.get("labels") or []) if l.get("id") == label_id), None)
    if target is None:
        return _err("label_not_found", f"no label {label_id!r} on {file!r}",
                    started_at=started)
    target.setdefault("attributes", {}).update(attrs_patch)
    return await _write_labels(key, file, payload, started)


_VALID_LABEL_STATUS = {"readable", "not_readable", "missing", "uncertain"}


@mcp.tool()
async def set_label_status(
    key: str,
    file: str,
    label_id: str,
    status: str,
    idempotency_key: str | None = None,
) -> dict:
    """Set the honesty axis on a label.

    USE when:
      - You labelled a dim but can't read the value confidently — set
        `status='uncertain'` so a human reviewer is alerted.
      - A label is for a feature that's missing in the drawing entirely
        — set `status='missing'`.

    Args:
      status: 'readable' | 'not_readable' | 'missing' | 'uncertain'
    """
    started = time.time()
    if status not in _VALID_LABEL_STATUS:
        return _err("schema_invalid", f"unknown status {status!r}",
                    hint=f"use one of {sorted(_VALID_LABEL_STATUS)}", started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    target = next((l for l in (payload.get("labels") or []) if l.get("id") == label_id), None)
    if target is None:
        return _err("label_not_found", f"no label {label_id!r}", started_at=started)
    target["status"] = status
    return await _write_labels(key, file, payload, started)


# ── §5.6 Reference / homography ──────────────────────────────────────────


@mcp.tool()
async def add_reference_dim(
    key: str,
    file: str,
    orientation: str,
    start: list[float],
    end: list[float],
    value_mm: float,
    dimension_text: str | None = None,
    assume_isotropic: bool = False,
    idempotency_key: str | None = None,
) -> dict:
    """Sugar tool: create a `dimensioned_distance` with `is_reference=true`
    AND a paired `dimension_number` at the midpoint.

    USE when:
      - W4 calibration: every Ansicht/Schnitt needs ≥1 horizontal +
        ≥1 vertical reference dim.
      - W2 footprint: horizontal + vertical reference dims along the
        outer edges of EG-Grundriss.

    Args:
      key: house key.
      file: scene filename.
      orientation: 'horizontal' | 'vertical' (controls
                   target_orientation on the distance).
      start, end: pixel coordinates [x, y] in the SOURCE image frame
                  (read off the grid overlay).
      value_mm: numeric value in millimeters (e.g. 11200 for "11.20 m").
      dimension_text: optional as-written text, e.g. "11,20 m".

      assume_isotropic: forwarded to the recompute that runs after the
                   write. Set True only when the scene is an axis-aligned
                   orthographic drawing (square pixels — any normal
                   Ansicht/Schnitt) and you intend to calibrate from a
                   single reference dim per the square-pixel assumption.
                   See `recompute_homography` for the full contract. With
                   only one ref dim on the scene and this left False, the
                   recompute reports `homography_degenerate` as before.

    Returns: `data` = {distance_id, dim_number_id, recompute_homography:
                       {status, rms_residual_px, ...}}
    The tool calls recompute_homography immediately so the agent
    knows whether the new ref dim is good without a second round-trip.
    """
    started = time.time()
    if orientation not in {"horizontal", "vertical"}:
        return _err("schema_invalid", f"orientation must be 'horizontal' or 'vertical'",
                    started_at=started)
    if not (isinstance(start, (list, tuple)) and len(start) == 2 and
            isinstance(end, (list, tuple)) and len(end) == 2):
        return _err("schema_invalid", "start/end must be [x, y] pairs",
                    started_at=started)
    # H6 (followups-2): refuse degenerate (zero-length) dim lines. Seen
    # on the 2026-05-29 house-22 drive — agent passed start == end for a
    # ref dim, the homography solver got a singular matrix and silently
    # failed. Catch it at the door.
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_px = (dx * dx + dy * dy) ** 0.5
    if length_px < 2.0:
        return _err(
            "degenerate_dim_line",
            f"start {start!r} and end {end!r} are < 2 px apart — "
            "this is not a usable reference dim",
            hint=(
                "a ref dim must span the actual drawn dim line. Zoom in "
                "with get_scene_view(region=…) around the dim, read off "
                "the two endpoint coords from the grid labels, and pass "
                "them as separate [x, y] pairs."
            ),
            retry=False,
            started_at=started,
        )
    # H6: also refuse a dim line that doesn't match its declared
    # orientation (horizontal stroke claimed as vertical, etc.) when
    # the mismatch is severe (axis ratio > 1:3). Catches the agent
    # passing a horizontal candidate but calling it "vertical".
    if orientation == "horizontal" and abs(dy) > abs(dx) * 3:
        return _err(
            "orientation_mismatch",
            f"declared orientation 'horizontal' but the line from {start!r} "
            f"to {end!r} is dominantly vertical (|dy|={abs(dy):.1f}, "
            f"|dx|={abs(dx):.1f})",
            hint=(
                "swap orientation to 'vertical' or re-pick endpoints along "
                "the actual horizontal dim line."
            ),
            retry=False,
            started_at=started,
        )
    if orientation == "vertical" and abs(dx) > abs(dy) * 3:
        return _err(
            "orientation_mismatch",
            f"declared orientation 'vertical' but the line from {start!r} "
            f"to {end!r} is dominantly horizontal (|dx|={abs(dx):.1f}, "
            f"|dy|={abs(dy):.1f})",
            hint=(
                "swap orientation to 'horizontal' or re-pick endpoints along "
                "the actual vertical dim line."
            ),
            retry=False,
            started_at=started,
        )
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    # G4-2 (followups-tracker): refuse endpoints outside image_size_px.
    # Catches the failure mode where the agent reads a building-scale
    # dim off the broad tier on a detail crop — endpoints land outside
    # the actual cropped image bounds.
    image_size = payload.get("image_size_px") or []
    if len(image_size) == 2:
        img_w, img_h = image_size[0], image_size[1]
        for name, pt in (("start", start), ("end", end)):
            if not (0 <= pt[0] <= img_w and 0 <= pt[1] <= img_h):
                return _err(
                    "endpoint_out_of_image",
                    f"{name} {pt!r} is outside image bounds ({img_w}x{img_h})",
                    hint=(
                        "the dim line endpoint must be inside the scene's "
                        "visible area. Re-check via get_scene_view(region=…) "
                        "around the dim; a building-scale dim probably bled "
                        "into this view from outside the crop frame."
                    ),
                    retry=False,
                    started_at=started,
                )
    # G4-1 (followups-tracker): refuse a 3rd is_reference dim in the same
    # orientation. The homography only needs one ref dim per axis; a 3rd
    # is almost always the agent thrashing instead of fixing the broken
    # one. Force the agent to delete first.
    existing_refs_same_orient = [
        l for l in (payload.get("labels") or [])
        if l.get("type") == "dimensioned_distance"
        and (l.get("attributes") or {}).get("is_reference") is True
        and (l.get("attributes") or {}).get("target_orientation") == orientation
    ]
    if len(existing_refs_same_orient) >= 2:
        return _err(
            "too_many_reference_dims",
            f"scene already has {len(existing_refs_same_orient)} {orientation} "
            f"reference dims — refusing to add a 3rd",
            hint=(
                f"delete one of: {[l.get('id') for l in existing_refs_same_orient]} "
                "first. The homography only needs ONE ref dim per axis; a "
                "third is almost always the agent thrashing instead of "
                "fixing the broken one."
            ),
            retry=False,
            details={"existing_ref_ids": [l.get("id") for l in existing_refs_same_orient]},
            started_at=started,
        )
    distance_id = _new_label_id()
    midpoint = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2]
    # Offset the dim number 14 px perpendicular to the stroke so it
    # doesn't render on top.
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = max((dx * dx + dy * dy) ** 0.5, 1.0)
    perp_x, perp_y = -dy / length, dx / length
    text_anchor = [midpoint[0] + perp_x * 14, midpoint[1] + perp_y * 14]
    dim_number_id = _new_label_id()
    distance_label = {
        "id": distance_id,
        "type": "dimensioned_distance",
        "geometry": {"start": [float(start[0]), float(start[1])],
                     "end": [float(end[0]), float(end[1])]},
        "attributes": {
            "value_mm": float(value_mm),
            "is_reference": True,
            "target_orientation": orientation,
        },
        "status": "readable",
    }
    dim_number_label = {
        "id": dim_number_id,
        "type": "dimension_number",
        "geometry": {"anchor": [float(text_anchor[0]), float(text_anchor[1])]},
        "attributes": {
            "text": dimension_text or f"{value_mm / 1000:.2f} m",
            "parsed_value_mm": float(value_mm),
        },
        "relations": [{"kind": "labels", "other_id": distance_id}],
        "status": "readable",
    }
    payload.setdefault("labels", []).extend([distance_label, dim_number_label])
    result = await _write_labels(key, file, payload, started)
    if not result.get("ok"):
        return result
    # Compute homography (best-effort).
    homo = await recompute_homography(
        key=key, file=file, assume_isotropic=assume_isotropic
    )
    result["data"]["distance_id"] = distance_id
    result["data"]["dim_number_id"] = dim_number_id
    result["data"]["homography"] = homo.get("data") if homo.get("ok") else None
    result["data"]["homography_error"] = homo.get("error") if not homo.get("ok") else None
    return result


@mcp.tool()
async def recompute_homography(
    key: str, file: str, assume_isotropic: bool = False
) -> dict:
    """Run the rectification compute over the scene's reference dims.

    USE when:
      - After adding/removing/changing a reference dim, to confirm the
        transform converges.

    DON'T USE proactively — it's a derived value, the export pipeline
    runs it for you. Call it only when you need to verify a calibration
    landed cleanly.

    Args:
      assume_isotropic (issue #26): set True ONLY after YOU (the harness
        vision-LLM) have judged the drawing to be an axis-aligned
        orthographic projection — i.e. square pixels, same scale on both
        axes. This is true for essentially every German Ansicht/Schnitt and
        false for perspective/isometric views. When True and the scene has
        exactly ONE reference dim, the engine derives the second-axis scale
        from the same px-per-mm instead of returning `homography_degenerate`,
        and stamps `single_ref_assumed_isotropic=true` into the result for
        honesty. Leave it False (default) to keep the strict two-ref gate.

    Returns: `data` = {status: 'ok'|'degenerate', rms_residual_px?,
                       matrix?, computed_from?, single_ref_assumed_isotropic?,
                       reason?}

    Backend lives in api/homography.py + the export preview endpoint.
    We use the per-scene export preview as the cheapest way to trigger
    a recompute; it returns the homography snapshot.
    """
    started = time.time()
    params = {"assume_isotropic": "true"} if assume_isotropic else None
    try:
        status, body = await _api_post(f"/exports/{key}/{file}/preview", params=params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_post(f"/exports/{key}/{file}/preview", params=params)
    if status >= 400:
        return _http_status_to_error(status, body, started)
    homo = body.get("homography") or {}
    state = body.get("status") or "unknown"
    if state == "ok":
        rms = homo.get("rms_residual_px")
        if rms is not None and rms > 10:
            return _err("homography_high_residual",
                        f"RMS {rms:.1f}px exceeds 10px quality bar",
                        hint="delete one of the reference dims and pick a more-orthogonal pair",
                        retry=True,
                        details={"rms_residual_px": rms, "matrix": homo.get("matrix"),
                                 "used_label_ids": homo.get("computed_from", [])},
                        started_at=started)
        return _ok({
            "status": "ok",
            "rms_residual_px": rms,
            "matrix": homo.get("matrix"),
            "computed_from": homo.get("computed_from"),
            "rectified_size_px": homo.get("rectified_size_px"),
            "single_ref_assumed_isotropic": homo.get("single_ref_assumed_isotropic", False),
        }, started_at=started, status_code=status)
    return _err("homography_degenerate",
                body.get("reason") or "rectification could not produce a valid transform",
                hint=(
                    "add a second reference dim on the missing axis, OR — if "
                    "this is an axis-aligned orthographic drawing (any normal "
                    "Ansicht/Schnitt: square pixels, same scale both axes) — "
                    "re-call with assume_isotropic=True to calibrate from the "
                    "single ref dim under the square-pixel assumption"
                ),
                retry=True,
                details={"computed_from": body.get("computed_from")},
                started_at=started)


# ── §5.7 Facts ────────────────────────────────────────────────────────────


@mcp.tool()
async def get_house_facts(key: str) -> dict:
    """Full HouseFacts for a house — extent, heights, wall_thickness,
    orientation, calibration_per_scene, scene_metadata, workflow pointer.

    USE when:
      - Reading the current phase predicates before deciding the next
        write. Cheap (single GET).
      - Verifying a `set_house_facts` patch landed.

    DON'T USE when:
      - You only need to know which phase is next — `get_workflow_state`
        is more targeted.

    Args:
      key: house key.

    Returns: full HouseFacts dict, or `data: null` if no
    `data/dataset/<key>/house_facts.json` exists yet (a brand-new house
    surfaces as null until the first `set_house_facts` call).
    """
    started = time.time()
    try:
        status, body = await _api_get(f"/datasets/{key}/house_facts")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(f"/datasets/{key}/house_facts")
    if status == 404:
        return _ok(None, started_at=started, status_code=status)
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def set_house_facts(
    key: str,
    patch: dict,
    idempotency_key: str | None = None,
) -> dict:
    """Deep-merge patch into HouseFacts (server-side replace by default;
    this tool reads-merges-writes to give patch semantics on top).

    USE when:
      - W1: set `heights = {bezug_mm, first_mm}`.
      - W2: set `extent = {width_mm, depth_mm}`, `wall_thickness = {outer_mm}`.
      - W3: set `orientation = {north_edge_label_id} or {north_angle_deg}`.
      - W4: the per-scene `calibration_per_scene[file]` is auto-populated
        by `add_reference_dim` + `recompute_homography`; do not set it
        manually.

    Args:
      patch: partial HouseFacts. Top-level keys merge (other keys
             preserved); nested objects deep-merge one level. Lists are
             replaced atomically.
    """
    started = time.time()
    if not isinstance(patch, dict) or not patch:
        return _err("schema_invalid", "patch must be a non-empty dict",
                    started_at=started)
    # Read current
    cur_status, current = await _api_get(f"/datasets/{key}/house_facts")
    if cur_status == 404:
        current = {"schema_version": "1.0"}
    elif cur_status >= 400:
        return _http_status_to_error(cur_status, current, started)

    # G4-3 (followups-tracker): force assumed:true when north_angle_deg
    # is set without a north_edge_label_id. Catches the agent's "I
    # guessed but said I knew" failure mode (B3).
    warnings: list[str] = []
    auto_corrections: list[str] = []
    orient_patch = patch.get("orientation") if isinstance(patch.get("orientation"), dict) else None
    if orient_patch is not None:
        existing_orient = current.get("orientation") if isinstance(current.get("orientation"), dict) else {}
        # Merge patch + existing to see the post-merge state.
        merged_orient = {**(existing_orient or {}), **orient_patch}
        has_angle = isinstance(merged_orient.get("north_angle_deg"), (int, float))
        has_edge = bool(merged_orient.get("north_edge_label_id"))
        if has_angle and not has_edge:
            if merged_orient.get("assumed") is not True:
                # Inject the correction into the patch we're about to apply.
                if not isinstance(patch.get("orientation"), dict):
                    patch["orientation"] = {}
                patch["orientation"]["assumed"] = True
                auto_corrections.append(
                    "orientation.assumed forced to true (north_angle_deg set "
                    "without north_edge_label_id — see §G4-3)"
                )

    # G4-4 (followups-tracker): warn when heights.{bezug_mm, first_mm}
    # is set without matching height_mark labels. Block in
    # HOUSE_FACTS_STRICT mode (per §8 decision 2).
    heights_patch = patch.get("heights") if isinstance(patch.get("heights"), dict) else None
    if heights_patch is not None and any(k in heights_patch for k in ("bezug_mm", "first_mm")):
        # Check whether any scene's labels contain a matching height_mark.
        try:
            ds_status, ds_body = await _api_get(f"/datasets/{key}")
            scenes = (ds_body or {}).get("drawings") or []
            need_bezug = "bezug_mm" in heights_patch
            need_first = "first_mm" in heights_patch
            saw_bezug_label = False
            saw_first_label = False
            for d in scenes:
                f = d.get("file")
                if not f:
                    continue
                lbl_status, lbl = await _api_get(f"/labels/dataset/{key}/{f}")
                if lbl_status != 200 or not isinstance(lbl, dict):
                    continue
                for lab in (lbl.get("labels") or []):
                    if lab.get("type") != "height_mark":
                        continue
                    attrs = lab.get("attributes") or {}
                    if need_bezug and attrs.get("value_mm") == 0:
                        saw_bezug_label = True
                    if need_first and attrs.get("datum") == "first":
                        saw_first_label = True
                if (not need_bezug or saw_bezug_label) and (not need_first or saw_first_label):
                    break
            missing = []
            if need_bezug and not saw_bezug_label:
                missing.append("bezug_mm (need a height_mark with value_mm == 0)")
            if need_first and not saw_first_label:
                missing.append("first_mm (need a height_mark with datum == 'first')")
            if missing:
                strict = os.environ.get("HOUSE_FACTS_STRICT", "0").strip() not in ("", "0", "false")
                msg = "heights set without matching height_mark labels: " + "; ".join(missing)
                if strict:
                    return _err(
                        "heights_without_labels",
                        msg,
                        hint=(
                            "drop the height_mark labels first (via upsert_label "
                            "or follow the W1-height-anchor MCP prompt). "
                            "HOUSE_FACTS_STRICT=1 blocks this write."
                        ),
                        retry=False,
                        started_at=started,
                    )
                else:
                    warnings.append(msg + " (would block in strict mode)")
        except Exception:  # noqa: BLE001
            # Lookup failure is non-fatal — the heights write itself still goes through.
            pass

    merged = _deep_merge(current or {}, patch)
    merged.setdefault("schema_version", "1.0")
    put_status, put_body = await _api_put(f"/datasets/{key}/house_facts", merged)
    if put_status >= 400:
        return _http_status_to_error(put_status, put_body, started)
    envelope = _ok(merged, started_at=started, status_code=put_status)
    if warnings:
        envelope["_meta"]["warnings"] = warnings
    if auto_corrections:
        envelope["_meta"]["auto_corrections"] = auto_corrections
    return envelope


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ── §5.7b Building-global facts (issue #8) ───────────────────────────────


@mcp.tool()
async def set_building_global_fact(
    key: str,
    fact: str,
    value: float,
    source_scene: str,
    source_label_id: str | None = None,
    confidence: str = "medium",
    unit: str = "mm",
    notes: str | None = None,
) -> dict:
    """Record a BUILDING-GLOBAL fact with provenance (issue #8).

    Höhenkoten (FH/TH/DG/EG/UG/Bezug), the müNN datum, roof pitch and
    Kniestock are properties of the *building*, not of one view — identical
    on every facade. Read each one ONCE from its best source (usually the
    Schnitt) and record it here; it is then available on every scene of
    the house. Each value stores which scene + label it came from and a
    confidence, so the cross-scene propagation is auditable.

    USE when:
      - You read a height/datum/roof value that holds for the whole
        building (not a facade-specific dimension). Cite the scene + the
        label it came from.

    DON'T USE when:
      - The value is facade-specific (a wall width on one Ansicht) — that
        belongs in the per-scene labels / extent, not here.

    Args:
      fact:            one of the recognized names — UG_mm, EG_mm, OG_mm,
                       DG_mm, TH_mm, FH_mm (relative to EG ±0.00),
                       EG_munn_mm (müNN datum), bezug_mm, first_mm,
                       roof_pitch_deg, kniestock_mm, ridge_munn_mm.
      value:           numeric value in `unit`.
      source_scene:    the scene file the value was read from (required —
                       provenance is the point).
      source_label_id: the label it was read from, when there is one.
      confidence:      low | medium | high.
      unit:            mm (default) or deg for roof_pitch_deg.
      notes:           optional free text.

    Returns: `data` = {fact, entry, building_global_facts}. Call
    `get_building_global_facts` to see the propagated + derived view.
    """
    started = time.time()
    from api.building_facts import (
        CONFIDENCE_LEVELS, KNOWN_FACTS, SCHEMA, make_fact,
    )
    if not source_scene:
        return _err("missing_provenance",
                    "source_scene is required — building-global facts must cite where they were read",
                    started_at=started)
    if fact not in KNOWN_FACTS:
        return _err("unknown_fact", f"{fact!r} is not a recognized building-global fact",
                    hint=f"known facts: {sorted(KNOWN_FACTS)}", started_at=started)
    if confidence not in CONFIDENCE_LEVELS:
        return _err("bad_confidence", f"confidence must be one of {sorted(CONFIDENCE_LEVELS)}",
                    started_at=started)
    if not isinstance(value, (int, float)):
        return _err("bad_value", "value must be numeric", started_at=started)
    try:
        entry = make_fact(
            float(value), source_scene=source_scene, source_label_id=source_label_id,
            confidence=confidence, unit=unit, notes=notes,
        )
    except ValueError as e:
        return _err("bad_fact", str(e), started_at=started)
    patch = {"building_global": {"schema": SCHEMA, "facts": {fact: entry}}}
    res = await set_house_facts(key=key, patch=patch)
    if not res.get("ok"):
        return res
    bg = ((res.get("data") or {}).get("building_global") or {})
    return _ok(
        {"fact": fact, "entry": entry, "building_global_facts": bg.get("facts", {})},
        started_at=started, status_code=200,
    )


@mcp.tool()
async def get_building_global_facts(key: str) -> dict:
    """Read the building-global facts tier + deterministic derivations.

    USE when:
      - At the start of labeling any Ansicht/Schnitt: pull the shared
        heights/datum/roof so you don't re-read what's already known.
      - Before W1/W4 to see which building-wide anchors exist and which
        derived values follow from them.

    Returns: `data` = {
      facts:        stored values, each with {value, unit, confidence,
                    source:{scene,label_id}},
      derived:      deterministically computed facts (math, not OCR), each
                    flagged derived:true + needs_cross_check:true — e.g.
                    <X>_munn_mm = EG_munn_mm + <X>_mm; storey heights from
                    level deltas; roof rise from pitch (+ extent depth),
      propagation:  {applies_to_scenes:[...]} — these hold on every scene.
    }
    """
    started = time.time()
    from api.building_facts import build_global_view
    try:
        f_status, facts = await _api_get(f"/datasets/{key}/house_facts")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        f_status, facts = await _api_get(f"/datasets/{key}/house_facts")
    if f_status == 404:
        facts = {}
    elif f_status >= 400:
        return _http_status_to_error(f_status, facts, started)
    ds_status, ds = await _api_get(f"/datasets/{key}")
    if ds_status >= 400:
        return _http_status_to_error(ds_status, ds, started)
    scene_files = [d.get("file") for d in ((ds or {}).get("drawings") or []) if d.get("file")]
    view = build_global_view(
        (facts or {}).get("building_global"), scene_files,
        extent=(facts or {}).get("extent"),
    )
    return _ok(view, started_at=started, status_code=200)


# ── §5.8 Export ──────────────────────────────────────────────────────────


@mcp.tool()
async def validate_export_readiness(key: str) -> dict:
    """Server-side gate: is the house HONESTLY complete enough to export?

    USE when:
      - Before calling export_house, to surface blockers without
        committing to the (expensive) export pipeline.
      - As the autonomous label driver's stop-condition. `ready:true`
        now means the substantive ground-truth phases exist — not just
        that the server's minimal sanity gate would accept the bytes.

    Why this is stricter than the export pipeline's own gate (issue #6):
    `export_house`'s sanity check (api/main._sanity_check_house) only
    requires ≥1 drawing + ≥1 labeled scene. Because every scene gets the
    `labeled` flag at W0 tagging time, a house with W0 tags + an assumed
    orientation and ZERO geometry (no heights, no extent, no calibration)
    used to pass `ready:true` — inviting an honest agent to export an
    empty dataset. `ready` now reflects honest completeness instead.

    Required phases for `ready`/`honest_complete`:
      - W0 (every scene tagged; grundriss carry a level)
      - W1 (heights: bezug_mm == 0 and first_mm set)
      - W2 (extent width+depth and wall_thickness.outer)
      - W3 (orientation set — assumed is fine, absent is not)
      - W4 (calibration per ansicht/schnitt) — only when the house has
        any ansicht/schnitt scenes; skipped for floorplan-only houses.
    W5 (detail) is optional and never blocks.

    Returns: `data` = {
      ready: bool,                # == honest_complete
      honest_complete: bool,      # all required phases done
      minimal_export_ok: bool,    # the permissive gate export_house enforces
      blockers: [str, …],         # missing required phases + their reasons
      phase_completeness: {Wn: {status, required, blockers}},
      required_phases: [str, …],
      scenes_total, labeled_scenes,
    }
    """
    started = time.time()
    try:
        status, body = await _api_get(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_get(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    drawings = body.get("drawings") or []
    facts, scene_meta = await _load_facts_and_scene_meta(key, body or {})
    state = _derive_workflow_state(body or {}, facts, scene_meta)
    phases = state["phases"]

    # W4 only applies when the house actually has scenes that need
    # calibration. Floorplan-only houses have nothing to calibrate, so
    # requiring W4 there would make `ready` unreachable. This mirrors the
    # has_calibration_targets gate inside _derive_workflow_state.
    has_calibration_targets = any(
        scene_meta.get(d.get("file"), {}).get("scene_tag") in ("ansicht", "schnitt")
        for d in drawings
    )
    required = ["W0", "W1", "W2", "W3"]
    if has_calibration_targets:
        required.append("W4")
    # V5.1: require real geometry whenever the house has any
    # geometry-bearing scene (grundriss/schnitt/ansicht). This is the
    # honest-gate fix — facts present but zero polygons is NOT ready.
    has_geometry_targets = any(
        scene_meta.get(d.get("file"), {}).get("scene_tag") in _REQUIRED_GEOMETRY
        for d in drawings
    )
    if has_geometry_targets:
        required.append("Wgeo")

    phase_completeness = {
        p: {
            "status": phases[p]["status"],
            "required": p in required,
            "blockers": phases[p]["blockers"],
        }
        for p in ("W0", "W1", "W2", "W3", "W4", "Wgeo", "W5")
    }

    # Honest blockers: every required phase that isn't done, with its own
    # predicate reasons spelled out so callers see the missing geometry.
    honest_blockers: list[str] = []
    for p in required:
        if phases[p]["status"] != "done":
            reason = "; ".join(phases[p]["blockers"]) or "incomplete"
            honest_blockers.append(f"{p} incomplete: {reason}")

    # The permissive gate the export pipeline actually enforces. Surfaced
    # so callers understand a dishonest export would still be ACCEPTED
    # (and so they don't gate on it).
    minimal_blockers: list[str] = []
    if not drawings:
        minimal_blockers.append("house has zero drawings")
    elif not any(d.get("labeled") for d in drawings):
        minimal_blockers.append("no annotated scenes")

    all_blockers = list(dict.fromkeys(honest_blockers + minimal_blockers))
    honest_complete = not all_blockers

    # Issue #27: surface scenes whose W4 calibration rests on the
    # single-ref isotropic (square-pixel) assumption — they count as
    # calibrated, but an honest export should record the assumption.
    assumed_isotropic_scenes = phases["W4"].get("assumed_isotropic_scenes") or []

    return _ok({
        "ready": honest_complete,
        "honest_complete": honest_complete,
        "minimal_export_ok": not minimal_blockers,
        "blockers": all_blockers,
        "phase_completeness": phase_completeness,
        "required_phases": required,
        "scenes_total": len(drawings),
        "labeled_scenes": sum(1 for d in drawings if d.get("labeled")),
        "calibration_assumptions": {
            "single_ref_assumed_isotropic": assumed_isotropic_scenes,
        },
    }, started_at=started, status_code=status)


@mcp.tool()
async def export_house(
    key: str,
    force: bool = False,
    idempotency_key: str | None = None,
) -> dict:
    """Render the Set A / Set B export for one house.

    USE when:
      - Workflow is complete (all required phases done, ≥1 labeled
        scene). This is the "done" signal of an agent run.

    DON'T USE when:
      - `validate_export_readiness` returns ready=false — fix the
        blockers first.

    Args:
      key: house key.
      force: if True, bypass the sanity gate. Default false.
    """
    started = time.time()
    if not force:
        readiness = await validate_export_readiness(key=key)
        if not readiness.get("ok"):
            return readiness
        data = readiness.get("data") or {}
        if not data.get("ready"):
            return _err(
                "export_blocked",
                "honest readiness gate blocked the export",
                hint="fix error.details.blockers or call export_house(force=true) only for an explicit debug export",
                retry=True,
                details={"blockers": data.get("blockers") or []},
                started_at=started,
            )
    try:
        status, body = await _api_post(
            f"/exports/{key}",
            params={"force": "true" if force else "false"},
        )
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_post(
            f"/exports/{key}",
            params={"force": "true" if force else "false"},
        )
    if status == 409:
        detail = body.get("detail") if isinstance(body, dict) else body
        anomalies = (detail or {}).get("anomalies") if isinstance(detail, dict) else None
        return _err("export_blocked",
                    "sanity gate blocked the export",
                    hint="see error.details.blockers; pass force=true to bypass",
                    retry=True,
                    details={"blockers": anomalies or []},
                    started_at=started, status_code=status)
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


# ── §5.9 Audit ───────────────────────────────────────────────────────────


@mcp.tool()
async def list_anomalies(key: str) -> dict:
    """List validator-flagged issues for a house — everything blocking
    a clean export plus per-phase predicate failures, server-side
    derivation warnings, and any assumed/uncertain rows the agent
    or human flagged.

    USE when:
      - Triaging a failed `export_house`: which blockers must be cleared?
      - Pre-flight before committing a labeling pass: how clean is the
        house?
      - Looking for the agent's "I guessed" markers (assumed: true on
        orientation, status: uncertain on labels) before exporting.

    DON'T USE when:
      - The agent already knows the current phase's blockers from
        `get_workflow_state`; this tool aggregates across all phases.

    Returns: `data.anomalies = [{phase, kind, message, severity}]`
    where severity ∈ {"blocker", "warning", "info"}.

    Augmented per agentic-labeling-followups-tracker §G5-2 to include
    server-side derivation warnings (G1) + assumed-orientation rows
    (G4-3) + uncertain labels (B6).
    """
    started = time.time()
    wf = await get_workflow_state(key=key)
    if not wf.get("ok"):
        return wf
    state = wf["data"]
    anomalies: list[dict] = []
    for phase, ph in state["phases"].items():
        for b in ph.get("blockers", []):
            anomalies.append({
                "phase": phase, "kind": "phase_blocker",
                "message": b, "severity": "blocker",
            })
    if not state.get("exportable"):
        anomalies.append({
            "phase": "export", "kind": "export_blocker",
            "message": "no labeled scenes yet", "severity": "blocker",
        })

    # G5-2: derivation warnings from fact_derivation.recompute_…
    # (HOUSE_FACTS_STRICT mode drops fields, surfaces them here).
    facts_env = await get_house_facts(key=key)
    facts = (facts_env or {}).get("data") or {}
    for w in facts.get("_derivation_warnings", []) or []:
        anomalies.append({
            "phase": "facts", "kind": "derivation_warning",
            "message": w, "severity": "warning",
        })

    # G5-2: assumed orientation surfaces here so reviewers prioritize.
    orient = facts.get("orientation") or {}
    if orient.get("assumed") is True:
        msg = "orientation is assumed (no compass mark on drawing)"
        if isinstance(orient.get("north_angle_deg"), (int, float)):
            msg += f" — north_angle_deg={orient['north_angle_deg']}"
        anomalies.append({
            "phase": "W3", "kind": "assumed_orientation",
            "message": msg, "severity": "warning",
        })

    # G5-2 + H3: per-scene anomalies — uncertain labels + missing
    # orientation on ansicht/schnitt.
    try:
        ds_status, ds_body = await _api_get(f"/datasets/{key}")
        for d in (ds_body or {}).get("drawings") or []:
            f = d.get("file")
            if not f:
                continue
            lbl_status, lbl = await _api_get(f"/labels/dataset/{key}/{f}")
            if lbl_status != 200 or not isinstance(lbl, dict):
                continue
            uncertain = sum(
                1 for lab in (lbl.get("labels") or [])
                if lab.get("status") == "uncertain"
            )
            if uncertain:
                anomalies.append({
                    "phase": "labels", "kind": "uncertain_labels",
                    "message": f"{f}: {uncertain} label(s) marked uncertain",
                    "severity": "info",
                    "details": {"file": f, "count": uncertain},
                })
            # H3: missing orientation on ansicht/schnitt is now a warning,
            # not a W0 blocker. Surface for reviewer triage.
            tag = lbl.get("scene_tag")
            if tag in ("ansicht", "schnitt") and not lbl.get("scene_orientation"):
                anomalies.append({
                    "phase": "W0", "kind": "missing_orientation",
                    "message": f"{f}: scene_orientation not set (was previously a blocker; now a warning so reviewers can spot-check)",
                    "severity": "warning",
                    "details": {"file": f, "scene_tag": tag},
                })
    except Exception:  # noqa: BLE001
        pass

    # G5-2: surface the agent's run marker so reviewers see it.
    wf_obj = facts.get("workflow") or {}
    if wf_obj.get("driven_by"):
        anomalies.append({
            "phase": "review", "kind": "agent_labeled",
            "message": (
                f"labeled by {wf_obj['driven_by']!r}"
                + (f", run {wf_obj.get('driven_by_run_id')!r}"
                   if wf_obj.get("driven_by_run_id") else "")
                + " — needs human spot-check"
            ),
            "severity": "info",
        })

    counts = {
        "blocker": sum(1 for a in anomalies if a["severity"] == "blocker"),
        "warning": sum(1 for a in anomalies if a["severity"] == "warning"),
        "info": sum(1 for a in anomalies if a["severity"] == "info"),
    }
    return _ok({"anomalies": anomalies, "count": len(anomalies), "by_severity": counts},
               started_at=started)


@mcp.tool()
async def dump_run_summary(key: str, run_id: str, notes: str = "") -> dict:
    """Write a Markdown run summary to tmp/agent-runs/<run-id>/<key>.md.

    USE when:
      - Driver finishes a phase or a whole run and wants to capture a
        human-readable record.

    Args:
      key: house key.
      run_id: any short string. Driver convention:
              `YYYYMMDD-HHMM-<key>` (e.g. `20260530-1142-house-22`).
      notes: optional free-text to append after the auto-generated body.
    """
    started = time.time()
    safe_run = "".join(c for c in run_id if c.isalnum() or c in "-_")
    if not safe_run:
        return _err("schema_invalid", "run_id must be non-empty alphanumeric",
                    started_at=started)
    wf_env = await get_workflow_state(key=key)
    if not wf_env.get("ok"):
        return wf_env
    state = wf_env["data"]
    out_dir = Path(__file__).parent / "tmp" / "agent-runs" / safe_run
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{key}.md"
    body = ["# Run summary",
            f"- house: `{key}`",
            f"- run_id: `{safe_run}`",
            f"- generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            f"- exportable: {state.get('exportable')}",
            f"- next_phase: {state.get('next_phase')}",
            f"- scenes_total: {state.get('scenes_total')}",
            f"- labeled_scenes: {state.get('labeled_scenes')}",
            "",
            "## Phases"]
    for p, ph in state["phases"].items():
        body.append(f"- **{p}** — {ph['status']}")
        for b in ph.get("blockers", []):
            body.append(f"    - blocker: {b}")
    if notes:
        body.extend(["", "## Notes", notes])
    out_path.write_text("\n".join(body) + "\n")
    return _ok({"path": str(out_path.relative_to(Path(__file__).parent)),
                "bytes": out_path.stat().st_size},
               started_at=started)


@mcp.tool()
async def write_handoff_summary(
    key: str,
    run_id: str,
    file: str | None = None,
    phase: str = "scene",
    status: str = "needs_repair",
    labels_added: int = 0,
    labels_changed: int = 0,
    open_defects: list[str] | None = None,
    uncertain_labels: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    next_action: str | None = None,
    notes: str = "",
    quality: dict | None = None,
    calibration: dict | None = None,
    max_items: int = 20,
) -> dict:
    """Write a compact scene/phase handoff summary for context reduction.

    USE when:
      - A scene or phase worker is finished or pausing and the parent
        should receive durable state without inheriting the worker's full
        image/tool transcript.

    DON'T USE when:
      - You still need to perform visual verification. Write the handoff
        only after labels, defects, and evidence have been updated.
    """
    started = time.time()
    safe_run = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-") or "run"
    base = Path(__file__).parent / "tmp" / "agent-runs" / safe_run / "handoffs"
    base.mkdir(parents=True, exist_ok=True)
    target = file or phase
    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "-", target).strip("-") or "handoff"
    max_items = max(0, int(max_items))

    def bounded(values: list[str] | None) -> tuple[list[str], dict[str, int]]:
        values = values or []
        return values[:max_items], {
            "total": len(values),
            "returned": min(len(values), max_items),
            "omitted": max(0, len(values) - max_items),
        }

    defects, defect_counts = bounded(open_defects)
    uncertain, uncertain_counts = bounded(uncertain_labels)
    evidence, evidence_counts = bounded(evidence_refs)
    payload = {
        "summary_contract": "mcp-context-bloat/handoff-summary-v1",
        "key": key,
        "file": file,
        "phase": phase,
        "status": status,
        "labels_added": labels_added,
        "labels_changed": labels_changed,
        "open_defects": defects,
        "uncertain_labels": uncertain,
        "calibration": calibration or {},
        "quality": quality or {},
        "evidence_refs": evidence,
        "next_action": next_action,
        "notes": notes,
        "truncated": any(c["omitted"] for c in (defect_counts, uncertain_counts, evidence_counts)),
        "truncation": {
            "open_defects": defect_counts,
            "uncertain_labels": uncertain_counts,
            "evidence_refs": evidence_counts,
        },
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    json_path = base / f"{safe_target}.json"
    md_path = base / f"{safe_target}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    md_path.write_text("\n".join([
        f"# Handoff {key}",
        "",
        f"- File: {file or ''}",
        f"- Phase: {phase}",
        f"- Status: {status}",
        f"- Labels added: {labels_added}",
        f"- Labels changed: {labels_changed}",
        f"- Open defects: {len(open_defects or [])}",
        f"- Uncertain labels: {len(uncertain_labels or [])}",
        f"- Next action: {next_action or ''}",
        "",
        notes,
    ]) + "\n")
    return _ok({
        "json_path": str(json_path.relative_to(Path(__file__).parent)),
        "markdown_path": str(md_path.relative_to(Path(__file__).parent)),
        "bytes": json_path.stat().st_size,
        "summary": payload,
    }, started_at=started)


# ── §5.10 MCP resources (read-only context) ──────────────────────────────


@mcp.resource("bim-db://version")
def resource_version() -> str:
    return json.dumps({
        "server_version": SERVER_VERSION,
        "api_base": API_BASE,
        "tool_count": "phase-A subset (4 tools; Phase B adds 18)",
    }, indent=2)


@mcp.resource("bim-db://schema/scene_labels")
def resource_scene_labels_schema() -> str:
    p = Path(__file__).parent / "schema" / "scene_labels.schema.json"
    return p.read_text() if p.exists() else "{}"


@mcp.resource("bim-db://schema/intake_manifest")
def resource_intake_manifest_schema() -> str:
    p = Path(__file__).parent / "schema" / "intake_manifest.schema.json"
    return p.read_text() if p.exists() else "{}"


@mcp.resource("bim-db://docs/grid-coordinates")
def resource_grid_coordinates() -> str:
    return """# Grid coordinate frame

Every image returned by `get_scene_view` or `get_pdf_page_view` carries
a three-tier grid overlay. The coordinate labels in the margins ALWAYS
reference SOURCE pixels — never the rendered output pixels, never any
internal cache scale. You can feed any label-frame coordinate you read
off the grid directly into a tool call like `upsert_label`.

Tiers (from bold to faint):

| Tier   | Cell size                            | Use for                                 |
|--------|--------------------------------------|-----------------------------------------|
| broad  | image_long_edge / 10 (~200–500 px)   | scoping which quadrant a feature is in  |
| finer  | image_long_edge / 50 (~40–100 px)    | naming a polygon vertex ±25 px          |
| detail | image_long_edge / 200 (~10–25 px)    | snap-style precision; no labels (noise) |

To zoom into a region, call `get_scene_view(file=..., region="x0,y0,x1,y1")`.
The labels in the zoom still read in source-pixel coords — so a vertex
you identify in a zoom at (1240, 670) maps to (1240, 670) in the
un-cropped scene without any translation.

Don't trace coordinates across the dense grid if you can avoid it
(issue #10): vision-LLMs are strong at "that feature, there" and weak at
"row 1797, col 232". Instead:

  - Point in the crop's LOCAL frame. Call
    `resolve_scene_point(point=[lx,ly], region=..., frame="crop")` with the
    point in the zoom's own pixel frame (0..w, 0..h). The server maps it
    back to source pixels for you.
  - Snap to the real mark. `resolve_scene_point(..., snap=true)` snaps the
    point to the nearest drawn feature (tick-triangle, line, dim arrow)
    within `snap_radius_px`. Place approximately; the server lands you on
    the feature. Feed the returned `source_point` into upsert_label /
    add_reference_dim.
  - Correct by a delta. After a write, `verify_label_placement` reports
    `offset_px` — the vector from your anchor to the nearest feature — so
    you nudge by an exact amount instead of eyeballing.
"""


# ── §5.11 MCP prompts (adapter playbooks) ─────────────────────────────────
# These prompts are transport/discovery adapters for MCP clients. They are NOT
# the source of truth for labeling methodology. Keep durable workflow rules in
# bim-agent/spec/labeling-methodology.md and exact tool contracts in
# bim-agent/spec/labeling-tool-contract.md; model-specific harness files and
# MCP prompts should point there and carry only invocation/routing glue.


def _model_neutral_labeling_spec_notice() -> str:
    return """## Model-neutral source of truth

This MCP prompt is an adapter. The canonical labeling workflow lives in:

- `~/repos/bim-agent/spec/labeling-methodology.md`
- `~/repos/bim-agent/spec/labeling-tool-contract.md`
- `~/repos/bim-agent/spec/driver-loop-contract.md`

If this prompt conflicts with those specs, follow the specs and treat this
prompt as stale adapter guidance.
"""


@mcp.prompt(name="label-house")
def prompt_label_house(key: str) -> str:
    return f"""# Label house `{key}` end-to-end

{_model_neutral_labeling_spec_notice()}

You are driving the bim-database annotation workflow for one house. Your
goal: produce an export-ready labeled house. Open the bim-database SPA
at http://localhost:12500/{key} alongside this session — your writes
appear there immediately.

## Tools you'll use (from the bim-database MCP server)

| Phase | Primary tools                                                                |
|-------|------------------------------------------------------------------------------|
| W0    | get_house, get_scene_view, set_scene_tag, set_scene_orientation, set_scene_level |
| W1    | get_scene_view, upsert_label (height_mark), set_house_facts                  |
| W2    | get_scene_view, add_reference_dim, upsert_label (wall), set_house_facts      |
| W3    | get_scene_view, set_house_facts                                              |
| W4    | get_scene_view, add_reference_dim, recompute_homography                      |
| plan  | create_scene_plan_state_from_template, get_scene_plan_status, get_scene_plan_next_action |
| any   | get_workflow_state, get_recommended_next_action, validate_export_readiness, export_house |

## Context-bloat policy

Quality remains more important than saving tokens: request inline images
whenever the current decision needs pixels. But keep context focused:

1. Start routing with `get_house_context_summary` and
   `get_scene_context_summary`, not full house/label/plan dumps.
2. Use one low-detail overview per scene to choose work regions.
3. Use `verify_label_placement` for routine verify-after-write. It auto-crops
   around the edited label and reports numeric offset hints.
4. Use full `get_scene_view_with_labels` only for multi-label/global topology
   QA, final scene review, or when a crop lacks enough context.
5. For large overview/debug renders in runtimes that can inspect file
   handles, pass `image_delivery="handle"` or `"auto"`; pass
   `image_delivery="inline"` when the model must see pixels now.
6. After each scene/phase, call `write_handoff_summary` with open blockers,
   evidence refs, quality metrics, and next action. The parent should keep
   that summary, not the full visual transcript.
7. If a bounded QA result says `truncated=true`, use the returned counts and
   fetch/fix the highest-priority visible blockers first; do not treat
   truncation as a pass.

## Scene-plan gate (REQUIRED before geometry labels)

Once W0 has classified a scene and the crop/bounding box exists, every
geometry-bearing scene (`grundriss`, `ansicht`, `schnitt`) MUST have a
structured scene plan before any subagent places geometry labels.

For each such scene:

1. `get_scene_plan_status(key="{key}", file=<scene>)`.
2. If `data.exists == false`, call
   `create_scene_plan_state_from_template(key="{key}", file=<scene>,
   scene_tag=<scene_tag>, level_or_orientation=<level-or-orientation>)`.
   This writes both `*.plan.json` and the synced `plan.md`.
3. Scene subagents work only through the plan loop:
   `get_scene_plan_next_action` → `start_scene_plan_action` →
   analyze/edit/verify → `add_scene_plan_evidence` /
   `record_scene_plan_attempt` → `finish_scene_plan_action` →
   `evaluate_scene_plan_gates` → repeat.
4. A scene is NOT complete until every required task is `verified` and
   `get_scene_plan_status(...).data.required_complete == true`. A plan
   that merely exists, a `draft` plan, or a plan whose required tasks are
   `accepted_incomplete` is not an honest pass.
5. If a scene has labels but required tasks are not verified, treat the
   labels as legacy/unverified. Continue through the plan loop until the
   required tasks verify; do not use minimal labels or accepted-incomplete
   waivers to satisfy export.

`get_workflow_state` and `validate_export_readiness` intentionally keep
`Wgeo` pending for geometry-bearing scenes whose plan state is missing or
whose required plan tasks are incomplete, even if labels exist. Labels
without the completed plan loop are not an honest completion.

## Ground-floor-first gate

After W0 has classified scenes, finish every EG/Ground-floor Grundriss
scene plan before touching non-groundfloor geometry, sections, elevations,
or export. In practice:

1. Call `get_recommended_next_action(key="{key}")`.
2. If it returns a `Wgeo` action for an EG scene, do exactly that action.
3. Repeat until all EG scene plans report `required_complete=true` through
   verified required tasks, not accepted-incomplete waivers.
4. Only then continue to UG/DG plans, sections, elevations, W4 calibration,
   and export.

## Resources to read first

- `bim-db://schema/scene_labels` — Label types + geometry shapes ([x,y] arrays)
- `bim-db://docs/grid-coordinates` — How to read the grid overlay

## Step 0 — STAMP YOUR RUN (per §G3-6, before any other write)

The bim-database SPA shows a `🤖 Agent` chip on the dataset card
when `house_facts.workflow.driven_by == "bim-agent"`. Reviewers use
the chip to find agent-labeled houses for spot-checking. STAMP THIS
FIRST, before any other tool call — if you crash mid-run, the partial
result is still attributable to you.

```
set_house_facts(key="{key}", patch={{
  "workflow": {{
    "driven_by": "bim-agent",
    "driven_by_run_id": "<your-run-id-or-iso-timestamp>",
    "driven_by_started_at": "<iso-timestamp>"
  }}
}})
```

## Operating loop

```
while true:
    action = get_recommended_next_action(key="{key}")
    if action.done:
        break
    do exactly action.suggested_tool/action.suggested_args
    if action.phase == "Wgeo":
        stay on that one scene-plan action until it has
        start_scene_plan_action -> evidence/attempt -> finish -> evaluate-gates
validate_export_readiness
export_house only if ready=true
```

## Core principles (DO NOT SKIP)

1. **Always look at the grid before naming coordinates.** Call
   `get_scene_view` (with `region=` zoom for precision) before EVERY
   label. The labels in the overlay show source pixels — feed them
   directly into tool calls.
2. **Honest values.** If you can't read a dim number confidently, set
   `status="uncertain"` on the label. Never invent.
3. **One reference dim at a time.** Add → call `recompute_homography`.
   If RMS > 8 px, delete it and try a more-orthogonal candidate.
4. **Never edit existing human work.** Check
   `get_house_facts.workflow.touched_by` before overwriting; if a human
   has touched the house, halt.
5. **Honest reporting.** When you halt or finish, call `dump_run_summary`
   so the developer sees what you did.
6. **Labels before facts.** For W1 + W2 specifically: drop the
   geometry-bearing labels (height_mark, dimensioned_distance with
   is_reference) BEFORE setting facts. Server-side derivation will
   populate facts automatically. Setting facts without labels makes
   the SPA's overlay rendering go blank — reviewers can't trust it.
7. **Stamp your run** (Step 0 above).
8. **VERIFY EVERY GEOMETRY WRITE (per §H5).** After every
   `upsert_label` / `add_reference_dim` / `update_label_attrs`, call
   `get_scene_view_with_labels(key, file, region=<tight crop>)` and
   check the rendered stroke / dot / chip sits on the feature you
   meant. The agent's single biggest historical failure mode was
   placing labels off-feature and never noticing — the verify view is
   the fix. Budget: 3 placement attempts per label; if the third still
   misses, `set_label_status(..., "uncertain")` and move on.
9. **PLAN BEFORE LABELS.** Do not place walls, openings, component lines,
   height marks, or reference dimensions on a geometry-bearing scene until
   `get_scene_plan_status(...).data.exists == true` for that scene and you
   have claimed the relevant plan action. If a prior run left labels but
   no verified plan, create/repair the plan and verify through the plan loop.
10. **EG BEFORE EVERYTHING ELSE.** If any EG Grundriss plan has
    `required_complete=false`, do not label UG/DG, sections, elevations,
    or W4 calibration. Finish EG final QA as verified first.
11. **NO BASELINE WAIVERS.** Do not mark required tasks
    `accepted_incomplete` to reach export. If you cannot verify a required
    task, leave it open or `blocked_external` with evidence; export must
    remain blocked.

Start now: call `get_workflow_state(key="{key}")` and follow the
appropriate phase playbook.
"""


@mcp.prompt(name="W0-inventory")
def prompt_w0_inventory(key: str) -> str:
    return f"""# W0 · Inventory — categorise every scene of `{key}`

{_model_neutral_labeling_spec_notice()}

Goal: every scene has a non-null `scene_tag`, Ansicht/Schnitt have
`scene_orientation`, Grundriss have `scene_level`.

## DEFAULT MAPPING (per §G3-1)

Each scene's manifest carries an extraction-time `kind` (different
vocabulary). Start from this default → only override with explicit
evidence:

| manifest.kind | default scene_tag | when to override                                    |
|---------------|-------------------|-----------------------------------------------------|
| `floorplan`   | `grundriss`       | almost never — confirm by reading the title block   |
| `elevation`   | `ansicht`         | almost never                                        |
| `section`     | `schnitt`         | almost never                                        |
| `detail`      | **`sonstiges`**   | only set `schnitt` if you can point at VISIBLE evidence: floor heights spanning multiple stories, cutaway hatching across the FULL building width, OR a title-block label like "Schnitt A-A". A close-up of a roof corner or eave is NOT a Schnitt — it's `sonstiges`. |

This default mapping prevents the most common W0 mis-tag (a detail
crop tagged `schnitt` because the cutaway-ish lines looked sectional
at a glance).

## Steps

For each scene returned by `get_house(key="{key}").drawings`:

1. `get_scene_view(key="{key}", file=<file>, tiers="broad")` — overview only.
2. Look up the default scene_tag from the table above based on
   `drawing.kind`. That's your starting answer.
3. Confirm by reading the title-block text (usually bottom-right):
   "EG-Grundriss", "Süd-Ansicht", "Schnitt A-A" — best ground truth.
   Override the default only when the title block contradicts it.
4. `set_scene_tag(key="{key}", file=<file>, tag=<tag>)`.
5. **scene_orientation (per §G3-2 + §H3): OPTIONAL.** If
   Ansicht/Schnitt with a CLEAR cardinal face (elevation labeled
   "Süd"/"South"; compass mark visible AND the wall it points to is
   the wall this scene shows), call `set_scene_orientation(...)`.
   **If unclear, leave null — DO NOT GUESS.** Per §H3 missing
   orientation does NOT block W0 anymore; it surfaces as a `warning`
   in `list_anomalies` so a human reviewer knows to spot-check.
   Detail crops never have a cardinal orientation; leave null always.
6. If Grundriss: identify the floor level (kg/ug/eg/og/dg/spitzboden)
   from the title text or by elimination (count the floors). Call
   `set_scene_level(...)`. If genuinely unclear, leave null.
7. For every scene now tagged as `grundriss`, `ansicht`, or `schnitt`,
   call `get_scene_plan_status`. If it is missing, call
   `create_scene_plan_state_from_template` with the confirmed tag and
   level/orientation. This is the handoff contract for later subagents.
8. Before leaving W0, call `get_recommended_next_action`. If it points to
   an EG `Wgeo` scene-plan action, that is the next required work. Do not
   start UG/DG, section, elevation, or export work while EG plans remain
   incomplete.

## Heuristics for ambiguous cases

- A drawing with both plan and section (split sheet) → tag as the
  dominant element; flag with `dump_run_summary` for human review.
- "EG" is the ground floor (Erdgeschoss), "OG" upper, "DG" attic,
  "KG" basement (Kellergeschoss).
- Cardinal directions in German labels: Nord/Süd/Ost/West.

## Exit

`get_workflow_state(key="{key}")["phases"]["W0"]["status"] == "done"`

If W0 still has blockers after one full pass, re-call `get_scene_view`
on the blocked scene with `region=` zoom to inspect the title block.
"""


@mcp.prompt(name="W1-height-anchor")
def prompt_w1_height_anchor(key: str) -> str:
    return f"""# W1 · Height anchor — establish ±0.00 + Firsthöhe for `{key}`

{_model_neutral_labeling_spec_notice()}

Goal: `facts.heights.bezug_mm == 0` and `facts.heights.first_mm != null`.

## ORDER MATTERS (per §G3-3)

**Drop the height_mark LABELS first, then optionally confirm via
`set_house_facts`.** Server-side derivation (G1) auto-populates
`facts.heights` from `height_mark` labels with `datum` + `value_mm`
set — calling `set_house_facts` afterwards is usually redundant.
SKIPPING the labels and just setting facts is the WRONG shortcut: the
SPA's Höhenkote rendering reads the LABELS, not the facts. A scene
with `facts.heights.first_mm = 8500` but no height_mark label shows
nothing on the canvas. Reviewers can't trust it.

## Steps

0. Call `get_recommended_next_action(key="{key}")`. If it returns an EG
   `Wgeo` scene-plan action, stop this W1 pass and finish that EG action
   first. Ground-floor plans have priority over global height anchoring.
1. `get_house(key="{key}")` — pick an Ansicht with the most visible
   vertical dimension lines (usually the one labeled "Süd-Ansicht" or
   "Hauptansicht").
2. `get_scene_view(key="{key}", file=<ansicht>, tiers="broad,finer")`
   — find the `±0,00` reference line at the ground floor and the
   Firsthöhe (ridge) line at the top.
3. For the bezug (±0.00) line:
   `get_scene_view(file=<ansicht>, region="<tight crop around the ±0 mark>")`
   to identify its exact pixel position. Then:
   ```
   upsert_label(key="{key}", file=<ansicht>, label={{
     "type": "height_mark",
     "geometry": {{"anchor": [x, y]}},
     "attributes": {{"value_mm": 0, "datum": "ok_ffb"}},
     "status": "readable"
   }})
   ```
4. For the Firsthöhe: same workflow. Read the value from the drawing
   (e.g. "8,50 m" → 8500 mm). Then:
   ```
   upsert_label(key="{key}", file=<ansicht>, label={{
     "type": "height_mark",
     "geometry": {{"anchor": [x, y]}},
     "attributes": {{"value_mm": 8500, "datum": "first"}},
     "status": "readable"
   }})
   ```
5. **VERIFY THE PLACEMENT (per §H5).** Immediately after each
   `upsert_label`, call:
   ```
   get_scene_view_with_labels(key="{key}", file=<ansicht>,
                              region=<crop around the just-placed mark>,
                              tiers="finer,detail")
   ```
   The dot + faint Bezugslinie + value chip must visibly sit on the
   `±0,00` / Firsthöhe line you intended. If it floats above/below, the
   anchor is wrong: `update_label_attrs` with corrected `anchor`, then
   re-verify. Budget 3 attempts per height_mark — if the third still
   misses, `set_label_status(..., "uncertain")` and move on.
6. `get_house_facts(key="{key}")` — confirm `heights.bezug_mm == 0`
   and `heights.first_mm == <expected>` BOTH appear. If they do, you're
   done; the server-side derivation already filled them in. If not, the
   `datum` on your height_mark labels is probably wrong (`datum: "first"`
   is required for first_mm; `value_mm: 0` is required for bezug_mm).
   Fix the labels and re-check — DO NOT just set facts manually.

## Exit

`get_workflow_state[...]["W1"]["status"] == "done"` AND
`get_house_facts.heights.sources` references at least one `hm:` source
for each populated key (proves labels back the facts).
"""


@mcp.prompt(name="W2-footprint")
def prompt_w2_footprint(key: str) -> str:
    return f"""# W2 · Footprint — width + depth + outer wall thickness for `{key}`

{_model_neutral_labeling_spec_notice()}

Goal: `facts.extent.width_mm`, `facts.extent.depth_mm`, and
`facts.wall_thickness.outer_mm` all set.

## Axis convention (per §H2)

On a Grundriss (plan view), the building's dimensions are:
- **horizontal dim → `extent.width_mm`** (Gebäudebreite)
- **vertical dim → `extent.depth_mm`** (Gebäudetiefe)

So adding ONE horizontal + ONE vertical reference dim on an EG-
Grundriss populates BOTH `width_mm` AND `depth_mm` via server-side
derivation. No need for a follow-up `set_house_facts` patch on
extent — just label the dims and confirm via `get_house_facts`.

## Steps

1. Pick EG-Grundriss (the one with `scene_level == "eg"`).
2. `get_scene_plan_status(key="{key}", file=<eg-grundriss>)`.
   If missing, call `create_scene_plan_state_from_template` with
   `scene_tag="grundriss"` and `level_or_orientation="eg"`.
   Then call `get_scene_plan_next_action` and `start_scene_plan_action`
   before placing any geometry/reference labels.
3. `get_scene_view(key="{key}", file=<eg-grundriss>, tiers="broad,finer")`
   — find a horizontal dim along the outer edge (full façade length;
   typically the longest dim on the sheet) and a vertical one along
   the depth.
4. Read both dim values from the drawing (e.g. "12,40 m" → 12400 mm).
5. For each, call:
   ```
   add_reference_dim(key="{key}", file=<eg>, orientation="horizontal",
                     start=[x1, y1], end=[x2, y2],
                     value_mm=12400, dimension_text="12,40 m")
   ```
   The tool returns `homography.rms_residual_px`. **Reject if > 8 px**
   — delete the dim and try a more-clearly-outer edge. (Use
   `delete_label(label_id=data.distance_id)` and the partner dim_number.)

   **VERIFY (per §H5).** Then immediately:
   ```
   get_scene_view_with_labels(key="{key}", file=<eg>,
                              region=<crop around the dim line>,
                              tiers="finer,detail")
   ```
   The green/red dim stroke must sit ON the building's outer edge, not
   on an interior wall or an unrelated line of text. Endpoint caps must
   line up with the corners. If it's wrong: `delete_label` and re-place;
   3-attempt budget then `status="uncertain"`.
6. Once both pass: identify an outer wall on the drawing — typically
   30-40 cm thick (drawn as a thick double line). Read its thickness:
   ```
   upsert_label(key="{key}", file=<eg>, label={{
     "type": "wall",
     "geometry": {{"start": [x1,y1], "end": [x2,y2]}},
     "attributes": {{"thickness_mm": 365}},
     "status": "readable"
   }})
   ```
   **VERIFY (per §H5)** — call `verify_label_placement` for the new wall.
   The orange wall stroke must lie along the drawn wall, not floating in
   empty space or crossing through openings. Escalate to a wider
   `get_scene_view_with_labels(region=...)` only if the crop lacks context.
7. Add compact scene-plan evidence for the dimensions/wall and call
   `evaluate_scene_plan_gates(key="{key}", file=<eg-grundriss>)`. Finish
   the claimed action with `finish_scene_plan_action` only after the
   verification evidence exists.
8. Confirm via `get_house_facts(key="{key}")`:
   - `extent.width_mm` = horizontal dim value
   - `extent.depth_mm` = vertical dim value
   - `wall_thickness.outer_mm` set
   You should NOT need to call `set_house_facts` for extent — derivation
   handles it. The only manual `set_house_facts` is for
   `wall_thickness.outer_mm` (since walls don't derive that
   automatically yet).

## Exit

`get_workflow_state[...]["W2"]["status"] == "done"` AND the auto-derived
`facts.extent` matches the dim values within 2 %.
"""


@mcp.prompt(name="W3-orientation")
def prompt_w3_orientation(key: str) -> str:
    return f"""# W3 · Orientation — pick the north edge for `{key}`

{_model_neutral_labeling_spec_notice()}

Goal: `facts.orientation.north_edge_label_id` set (or
`north_angle_deg` as fallback).

## HONESTY RULE (per §G3-4)

The `assumed` flag MUST reflect reality. Only set `assumed: false` when
there's an EXPLICIT on-drawing compass — a "N" arrow, a "Norden" label,
a compass rose. Everything else is a guess, and a guess MUST carry
`assumed: true`. A human reviewer scans for `assumed: true` rows to
prioritize what to spot-check.

## Steps

1. EG-Grundriss again. `get_scene_view(tiers="broad")`.
2. Look for a compass mark or "Norden" label. Look carefully — small
   compass arrows often hide in corners or near the title block.
3. **If you see an explicit compass mark:**
   - Identify the wall that aligns with north (the wall the compass
     arrow points along, or the wall labeled with "N"). Take its
     label_id from `list_scene_labels`.
   - ```
     set_house_facts(patch={{"orientation": {{
       "north_edge_label_id": <wall_id>,
       "assumed": false
     }}}})
     ```
4. **If NO compass mark visible:**
   - Default to north_angle_deg=0 (most catalog houses face the street,
     which is often south — so the back wall points roughly north).
   - You MUST mark this as a guess:
     ```
     set_house_facts(patch={{"orientation": {{
       "north_angle_deg": 0,
       "assumed": true
     }}}})
     ```
   - The server-side guard (§G4-3) will auto-correct `assumed: false`
     to `assumed: true` if you forget — but don't rely on that.

## Exit

`get_workflow_state[...]["W3"]["status"] == "done"`
"""


@mcp.prompt(name="W4-calibration")
def prompt_w4_calibration(key: str) -> str:
    return f"""# W4 · Calibration — per-scene reference dims for `{key}`

{_model_neutral_labeling_spec_notice()}

Goal: every Ansicht/Schnitt has `facts.calibration_per_scene[file]`
populated (one horizontal + one vertical reference dim, homography
RMS ≤ 8 px).

## ZOOM-BEFORE-NAMING DISCIPLINE (per §G3-5)

Every `add_reference_dim` call MUST be preceded by a
`get_scene_view(region=…)` call cropping to a tight bbox around the
dim line + its numeric label. Reading endpoints off the BROAD-tier
full-image view is what causes building-scale values to land on
detail-crop scenes (the 9084 mm horizontal ref on a roof-corner
detail bug, §B4). The plan.yaml the driver writes records every
zoom region used — if a plan step adds a ref dim without a paired
zoom call, the reviewer rejects the run.

## Steps per scene

0. Call `get_recommended_next_action(key="{key}")`. If it returns an EG
   `Wgeo` scene-plan action, stop W4 immediately. Do not calibrate
   Ansichten/Schnitte while ground-floor plans are incomplete.

For each scene where `scene_tag` ∈ {{"ansicht", "schnitt"}} AND
`get_house_facts.calibration_per_scene[file]` is absent:

1. `get_scene_view(key="{key}", file=<scene>, tiers="broad,finer")`
   — full image.
2. Apply the **is_reference selection ladder**
   (per agentic-labeling-tracker §8 decision 3):
   a. Identify the title-block bbox (usually bottom-right; it's the
      densest-text region). Exclude this half of the image.
   b. Find the **longest clearly-labeled horizontal** dim line in the
      remaining area — typically along the eaves or the foundation. The
      grid overlay's broad tier (~100-200 px cells) tells you the gross
      length.
   c. Find the **longest clearly-labeled vertical** dim — typically
      ground-to-eaves or ground-to-ridge.
3. **ZOOM FIRST — REQUIRED.** Pick a tight rectangle that includes
   BOTH the dim line's endpoints AND the numeric label text. Call:
   ```
   get_scene_view(file=<scene>,
                  region="<x0>,<y0>,<x1>,<y1>",
                  tiers="finer,detail")
   ```
   Read off the endpoint coords from the GRID LABELS IN THE ZOOM (they
   still reference source pixels) and read the numeric value from the
   visible text.
4. Sanity check the value: does the value match the scene's expected
   scale? A 9000+ mm dim on a 600-px-wide detail crop is almost
   certainly a building-scale dim that bled into the crop frame —
   reject and pick a smaller candidate.
5. ```
   add_reference_dim(key="{key}", file=<scene>, orientation="horizontal",
                     start=[x1,y1], end=[x2,y2],
                     value_mm=<value>, dimension_text="<as written>")
   ```
   Check `homography.rms_residual_px` in the response:
     - ≤ 8: keep going.
     - > 8: delete this dim + its partner dim_number, try the
       second-best candidate. Repeat up to 3 times.
6. **VERIFY THE PLACEMENT (per §H5).** Immediately call:
   ```
   get_scene_view_with_labels(key="{key}", file=<scene>,
                              region="<same zoom region as step 3>",
                              tiers="finer,detail")
   ```
   - The red REF-dim stroke (reference dims render red) must visibly
     span the dim line you read the value off of, with endpoint caps
     on the exact corners.
   - The value chip must show `REF <value>m`.
   - If the stroke floats next to / beside / through the dim instead
     of along it: `delete_label(label_id=data.distance_id)` + delete
     the dim_number partner, then re-place using the corrected
     endpoint reads. 3-attempt budget per scene per orientation.
   - After the third miss: `set_label_status(..., "uncertain")` on
     the closest attempt, log via `dump_run_summary`, move on.
7. Repeat for vertical (including a fresh verify pass).
8. Confirm `get_house_facts.calibration_per_scene` now has the file.

## Hard caps (per scene budget)

- 6 tool calls including all `get_scene_view`s.
- If still failing after 3 ref-dim attempts: `set_label_status(...,
  "uncertain")` on whichever dim came closest, then call
  `dump_run_summary` with `notes="W4 calibration failed on <scene>;
  human review needed"` and move to the next scene.

## Exit

`get_workflow_state[...]["W4"]["status"] == "done"`
"""


@mcp.prompt(name="W5-detail")
def prompt_w5_detail(key: str) -> str:
    return f"""# W5 · Detail (OPT-IN) — labels for `{key}`

{_model_neutral_labeling_spec_notice()}

W5 is off by default; the driver invokes this playbook only when
`--with-detail` is set. The export gate passes without it.

Goal: per scene, label what's visible:
- Grundriss: walls + openings (doors, windows, garage_doors).
- Ansicht: view_openings (windows, doors), height_marks at floor
  divisions, component_lines at roof edges (first/traufe/dachschraege).
- Schnitt: component_lines at floor slabs + roof edges, height_marks.

## Per-scene budget

20 tool calls. The agent stops on budget exhaustion and moves on; the
SKILL never blocks on perfect W5.

## Steps per scene

For each scene:

1. `get_scene_view(key="{key}", file=<scene>, tiers="broad,finer")`.
2. Enumerate visible features the scene_tag supports (see
   `bim-db://ontology/scene_tags` for the tool palette).
3. For each: zoom (`region=`), draw with `upsert_label`, mark
   `status="uncertain"` if you can't read the type / dimension
   confidently.
4. Run `recompute_homography` periodically (every ~5 labels) so the
   per-scene calibration stays valid.

## Exit

`set_house_facts(patch={{"workflow": {{"phase_completed_at":
                                       {{"detail": "<ISO timestamp>"}}}}}})`
to mark W5 manually complete.
"""


@mcp.prompt(name="diagnose-failed-export")
def prompt_diagnose_failed_export(key: str) -> str:
    return f"""# Diagnose why `{key}` won't export

{_model_neutral_labeling_spec_notice()}

The agent exited W4 but `export_house` returned 409. Find what's blocking.

## Steps

1. `validate_export_readiness(key="{key}")` — read the blockers list.
2. Common causes + fixes:
   - **"no annotated scenes"** → no scene has labels. Re-run W4 (it
     adds dim labels) or run a W5 pass.
   - **"house has zero drawings"** → re-run extract_scenes via the W0
     bootstrap.
   - **homography degenerate on Scene X** → call `recompute_homography`
     on that scene; the error tells you which ref dims are degenerate.
     Delete + retry.
3. After fixing, re-call `export_house(key="{key}")` — should return 201
   with the export manifest.

## When to give up

If the same blocker survives 2 fix attempts: `dump_run_summary` with
notes, exit non-zero so the driver records the failure for human
review.
"""


@mcp.prompt(name="diagnose-degenerate-homography")
def prompt_diagnose_degenerate_homography(key: str, file: str) -> str:
    return f"""# Diagnose degenerate homography on `{key}` / `{file}`

{_model_neutral_labeling_spec_notice()}

`recompute_homography` returned `status="degenerate"` or
`rms_residual_px > 10`. Recover.

## Steps

1. `list_scene_labels(key="{key}", file="{file}")` — find every
   `dimensioned_distance` with `is_reference=true`.
2. For each, `get_label(label_id=<id>)` and check:
   - Is `target_orientation` set ("horizontal" / "vertical")? If not,
     the rectifier can't tell which axis it anchors. `update_label_attrs`
     to set it.
   - Are the start/end endpoints actually horizontal / vertical in the
     image? Compute angle; if > 10° off-axis, the dim is mis-drawn.
     Delete it.
3. If only one ref dim survives: add a new one in the missing
   orientation per the W4 playbook.
4. `recompute_homography(key="{key}", file="{file}")` — confirm
   `status="ok"` with `rms_residual_px ≤ 8`.

## When the drawing truly has no orthogonal dim pair

(e.g. a perspective sketch or a detail with only one dim line)
`set_label_status(label_id=<best dim>, status="uncertain")` and
`dump_run_summary` flagging the scene for human review.
"""


# ── entry point ──────────────────────────────────────────────────────────

_TOOL_PROFILES: dict[str, set[str]] = {
    "inventory": {
        "list_houses", "get_house", "get_house_context_summary",
        "list_pdfs", "get_pdf_info", "get_pdf_page_view", "extract_scenes",
        "split_scene", "get_scene_view", "get_scene_meta",
        "set_scene_tag", "set_scene_orientation", "set_scene_level",
        "create_scene_plan_state_from_template", "get_scene_plan_status",
        "get_workflow_state", "get_recommended_next_action", "write_handoff_summary",
    },
    "floorplan": {
        "get_house_context_summary", "get_scene_context_summary",
        "create_scene_plan_state_from_template", "get_scene_plan_state",
        "get_scene_plan_status", "get_scene_plan_next_action",
        "get_scene_plan_next_actions", "start_scene_plan_action",
        "record_scene_plan_attempt", "finish_scene_plan_action",
        "add_scene_plan_evidence", "set_scene_plan_task_state",
        "evaluate_scene_plan_gates",
        "get_scene_view", "get_scene_view_with_labels", "verify_label_placement",
        "resolve_scene_point", "list_scene_labels", "get_label", "upsert_label",
        "delete_label", "update_label_attrs", "set_label_status",
        "add_reference_dim", "dimension_chain_candidates", "dimension_chain_context",
        "dimension_station_graph", "opening_candidates",
        "get_scene_view_with_opening_candidate",
        "apply_opening_candidate", "decide_opening_candidate",
        "score_walls", "score_measurements", "wall_topology_qa",
        "wall_continuity_check", "ambiguous_line_context", "propose_wall_edit",
        "get_scene_repair_candidates", "get_scene_view_with_repair_candidate",
        "apply_repair_candidate", "decide_repair_candidate",
        "get_scene_plan_quality_report", "get_scene_topology_snapshot",
        "detect_wall_corners", "check_corner", "refine_wall", "connect_corners",
        "get_workflow_state", "write_handoff_summary",
    },
    "elevation": {
        "get_house_context_summary", "get_scene_context_summary",
        "get_building_global_facts", "set_building_global_fact",
        "create_scene_plan_state_from_template", "get_scene_plan_state",
        "get_scene_plan_status", "get_scene_plan_next_action",
        "get_scene_plan_next_actions", "start_scene_plan_action",
        "record_scene_plan_attempt", "finish_scene_plan_action",
        "set_scene_plan_task_state",
        "get_scene_view", "get_scene_view_with_labels", "verify_label_placement",
        "view_geometry_candidates",
        "resolve_scene_point", "list_scene_labels", "get_label", "upsert_label",
        "delete_label", "update_label_attrs", "set_label_status",
        "add_reference_dim", "recompute_homography",
        "add_scene_plan_evidence", "evaluate_scene_plan_gates",
        "get_workflow_state", "write_handoff_summary",
    },
    "review": {
        "get_house_context_summary", "get_scene_context_summary",
        "get_workflow_state", "validate_export_readiness", "list_anomalies",
        "get_scene_plan_status", "get_scene_plan_next_action",
        "set_scene_plan_task_state",
        "get_scene_view_with_labels", "verify_label_placement",
        "score_walls", "score_measurements", "wall_topology_qa",
        "get_scene_repair_candidates", "get_scene_view_with_repair_candidate",
        "apply_repair_candidate", "decide_repair_candidate",
        "get_scene_plan_quality_report", "get_scene_topology_snapshot",
        "export_house", "dump_run_summary", "write_handoff_summary",
    },
}


def _apply_tool_profile(profile: str | None = None) -> list[str]:
    """Remove tools outside BIM_MCP_TOOL_PROFILE.

    Default profile is `all` for compatibility. Operators can launch a
    narrower worker, e.g. BIM_MCP_TOOL_PROFILE=floorplan, to reduce schema
    context while keeping debug/all access available.
    """
    selected = (profile or os.environ.get("BIM_MCP_TOOL_PROFILE") or "all").lower()
    if selected in {"", "all", "*"}:
        return []
    allowed = _TOOL_PROFILES.get(selected)
    if allowed is None:
        log.warning("unknown BIM_MCP_TOOL_PROFILE=%s; keeping all tools", selected)
        return []
    tools = getattr(mcp._tool_manager, "_tools", {})
    removed: list[str] = []
    for name in list(tools):
        if name not in allowed:
            mcp.remove_tool(name)
            removed.append(name)
    log.info("applied tool profile %s: removed %s tools", selected, len(removed))
    return removed


def main() -> None:
    _apply_tool_profile()
    log.info("running mcp.run(stdio)")
    try:
        mcp.run(transport="stdio")
    finally:
        if _http is not None:
            asyncio.get_event_loop().run_until_complete(_http.aclose())


if __name__ == "__main__":
    main()
