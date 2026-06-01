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
import collections
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

from mcp_envelope import (
    configure_envelope,
    err as _err,
    http_status_to_error as _http_status_to_error,
    ok as _ok,
    wrap_text as _wrap_text,
)
from mcp_context_summary import (
    compact_plan_status as _compact_plan_status,
    compact_scene_row as _compact_scene_row,
    label_summary as _label_summary,
)
from mcp_geometry_tools import register_geometry_tools
from mcp_image_delivery import IMAGE_ARTIFACT_DIR, image_response
from api.workflow_state import (
    REQUIRED_GEOMETRY as _REQUIRED_GEOMETRY,
    derive_workflow_state as _derive_workflow_state,
    missing_geometry as _missing_geometry,
)

# Server identity — version is read by the skill at startup to verify
# compatibility (tracker §6.3). Bump MAJOR on any tool signature break.
SERVER_VERSION = "0.1.1"
configure_envelope(SERVER_VERSION)

API_BASE = os.environ.get("BIM_DATABASE_API_BASE", "http://127.0.0.1:12500").rstrip("/")
HEALTH_PROBE_TIMEOUT_S = float(os.environ.get("BIM_MCP_HEALTH_TIMEOUT_S", "10"))
HEALTH_PROBE_INTERVAL_S = 2.0

LOG_PATH = Path(__file__).parent / "tmp" / "mcp-server.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bim-db-mcp")
log.info("startup: API_BASE=%s version=%s", API_BASE, SERVER_VERSION)

mcp = FastMCP("bim-database")

# Shared HTTP client — keep-alive across tool calls.
_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(30.0))
    return _http


def _image_response(
    content: bytes,
    ctype: str,
    data: dict,
    *,
    started_at: float,
    status_code: int,
    delivery: str | None = None,
    artifact_meta: dict | None = None,
) -> list[ImageContent | TextContent]:
    return image_response(
        content,
        ctype,
        data,
        started_at=started_at,
        status_code=status_code,
        server_version=SERVER_VERSION,
        delivery=delivery,
        artifact_meta=artifact_meta,
        artifact_dir=IMAGE_ARTIFACT_DIR,
    )


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


async def _cv_get(path: str, params: dict, started: float) -> dict:
    """Shared GET->envelope helper for thin API-backed tools."""
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
    """Shared POST->envelope helper for thin API-backed tools."""
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
            scene_meta_by_file[f] = {
                "scene_tag": lbl.get("scene_tag"),
                "scene_orientation": lbl.get("scene_orientation"),
                "scene_level": lbl.get("scene_level"),
                # Height labels still matter for section/elevation stages,
                # but they no longer form a standalone house-level phase.
                "has_height_mark": any(
                    isinstance(la, dict) and la.get("type") == "height_mark"
                    for la in labels
                ),
                # V5.1: the geometry label types present on this scene, so
                # the workflow gate can require real polygons (walls, roof,
                # openings) — not just facts — before a scene is "done".
                "label_types": sorted(t for t in label_types if t),
                "label_count": len(labels),
            }
        else:
            scene_meta_by_file[f] = {"scene_tag": None}
    return facts or {}, scene_meta_by_file


