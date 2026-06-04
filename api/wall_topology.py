"""Wall-topology QA helpers for plan-driven labeling.

These checks reason over saved label geometry. They are advisory repair lists
for the vision agent, not an authoritative wall reader.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from .geometry_util import dist as _dist
from .geometry_util import point_seg_distance

Point = tuple[float, float]
Seg = tuple[Point, Point]


def _pt(v: Any) -> Point | None:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            return (float(v[0]), float(v[1]))
        except (TypeError, ValueError):
            return None
    return None


def _length(seg: Seg) -> float:
    return _dist(seg[0], seg[1])


def _angle(seg: Seg) -> float:
    (a, b) = seg
    deg = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    deg = deg % 180.0
    return deg


def _angle_delta(a: float, b: float) -> float:
    d = abs((a - b) % 180.0)
    return min(d, 180.0 - d)


def _point_seg_distance(pt: Point, seg: Seg) -> float:
    # Seg-form adapter over the canonical (pt, a, b) helper.
    return point_seg_distance(pt, seg[0], seg[1])


def _point_line_distance(pt: Point, seg: Seg) -> float:
    (a, b) = seg
    ax, ay = a
    bx, by = b
    px, py = pt
    dx, dy = bx - ax, by - ay
    denom = math.hypot(dx, dy)
    if denom == 0:
        return _dist(pt, a)
    return abs(dy * px - dx * py + bx * ay - by * ax) / denom


def _projection_t(pt: Point, seg: Seg) -> float:
    (a, b) = seg
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return 0.0
    return ((pt[0] - ax) * dx + (pt[1] - ay) * dy) / l2


def _bbox(points: list[Point], pad: float = 80.0) -> list[float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]


def _merged_segment(a: Seg, b: Seg) -> list[list[float]]:
    """Return a single segment spanning both nearly-collinear input segments."""
    base = a if _length(a) >= _length(b) else b
    pts = [a[0], a[1], b[0], b[1]]
    ordered = sorted(pts, key=lambda pt: _projection_t(pt, base))
    return [
        [round(ordered[0][0], 2), round(ordered[0][1], 2)],
        [round(ordered[-1][0], 2), round(ordered[-1][1], 2)],
    ]


def _walls(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for lab in labels:
        if lab.get("type") != "wall":
            continue
        g = lab.get("geometry") or {}
        s = _pt(g.get("start"))
        e = _pt(g.get("end"))
        if s is None or e is None:
            continue
        out.append({"id": lab.get("id"), "seg": (s, e), "label": lab})
    return out


def _opening_axes(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for lab in labels:
        if lab.get("type") != "floorplan_opening":
            continue
        quad = (lab.get("geometry") or {}).get("quad") or []
        if not isinstance(quad, list) or len(quad) != 4:
            continue
        pts = [_pt(p) for p in quad]
        if any(p is None for p in pts):
            continue
        a, b, c, d = pts  # type: ignore[misc]
        axis = (
            ((a[0] + d[0]) / 2.0, (a[1] + d[1]) / 2.0),
            ((b[0] + c[0]) / 2.0, (b[1] + c[1]) / 2.0),
        )
        parent = next(
            (
                rel.get("other_id")
                for rel in lab.get("relations") or []
                if isinstance(rel, dict) and rel.get("kind") == "belongs_to"
            ),
            None,
        )
        out.append({"id": lab.get("id"), "axis": axis, "parent_wall_id": parent, "label": lab})
    return out


def _scale_px(val: float, source_dpi: int | None, ref_dpi: int = 600, floor: float = 3.0) -> float:
    """WS-2.2: scale a pixel tolerance from the 600-dpi reference to the scene's
    actual extraction DPI, so 'same corner' means the same physical distance at
    any resolution. No-op when source_dpi is absent or already the reference."""
    if not source_dpi or source_dpi <= 0 or source_dpi == ref_dpi:
        return val
    return max(floor, val * source_dpi / ref_dpi)


def wall_topology_qa(
    labels: list[dict[str, Any]],
    *,
    endpoint_tol_px: float = 18.0,
    near_miss_px: float = 60.0,
    collinear_tol_deg: float = 8.0,
    collinear_gap_px: float = 140.0,
    short_stub_px: float = 80.0,
    closeable_gap_px: float = 72.0,
    source_dpi: int | None = None,
) -> dict[str, Any]:
    endpoint_tol_px = _scale_px(endpoint_tol_px, source_dpi)
    near_miss_px = _scale_px(near_miss_px, source_dpi)
    collinear_gap_px = _scale_px(collinear_gap_px, source_dpi)
    short_stub_px = _scale_px(short_stub_px, source_dpi)
    closeable_gap_px = _scale_px(closeable_gap_px, source_dpi)
    walls = _walls(labels)
    endpoints = []
    for w in walls:
        for which, pt in (("start", w["seg"][0]), ("end", w["seg"][1])):
            endpoints.append({"wall_id": w["id"], "which": which, "pt": pt})

    endpoint_clusters: list[list[dict[str, Any]]] = []
    used = set()
    for i, ep in enumerate(endpoints):
        if i in used:
            continue
        cluster = [ep]
        used.add(i)
        for j, other in enumerate(endpoints[i + 1 :], start=i + 1):
            if j not in used and _dist(ep["pt"], other["pt"]) <= endpoint_tol_px:
                cluster.append(other)
                used.add(j)
        endpoint_clusters.append(cluster)

    connected = {
        (item["wall_id"], item["which"])
        for cluster in endpoint_clusters
        if len(cluster) > 1
        for item in cluster
    }

    dangling = []
    near_misses = []
    for ep in endpoints:
        key = (ep["wall_id"], ep["which"])
        if key in connected:
            continue
        nearest = None
        for other in endpoints:
            if other is ep or other["wall_id"] == ep["wall_id"]:
                continue
            d = _dist(ep["pt"], other["pt"])
            if nearest is None or d < nearest["distance_px"]:
                nearest = {"wall_id": other["wall_id"], "which": other["which"], "point": list(other["pt"]), "distance_px": round(d, 2)}
        # WS-3: a dangling endpoint with a nearby corner is closeable (snap it /
        # close_wall_graph); one with a too-large gap means a wall is MISSING
        # there (trace it) — the gate/next-action should say which.
        gap_px = nearest["distance_px"] if nearest else None
        gap_class = (
            "closeable_corner" if (gap_px is not None and gap_px <= closeable_gap_px)
            else "missing_wall_endpoint"
        )
        issue = {
            "wall_id": ep["wall_id"],
            "endpoint": ep["which"],
            "point": list(ep["pt"]),
            "nearest_endpoint": nearest,
            "gap_px": gap_px,
            "gap_class": gap_class,
            "review_region": _bbox([ep["pt"], tuple(nearest["point"]) if nearest else ep["pt"]]),
        }
        dangling.append(issue)
        if nearest and nearest["distance_px"] <= near_miss_px:
            near_misses.append(issue)

    short_stubs = [
        {
            "wall_id": w["id"],
            "length_px": round(_length(w["seg"]), 2),
            "review_region": _bbox([w["seg"][0], w["seg"][1]]),
        }
        for w in walls
        if _length(w["seg"]) <= short_stub_px
    ]

    collinear_fragments = []
    for i, a in enumerate(walls):
        for b in walls[i + 1 :]:
            if _angle_delta(_angle(a["seg"]), _angle(b["seg"])) > collinear_tol_deg:
                continue
            # Endpoints close to the other segment's infinite/finite line and
            # nearest endpoints separated by a moderate gap are likely fragments.
            line_dist = min(
                _point_line_distance(a["seg"][0], b["seg"]),
                _point_line_distance(a["seg"][1], b["seg"]),
                _point_line_distance(b["seg"][0], a["seg"]),
                _point_line_distance(b["seg"][1], a["seg"]),
            )
            nearest_pair = min(
                ((pa, pb) for pa in a["seg"] for pb in b["seg"]),
                key=lambda pair: _dist(pair[0], pair[1]),
            )
            gap = _dist(nearest_pair[0], nearest_pair[1])
            if line_dist <= endpoint_tol_px and endpoint_tol_px < gap <= collinear_gap_px:
                collinear_fragments.append(
                    {
                        "wall_ids": [a["id"], b["id"]],
                        "gap_px": round(gap, 2),
                        "line_distance_px": round(line_dist, 2),
                        "suggested_repair": {
                            "action": "merge_or_extend_wall_unless_gap_is_a_real_endpoint",
                            "candidate_wall": _merged_segment(a["seg"], b["seg"]),
                            "replace_wall_ids": [a["id"], b["id"]],
                        },
                        "review_region": _bbox([a["seg"][0], a["seg"][1], b["seg"][0], b["seg"][1]]),
                    }
                )

    parent: dict[str, str] = {}
    for w in walls:
        parent[w["id"]] = w["id"]

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for cluster in endpoint_clusters:
        ids = [item["wall_id"] for item in cluster if item["wall_id"] in parent]
        for other in ids[1:]:
            union(ids[0], other)

    comps: dict[str, list[str]] = defaultdict(list)
    for wid in parent:
        comps[find(wid)].append(wid)
    components = [{"wall_ids": sorted(v), "count": len(v)} for v in comps.values()]
    components.sort(key=lambda c: c["count"], reverse=True)

    return {
        "wall_count": len(walls),
        "endpoint_count": len(endpoints),
        "dangling_endpoints": dangling,
        "near_miss_corners": near_misses,
        "collinear_fragments": collinear_fragments,
        "short_stubs": short_stubs,
        "components": components,
        "params": {
            "endpoint_tol_px": endpoint_tol_px,
            "near_miss_px": near_miss_px,
            "collinear_tol_deg": collinear_tol_deg,
            "collinear_gap_px": collinear_gap_px,
            "short_stub_px": short_stub_px,
        },
    }


def wall_continuity_check(
    labels: list[dict[str, Any]],
    *,
    collinear_tol_deg: float = 8.0,
    gap_px: float = 180.0,
    line_tol_px: float = 24.0,
    opening_near_px: float = 80.0,
    source_dpi: int | None = None,
) -> dict[str, Any]:
    gap_px = _scale_px(gap_px, source_dpi)
    line_tol_px = _scale_px(line_tol_px, source_dpi)
    opening_near_px = _scale_px(opening_near_px, source_dpi)
    walls = _walls(labels)
    openings = _opening_axes(labels)
    candidates = []
    for i, a in enumerate(walls):
        for b in walls[i + 1 :]:
            if _angle_delta(_angle(a["seg"]), _angle(b["seg"])) > collinear_tol_deg:
                continue
            nearest_pair = min(
                ((pa, pb) for pa in a["seg"] for pb in b["seg"]),
                key=lambda pair: _dist(pair[0], pair[1]),
            )
            gap = _dist(nearest_pair[0], nearest_pair[1])
            if gap <= line_tol_px or gap > gap_px:
                continue
            line_dist = min(_point_line_distance(nearest_pair[0], b["seg"]), _point_line_distance(nearest_pair[1], a["seg"]))
            if line_dist > line_tol_px:
                continue
            mid = ((nearest_pair[0][0] + nearest_pair[1][0]) / 2, (nearest_pair[0][1] + nearest_pair[1][1]) / 2)
            near_openings = []
            for op in openings:
                od = min(_point_seg_distance(mid, op["axis"]), _dist(mid, op["axis"][0]), _dist(mid, op["axis"][1]))
                if od <= opening_near_px:
                    near_openings.append({"opening_id": op["id"], "parent_wall_id": op["parent_wall_id"], "distance_px": round(od, 2)})
            candidates.append(
                {
                    "wall_ids": [a["id"], b["id"]],
                    "gap_px": round(gap, 2),
                    "gap_midpoint": [round(mid[0], 2), round(mid[1], 2)],
                    "near_openings": near_openings,
                    "reason": "collinear wall fragments separated by a short gap; openings do not by themselves end walls",
                    "suggested_repair": {
                        "action": "merge_or_extend_wall_unless_gap_is_a_real_endpoint",
                        "candidate_wall": _merged_segment(a["seg"], b["seg"]),
                        "replace_wall_ids": [a["id"], b["id"]],
                    },
                    "review_region": _bbox([a["seg"][0], a["seg"][1], b["seg"][0], b["seg"][1]]),
                }
            )
    return {
        "candidate_count": len(candidates),
        "candidates": candidates,
        "params": {
            "collinear_tol_deg": collinear_tol_deg,
            "gap_px": gap_px,
            "line_tol_px": line_tol_px,
            "opening_near_px": opening_near_px,
        },
    }


def ambiguous_line_context(
    labels: list[dict[str, Any]],
    *,
    bbox: list[float] | None = None,
    line: list[list[float]] | None = None,
    pad_px: float = 120.0,
) -> dict[str, Any]:
    points: list[Point] = []
    if line and len(line) == 2:
        a = _pt(line[0])
        b = _pt(line[1])
        if a and b:
            points.extend([a, b])
    if bbox and len(bbox) == 4:
        x0, y0, x1, y1 = [float(v) for v in bbox]
        points.extend([(x0, y0), (x1, y1)])
    review_region = _bbox(points, pad_px) if points else None
    nearby = []
    if review_region:
        x0, y0, x1, y1 = review_region
        for lab in labels:
            lab_points = _label_points(lab)
            if any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in lab_points):
                nearby.append({"id": lab.get("id"), "type": lab.get("type"), "status": lab.get("status")})
    return {
        "review_region": review_region,
        "nearby_labels": nearby,
        "classification_checklist": [
            "structural_wall",
            "opening_edge",
            "door_swing_or_hint",
            "dashed_projection",
            "furniture_or_fixture",
            "stair",
            "dimension_or_annotation",
            "site_garage_car_or_landscape",
            "unknown",
        ],
        "instructions": [
            "Inspect the tight crop and a wider context crop before labeling this as a wall.",
            "A door swing/hint, dashed projection, furniture line, or dimension stroke is not a wall.",
            "Record the classification in the scene plan before editing.",
        ],
    }


def _label_points(label: dict[str, Any]) -> list[Point]:
    g = label.get("geometry") or {}
    pts: list[Point] = []
    for key in ("start", "end", "anchor", "center"):
        p = _pt(g.get(key))
        if p:
            pts.append(p)
    for key in ("quad", "polygon", "polyline", "top_edge", "bottom_edge", "bbox"):
        arr = g.get(key)
        if isinstance(arr, list):
            for item in arr:
                p = _pt(item)
                if p:
                    pts.append(p)
    return pts
