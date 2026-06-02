"""Pure geometry / scale sanity predicates for labeling correctness.

V3.3 (scale-consistency) and V3.4 (closure / sanity) of the
labeling-correctness-verification tracker. All functions are PURE — no I/O,
no rendering — so they unit-test deterministically on synthetic polygon sets
and are cheap to call as cross-checks during a run. The vision-LLM still
decides what to do with a failed predicate.

Coordinates are source-pixel (x, y) tuples unless noted.
"""
from __future__ import annotations

import math

Point = tuple[float, float]
Seg = tuple[Point, Point]


# ── V3.3 scale-consistency ──────────────────────────────────────────────

def segment_length_px(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def scale_consistent(
    px_length: float,
    px_per_mm: float,
    printed_mm: float,
    *,
    tol_frac: float = 0.05,
) -> dict:
    """Does a measured pixel length, converted through the scene calibration,
    match the dimension-chain's printed mm value?

    Returns {consistent, derived_mm, printed_mm, rel_err, tol_frac}.
    `consistent` is True when |derived_mm - printed_mm| / printed_mm <=
    tol_frac. Guards against zero/negative calibration or printed value.
    """
    if px_per_mm <= 0 or printed_mm <= 0:
        return {"consistent": False, "derived_mm": None, "printed_mm": printed_mm,
                "rel_err": None, "tol_frac": tol_frac,
                "reason": "non-positive px_per_mm or printed_mm"}
    derived_mm = px_length / px_per_mm
    rel_err = abs(derived_mm - printed_mm) / printed_mm
    return {"consistent": rel_err <= tol_frac,
            "derived_mm": round(derived_mm, 2),
            "printed_mm": printed_mm,
            "rel_err": round(rel_err, 4),
            "tol_frac": tol_frac}


# ── V3.4 closure / sanity ───────────────────────────────────────────────

def chain_is_closed(points: list[Point], *, tol_px: float = 6.0) -> bool:
    """Does an ordered outer-wall vertex chain form a closed loop — i.e. the
    last vertex returns to (within tol_px of) the first? A polygon given
    without an explicit repeated closing vertex is still 'closed' if its ends
    coincide; we test first-vs-last so callers may pass either form."""
    if len(points) < 3:
        return False
    return segment_length_px(points[0], points[-1]) <= tol_px


def point_in_polygon(pt: Point, polygon: list[Point]) -> bool:
    """Even-odd ray cast. polygon is an ordered ring (closing vertex optional).
    Boundary points may read either way — used for 'ridge within footprint'
    where the ridge is well inside, so boundary ambiguity is immaterial."""
    if len(polygon) < 3:
        return False
    x, y = pt
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def _point_seg_distance(pt: Point, a: Point, b: Point) -> float:
    """Shortest distance from pt to segment ab (not the infinite line)."""
    ax, ay = a
    bx, by = b
    px, py = pt
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        return segment_length_px(pt, a)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len2
    t = max(0.0, min(1.0, t))
    proj = (ax + t * dx, ay + t * dy)
    return segment_length_px(pt, proj)


def _project_t(pt: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    px, py = pt
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        return 0.0
    return ((px - ax) * dx + (py - ay) * dy) / seg_len2


def _angle_delta_deg(a0: Point, a1: Point, b0: Point, b1: Point) -> float:
    adx, ady = a1[0] - a0[0], a1[1] - a0[1]
    bdx, bdy = b1[0] - b0[0], b1[1] - b0[1]
    if math.hypot(adx, ady) < 1 or math.hypot(bdx, bdy) < 1:
        return 90.0
    aa = math.degrees(math.atan2(ady, adx))
    ba = math.degrees(math.atan2(bdy, bdx))
    d = abs((aa - ba + 90.0) % 180.0 - 90.0)
    return d


def opening_on_wall(
    opening: Seg,
    walls: list[Seg],
    *,
    tol_px: float = 10.0,
) -> dict:
    """Does an opening (door/window) segment lie ON one of the wall segments —
    both its endpoints within tol_px of the same wall? Returns
    {on_wall, wall_index, max_endpoint_dist}."""
    (o0, o1) = opening
    best_idx = -1
    best_dist = math.inf
    for i, (w0, w1) in enumerate(walls):
        d0 = _point_seg_distance(o0, w0, w1)
        d1 = _point_seg_distance(o1, w0, w1)
        worst = max(d0, d1)
        if worst < best_dist:
            best_dist = worst
            best_idx = i
    on = best_dist <= tol_px
    return {"on_wall": on,
            "wall_index": best_idx if on else None,
            "max_endpoint_dist": round(best_dist, 2) if best_dist != math.inf else None,
            "tol_px": tol_px}


def floorplan_opening_quality(
    opening_axis: Seg,
    opening_depth_axis: Seg,
    parent_wall: Seg,
    *,
    tol_px: float = 30.0,
    angle_tol_deg: float = 8.0,
    extension_tol_px: float = 20.0,
    max_length_fraction: float = 0.95,
    is_garage_door: bool = False,
    expected_depth_px: float | None = None,
    depth_tol_frac: float = 0.60,
) -> dict:
    """Geometry QA for a floorplan opening against its parent wall.

    Returns `{ok, defects:[...]}`. Defect categories are stable and intended
    for scene-plan gates: opening_off_wall, opening_not_collinear,
    opening_outside_parent, opening_too_long, opening_depth_mismatch.
    """
    defects: list[dict] = []
    o0, o1 = opening_axis
    d0, d1 = opening_depth_axis
    w0, w1 = parent_wall
    wall_len = segment_length_px(w0, w1)
    op_len = segment_length_px(o0, o1)
    op_depth = segment_length_px(d0, d1)
    placement = opening_on_wall(opening_axis, [parent_wall], tol_px=tol_px)
    if not placement["on_wall"]:
        defects.append({
            "category": "opening_off_wall",
            "message": f"opening centerline endpoints are not on parent wall within {tol_px:g}px",
            "details": placement,
        })
    angle = _angle_delta_deg(o0, o1, w0, w1)
    if angle > angle_tol_deg:
        defects.append({
            "category": "opening_not_collinear",
            "message": f"opening axis differs from parent wall by {angle:.1f}°",
            "details": {"angle_delta_deg": round(angle, 2), "tol_deg": angle_tol_deg},
        })
    t0 = _project_t(o0, w0, w1)
    t1 = _project_t(o1, w0, w1)
    if wall_len > 0:
        ext = extension_tol_px / wall_len
        if min(t0, t1) < -ext or max(t0, t1) > 1.0 + ext:
            defects.append({
                "category": "opening_outside_parent",
                "message": "opening projects outside the parent wall segment",
                "details": {"t0": round(t0, 4), "t1": round(t1, 4), "extension_tol_px": extension_tol_px},
            })
        if not is_garage_door and op_len > wall_len * max_length_fraction:
            defects.append({
                "category": "opening_too_long",
                "message": "opening length is implausibly large for its parent wall",
                "details": {
                    "opening_length_px": round(op_len, 2),
                    "parent_length_px": round(wall_len, 2),
                    "max_length_fraction": max_length_fraction,
                },
            })
    if expected_depth_px and expected_depth_px > 0:
        rel = abs(op_depth - expected_depth_px) / expected_depth_px
        if rel > depth_tol_frac:
            defects.append({
                "category": "opening_depth_mismatch",
                "message": "opening quad depth does not match parent wall thickness",
                "details": {
                    "opening_depth_px": round(op_depth, 2),
                    "expected_depth_px": round(expected_depth_px, 2),
                    "rel_err": round(rel, 4),
                    "tol_frac": depth_tol_frac,
                },
            })
    return {
        "ok": not defects,
        "defects": defects,
        "metrics": {
            "opening_length_px": round(op_len, 2),
            "opening_depth_px": round(op_depth, 2),
            "parent_length_px": round(wall_len, 2),
            "angle_delta_deg": round(angle, 2),
            "projection_t": [round(t0, 4), round(t1, 4)],
        },
    }


def ridge_within_footprint(ridge: Seg, footprint: list[Point]) -> dict:
    """Is a roof ridge segment inside the building footprint polygon? Tests
    both endpoints and the midpoint (a ridge spanning a concave footprint
    could bow outside while its ends are inside)."""
    (r0, r1) = ridge
    mid = ((r0[0] + r1[0]) / 2.0, (r0[1] + r1[1]) / 2.0)
    checks = {"start": point_in_polygon(r0, footprint),
              "mid": point_in_polygon(mid, footprint),
              "end": point_in_polygon(r1, footprint)}
    return {"within": all(checks.values()), "points": checks}