@mcp.tool()
async def get_workflow_state(key: str) -> dict:
    """Class-stage status derived from on-disk labels/facts.

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
                next_phase: "floorplans",
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


@mcp.tool()
async def get_house_context_summary(
    key: str,
    include_plan_status: bool = True,
    max_blockers_per_scene: int = 3,
) -> dict:
    """Compact house dashboard for agent routing.

    Prefer this over fetching the full house, every labels file, and every
    plan-state when deciding the next scene/phase. It returns bounded scene
    rows and optional plan statuses only.
    """
    started = time.time()
    try:
        status, dataset = await _api_get(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, dataset = await _api_get(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, dataset, started)

    facts, scene_meta_by_file = await _load_facts_and_scene_meta(key, dataset or {})
    workflow = _derive_workflow_state(dataset or {}, facts, scene_meta_by_file)
    scenes: list[dict] = []
    for drawing in (dataset or {}).get("drawings") or []:
        file = drawing.get("file")
        if not file:
            continue
        row = _compact_scene_row(drawing, scene_meta_by_file.get(file) or {})
        if include_plan_status:
            plan_status, plan_body = await _api_get(f"/datasets/{key}/{file}/plan-state/status")
            if plan_status == 200 and isinstance(plan_body, dict):
                plan_data = plan_body.get("data") or plan_body
                row["plan"] = _compact_plan_status(plan_data, max_blockers=max_blockers_per_scene)
            else:
                row["plan"] = {"exists": False, "status": "missing"}
        scenes.append(row)
    return _ok({
        "key": key,
        "workflow": {
            "next_phase": workflow.get("next_phase"),
            "exportable": workflow.get("exportable"),
            "blockers": workflow.get("blockers") or [],
            "phases": {
                name: {
                    "status": phase.get("status"),
                    "blocker_count": len(phase.get("blockers") or []),
                }
                for name, phase in (workflow.get("phases") or {}).items()
            },
        },
        "scene_count": len(scenes),
        "scenes": scenes,
        "summary_contract": "mcp-context-bloat/house-context-summary-v1",
    }, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_context_summary(
    key: str,
    file: str,
    include_plan_status: bool = True,
    include_label_summaries: bool = True,
    max_labels: int = 40,
) -> dict:
    """Compact scene state for routing and resume.

    Prefer this before visual work. It returns scene metadata, label counts,
    optional bounded label summaries, and optional compact plan status.
    """
    started = time.time()
    try:
        ds_status, dataset = await _api_get(f"/datasets/{key}")
        lbl_status, labels_doc = await _api_get(f"/labels/dataset/{key}/{file}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        ds_status, dataset = await _api_get(f"/datasets/{key}")
        lbl_status, labels_doc = await _api_get(f"/labels/dataset/{key}/{file}")
    if ds_status >= 400:
        return _http_status_to_error(ds_status, dataset, started)
    if lbl_status >= 400:
        return _http_status_to_error(lbl_status, labels_doc, started)

    drawing = next((d for d in (dataset or {}).get("drawings") or [] if d.get("file") == file), {})
    labels = (labels_doc or {}).get("labels") or []
    label_counts = collections.Counter(l.get("type") for l in labels if isinstance(l, dict))
    uncertain = [
        l.get("id") for l in labels
        if isinstance(l, dict) and l.get("status") in {"uncertain", "missing", "not_readable"}
    ]
    data: dict[str, Any] = {
        "key": key,
        "file": file,
        "scene": _compact_scene_row(drawing, {
            "scene_tag": labels_doc.get("scene_tag"),
            "scene_orientation": labels_doc.get("scene_orientation"),
            "scene_level": labels_doc.get("scene_level"),
            "label_count": len(labels),
            "label_types": sorted(k for k in label_counts if k),
        }),
        "label_counts": dict(label_counts),
        "uncertain_label_ids": uncertain[:20],
        "uncertain_label_count": len(uncertain),
        "summary_contract": "mcp-context-bloat/scene-context-summary-v1",
    }
    if include_label_summaries:
        data["labels"] = [
            {
                "id": lab.get("id"),
                "type": lab.get("type"),
                "status": lab.get("status"),
                "summary": _label_summary(lab),
            }
            for lab in labels[:max(0, max_labels)]
            if isinstance(lab, dict)
        ]
        data["labels_truncated"] = len(labels) > max_labels
    if include_plan_status:
        plan_status, plan_body = await _api_get(f"/datasets/{key}/{file}/plan-state/status")
        if plan_status == 200 and isinstance(plan_body, dict):
            data["plan"] = _compact_plan_status(plan_body.get("data") or plan_body)
        else:
            data["plan"] = {"exists": False, "status": "missing"}
    return _ok(data, started_at=started, status_code=lbl_status)


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
    image_delivery: str | None = None,
) -> list[ImageContent | TextContent]:
    """Scene image with the three-tier coordinate grid overlay.

    USE when:
      - Labeling a scene — every coordinate-setting decision should
        consult a fresh grid view first.
      - Identifying scene_tag during inventory (without region; full image).
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
    data = {
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
    }
    return _image_response(
        content, ctype, data,
        started_at=started,
        status_code=status,
        delivery=image_delivery,
        artifact_meta={"tool": "get_scene_view", "key": key, "file": file, "region": region, "params": params},
    )


@mcp.tool()
async def get_scene_view_with_labels(
    key: str,
    file: str,
    region: str | None = None,
    tiers: str = "broad",
    max_dim: int = 1600,
    enhance: str | None = None,
    format: str = "png8",
    style: str = "standard",
    target: str | None = None,
    target_line: str = "none",
    background_opacity: float | None = None,
    clean: bool = True,
    contrast: str = "high",
    show_relations: str = "required",
    show_height_guides: str = "auto",
    include_hidden: bool = False,
    image_delivery: str | None = None,
) -> list[ImageContent | TextContent]:
    """Scene image + grid overlay + EVERY LABEL CURRENTLY SAVED rendered
    on top. This is the agent's verify view — call it after every
    geometry-bearing label write to confirm the label landed on the
    intended feature.

    USE when:
      - You just called `upsert_label`, `add_reference_dim`, or
        `update_label_attrs`. Always fetch this view immediately after
        and visually verify the label sits on the feature you meant
        (the wall, the dim line, the height mark line).
      - You're suspicious of an earlier label and want to spot-check
        without opening the SPA in a browser.

    DON'T USE when:
      - You haven't placed any labels yet — use `get_scene_view` for
        a clean image.

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
      style:   standard|coordinate_multicolor|coordinate_audit|coordinate_pair.
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
      include_hidden:
               When false, respect display.hidden_label_ids like the UI.

    Returns: one ImageContent (PNG) + one TextContent envelope.

    Render vocabulary:
      wall body band + axis    — wall (thickness_mm is rendered)
      opening body + internals — opening (door swing/window sash when known)
      polyline/region          — component_line
      datum marker + line      — height_mark (Bezug is visually distinct)
      dimension + caps + value — dimensioned_distance
      text chip / bbox         — dimension_number
      warning chips/rings      — uncertain/missing/not_readable

    Per the H5 verify loop (followups-2-tracker), the agent should
    inspect this image after EVERY geometry write. If the rendered
    geometry doesn't land on the intended feature, `update_label_attrs`
    or `delete_label` + re-place. Budget 3 attempts per label; flag
    `status: uncertain` on the closest if it still misses.
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
    data = {
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
        "include_hidden": include_hidden,
        "render_contract_version": "labeling-render-contract/2026-05-31",
        "hint": (
            "Verify the rendered geometry lands on the intended feature. "
            "If a label is off, update_label_attrs (preferred for small "
            "shifts) or delete_label + re-place. Budget 3 attempts per "
            "label, then set status='uncertain' on the closest miss."
        ),
    }
    return _image_response(
        content, ctype, data,
        started_at=started,
        status_code=status,
        delivery=image_delivery,
        artifact_meta={"tool": "get_scene_view_with_labels", "key": key, "file": file, "region": region, "params": params},
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
    include_hidden: bool = False,
    image_delivery: str | None = None,
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
      max_dim:   max output dim; per H4 small crops stay 1:1.
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
        background_opacity=background_opacity,
        clean=True,
        contrast=contrast,
        show_relations=show_relations,
        show_height_guides=show_height_guides,
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
    image_delivery: str | None = None,
) -> list[ImageContent | TextContent]:
    """PDF page render with grid overlay — used for scene identification.

    USE when:
      - Identifying scenes at inventory / extract-time: render each page,
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
    data = {
        "image_format": "PNG",
        "page": page,
        "dpi": dpi,
        "page_pdf_size": page_meta,
        "region": region,
        "tiers": tiers.split(","),
        "hint": "If you emit a bbox from this view, remember to pass the same dpi to extract_scenes so pixel→PDF conversion is correct.",
    }
    return _image_response(
        content, ctype, data,
        started_at=started,
        status_code=status,
        delivery=image_delivery,
        artifact_meta={"tool": "get_pdf_page_view", "key": key, "page": page, "region": region, "params": params},
    )


