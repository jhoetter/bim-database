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
    state = _derive_workflow_state(body or {}, facts or {})
    next_tool = None
    if not state.get("exportable") and state.get("next_phase"):
        next_tool = {
            "name": "get_recommended_next_action",
            "args": {"key": key},
            "reason": f"phase {state['next_phase']} is the next to advance",
        }
    return _ok(state, next_tool=next_tool, started_at=started, status_code=status)


def _derive_workflow_state(dataset: dict, facts: dict) -> dict:
    """Server-side approximation of ui/src/lib/workflow.ts predicates.

    Keep deliberately conservative: when in doubt, return `pending` and
    let the skill's actual labeling behavior drive the SPA to fill in
    the gaps. The status flips only on clear, observable conditions.
    """
    drawings = dataset.get("drawings") or []
    scenes_by_file = {d.get("file"): d for d in drawings}

    # W0: every scene categorized + Ansicht/Schnitt have orientation,
    # Grundriss have level.
    w0_blockers: list[str] = []
    if not drawings:
        w0_blockers.append("no scenes extracted yet")
    for d in drawings:
        tag = d.get("kind") or d.get("scene_tag")
        if tag in (None, "nicht_klassifiziert"):
            w0_blockers.append(f"{d.get('file')}: untagged")
            continue
        if tag in ("ansicht", "schnitt") and not d.get("view"):
            w0_blockers.append(f"{d.get('file')}: missing orientation")
        if tag == "grundriss" and not d.get("floor"):
            w0_blockers.append(f"{d.get('file')}: missing level")
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

    # W4: every non-detail Ansicht/Schnitt has facts.calibration_per_scene[file]
    cps = facts.get("calibration_per_scene") or {}
    w4_blockers: list[str] = []
    for d in drawings:
        tag = d.get("kind") or d.get("scene_tag")
        if tag in ("ansicht", "schnitt") and d.get("kind") != "detail":
            if d.get("file") not in cps:
                w4_blockers.append(f"{d.get('file')}: not calibrated")
    w4_status = "done" if not w4_blockers and any(t in ("ansicht", "schnitt") for t in (d.get("kind") for d in drawings)) else "pending"

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
