"""Reference MCP tools (extracted from mcp_server.py, H5).

Stable helpers imported by value; HTTP helpers, __file__, and inter-tool
calls go through `mcp_server.` so the test-harness patches still apply.
"""
from __future__ import annotations

import httpx
import time

import mcp_server
from mcp_server import (
    _api_unreachable_error,
    _err,
    _http_status_to_error,
    _new_label_id,
    _ok,
    _preflight_label_write,
    _read_labels,
    _wait_for_api,
    _write_labels,
    mcp,
)


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
    allow_plan_order_override: bool = False,
    override_reason: str | None = None,
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
        return _err("schema_invalid", "orientation must be 'horizontal' or 'vertical'",
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
                "with mcp_server.get_scene_view(region=…) around the dim, read off "
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
    preflight, preflight_err = await _preflight_label_write(
        key,
        file,
        ["dimensioned_distance", "dimension_number"],
        tool="add_reference_dim",
        allow_override=allow_plan_order_override,
        override_reason=override_reason,
        started_at=started,
    )
    if preflight_err is not None:
        return preflight_err
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
                        "visible area. Re-check via mcp_server.get_scene_view(region=…) "
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
    result["data"]["plan_preflight"] = preflight
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
        status, body = await mcp_server._api_post(f"/exports/{key}/{file}/preview", params=params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_post(f"/exports/{key}/{file}/preview", params=params)
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