# ── §5.1 Discovery (cont.) ────────────────────────────────────────────────


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
    phase = state.get("next_phase") or "inventory"

    async def first_incomplete_scene(tag: str) -> str | None:
        ds_status, ds_body = await _api_get(f"/datasets/{key}")
        if ds_status >= 400:
            return None
        _facts, meta_by_file = await _load_facts_and_scene_meta(key, ds_body or {})
        for d in (ds_body or {}).get("drawings") or []:
            f = d.get("file")
            meta = meta_by_file.get(f, {})
            if meta.get("scene_tag") != tag:
                continue
            if _missing_geometry(tag, meta.get("label_types")):
                return f
        return None

    if phase == "inventory":
        tool_name, tool_args, reason = (
            "get_house",
            {"key": key},
            "extract/split as needed, then classify every scene; set levels on grundriss scenes",
        )
    elif phase == "floorplans":
        file = await first_incomplete_scene("grundriss")
        tool_name, tool_args, reason = (
            "get_scene_plan_next_action" if file else "get_house",
            {"key": key, "file": file} if file else {"key": key},
            "label the next incomplete Grundriss first: silhouette/walls, openings, measurements",
        )
    elif phase == "sections":
        file = await first_incomplete_scene("schnitt")
        tool_name, tool_args, reason = (
            "get_scene_plan_next_action" if file else "get_house",
            {"key": key, "file": file} if file else {"key": key},
            "label Schnitt scenes after floorplans: heights, datum, structure, reference dims",
        )
    elif phase == "elevations":
        file = await first_incomplete_scene("ansicht")
        tool_name, tool_args, reason = (
            "get_scene_plan_next_action" if file else "get_house",
            {"key": key, "file": file} if file else {"key": key},
            "label Ansicht scenes after sections: facade openings, component lines, reference dims",
        )
    else:
        tool_name, tool_args, reason = (
            "get_workflow_state",
            {"key": key},
            "review is opt-in; inspect anomalies and export readiness",
        )
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
        renders (inventory/extract stage).
      - Re-extracting after adjusting a bbox (idempotent on (page, slug);
        re-extract overwrites the JPG and updates the manifest entry but
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
          "dpi": 144,                 // the DPI of the get_pdf_page_view render
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
    so this tool multiplies by (72 / dpi) before posting.
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
        dpi = int(raw.get("dpi", 144))
        if dpi <= 0:
            return _err("schema_invalid", "dpi must be > 0", started_at=started)
        x0, y0, x1, y1 = (float(v) for v in bbox_px)
        if not (x1 > x0 and y1 > y0):
            return _err("bbox_zero_area", f"bbox has non-positive area: {bbox_px}",
                        started_at=started)
        factor = 72.0 / dpi if "bbox_pixels" in raw else 1.0
        api_items.append({
            "page": int(raw.get("page", 0)),
            "bbox_pdf_units": [x0 * factor, y0 * factor, x1 * factor, y1 * factor],
            "kind": raw.get("kind", "detail"),
            "view": raw.get("view"),
            "floor": raw.get("floor"),
            "title": raw.get("title"),
            "slug_override": raw.get("slug_override"),
            "dpi": int(raw.get("crop_dpi", 300)),
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
                   "reason": "see what inventory needs next now that scenes exist",
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
async def get_scene_plan(key: str, file: str) -> dict:
    """Read the per-scene Markdown plan.

    USE when:
      - Starting or resuming a scene subagent.
      - Checking whether analysis/edit/verification tasks already exist.

    Returns: `data` = {exists, markdown, version, status, path, last_updated}.
    """
    started = time.time()
    return await _cv_get(f"/datasets/{key}/{file}/plan", {}, started)


@mcp.tool()
async def create_scene_plan_from_template(
    key: str,
    file: str,
    scene_tag: str = "nicht_klassifiziert",
    level_or_orientation: str | None = None,
    created_by: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Create the standard plan.md for one scene.

    USE at the start of every scene subagent BEFORE geometry labels. The plan
    records analysis, editing tasks, verification results, and ambiguity.
    """
    started = time.time()
    body = {
        "scene_tag": scene_tag,
        "level_or_orientation": level_or_orientation,
        "created_by": created_by,
        "overwrite": overwrite,
    }
    return await _cv_post(f"/datasets/{key}/{file}/plan/template", body, started)


@mcp.tool()
async def update_scene_plan(
    key: str,
    file: str,
    markdown: str,
    expected_version: str | None = None,
    create_only: bool = False,
) -> dict:
    """Create or replace the scene plan Markdown.

    Use `expected_version` from `get_scene_plan` to avoid clobbering another
    worker's update. With `create_only=true`, an existing plan is rejected.
    """
    started = time.time()
    body = {
        "markdown": markdown,
        "expected_version": expected_version,
        "create_only": create_only,
    }
    try:
        status, resp = await _api_put(f"/datasets/{key}/{file}/plan", body)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, resp = await _api_put(f"/datasets/{key}/{file}/plan", body)
    if status >= 400:
        return _http_status_to_error(status, resp, started)
    return _ok(resp.get("data", resp), started_at=started, status_code=status)


@mcp.tool()
async def append_scene_plan_log(
    key: str,
    file: str,
    mode: str,
    evidence: str,
    decision: str,
    result: str,
    expected_version: str | None = None,
) -> dict:
    """Append a row to the scene plan Decision Log.

    `mode` should be analysis, editing, or verification. Use this after every
    meaningful reasoning/edit/QA step so the plan remains reviewable.
    """
    started = time.time()
    body = {
        "mode": mode,
        "evidence": evidence,
        "decision": decision,
        "result": result,
        "expected_version": expected_version,
    }
    return await _cv_post(f"/datasets/{key}/{file}/plan/log", body, started)


@mcp.tool()
async def set_scene_plan_task(
    key: str,
    file: str,
    task_id: str,
    status: str,
    note: str | None = None,
    expected_version: str | None = None,
) -> dict:
    """Update one checkbox-style task in the scene plan.

    status: pending|in_progress|done|blocked. Task IDs are the leading tokens
    in the standard template, e.g. A2, E2, V2.
    """
    started = time.time()
    body = {"status": status, "note": note, "expected_version": expected_version}
    try:
        err = await _api_patch(f"/datasets/{key}/{file}/plan/tasks/{task_id}", body, started)
        if err:
            return err
        status_code, resp = await _api_get(f"/datasets/{key}/{file}/plan")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        err = await _api_patch(f"/datasets/{key}/{file}/plan/tasks/{task_id}", body, started)
        if err:
            return err
        status_code, resp = await _api_get(f"/datasets/{key}/{file}/plan")
    if status_code >= 400:
        return _http_status_to_error(status_code, resp, started)
    return _ok(resp.get("data", resp), started_at=started, status_code=status_code)


@mcp.tool()
async def get_scene_plan_state(key: str, file: str) -> dict:
    """Read the structured per-scene plan state sidecar.

    USE when starting or resuming a scene subagent. Returns `data` with
    `{exists, state, version, markdown, path, markdown_path}`.
    """
    started = time.time()
    return await _cv_get(f"/datasets/{key}/{file}/plan-state", {}, started)


@mcp.tool()
async def get_scene_plan_status(key: str, file: str) -> dict:
    """Return concise terminality/progress status for a scene plan.

    USE before spawning/resuming a scene subagent. Distinguishes actionable
    `needs_repair` from terminal `blocked_external`.
    """
    started = time.time()
    return await _cv_get(f"/datasets/{key}/{file}/plan-state/status", {}, started)


@mcp.tool()
async def create_scene_plan_state_from_template(
    key: str,
    file: str,
    scene_tag: str = "nicht_klassifiziert",
    level_or_orientation: str | None = None,
    created_by: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Create authoritative plan-state JSON plus rendered Markdown.

    USE at the start of every scene subagent BEFORE geometry labels.
    """
    started = time.time()
    return await _cv_post(
        f"/datasets/{key}/{file}/plan-state/template",
        {
            "scene_tag": scene_tag,
            "level_or_orientation": level_or_orientation,
            "created_by": created_by,
            "overwrite": overwrite,
        },
        started,
    )


@mcp.tool()
async def add_scene_plan_evidence(
    key: str,
    file: str,
    kind: str,
    mode: str,
    summary: str,
    tool: str | None = None,
    params: dict | None = None,
    result: dict | None = None,
    observation_id: str | None = None,
    image_url: str | None = None,
    task_ids: list[str] | None = None,
    expected_version: str | None = None,
) -> dict:
    """Add evidence to the structured scene plan.

    Evidence is required for verified/rejected/accepted_incomplete task states.
    """
    started = time.time()
    return await _cv_post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        {
            "kind": kind,
            "mode": mode,
            "summary": summary,
            "tool": tool,
            "params": params or {},
            "result": result or {},
            "observation_id": observation_id,
            "image_url": image_url,
            "task_ids": task_ids or [],
            "expected_version": expected_version,
        },
        started,
    )


@mcp.tool()
async def upsert_scene_plan_defect(
    key: str,
    file: str,
    title: str,
    severity: str,
    category: str,
    description: str,
    expected_resolution: str,
    defect_id: str | None = None,
    status: str = "open",
    region: list | None = None,
    evidence_ids: list[str] | None = None,
    expected_version: str | None = None,
) -> dict:
    """Create or update a first-class plan defect."""
    started = time.time()
    return await _cv_post(
        f"/datasets/{key}/{file}/plan-state/defects",
        {
            "id": defect_id,
            "title": title,
            "status": status,
            "severity": severity,
            "category": category,
            "region": region,
            "description": description,
            "expected_resolution": expected_resolution,
            "evidence_ids": evidence_ids or [],
            "expected_version": expected_version,
        },
        started,
    )


@mcp.tool()
async def update_scene_plan_defect(
    key: str,
    file: str,
    defect_id: str,
    status: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    description: str | None = None,
    expected_resolution: str | None = None,
    region: list | None = None,
    evidence_ids: list[str] | None = None,
    expected_version: str | None = None,
) -> dict:
    """Patch one structured scene-plan defect.

    Valid statuses: open|in_progress|fixed|rejected|accepted_uncertain.
    """
    started = time.time()
    body = {"expected_version": expected_version}
    for k, v in {
        "status": status,
        "severity": severity,
        "category": category,
        "description": description,
        "expected_resolution": expected_resolution,
        "region": region,
        "evidence_ids": evidence_ids,
    }.items():
        if v is not None:
            body[k] = v
    try:
        err = await _api_patch(f"/datasets/{key}/{file}/plan-state/defects/{defect_id}", body, started)
        if err:
            return err
        status_code, resp = await _api_get(f"/datasets/{key}/{file}/plan-state")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        err = await _api_patch(f"/datasets/{key}/{file}/plan-state/defects/{defect_id}", body, started)
        if err:
            return err
        status_code, resp = await _api_get(f"/datasets/{key}/{file}/plan-state")
    if status_code >= 400:
        return _http_status_to_error(status_code, resp, started)
    return _ok(resp.get("data", resp), started_at=started, status_code=status_code)


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
    """Set one structured task state.

    Valid statuses: todo|in_progress|blocked|rejected|verified|
    accepted_incomplete. Prefer evaluate_scene_plan_gates for verification.
    """
    started = time.time()
    body = {
        "status": status,
        "evidence_ids": evidence_ids,
        "blocked_by": blocked_by,
        "gate_updates": gate_updates,
        "note": note,
        "expected_version": expected_version,
    }
    try:
        err = await _api_patch(f"/datasets/{key}/{file}/plan-state/tasks/{task_id}", body, started)
        if err:
            return err
        status_code, resp = await _api_get(f"/datasets/{key}/{file}/plan-state")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        err = await _api_patch(f"/datasets/{key}/{file}/plan-state/tasks/{task_id}", body, started)
        if err:
            return err
        status_code, resp = await _api_get(f"/datasets/{key}/{file}/plan-state")
    if status_code >= 400:
        return _http_status_to_error(status_code, resp, started)
    return _ok(resp.get("data", resp), started_at=started, status_code=status_code)


@mcp.tool()
async def evaluate_scene_plan_gates(
    key: str,
    file: str,
    run_score_walls: bool = True,
    run_score_measurements: bool = True,
    run_topology_qa: bool = True,
    run_continuity_check: bool = True,
    visual_evidence: bool = False,
    expected_version: str | None = None,
) -> dict:
    """Evaluate deterministic plan gates and update defects/tasks/status."""
    started = time.time()
    return await _cv_post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        {
            "run_score_walls": run_score_walls,
            "run_score_measurements": run_score_measurements,
            "run_topology_qa": run_topology_qa,
            "run_continuity_check": run_continuity_check,
            "visual_evidence": visual_evidence,
            "expected_version": expected_version,
        },
        started,
    )


@mcp.tool()
async def get_scene_plan_next_actions(key: str, file: str, limit: int = 3) -> dict:
    """Return blocker-first, subagent-ready next actions for one scene."""
    started = time.time()
    return await _cv_get(f"/datasets/{key}/{file}/plan-state/next-actions", {"limit": limit}, started)


@mcp.tool()
async def get_scene_plan_next_action(key: str, file: str) -> dict:
    """Return exactly one blocker-first, subagent-ready next action."""
    started = time.time()
    return await _cv_get(f"/datasets/{key}/{file}/plan-state/next-action", {}, started)


@mcp.tool()
async def start_scene_plan_action(
    key: str,
    file: str,
    action_id: str,
    agent_id: str | None = None,
    expected_version: str | None = None,
) -> dict:
    """Claim a scene-plan action and mark its task/defect in progress."""
    started = time.time()
    return await _cv_post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/start",
        {"agent_id": agent_id, "expected_version": expected_version},
        started,
    )


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
    """Record one coherent edit/review attempt for the current action."""
    started = time.time()
    return await _cv_post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/attempts",
        {
            "id": attempt_id,
            "hypothesis": hypothesis,
            "edits": edits or [],
            "evidence_ids": evidence_ids or [],
            "expected_version": expected_version,
        },
        started,
    )


