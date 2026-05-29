"""Point resolution for the agentic-labeling write path (issue #10).

Two deterministic helpers that invert the "read absolute pixel coords off
a dense grid" burden the vision-LLM is weakest at:

1. `map_crop_to_source` — translate a point the agent picks in a *local
   crop frame* (the pixel frame of the image `get_scene_view(region=...)`
   returned) back to SOURCE pixels. The agent reasons about a 300px crop
   (0..w, 0..h) instead of interpolating a 4-digit coordinate across a
   dense grid. Short tracing distance in a small crop → low error.

2. `nearest_ink_feature` — snap a roughly-placed anchor to the nearest
   drawn feature (Höhenkote tick-triangle, line, dim arrow) by finding
   the nearest sufficiently-dark "ink" pixel within a radius. This is the
   server-side snap exposed to the MCP write path, and the same primitive
   that produces the numeric `offset_px` feedback on verify.

Both work purely in source-pixel space (after mapping) and are pure
functions of (image, point, params) — easy to unit-test and free of any
LLM call.
"""
from __future__ import annotations

from typing import Sequence

from PIL import Image

from .grid_render import compute_output_size

# Defaults tuned for light architectural scans: ink is clearly darker than
# paper, and the intended feature is usually within ~15px of a roughly
# placed anchor.
DEFAULT_SNAP_RADIUS_PX = 14
DEFAULT_INK_THRESHOLD = 140  # 0=black .. 255=white; below this counts as ink


def map_crop_to_source(
    point_local: Sequence[float],
    region: tuple[int, int, int, int] | None,
    max_dim: int,
) -> tuple[float, float]:
    """Map a point given in the rendered crop's LOCAL pixel frame back to
    source pixels.

    `region` is the (x0,y0,x1,y1) source-pixel rect passed to
    `get_scene_view`; `max_dim` is the same cap. When the crop was kept
    1:1 (the common case, per H4) this is a pure translation by the crop
    origin. When the crop was downscaled to fit max_dim, the local point
    is scaled back up by the same ratio first.

    With `region=None` the point is already in source pixels and is
    returned unchanged.
    """
    lx, ly = float(point_local[0]), float(point_local[1])
    if region is None:
        return (lx, ly)
    x0, y0, x1, y1 = region
    crop_w = x1 - x0
    crop_h = y1 - y0
    out_w, out_h = compute_output_size(crop_w, crop_h, max_dim)
    sx = crop_w / out_w if out_w else 1.0
    sy = crop_h / out_h if out_h else 1.0
    return (x0 + lx * sx, y0 + ly * sy)


def nearest_ink_feature(
    image: Image.Image,
    point: Sequence[float],
    radius_px: int = DEFAULT_SNAP_RADIUS_PX,
    ink_threshold: int = DEFAULT_INK_THRESHOLD,
) -> dict:
    """Find the nearest drawn-ink pixel to `point` (SOURCE pixels) within
    `radius_px`.

    Returns a dict:
      found:        bool — was any ink pixel found in the window?
      point:        [sx, sy] — the snapped source-pixel coordinate (the
                    original point when nothing was found)
      offset_px:    [dx, dy] — vector FROM the input point TO the feature
                    (the correction the caller should apply); [0,0] if
                    not found
      distance_px:  Euclidean distance to the feature, or null

    "Ink" is any grayscale pixel darker than `ink_threshold`. This snaps
    to the nearest drawn mark — a tick-triangle, a line, a dim arrowhead —
    without needing to classify which. Use a small radius near dense
    content so it doesn't grab an unintended neighbour.
    """
    import numpy as np

    px, py = float(point[0]), float(point[1])
    w, h = image.size
    r = max(1, int(radius_px))
    x0 = max(0, int(round(px)) - r)
    y0 = max(0, int(round(py)) - r)
    x1 = min(w, int(round(px)) + r + 1)
    y1 = min(h, int(round(py)) + r + 1)
    not_found = {
        "found": False,
        "point": [px, py],
        "offset_px": [0.0, 0.0],
        "distance_px": None,
    }
    if x1 <= x0 or y1 <= y0:
        return not_found

    window = image.crop((x0, y0, x1, y1)).convert("L")
    arr = np.asarray(window)
    ys, xs = np.nonzero(arr < ink_threshold)
    if xs.size == 0:
        return not_found

    # Source-pixel coords of every ink pixel in the window.
    sxs = xs.astype(float) + x0
    sys = ys.astype(float) + y0
    dxs = sxs - px
    dys = sys - py
    d2 = dxs * dxs + dys * dys
    # Respect a circular radius (the window is square).
    within = d2 <= float(r * r)
    if not within.any():
        return not_found
    d2 = np.where(within, d2, np.inf)
    i = int(np.argmin(d2))
    fx, fy = float(sxs[i]), float(sys[i])
    dist = float(d2[i] ** 0.5)
    return {
        "found": True,
        "point": [fx, fy],
        "offset_px": [fx - px, fy - py],
        "distance_px": dist,
    }


def resolve_point(
    image: Image.Image,
    point_local: Sequence[float],
    *,
    region: tuple[int, int, int, int] | None = None,
    max_dim: int = 1600,
    frame: str = "source",
    snap: bool = True,
    snap_radius_px: int = DEFAULT_SNAP_RADIUS_PX,
    ink_threshold: int = DEFAULT_INK_THRESHOLD,
) -> dict:
    """Resolve a point to final SOURCE pixels: optional crop-local → source
    mapping, then optional snap-to-feature.

    frame: "source" (point already in source px) or "crop" (point is in
           the local frame of the `region` crop — `region` is required).

    Returns a dict with the resolved `source_point`, the `mapped_point`
    before snapping, and (when snap is on) `snapped`, `offset_px`,
    `distance_px`, and the detected `feature_point`.
    """
    if frame not in ("source", "crop"):
        raise ValueError(f"frame must be 'source' or 'crop', got {frame!r}")
    if frame == "crop" and region is None:
        raise ValueError("frame='crop' requires a region")

    mapped = (
        map_crop_to_source(point_local, region, max_dim)
        if frame == "crop"
        else (float(point_local[0]), float(point_local[1]))
    )

    result = {
        "source_point": [mapped[0], mapped[1]],
        "mapped_point": [mapped[0], mapped[1]],
        "frame": frame,
        "snapped": False,
        "offset_px": [0.0, 0.0],
        "distance_px": None,
        "feature_point": None,
    }
    if snap:
        feat = nearest_ink_feature(
            image, mapped, radius_px=snap_radius_px, ink_threshold=ink_threshold,
        )
        if feat["found"]:
            result["source_point"] = feat["point"]
            result["snapped"] = True
            result["offset_px"] = feat["offset_px"]
            result["distance_px"] = feat["distance_px"]
            result["feature_point"] = feat["point"]
    return result
