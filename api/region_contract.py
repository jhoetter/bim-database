"""Shared geometry-region normalization helpers.

Agents often pass four numbers while reading grid corners. This module keeps
the format explicit at API boundaries so xyxy corners do not silently become
xywh rectangles.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedRegion:
    region: list[float]
    bbox_format: str
    bbox_xyxy: list[float]
    clipped: bool = False


def _numeric4(region: Any) -> list[float] | None:
    if not isinstance(region, (list, tuple)) or len(region) < 4:
        return None
    vals: list[float] = []
    for v in region[:4]:
        if not isinstance(v, (int, float)):
            return None
        vals.append(float(v))
    return vals


def _xywh_to_xyxy(vals: list[float]) -> list[float]:
    x, y, w, h = vals
    return [x, y, x + max(0.0, w), y + max(0.0, h)]


def _xyxy_to_xyxy(vals: list[float]) -> list[float]:
    x0, y0, x1, y1 = vals
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _in_bounds(box: list[float], image_size: tuple[int, int] | None) -> bool:
    if box[2] <= box[0] or box[3] <= box[1]:
        return False
    if image_size is None:
        return True
    w, h = image_size
    return box[0] >= 0 and box[1] >= 0 and box[2] <= w and box[3] <= h


def clamp_xyxy(box: list[float], image_size: tuple[int, int]) -> tuple[list[float], bool]:
    w, h = image_size
    clipped = False
    out = [
        max(0.0, min(float(w), box[0])),
        max(0.0, min(float(h), box[1])),
        max(0.0, min(float(w), box[2])),
        max(0.0, min(float(h), box[3])),
    ]
    if out != box:
        clipped = True
    return out, clipped


def normalize_bbox_region(
    region: Any,
    *,
    bbox_format: str | None,
    image_size: tuple[int, int] | None = None,
    reject_out_of_bounds: bool = True,
) -> NormalizedRegion:
    """Normalize an API bbox to xyxy.

    If bbox_format is omitted, infer only when safe:
    - if only one of xyxy/xywh is valid in bounds, use that one;
    - if both are possible, reject as ambiguous.
    """
    vals = _numeric4(region)
    if vals is None:
        raise ValueError("region must be a numeric four-value bbox")
    fmt = str(bbox_format or "").strip().lower()
    if fmt and fmt not in {"xyxy", "xywh"}:
        raise ValueError("bbox_format must be 'xyxy' or 'xywh'")

    candidates = {
        "xyxy": _xyxy_to_xyxy(vals),
        "xywh": _xywh_to_xyxy(vals),
    }
    if fmt:
        box = candidates[fmt]
        if not _in_bounds(box, image_size):
            if reject_out_of_bounds:
                raise ValueError(f"{fmt} region is outside image bounds: {box}")
            if image_size is not None:
                box, clipped = clamp_xyxy(box, image_size)
                return NormalizedRegion(vals, fmt, box, clipped=clipped)
        return NormalizedRegion(vals, fmt, box)

    valid = [name for name, box in candidates.items() if _in_bounds(box, image_size)]
    if len(valid) == 1:
        chosen = valid[0]
        return NormalizedRegion(vals, chosen, candidates[chosen])
    if not valid:
        raise ValueError("region cannot be interpreted as a positive in-bounds xyxy or xywh bbox")
    raise ValueError("ambiguous bbox: pass bbox_format='xyxy' or bbox_format='xywh'")


def normalize_review_region(
    region: Any,
    *,
    region_kind: str | None = None,
    image_size: tuple[int, int] | None = None,
    pad_px: float = 0.0,
) -> dict[str, Any] | None:
    """Normalize score/topology review regions to bounded xyxy boxes."""
    vals = _numeric4(region)
    if vals is None:
        return None
    kind = str(region_kind or "").strip().lower()
    if kind in {"missing_region", "missing_region_xywh", "xywh"}:
        box = _xywh_to_xyxy(vals)
        fmt = "xywh"
    elif kind in {"off_ink_segment", "off_ink_segment_line", "line_segment", "segment"}:
        x0, y0, x1, y1 = vals
        box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        fmt = "line_segment"
    else:
        try:
            nr = normalize_bbox_region(vals, bbox_format=None, image_size=image_size)
            box = nr.bbox_xyxy
            fmt = nr.bbox_format
        except ValueError:
            x0, y0, x1, y1 = vals
            box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
            fmt = "line_segment"
    if pad_px:
        box = [box[0] - pad_px, box[1] - pad_px, box[2] + pad_px, box[3] + pad_px]
    clipped = False
    if image_size is not None:
        box, clipped = clamp_xyxy(box, image_size)
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return {"bbox_xyxy": box, "bbox_format": fmt, "clipped": clipped}