@mcp.tool()
async def finish_scene_plan_action(
    key: str,
    file: str,
    action_id: str,
    outcome: str,
    attempt_id: str | None = None,
    evidence_ids: list[str] | None = None,
    reason: str | None = None,
    expected_version: str | None = None,
) -> dict:
    """Finish one plan action after verification.

    outcome: fixed|still_open|rejected|accepted_uncertain|regressed|blocked_external.
    """
    started = time.time()
    return await _cv_post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/finish",
        {
            "outcome": outcome,
            "attempt_id": attempt_id,
            "evidence_ids": evidence_ids or [],
            "reason": reason,
            "expected_version": expected_version,
        },
        started,
    )


@mcp.tool()
async def reopen_scene_plan_task(
    key: str,
    file: str,
    task_id: str,
    reason: str,
    evidence_ids: list[str] | None = None,
    invalidate_dependents: bool = True,
    expected_version: str | None = None,
) -> dict:
    """Backtrack: reopen a task and optionally invalidate dependent tasks."""
    started = time.time()
    return await _cv_post(
        f"/datasets/{key}/{file}/plan-state/tasks/{task_id}/reopen",
        {
            "reason": reason,
            "evidence_ids": evidence_ids or [],
            "invalidate_dependents": invalidate_dependents,
            "expected_version": expected_version,
        },
        started,
    )


