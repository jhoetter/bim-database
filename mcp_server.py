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

# Server identity — version is read by the skill at startup to verify
# compatibility (tracker §6.3). Bump MAJOR on any tool signature break.
SERVER_VERSION = "0.1.0"

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
    facts_status, facts = await _api_get(f"/datasets/{key}/house_facts")
    facts = facts if facts_status == 200 else {}
    # Load each scene's labels JSON so we see the workflow-vocabulary
    # scene_tag / scene_orientation / scene_level (which live there, not
    # on the manifest's `kind` / `view` / `floor` extraction fields).
    scene_meta_by_file: dict[str, dict] = {}
    for d in (body.get("drawings") or []):
        f = d.get("file")
        if not f:
            continue
        lbl_status, lbl = await _api_get(f"/labels/dataset/{key}/{f}")
        if lbl_status == 200 and isinstance(lbl, dict):
            scene_meta_by_file[f] = {
                "scene_tag": lbl.get("scene_tag"),
                "scene_orientation": lbl.get("scene_orientation"),
                "scene_level": lbl.get("scene_level"),
            }
        else:
            scene_meta_by_file[f] = {"scene_tag": None}
    state = _derive_workflow_state(body or {}, facts or {}, scene_meta_by_file)
    next_tool = None
    if not state.get("exportable") and state.get("next_phase"):
        next_tool = {
            "name": "get_recommended_next_action",
            "args": {"key": key},
            "reason": f"phase {state['next_phase']} is the next to advance",
        }
    return _ok(state, next_tool=next_tool, started_at=started, status_code=status)


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
    for d in drawings:
        f = d.get("file")
        meta = scene_meta.get(f, {})
        tag = meta.get("scene_tag")
        if tag in (None, "nicht_klassifiziert"):
            w0_blockers.append(f"{f}: untagged")
            continue
        if tag in ("ansicht", "schnitt") and not meta.get("scene_orientation"):
            w0_blockers.append(f"{f}: missing orientation")
        if tag == "grundriss" and not meta.get("scene_level"):
            w0_blockers.append(f"{f}: missing level")
    w0_status = "done" if drawings and not w0_blockers else "pending"

    # W1: bezug_mm == 0 AND first_mm != None
    heights = (facts.get("heights") or {})
    w1_status = "done" if heights.get("bezug_mm") == 0 and heights.get("first_mm") not in (None, "") else "pending"

    # W2: extent.width_mm + depth_mm + wall_thickness.outer_mm
    extent = facts.get("extent") or {}
    wt = facts.get("wall_thickness") or {}
    w2_status = "done" if extent.get("width_mm") and extent.get("depth_mm") and wt.get("outer_mm") else "pending"

    # W3: orientation set (either north_edge_label_id or north_angle_deg)
    orient = facts.get("orientation") or {}
    w3_status = "done" if orient.get("north_edge_label_id") or orient.get("north_angle_deg") is not None else "pending"

    # W4: every Ansicht/Schnitt has facts.calibration_per_scene[file].
    cps = facts.get("calibration_per_scene") or {}
    w4_blockers: list[str] = []
    has_calibration_targets = False
    for d in drawings:
        f = d.get("file")
        tag = scene_meta.get(f, {}).get("scene_tag")
        if tag in ("ansicht", "schnitt"):
            has_calibration_targets = True
            if f not in cps:
                w4_blockers.append(f"{f}: not calibrated")
    w4_status = "done" if has_calibration_targets and not w4_blockers else "pending"

    # W5: manual; user_skipped or phase_completed_at.detail
    wf = (facts.get("workflow") or {})
    w5_status = "done" if (wf.get("phase_completed_at") or {}).get("detail") or (wf.get("user_skipped") or {}).get("detail") else "pending"

    phases = {
        "W0": {"status": w0_status, "blockers": w0_blockers},
        "W1": {"status": w1_status, "blockers": [] if w1_status == "done" else ["heights.bezug_mm or first_mm missing"]},
        "W2": {"status": w2_status, "blockers": [] if w2_status == "done" else ["extent or wall_thickness missing"]},
        "W3": {"status": w3_status, "blockers": [] if w3_status == "done" else ["orientation not set"]},
        "W4": {"status": w4_status, "blockers": w4_blockers},
        "W5": {"status": w5_status, "blockers": ["W5 not marked complete"] if w5_status != "done" else []},
    }
    next_phase = None
    for p in ("W0", "W1", "W2", "W3", "W4"):
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
    tiers: str = "broad,finer,detail",
    max_dim: int = 1600,
) -> list[ImageContent | TextContent]:
    """Scene image with the three-tier coordinate grid overlay.

    USE when:
      - Labeling a scene — every coordinate-setting decision should
        consult a fresh grid view first.
      - Identifying scene_tag at W0 (without region; full image).

    DON'T USE when:
      - You only need scene metadata — call `get_scene_meta`.

    Args:
      key:     house key, e.g. "house-22".
      file:    scene filename, e.g. "house-22-ansicht-sued.jpg".
      region:  optional 'x0,y0,x1,y1' (source-pixel coords) — agent zoom.
      tiers:   comma list of {broad, finer, detail}; default all three.
      max_dim: cap on the longer side of the output PNG; default 1600.

    Returns: one ImageContent (PNG, RGBA) and one TextContent with the
    image metadata (source dimensions, region applied, tier step sizes).
    Grid labels show SOURCE pixels — use them directly in `upsert_label`
    against the un-cropped scene.
    """
    started = time.time()
    params: dict[str, Any] = {"tiers": tiers, "max_dim": max_dim}
    if region:
        params["region"] = region
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
    meta_status, meta_body = await _api_get(f"/datasets/{key}")
    scene_meta = {}
    if meta_status == 200:
        for d in (meta_body or {}).get("drawings") or []:
            if d.get("file") == file:
                scene_meta = {
                    "file": file,
                    "scene_tag": d.get("kind"),
                    "view": d.get("view"),
                    "floor": d.get("floor"),
                    "labeled": d.get("labeled"),
                    "label_count": d.get("label_count"),
                }
                break
    image = ImageContent(
        type="image",
        data=base64.b64encode(content).decode("ascii"),
        mimeType=ctype or "image/png",
    )
    text = TextContent(
        type="text",
        text=json.dumps(_ok({
            "image_format": "PNG",
            "image_bytes": len(content),
            "scene_meta": scene_meta,
            "region": region,
            "tiers": tiers.split(","),
            "max_dim": max_dim,
        }, started_at=started, status_code=status), indent=2),
    )
    return [image, text]


