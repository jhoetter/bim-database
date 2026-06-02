"""Geometry MCP tools (extracted from mcp_server.py, H5).

Stable helpers imported by value; HTTP helpers, __file__, and inter-tool
calls go through `mcp_server.` so the test-harness patches still apply.
"""
from __future__ import annotations

from mcp.types import ImageContent
from mcp.types import TextContent
from typing import Any
import httpx
import json
import time

import mcp_server
from mcp_server import (
    _api_unreachable_error,
    _compact_plan_mutation_response,
    _http_status_to_error,
    _image_delivery_payload,
    _ok,
    _truncate_lists,
    _wait_for_api,
    _wrap_text,
    mcp,
)


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
        status, body = await mcp_server._api_get(
            f"/datasets/{key}/{file}/wall-corners", params
        )
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get(
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
        status, body = await mcp_server._api_get(
            f"/datasets/{key}/{file}/check-corner", params
        )
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get(
            f"/datasets/{key}/{file}/check-corner", params
        )
    if status >= 400:
        return _http_status_to_error(status, body, started)
    data = body.get("data", body) if isinstance(body, dict) else body
    return _ok(data, started_at=started, status_code=status)


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
    return await mcp_server._cv_get(f"/datasets/{key}/{file}/wall-outline", params, started)


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
    return await mcp_server._cv_get(f"/datasets/{key}/{file}/building-silhouette", params, started)


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
    return await mcp_server._cv_get(f"/datasets/{key}/{file}/refine-wall", params, started)


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
    result = await mcp_server._cv_get(f"/datasets/{key}/{file}/score-walls", params, started)
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
    result = await mcp_server._cv_get(f"/datasets/{key}/{file}/score-measurements", params, started)
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
    result = await mcp_server._cv_get(f"/datasets/{key}/{file}/wall-topology-qa", params, started)
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
    result = await mcp_server._cv_get(f"/datasets/{key}/{file}/wall-continuity-check", params, started)
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
    result = await mcp_server._cv_get(f"/datasets/{key}/{file}/ambiguous-line-context", params, started)
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
    return await mcp_server._cv_get(f"/datasets/{key}/{file}/dimension-chain-candidates", params, started)


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
    result = await mcp_server._cv_get(f"/datasets/{key}/{file}/dimension-station-graph", params, started)
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
    return await mcp_server._cv_get(f"/datasets/{key}/{file}/opening-candidates", params, started)


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
    return await mcp_server._cv_get(f"/datasets/{key}/{file}/view-geometry-candidates", params, started)


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
    status, content, ctype = await mcp_server._api_get_bytes(
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
    status, res = await mcp_server._api_post(f"/datasets/{key}/{file}/opening-candidates/{candidate_id}/apply", body)
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
    status, res = await mcp_server._api_post(f"/datasets/{key}/{file}/opening-candidates/{candidate_id}/decision", body)
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
    status, body = await mcp_server._api_get(f"/datasets/{key}/{file}/dimension-chain-candidates", params)
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
    img_status, content, ctype = await mcp_server._api_get_bytes(f"/datasets/{key}/{file}/grid", params=grid_params)
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
    return await mcp_server._cv_post(f"/datasets/{key}/{file}/propose-wall-edit", payload, started)


@mcp.tool()
async def connect_corners(edges: list, closed: bool = True) -> dict:
    """Pure geometry: given ORDERED fitted edges [[[x0,y0],[x1,y1]], ...] (each a
    refine-wall band centerline), return walls whose shared corners are the
    INTERSECTIONS of adjacent edges' lines, so the shell is closed by construction
    (honors tilt). Returns {walls, count, closed}."""
    started = time.time()
    return await mcp_server._cv_post("/geometry/connect-corners",
                          {"edges": edges, "closed": closed}, started)