@mcp.tool()
async def classify_plan_defect(
    key: str,
    file: str,
    defect_id: str,
    classification: str,
    evidence_ids: list[str] | None = None,
    note: str | None = None,
    expected_version: str | None = None,
) -> dict:
    """Classify an ambiguous/auto-generated defect before closure.

    Use for wall score missing/off-ink defects before fixed/rejected/
    accepted_uncertain. Classifications include real_missing_wall,
    bad_existing_wall, door_swing_or_hint, furniture_or_fixture,
    dimension_or_annotation, separate_structure, false_positive, ambiguous.
    """
    started = time.time()
    return await _cv_post(
        f"/datasets/{key}/{file}/plan-state/defects/{defect_id}/classify",
        {
            "classification": classification,
            "evidence_ids": evidence_ids or [],
            "note": note,
            "expected_version": expected_version,
        },
        started,
    )


@mcp.tool()
async def evaluate_scene_plan_terminality(key: str, file: str) -> dict:
    """Return deterministic terminality: verified, needs_repair, blocked_external, or accepted_incomplete."""
    started = time.time()
    return await _cv_post(f"/datasets/{key}/{file}/plan-state/evaluate-terminality", {}, started)


@mcp.tool()
async def render_scene_plan_markdown(
    key: str,
    file: str,
    sync: bool = True,
    expected_version: str | None = None,
) -> dict:
    """Render structured plan state to Markdown and optionally sync to disk."""
    started = time.time()
    return await _cv_post(
        f"/datasets/{key}/{file}/plan-state/render-markdown",
        {"sync": sync, "expected_version": expected_version},
        started,
    )