@mcp.tool()
async def get_pdf_page_view(
    key: str,
    page: int,
    dpi: int = 144,
    region: str | None = None,
    tiers: str = "broad,finer,detail",
    max_dim: int = 1600,
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
    image = ImageContent(
        type="image",
        data=base64.b64encode(content).decode("ascii"),
        mimeType=ctype or "image/png",
    )
    text = TextContent(
        type="text",
        text=json.dumps(_ok({
            "image_format": "PNG",
            "image_bytes": len(content),
            "page": page,
            "dpi": dpi,
            "page_pdf_size": page_meta,
            "region": region,
            "tiers": tiers.split(","),
            "hint": "If you emit a bbox from this view, remember to pass the same dpi to extract_scenes so pixel→PDF conversion is correct.",
        }, started_at=started, status_code=status), indent=2),
    )
    return [image, text]


def _wrap_text(envelope: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(envelope, indent=2))]


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
    phase = state.get("next_phase") or "W0"
    suggestions = {
        "W0": ("get_house", {"key": key}, "list scenes + their current tags; then set_scene_tag for each untagged"),
        "W1": ("get_house", {"key": key}, "pick an Ansicht with visible bezug + ridge; label height_marks; set_house_facts heights"),
        "W2": ("get_house", {"key": key}, "pick EG-Grundriss; add_reference_dim horizontal + vertical; set_house_facts extent + wall_thickness"),
        "W3": ("get_house", {"key": key}, "pick EG-Grundriss; identify north wall; set_house_facts orientation"),
        "W4": ("get_house", {"key": key}, "for each uncalibrated Ansicht/Schnitt: add_reference_dim h+v, recompute_homography"),
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
          "slug_override": null       // optional slug
        }
      idempotency_key: optional driver-supplied key for crash-replay safety.

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
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
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
    homo = await recompute_homography(key=key, file=file)
    result["data"]["distance_id"] = distance_id
    result["data"]["dim_number_id"] = dim_number_id
    result["data"]["homography"] = homo.get("data") if homo.get("ok") else None
    result["data"]["homography_error"] = homo.get("error") if not homo.get("ok") else None
    return result


