"""Shared 2-D geometry primitives (M2 — code-quality-tracker).

Single source of truth for the point/segment helpers that were copy-pasted
(with subtly divergent signatures) across api/main.py, scene_plan_state.py,
topology_repair.py, wall_topology.py, and geometry_checks.py.
"""
from __future__ import annotations

import math
from typing import Any

Point = tuple[float, float]
Segment = tuple[Point, Point]
Seg = Segment  # back-compat alias for modules that spell it `Seg`


def as_point(pt: Any) -> Point | None:
    """Coerce an ``[x, y]`` list/tuple of numbers into ``(float, float)``.

    Accepts both lists (UI/JSON geometry) and tuples (already-parsed points);
    returns None for anything else, so callers can guard malformed geometry.
    """
    if (
        isinstance(pt, (list, tuple))
        and len(pt) == 2
        and isinstance(pt[0], (int, float))
        and isinstance(pt[1], (int, float))
    ):
        return (float(pt[0]), float(pt[1]))
    return None


def wall_segment(label: dict[str, Any]) -> Segment | None:
    """Return ``(start, end)`` for a wall label from its geometry, or None."""
    geom = label.get("geometry") or {}
    start = as_point(geom.get("start"))
    end = as_point(geom.get("end"))
    if start is None or end is None:
        return None
    return (start, end)


def dist(a: Point, b: Point) -> float:
    """Euclidean distance between two points."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def point_seg_distance(pt: Point, a: Point, b: Point) -> float:
    """Shortest distance from ``pt`` to the *segment* ``ab`` (clamped to the
    segment, not the infinite line)."""
    ax, ay = a
    bx, by = b
    px, py = pt
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        return dist(pt, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len2))
    proj = (ax + t * dx, ay + t * dy)
    return dist(pt, proj)