@mcp.tool()
async def list_scene_labels(key: str, file: str) -> dict:
    """Compact list of labels on one scene — id, type, status, summary.

    USE when:
      - You want to see what's already on a scene without the full
        geometry payload. Cheap; ≤ 200 bytes per label.

    DON'T USE when:
      - You need the actual coordinates — use `get_label`.

    Returns: `data.labels` = [{id, type, status, summary}]
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
    summaries = []
    for lab in (body.get("labels") or []):
        summaries.append({
            "id": lab.get("id"),
            "type": lab.get("type"),
            "status": lab.get("status"),
            "summary": _label_summary(lab),
        })
    return _ok({
        "scene_tag": body.get("scene_tag"),
        "scene_orientation": body.get("scene_orientation"),
        "scene_level": body.get("scene_level"),
        "image_size_px": body.get("image_size_px"),
        "labels": summaries,
    }, started_at=started, status_code=status)



globals().update(register_geometry_tools(mcp, {
    "api_get": _api_get,
    "api_get_bytes": _api_get_bytes,
    "api_post": _api_post,
    "wait_for_api": _wait_for_api,
    "api_unreachable_error": _api_unreachable_error,
    "http_status_to_error": _http_status_to_error,
    "ok": _ok,
    "wrap_text": _wrap_text,
    "image_response": _image_response,
}))


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
    reset_plan: bool = False,
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
      - Preserves the scene plan by default. Pass reset_plan=true only when
        simulating a totally fresh scene-planning run too.

    DON'T USE when:
      - You want to remove extracted scenes and return to PDF extraction;
        call `reset_house_dataset` instead.
    """
    started = time.time()
    path = f"/labels/dataset/{key}/{file}"
    if reset_plan:
        path += "?reset_plan=true"
    try:
        status, body = await _api_delete(path)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_delete(path)
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def reset_house_labeling(
    key: str,
    reset_plans: bool = False,
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
      - Preserves scene plans by default. Pass reset_plans=true to delete
        `data/dataset/<key>/plans/*.md` as part of a fresh plan simulation.

    DON'T USE when:
      - You need to re-extract scenes from the incoming PDF. Use
        `reset_house_dataset` for that stronger reset.
    """
    started = time.time()
    path = f"/datasets/{key}/labels"
    if reset_plans:
        path += "?reset_plans=true"
    try:
        status, body = await _api_delete(path)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_delete(path)
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
        state and start over from inventory extraction.

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
                                      Requires relations:
                                      [{kind:"belongs_to", other_id:<wall_id>}]
                                      to an existing wall; the quad centerline
                                      must sit on that wall.
               view_opening:         one of
                                       {top_edge: [[x,y],...], bottom_edge: [[x,y],...]}
                                       {circle: {center: [x,y], radius_px: N}}
                                       {polygon: [[x,y],...]}
               component_line:       {points: [[x,y],...]}
               height_mark:          {anchor: [x,y]}
               dimensioned_distance: {start: [x,y], end: [x,y]}
               dimension_number:     {anchor: [x,y]} XOR {bbox: [[x,y]*4]}
                                      Requires relations:
                                      [{kind:"labels", other_id:<dimensioned_distance_id>}]
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
    return result


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
      - section/elevation calibration: every Ansicht/Schnitt needs reference dims.
      - floorplan measurement QA: horizontal + vertical reference dims along
        outer edges of EG-Grundriss where readable.

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
      - floorplans: set derived extent/wall_thickness/orientation facts only
        after backing labels exist.
      - sections/elevations: the per-scene `calibration_per_scene[file]` is auto-populated
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
                            "or follow the section/elevation stage prompt). "
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
      - Before section/elevation stages to see which building-wide anchors exist and which
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
    `labeled` flag at inventory tagging time, a house with tags + an assumed
    orientation and ZERO geometry used to pass `ready:true` — inviting
    an honest agent to export an empty dataset. `ready` now reflects
    honest scene-class completeness instead.

    Required phases for `ready`/`honest_complete`:
      - inventory (every scene tagged; grundriss carry a level)
      - floorplans (all grundriss scenes have required floorplan labels)
      - sections (required only when schnitt scenes exist)
      - elevations (required only when ansicht scenes exist)
    review/detail is optional and never blocks.

    Returns: `data` = {
      ready: bool,                # == honest_complete
      honest_complete: bool,      # all required phases done
      minimal_export_ok: bool,    # the permissive gate export_house enforces
      blockers: [str, …],         # missing required phases + their reasons
      phase_completeness: {phase: {status, required, blockers}},
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

    has_sections = any(
        scene_meta.get(d.get("file"), {}).get("scene_tag") == "schnitt"
        for d in drawings
    )
    has_elevations = any(
        scene_meta.get(d.get("file"), {}).get("scene_tag") == "ansicht"
        for d in drawings
    )
    required = ["inventory", "floorplans"]
    if has_sections:
        required.append("sections")
    if has_elevations:
        required.append("elevations")

    phase_completeness = {
        p: {
            "status": phases[p]["status"],
            "required": p in required,
            "blockers": phases[p]["blockers"],
        }
        for p in ("inventory", "floorplans", "sections", "elevations", "review")
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

    # Issue #27: surface scenes whose calibration rests on the
    # single-ref isotropic (square-pixel) assumption — they count as
    # calibrated, but an honest export should record the assumption.
    assumed_isotropic_scenes = state.get("assumed_isotropic_scenes") or []

    plan_state_blockers: list[str] = []
    accepted_incomplete: list[dict[str, Any]] = []
    for d in drawings:
        f = d.get("file")
        if not f:
            continue
        meta = scene_meta.get(f, {})
        if meta.get("scene_tag") not in _REQUIRED_GEOMETRY:
            continue
        plan_status, plan_body = await _api_get(f"/datasets/{key}/{f}/plan-state")
        plan = (plan_body or {}).get("data") if plan_status == 200 and isinstance(plan_body, dict) else None
        if not plan or not plan.get("exists"):
            if meta.get("label_count", 0) > 0:
                plan_state_blockers.append(f"{f}: labels exist but structured scene plan is missing")
            continue
        state_doc = plan.get("state") or {}
        blockers = [
            defect for defect in state_doc.get("defects") or []
            if defect.get("status") in ("open", "in_progress") and defect.get("severity") == "blocker"
        ]
        if blockers:
            plan_state_blockers.append(
                f"{f}: {len(blockers)} blocker plan defect(s): "
                + ", ".join(str(b.get("id")) for b in blockers)
            )
        for defect in state_doc.get("defects") or []:
            if defect.get("status") == "accepted_uncertain":
                accepted_incomplete.append({
                    "file": f,
                    "defect_id": defect.get("id"),
                    "title": defect.get("title"),
                    "severity": defect.get("severity"),
                })
        for task in state_doc.get("tasks") or []:
            if task.get("status") == "accepted_incomplete":
                accepted_incomplete.append({
                    "file": f,
                    "task_id": task.get("id"),
                    "title": task.get("title"),
                    "severity": "warning",
                })

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
        "plan_state_complete": not plan_state_blockers,
        "plan_state_blockers": plan_state_blockers,
        "accepted_incomplete": accepted_incomplete,
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
            "phase": "floorplans", "kind": "assumed_orientation",
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
            scene_labels = lbl.get("labels") or []
            uncertain = sum(
                1 for lab in scene_labels
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
            # not an inventory blocker. Surface for reviewer triage.
            tag = lbl.get("scene_tag")
            if tag in ("ansicht", "schnitt") and not lbl.get("scene_orientation"):
                anomalies.append({
                    "phase": "inventory", "kind": "missing_orientation",
                    "message": f"{f}: scene_orientation not set (warning only; spot-check if facade direction matters)",
                    "severity": "warning",
                    "details": {"file": f, "scene_tag": tag},
                })
            # Scene-plan framework: structured sidecar is authoritative. The
            # legacy Markdown plan remains a compatibility surface only.
            plan_status, plan_body = await _api_get(f"/datasets/{key}/{f}/plan-state")
            plan = (plan_body or {}).get("data") if plan_status == 200 and isinstance(plan_body, dict) else None
            plan_exists = bool(plan and plan.get("exists"))
            if scene_labels and not plan_exists:
                anomalies.append({
                    "phase": "plan", "kind": "missing_scene_plan",
                    "message": f"{f}: scene has labels but no structured scene plan",
                    "severity": "warning",
                    "details": {"file": f, "label_count": len(scene_labels)},
                })
            if plan_exists:
                from api.scene_plans import plan_has_analysis_summary, task_done
                md = str(plan.get("markdown") or "")
                state = plan.get("state") or {}
                open_defects = [
                    d for d in state.get("defects") or []
                    if d.get("status") in ("open", "in_progress")
                ]
                blockers = [d for d in open_defects if d.get("severity") == "blocker"]
                if blockers:
                    anomalies.append({
                        "phase": "plan", "kind": "plan_state_blockers",
                        "message": f"{f}: {len(blockers)} blocker scene-plan defect(s) open",
                        "severity": "warning",
                        "details": {"file": f, "defect_ids": [d.get("id") for d in blockers]},
                    })
                bad_verified = [
                    t for t in state.get("tasks") or []
                    if t.get("status") == "verified"
                    and any((g or {}).get("status") in ("failed", "pending") for g in t.get("gates") or [])
                ]
                if bad_verified:
                    anomalies.append({
                        "phase": "plan", "kind": "verified_task_with_failed_gate",
                        "message": f"{f}: {len(bad_verified)} verified task(s) still have failed/pending gates",
                        "severity": "warning",
                        "details": {"file": f, "task_ids": [t.get("id") for t in bad_verified]},
                    })
                final_qa = next((t for t in state.get("tasks") or [] if t.get("id") == "FINAL_QA"), None)
                if final_qa and final_qa.get("status") == "verified" and blockers:
                    anomalies.append({
                        "phase": "plan", "kind": "final_qa_verified_with_blockers",
                        "message": f"{f}: FINAL_QA is verified while blocker defects remain open",
                        "severity": "warning",
                        "details": {"file": f, "defect_ids": [d.get("id") for d in blockers]},
                    })
                if tag == "grundriss":
                    opening_task = next((t for t in state.get("tasks") or [] if t.get("id") in ("PLACE_OPENINGS", "VERIFY_OPENINGS") and t.get("status") == "verified"), None)
                    has_openings = any(lab.get("type") == "floorplan_opening" for lab in scene_labels)
                    if opening_task and not has_openings:
                        anomalies.append({
                            "phase": "plan", "kind": "opening_task_verified_with_zero_openings",
                            "message": f"{f}: opening task is verified but the scene has zero floorplan_opening labels",
                            "severity": "warning",
                            "details": {"file": f, "task_id": opening_task.get("id")},
                        })
                if scene_labels and not plan_has_analysis_summary(md):
                    anomalies.append({
                        "phase": "plan", "kind": "plan_missing_analysis",
                        "message": f"{f}: plan exists but Analysis Summary is still blank",
                        "severity": "warning",
                        "details": {"file": f},
                    })
                has_walls = any(lab.get("type") == "wall" for lab in scene_labels)
                has_openings = any(lab.get("type") == "floorplan_opening" for lab in scene_labels)
                if has_walls and not task_done(md, "A2"):
                    anomalies.append({
                        "phase": "plan", "kind": "walls_before_silhouette_plan",
                        "message": f"{f}: wall labels exist but A2 outer silhouette analysis is not complete",
                        "severity": "warning",
                        "details": {"file": f},
                    })
                if has_openings and not (task_done(md, "V2") or task_done(md, "V3")):
                    anomalies.append({
                        "phase": "plan", "kind": "openings_before_wall_topology_qa",
                        "message": f"{f}: openings exist but wall topology verification task is not complete",
                        "severity": "warning",
                        "details": {"file": f},
                    })
                lower = md.lower()
                last_failed = max(lower.rfind("verification"), lower.rfind("verify"))
                last_analysis = lower.rfind("analysis")
                if last_failed >= 0 and "fail" in lower[last_failed:last_failed + 240] and last_analysis < last_failed:
                    anomalies.append({
                        "phase": "plan", "kind": "failed_verification_without_followup_analysis",
                        "message": f"{f}: plan records a failed verification without later analysis",
                        "severity": "warning",
                        "details": {"file": f},
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


# ── §5.10 MCP resources + prompts ─────────────────────────────────────────

from mcp_metadata import register_metadata

register_metadata(mcp, server_version=SERVER_VERSION)


# ── entry point ──────────────────────────────────────────────────────────


def main() -> None:
    log.info("running mcp.run(stdio)")
    try:
        mcp.run(transport="stdio")
    finally:
        if _http is not None:
            asyncio.get_event_loop().run_until_complete(_http.aclose())


if __name__ == "__main__":
    main()
