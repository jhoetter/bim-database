"""Wall-geometry primitives that turn the proven labeling patterns into real,
tested tools (bim-agent tracker B2). Pure where possible so they are unit-tested
without an image; the image-dependent ones wrap the existing CV scorers.

- connect_corners       — shells closed BY CONSTRUCTION (corners = line
                          intersections of adjacent fitted edges).  [B2.3]
- rectilinearize        — snap a noisy outline to an axis-aligned stepped polygon.
- building_silhouette   — outer silhouette as ORDERED stepped polygon(s), masses
                          separated (house vs garage).                [B2.2]
- propose_wall_edit     — atomic test-and-apply: score a candidate, keep only on
                          improvement.                                [B2.1]

The reader is always the vision-LLM (see labeling-methodology.md); these are
deterministic geometry/scoring helpers, never the authoritative reader.
"""
from __future__ import annotations

from typing import Iterable, Optional

Point = tuple[float, float]
Edge = tuple[Point, Point]


# --------------------------------------------------------------------------- #
# B2.3 — connected-corner constructor
# --------------------------------------------------------------------------- #
def _line_through(p: Point, q: Point) -> tuple[float, float, float]:
    """Implicit line a*x + b*y = c through points p, q."""
    (x0, y0), (x1, y1) = p, q
    a = y1 - y0
    b = x0 - x1
    c = a * x0 + b * y0
    return a, b, c


def _intersect(l1, l2, *, eps: float = 1e-9) -> Optional[Point]:
    a1, b1, c1 = l1
    a2, b2, c2 = l2
    det = a1 * b2 - a2 * b1
    if abs(det) < eps:
        return None  # parallel / collinear
    x = (b2 * c1 - b1 * c2) / det
    y = (a1 * c2 - a2 * c1) / det
    return (x, y)


def connect_corners(edges: Iterable[Edge], *, closed: bool = True) -> list[Edge]:
    """Given ORDERED fitted edges (each two points defining a line — typically a
    `refine-wall` band centerline), return walls whose shared corners are the
    INTERSECTIONS of adjacent edges' infinite lines, so the shell is closed by
    construction (no floating segments to join post-hoc).

    Honors tilt (corners are not forced to equal-y). For two parallel adjacent
    edges (no intersection) the corner falls back to the midpoint of their facing
    endpoints. With closed=True returns N walls forming a loop; with closed=False
    returns N walls keeping the first/last free endpoints.
    """
    edges = [((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))) for a, b in edges]
    n = len(edges)
    if n == 0:
        return []
    if n == 1:
        return [edges[0]]
    lines = [_line_through(a, b) for a, b in edges]

    corner: list[Point] = [(0.0, 0.0)] * n
    for i in range(n):
        prev = (i - 1) % n
        if not closed and i == 0:
            corner[i] = edges[0][0]
            continue
        pt = _intersect(lines[prev], lines[i])
        if pt is None:
            ax, ay = edges[prev][1]
            bx, by = edges[i][0]
            pt = ((ax + bx) / 2.0, (ay + by) / 2.0)
        corner[i] = pt

    walls: list[Edge] = []
    for i in range(n):
        nxt = (i + 1) % n
        start = corner[i]
        if not closed and nxt == 0:
            end = edges[i][1]
        else:
            end = corner[nxt]
        walls.append((start, end))
    return walls


# --------------------------------------------------------------------------- #
# rectilinearize + B2.2 building silhouette
# --------------------------------------------------------------------------- #
def rectilinearize(polygon: list[Point], *, angle_tol_deg: float = 18.0) -> list[Point]:
    """Snap a near-axis-aligned outline to a clean stepped (rectilinear) polygon:
    each edge close to horizontal/vertical is forced exactly so, and the shared
    vertex is recomputed as the intersection of the two snapped neighbours. Edges
    genuinely off-axis (a real diagonal, beyond angle_tol_deg) are left alone.

    This is what makes a noisy CV outline read as the true stepped silhouette
    (L/T/U) instead of a jagged blob — the shape-first requirement in the
    methodology.
    """
    import math

    pts = [(float(x), float(y)) for x, y in polygon]
    n = len(pts)
    if n < 3:
        return pts
    # closed ring assumed; drop duplicate closing point if present
    if pts[0] == pts[-1]:
        pts = pts[:-1]
        n -= 1

    def snapped_line(p: Point, q: Point):
        (x0, y0), (x1, y1) = p, q
        dx, dy = x1 - x0, y1 - y0
        ang = math.degrees(math.atan2(abs(dy), abs(dx)))  # 0=horiz, 90=vert
        if ang <= angle_tol_deg:  # ~horizontal → y constant (avg)
            yy = (y0 + y1) / 2.0
            return (0.0, 1.0, yy)
        if ang >= 90 - angle_tol_deg:  # ~vertical → x constant (avg)
            xx = (x0 + x1) / 2.0
            return (1.0, 0.0, xx)
        return _line_through(p, q)  # genuine diagonal — keep

    lines = [snapped_line(pts[i], pts[(i + 1) % n]) for i in range(n)]
    out: list[Point] = []
    for i in range(n):
        prev = (i - 1) % n
        pt = _intersect(lines[prev], lines[i])
        out.append(pt if pt is not None else pts[i])
    return out


