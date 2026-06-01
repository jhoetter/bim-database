"""Geometry and CV MCP tools."""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
from mcp.types import ImageContent, TextContent

_api_get = None
_api_get_bytes = None
_api_post = None
_wait_for_api = None
_api_unreachable_error = None
_http_status_to_error = None
_ok = None
_wrap_text = None
_image_response = None


def configure_geometry_tools(deps: dict[str, Any]) -> None:
    globals().update({f"_{name}": value for name, value in deps.items()})


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


async def outer_wall_topology_context(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 12,
    thresh: int | None = None,
) -> dict:
    """Scene-plan context gatherer for the silhouette-first pass.

    Returns wall_outline/building_silhouette priors when available plus the
    questions the vision agent must answer in the plan before wall placement.
    Empty CV priors are normal on pencil scans and are not a blocker.
    """
    started = time.time()
    params: dict = {"min_wall_px": min_wall_px}
    if region is not None:
        params["region"] = region
    if thresh is not None:
        params["thresh"] = thresh
    return await _cv_get(f"/datasets/{key}/{file}/outer-wall-topology-context", params, started)


async def wall_topology_qa(
    key: str,
    file: str,
    endpoint_tol_px: float = 18.0,
    near_miss_px: float = 60.0,
    collinear_tol_deg: float = 8.0,
    collinear_gap_px: float = 140.0,
    short_stub_px: float = 80.0,
) -> dict:
    """Whole-wall-system verification after wall placement.

    Flags dangling endpoints, near-miss corners, mergeable collinear fragments,
    suspicious short stubs, and connected components/masses. Required after
    outer walls and after interior walls before openings are placed.
    """
    started = time.time()
    params = {
        "endpoint_tol_px": endpoint_tol_px,
        "near_miss_px": near_miss_px,
        "collinear_tol_deg": collinear_tol_deg,
        "collinear_gap_px": collinear_gap_px,
        "short_stub_px": short_stub_px,
    }
    return await _cv_get(f"/datasets/{key}/{file}/wall-topology-qa", params, started)


async def wall_continuity_check(
    key: str,
    file: str,
    collinear_tol_deg: float = 8.0,
    gap_px: float = 180.0,
    line_tol_px: float = 24.0,
    opening_near_px: float = 80.0,
) -> dict:
    """Detect likely walls split at openings.

    Returns collinear wall fragments separated by short gaps, with nearby
    opening symbols when present. Advisory only: the vision agent decides
    whether to merge/extend in the next edit cycle.
    """
    started = time.time()
    params = {
        "collinear_tol_deg": collinear_tol_deg,
        "gap_px": gap_px,
        "line_tol_px": line_tol_px,
        "opening_near_px": opening_near_px,
    }
    return await _cv_get(f"/datasets/{key}/{file}/wall-continuity-check", params, started)


async def ambiguous_line_context(
    key: str,
    file: str,
    bbox: str | None = None,
    line: str | None = None,
    pad_px: float = 120.0,
) -> dict:
    """Context checklist for suspicious line continuations.

    Use before treating a questionable stroke as a wall. `bbox` and `line` are
    comma-separated source-pixel coordinates. The result names non-wall classes
    to consider: door hints, dashed projections, furniture, stairs, dimensions,
    site/garage/car/landscape, or unknown.
    """
    started = time.time()
    params: dict = {"pad_px": pad_px}
    if bbox is not None:
        params["bbox"] = bbox
    if line is not None:
        params["line"] = line
    return await _cv_get(f"/datasets/{key}/{file}/ambiguous-line-context", params, started)


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


async def score_walls(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 16,
    tol_px: int = 18,
    thresh: int | None = None,
    thin_aware: bool = False,
    close_px: int = 82,
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
    return await _cv_get(f"/datasets/{key}/{file}/score-walls", params, started)


async def score_measurements(
    key: str,
    file: str,
    tol_px: int = 8,
    axis_tol_px: int = 14,
) -> dict:
    """Metric-correctness QA over score-walls: checks each dimension tick is the
    projection of a wall face (unmatched_ticks = misplaced/missing wall + nearest
    + delta) and per-chain collinearity + part-sum vs the printed overall."""
    started = time.time()
    params: dict = {"tol_px": tol_px, "axis_tol_px": axis_tol_px}
    return await _cv_get(f"/datasets/{key}/{file}/score-measurements", params, started)


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
    image_delivery: str | None = None,
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

    response_data = {
        **data,
        "image_format": "PNG",
        "region": crop_region,
        "tiers": tiers.split(","),
        "max_dim": max_dim,
        "enhance": enhance or "none",
        "format": format,
    }
    return _image_response(
        content, ctype, response_data,
        started_at=started,
        status_code=img_status,
        delivery=image_delivery,
        artifact_meta={"tool": "dimension_chain_context", "key": key, "file": file, "region": crop_region, "params": grid_params},
    )


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


async def connect_corners(edges: list, closed: bool = True) -> dict:
    """Pure geometry: given ORDERED fitted edges [[[x0,y0],[x1,y1]], ...] (each a
    refine-wall band centerline), return walls whose shared corners are the
    INTERSECTIONS of adjacent edges' lines, so the shell is closed by construction
    (honors tilt). Returns {walls, count, closed}."""
    started = time.time()
    return await _cv_post("/geometry/connect-corners",
                          {"edges": edges, "closed": closed}, started)


_GEOMETRY_TOOL_NAMES = [
    "detect_wall_corners",
    "check_corner",
    "wall_outline",
    "building_silhouette",
    "outer_wall_topology_context",
    "wall_topology_qa",
    "wall_continuity_check",
    "ambiguous_line_context",
    "refine_wall",
    "score_walls",
    "score_measurements",
    "dimension_chain_candidates",
    "dimension_chain_context",
    "propose_wall_edit",
    "connect_corners",
]


def register_geometry_tools(mcp: Any, deps: dict[str, Any]) -> dict[str, Any]:
    configure_geometry_tools(deps)
    exported = {name: globals()[name] for name in _GEOMETRY_TOOL_NAMES}
    for fn in exported.values():
        mcp.tool()(fn)
    return exported