@mcp.tool()
async def recompute_homography(key: str, file: str) -> dict:
    """Run the rectification compute over the scene's reference dims.

    USE when:
      - After adding/removing/changing a reference dim, to confirm the
        transform converges.

    DON'T USE proactively — it's a derived value, the export pipeline
    runs it for you. Call it only when you need to verify a calibration
    landed cleanly.

    Returns: `data` = {status: 'ok'|'degenerate', rms_residual_px?,
                       matrix?, computed_from?, reason?}

    Backend lives in api/homography.py + the export preview endpoint.
    We use the per-scene export preview as the cheapest way to trigger
    a recompute; it returns the homography snapshot.
    """
    started = time.time()
    try:
        status, body = await _api_post(f"/exports/{key}/{file}/preview")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await _api_post(f"/exports/{key}/{file}/preview")
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
        }, started_at=started, status_code=status)
    return _err("homography_degenerate",
                body.get("reason") or "rectification could not produce a valid transform",
                hint="add or replace a reference dim so the horizontal + vertical pair is more orthogonal",
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
    merged = _deep_merge(current or {}, patch)
    merged.setdefault("schema_version", "1.0")
    put_status, put_body = await _api_put(f"/datasets/{key}/house_facts", merged)
    if put_status >= 400:
        return _http_status_to_error(put_status, put_body, started)
    return _ok(merged, started_at=started, status_code=put_status)


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ── §5.8 Export ──────────────────────────────────────────────────────────


@mcp.tool()
async def validate_export_readiness(key: str) -> dict:
    """Server-side sanity check: would `export_house` succeed?

    USE when:
      - Before calling export_house, to surface blockers without
        committing to the (expensive) export pipeline.

    Returns: `data` = {ready: bool, blockers: [str, …]}
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
    blockers = []
    if not drawings:
        blockers.append("house has zero drawings")
    elif not any(d.get("labeled") for d in drawings):
        blockers.append("no annotated scenes")
    return _ok({
        "ready": not blockers,
        "blockers": blockers,
        "scenes_total": len(drawings),
        "labeled_scenes": sum(1 for d in drawings if d.get("labeled")),
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
    a clean export plus any per-phase predicate failures.

    USE when:
      - Triaging a failed `export_house`: which blockers must be cleared?
      - Pre-flight before committing a labeling pass: how clean is the
        house?

    DON'T USE when:
      - The agent already knows the current phase's blockers from
        `get_workflow_state`; this tool aggregates across all phases.

    v0.1: surfaces the workflow blockers + the export gate blockers.
    Future: pulls from a dedicated /datasets/{key}/anomalies endpoint
    when that exists.
    """
    started = time.time()
    wf = await get_workflow_state(key=key)
    if not wf.get("ok"):
        return wf
    state = wf["data"]
    anomalies = []
    for phase, ph in state["phases"].items():
        for b in ph.get("blockers", []):
            anomalies.append({"phase": phase, "kind": "phase_blocker", "message": b})
    if not state.get("exportable"):
        anomalies.append({"phase": "export", "kind": "export_blocker",
                          "message": "no labeled scenes yet"})
    return _ok({"anomalies": anomalies, "count": len(anomalies)},
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
"""


# ── §5.11 MCP prompts (canonical phase playbooks) ────────────────────────
# Per tracker §8 decision 7: prompts are the single source of truth for
# how an agent works the workflow. The house-labeling skill in bim-agent
# is a thin pointer that says "for phase X, follow this prompt".


@mcp.prompt(name="label-house")
def prompt_label_house(key: str) -> str:
    return f"""# Label house `{key}` end-to-end

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
| any   | get_workflow_state, get_recommended_next_action, validate_export_readiness, export_house |

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
state = get_workflow_state(key="{key}")
while not state.exportable:
    phase = state.next_phase
    follow the prompt named  bim-database.<phase>-playbook
    state = get_workflow_state(key="{key}")
validate_export_readiness then export_house
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

Start now: call `get_workflow_state(key="{key}")` and follow the
appropriate phase playbook.
"""


@mcp.prompt(name="W0-inventory")
def prompt_w0_inventory(key: str) -> str:
    return f"""# W0 · Inventory — categorise every scene of `{key}`

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
5. **scene_orientation (per §G3-2):** if Ansicht/Schnitt with a CLEAR
   cardinal face (the elevation labeled "Süd"/"South"; a compass mark
   visible AND the wall it points to is the wall this scene shows),
   call `set_scene_orientation(...)` with the value. **If unclear,
   leave null — DO NOT GUESS.** Detail crops never have a cardinal
   orientation; leave null always.
6. If Grundriss: identify the floor level (kg/ug/eg/og/dg/spitzboden)
   from the title text or by elimination (count the floors). Call
   `set_scene_level(...)`. If genuinely unclear, leave null.

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
5. `get_house_facts(key="{key}")` — confirm `heights.bezug_mm == 0`
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

Goal: `facts.extent.width_mm`, `facts.extent.depth_mm`, and
`facts.wall_thickness.outer_mm` all set.

## Steps

1. Pick EG-Grundriss (the one with `scene_level == "eg"`).
2. `get_scene_view(key="{key}", file=<eg-grundriss>, tiers="broad,finer")`
   — find a horizontal dim along the outer edge (full façade length;
   typically the longest dim on the sheet) and a vertical one along
   the depth.
3. Read both dim values from the drawing (e.g. "12,40 m" → 12400 mm).
4. For each, call:
   ```
   add_reference_dim(key="{key}", file=<eg>, orientation="horizontal",
                     start=[x1, y1], end=[x2, y2],
                     value_mm=12400, dimension_text="12,40 m")
   ```
   The tool returns `homography.rms_residual_px`. **Reject if > 8 px**
   — delete the dim and try a more-clearly-outer edge. (Use
   `delete_label(label_id=data.distance_id)` and the partner dim_number.)
5. Once both pass: identify an outer wall on the drawing — typically
   30-40 cm thick (drawn as a thick double line). Read its thickness:
   ```
   upsert_label(key="{key}", file=<eg>, label={{
     "type": "wall",
     "geometry": {{"start": [x1,y1], "end": [x2,y2]}},
     "attributes": {{"thickness_mm": 365}}
   }})
   ```
6. `set_house_facts(key="{key}", patch={{
     "extent": {{"width_mm": 12400, "depth_mm": 9800}},
     "wall_thickness": {{"outer_mm": 365}}
   }})`.

## Exit

`get_workflow_state[...]["W2"]["status"] == "done"` AND the auto-derived
`facts.extent` matches the dim values within 2 %.
"""


@mcp.prompt(name="W3-orientation")
def prompt_w3_orientation(key: str) -> str:
    return f"""# W3 · Orientation — pick the north edge for `{key}`

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
6. Repeat for vertical.
7. Confirm `get_house_facts.calibration_per_scene` now has the file.

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


def main() -> None:
    log.info("running mcp.run(stdio)")
    try:
        mcp.run(transport="stdio")
    finally:
        if _http is not None:
            asyncio.get_event_loop().run_until_complete(_http.aclose())


if __name__ == "__main__":
    main()
