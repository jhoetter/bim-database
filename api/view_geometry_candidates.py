"""Section/elevation geometry candidates.

Classic-CV priors for views where the agent needs component_line and
view_opening suggestions. These helpers do not classify architecture; they
return bounded, inspectable candidates from dark linework.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def _runs(values: np.ndarray, *, min_len: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = None
    for idx, value in enumerate(values):
        ok = bool(value)
        if ok and start is None:
            start = idx
        elif not ok and start is not None:
            if idx - start >= min_len:
                out.append((start, idx - 1))
            start = None
    if start is not None and len(values) - start >= min_len:
        out.append((start, len(values) - 1))
    return out


def _bbox_from_points(points: list[list[int]], pad: int = 18) -> list[int]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]


def view_geometry_candidates(
    image,
    *,
    region: tuple[int, int, int, int] | None = None,
    thresh: int = 185,
    min_line_px: int = 80,
    min_rect_px: int = 18,
    max_candidates: int = 40,
) -> dict[str, Any]:
    if region is not None:
        x0, y0, x1, y1 = region
        crop = image.crop((x0, y0, x1, y1))
        ox, oy = x0, y0
    else:
        crop = image
        ox = oy = 0
    gray = np.asarray(crop.convert("L"))
    mask = gray < thresh
    h, w = mask.shape
    candidates: list[dict[str, Any]] = []

    # Long horizontal/vertical dark runs: component lines for slabs, roof
    # edges, terrain, facade edges, or section cuts.
    for y in range(h):
        for a, b in _runs(mask[y, :], min_len=min_line_px):
            pts = [[ox + a, oy + y], [ox + b, oy + y]]
            candidates.append({
                "candidate_id": f"VIEW-{len(candidates) + 1:03d}",
                "kind": "component_line",
                "orientation": "horizontal",
                "confidence": "medium",
                "points": pts,
                "region": _bbox_from_points(pts),
                "suggested_label": {
                    "type": "component_line",
                    "geometry": {"polyline": pts},
                    "attributes": {"line_kind": "other"},
                },
                "instruction": "Inspect and set line_kind (first/traufe/geschoss/gelaende/etc.) before applying.",
            })
            if len(candidates) >= max_candidates:
                return _result(candidates, region, thresh, min_line_px, min_rect_px)
    for x in range(w):
        for a, b in _runs(mask[:, x], min_len=min_line_px):
            pts = [[ox + x, oy + a], [ox + x, oy + b]]
            candidates.append({
                "candidate_id": f"VIEW-{len(candidates) + 1:03d}",
                "kind": "component_line",
                "orientation": "vertical",
                "confidence": "medium",
                "points": pts,
                "region": _bbox_from_points(pts),
                "suggested_label": {
                    "type": "component_line",
                    "geometry": {"polyline": pts},
                    "attributes": {"line_kind": "gebaeudekante"},
                },
                "instruction": "Inspect and set line_kind before applying.",
            })
            if len(candidates) >= max_candidates:
                return _result(candidates, region, thresh, min_line_px, min_rect_px)

    # Rectangular openings: scan connected components of dark pixels. This is
    # deliberately conservative and only suggests small/medium boxed regions.
    try:
        import cv2  # type: ignore
        num, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype("uint8"), 8)
        for comp in range(1, num):
            x, y, ww, hh, area = [int(v) for v in stats[comp]]
            if ww < min_rect_px or hh < min_rect_px:
                continue
            if ww > w * 0.45 or hh > h * 0.45:
                continue
            fill = area / max(1, ww * hh)
            if fill < 0.08 or fill > 0.75:
                continue
            top = [[ox + x, oy + y], [ox + x + ww, oy + y]]
            bottom = [[ox + x, oy + y + hh], [ox + x + ww, oy + y + hh]]
            candidates.append({
                "candidate_id": f"VIEW-{len(candidates) + 1:03d}",
                "kind": "view_opening",
                "confidence": "low" if fill < 0.18 else "medium",
                "region": [ox + x - 12, oy + y - 12, ox + x + ww + 12, oy + y + hh + 12],
                "suggested_label": {
                    "type": "view_opening",
                    "geometry": {"top_edge": top, "bottom_edge": bottom},
                    "attributes": {"opening_kind": "window", "frame_visible": True},
                },
                "evidence": {"bbox_px": [ox + x, oy + y, ox + x + ww, oy + y + hh], "fill_fraction": round(float(fill), 3)},
                "instruction": "Inspect before applying; false positives can be furniture, text boxes, hatching, or title-block lines.",
            })
            if len(candidates) >= max_candidates:
                break
    except Exception:
        pass
    return _result(candidates, region, thresh, min_line_px, min_rect_px)


def _result(candidates: list[dict[str, Any]], region, thresh: int, min_line_px: int, min_rect_px: int) -> dict[str, Any]:
    return {
        "candidate_contract": "view-geometry-candidates/v1",
        "count": len(candidates),
        "candidates": candidates,
        "params": {
            "region": list(region) if region else None,
            "thresh": thresh,
            "min_line_px": min_line_px,
            "min_rect_px": min_rect_px,
        },
        "note": "CV positional prior only; inspect candidates before writing labels.",
    }