def building_silhouette(image, *, region=None, min_wall_px: int = 16,
                        thresh: Optional[int] = None, angle_tol_deg: float = 18.0,
                        min_area_frac: float = 0.02) -> dict:
    """Outer silhouette of the building as ORDERED stepped polygon(s), one per
    connected mass (house vs detached garage auto-separate), non-wall specks
    dropped. Wraps the existing CV outline detector, then rectilinearizes.

    Returns {"masses": [{"polygon": [[x,y]...], "area": float, "bbox":[x0,y0,x1,y1]}],
             "count": int}. Masses are sorted largest-first.
    """
    import numpy as np  # noqa: F401  (kept for parity with CV modules)
    from api.corner_detect import detect_wall_outline

    out = detect_wall_outline(image, region, min_wall_px=min_wall_px, thresh=thresh)
    raw_masses = out.get("masses")
    if raw_masses is None:
        # single-outline API shape → wrap as one mass
        poly = out.get("outline") or []
        raw_masses = [{"outline": poly, "area": out.get("area", _poly_area(poly))}] if poly else []

    total = 0.0
    cleaned = []
    for m in raw_masses:
        poly = m.get("outline") or m.get("polygon") or []
        if len(poly) < 3:
            continue
        rp = rectilinearize([tuple(p) for p in poly], angle_tol_deg=angle_tol_deg)
        area = abs(_poly_area(rp))
        total += area
        xs = [p[0] for p in rp]
        ys = [p[1] for p in rp]
        cleaned.append({"polygon": [[round(x, 2), round(y, 2)] for x, y in rp],
                        "area": area,
                        "bbox": [min(xs), min(ys), max(xs), max(ys)]})

    if total > 0 and min_area_frac > 0:
        cleaned = [m for m in cleaned if m["area"] >= min_area_frac * total]
    cleaned.sort(key=lambda m: m["area"], reverse=True)
    return {"masses": cleaned, "count": len(cleaned)}


def _poly_area(poly) -> float:
    pts = [(float(x), float(y)) for x, y in poly]
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return s / 2.0


# --------------------------------------------------------------------------- #
# B2.1 — atomic test-and-apply
# --------------------------------------------------------------------------- #
# Canonical 600 dpi scoring params (see labeling-methodology.md §5).
CANONICAL_SCORE_PARAMS = {"min_wall_px": 16, "tol_px": 18, "close_px": 82}


def apply_candidate(walls: list[Edge], candidate: dict) -> list[Edge]:
    """Pure: return a NEW wall list with the candidate edit applied.
    candidate = {"op": "add"|"move"|"delete", ...}:
      add    -> {"op":"add","wall":[[x0,y0],[x1,y1]]}
      move   -> {"op":"move","index":i,"wall":[[x0,y0],[x1,y1]]}
      delete -> {"op":"delete","index":i}
    """
    walls = list(walls)
    op = candidate.get("op")
    if op == "add":
        w = candidate["wall"]
        walls.append(((w[0][0], w[0][1]), (w[1][0], w[1][1])))
    elif op == "move":
        i = candidate["index"]
        w = candidate["wall"]
        walls[i] = ((w[0][0], w[0][1]), (w[1][0], w[1][1]))
    elif op == "delete":
        del walls[candidate["index"]]
    else:
        raise ValueError(f"unknown candidate op: {op!r}")
    return walls


def propose_wall_edit(image, walls: list[Edge], candidate: dict, *,
                      region=None, params: Optional[dict] = None,
                      metric: str = "f1", min_gain: float = 1e-6) -> dict:
    """Atomic test-and-apply. Score the current walls and the candidate-edited
    walls with the canonical params; return whether the edit should be applied
    (metric strictly improved by > min_gain) plus before/after scores and the
    resulting wall list. The caller persists `walls_after` ONLY when applied —
    removing the test-vs-apply desync that caused repeated manual corrections.

    Never deletes-to-win silently: a delete that lowers recall will score worse
    and be rejected, enforcing 'never delete a real wall to chase a metric'.
    """
    from api.wall_score import score_walls

    p = {**CANONICAL_SCORE_PARAMS, **(params or {})}
    before = score_walls(image, walls, region=region, **p)
    walls_after = apply_candidate(walls, candidate)
    after = score_walls(image, walls_after, region=region, **p)
    gain = float(after.get(metric, 0.0)) - float(before.get(metric, 0.0))
    applied = gain > min_gain
    return {
        "applied": applied,
        "metric": metric,
        "gain": gain,
        "before": before,
        "after": after,
        "walls_after": [[[w[0][0], w[0][1]], [w[1][0], w[1][1]]] for w in walls_after],
        "params": p,
    }
