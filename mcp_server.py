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

# Launched as `python mcp_server.py`, so __name__ == "__main__". The per-domain
# tool modules do `from mcp_server import ...`, which would re-execute this
# whole file as a *second* module object ("mcp_server") and double-register
# every tool. Re-exec ourselves as the importable `mcp_server` module so there
# is exactly one module object, then run it. (No-op under normal `import`.)
if __name__ == "__main__":  # pragma: no cover
    import mcp_server

    mcp_server.main()
    raise SystemExit(0)

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

import mcp_prompts
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


# ── §5.2 Intake ──────────────────────────────────────────────────────────


# ── §5.3 Scene inspection (cont.) ────────────────────────────────────────


# ── §5.3b Scene plan workflow ───────────────────────────────────────────


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


# ── §5.4 Tagging ──────────────────────────────────────────────────────────

_VALID_TAGS = {"grundriss", "ansicht", "schnitt", "sonstiges", "nicht_klassifiziert"}
_VALID_ORIENTATIONS = {"north", "south", "east", "west", None}
_VALID_LEVELS = {"kg", "ug", "eg", "og", "dg", "spitzboden", None}


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


_VALID_LABEL_STATUS = {"readable", "not_readable", "missing", "uncertain"}


# ── §5.6 Reference / homography ──────────────────────────────────────────


# ── §5.7 Facts ────────────────────────────────────────────────────────────


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ── §5.7b Building-global facts (issue #8) ───────────────────────────────


# ── §5.8 Export ──────────────────────────────────────────────────────────


# ── §5.9 Audit ───────────────────────────────────────────────────────────


# Tool domains extracted to mcp_tools_*.py (H5); importing registers them
# on `mcp` and re-exports the tool callables for the test harness.
from mcp_tools_audit import *  # noqa: E402,F401,F403
from mcp_tools_discovery import *  # noqa: E402,F401,F403
from mcp_tools_intake import *  # noqa: E402,F401,F403
from mcp_tools_scene import *  # noqa: E402,F401,F403
from mcp_tools_plan import *  # noqa: E402,F401,F403
from mcp_tools_geometry import *  # noqa: E402,F401,F403
from mcp_tools_labels import *  # noqa: E402,F401,F403
from mcp_tools_reference import *  # noqa: E402,F401,F403
from mcp_tools_facts import *  # noqa: E402,F401,F403
from mcp_tools_export import *  # noqa: E402,F401,F403


# ── §5.10/§5.11 resources + prompts (externalized to prompts/*.md) ──────
mcp_prompts.register(mcp, server_version=SERVER_VERSION, api_base=API_BASE)


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
