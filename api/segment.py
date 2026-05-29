"""Scene segmentation helpers (issue #11).

A PDF page that holds several distinct drawings must become one scene PER
drawing, not a single full-page lump — lumping makes scene_tag meaningless,
breaks best-source routing, and makes calibration impossible (multiple
coordinate frames in one image).

Per repo convention the *region detection* is done by the vision-LLM (the
harness renders the page and identifies the N drawing regions); these
helpers are the deterministic glue that turns a region expressed in the
PARENT scene's own pixel frame into a PDF-unit bbox on the parent page, so
the existing extract path can crop it as a standalone child scene.
"""
from __future__ import annotations

from typing import Sequence


def scene_px_dims(bbox_pdf: Sequence[float], dpi: int) -> tuple[int, int]:
    """Pixel dimensions of a scene cropped from `bbox_pdf` at `dpi` —
    matches how extract_scenes rasterizes (scale = dpi/72)."""
    x0, y0, x1, y1 = (float(v) for v in bbox_pdf)
    f = dpi / 72.0
    return (round((x1 - x0) * f), round((y1 - y0) * f))


def scene_px_to_pdf(
    region_px: Sequence[float],
    parent_bbox_pdf: Sequence[float],
    parent_dpi: int,
) -> list[float]:
    """Map a region given in the PARENT scene's source-pixel frame back to
    PDF units on the parent page.

    The parent scene is the crop of `parent_bbox_pdf` rendered at
    `parent_dpi`, so a scene pixel `p` sits at PDF coordinate
    `bbox_origin + p * 72/dpi`. The result is clamped to the parent bbox
    (a region can't extend beyond the page area the parent covered).
    """
    px0, py0, px1, py1 = (float(v) for v in region_px)
    X0, Y0, X1, Y1 = (float(v) for v in parent_bbox_pdf)
    f = 72.0 / parent_dpi

    def clamp(v: float, lo: float, hi: float) -> float:
        return min(max(v, lo), hi)

    rx0 = clamp(X0 + px0 * f, X0, X1)
    rx1 = clamp(X0 + px1 * f, X0, X1)
    ry0 = clamp(Y0 + py0 * f, Y0, Y1)
    ry1 = clamp(Y0 + py1 * f, Y0, Y1)
    return [rx0, ry0, rx1, ry1]


def validate_region_px(
    region_px: Sequence[float],
    parent_dims: tuple[int, int],
) -> str | None:
    """Return an error string if `region_px` is malformed or outside the
    parent scene, else None."""
    if not (isinstance(region_px, (list, tuple)) and len(region_px) == 4):
        return "bbox_pixels must be [x0,y0,x1,y1]"
    x0, y0, x1, y1 = (float(v) for v in region_px)
    if not (x1 > x0 and y1 > y0):
        return f"bbox_pixels has non-positive area: {list(region_px)}"
    w, h = parent_dims
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return f"bbox_pixels {list(region_px)} outside parent scene {w}x{h}"
    return None
