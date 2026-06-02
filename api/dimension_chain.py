"""Dimension-chain positional prior.

This is deliberately NOT OCR. It finds a likely running dimension line and tick
positions in a crop so the harness vision agent can read the printed values.
On faint scans it may return found=false; it must never be treated as the
authoritative reader.
"""
from __future__ import annotations

from typing import Literal

import numpy as np

Orientation = Literal["horizontal", "vertical"]


def _dark_mask(image, *, thresh: int) -> np.ndarray:
    gray = np.asarray(image.convert("L"))
    return gray < thresh


def _runs(values: np.ndarray, *, min_len: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    for i, ok in enumerate(values):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            if i - start >= min_len:
                runs.append((start, i - 1))
            start = None
    if start is not None and len(values) - start >= min_len:
        runs.append((start, len(values) - 1))
    return runs


def _line_score(mask: np.ndarray, orientation: Orientation) -> tuple[int, int, int]:
    """Return (line_coord, score, span) in local crop coordinates."""
    h, w = mask.shape
    if orientation == "horizontal":
        scores = mask.sum(axis=1)
        coord = int(np.argmax(scores)) if h else 0
        row = mask[coord, :] if h else np.zeros((0,), dtype=bool)
        min_len = max(8, int(w * 0.08))
    else:
        scores = mask.sum(axis=0)
        coord = int(np.argmax(scores)) if w else 0
        row = mask[:, coord] if w else np.zeros((0,), dtype=bool)
        min_len = max(8, int(h * 0.08))
    runs = _runs(row, min_len=min_len)
    span = max((b - a + 1 for a, b in runs), default=0)
    return coord, int(scores[coord]) if len(scores) else 0, int(span)


def _detect_ticks(
    mask: np.ndarray,
    *,
    orientation: Orientation,
    line_coord: int,
    min_tick_px: int,
    tick_search_px: int,
) -> list[int]:
    h, w = mask.shape
    ticks: list[int] = []
    if orientation == "horizontal":
        y0 = max(0, line_coord - tick_search_px)
        y1 = min(h, line_coord + tick_search_px + 1)
        band = mask[y0:y1, :]
        counts = band.sum(axis=0)
        candidates = counts >= min_tick_px
    else:
        x0 = max(0, line_coord - tick_search_px)
        x1 = min(w, line_coord + tick_search_px + 1)
        band = mask[:, x0:x1]
        counts = band.sum(axis=1)
        candidates = counts >= min_tick_px
    for a, b in _runs(candidates, min_len=1):
        ticks.append(int(round((a + b) / 2)))
    # Merge near duplicates from thick ticks / text noise.
    merged: list[int] = []
    for t in ticks:
        if not merged or abs(t - merged[-1]) > 8:
            merged.append(t)
        else:
            merged[-1] = int(round((merged[-1] + t) / 2))
    return merged


def detect_dimension_chain(
    image,
    *,
    region: tuple[int, int, int, int] | None = None,
    orientation: Orientation | None = None,
    thresh: int = 205,
    min_line_frac: float = 0.25,
    min_tick_px: int = 12,
    tick_search_px: int = 45,
    pad_px: int = 80,
) -> dict:
    """Find a likely dimension chain in `region`.

    Returns source-pixel coordinates:
    `{found, orientation, line, ticks, crop_region, params}`.
    """
    if region is not None:
        x0, y0, x1, y1 = region
        crop = image.crop((x0, y0, x1, y1))
        ox, oy = x0, y0
    else:
        crop = image
        ox = oy = 0
    w, h = crop.size
    if w <= 0 or h <= 0:
        return _empty(region, orientation, thresh, min_line_frac, min_tick_px, tick_search_px, pad_px)

    mask = _dark_mask(crop, thresh=thresh)
    orientations: list[Orientation] = [orientation] if orientation in ("horizontal", "vertical") else ["horizontal", "vertical"]
    scored = []
    for o in orientations:
        coord, score, span = _line_score(mask, o)
        denom = w if o == "horizontal" else h
        scored.append((span / max(1, denom), score, span, coord, o))
    frac, score, span, coord, orient = max(scored, key=lambda item: (item[0], item[1]))
    found = frac >= min_line_frac
    ticks_local = _detect_ticks(
        mask,
        orientation=orient,
        line_coord=coord,
        min_tick_px=min_tick_px,
        tick_search_px=tick_search_px,
    ) if found else []

    if orient == "horizontal":
        line = [[ox, oy + coord], [ox + w, oy + coord]]
        ticks = [[ox + t, oy + coord] for t in ticks_local]
        crop_region = [
            ox,
            max(0, oy + coord - tick_search_px - pad_px),
            ox + w,
            oy + coord + tick_search_px + pad_px,
        ]
    else:
        line = [[ox + coord, oy], [ox + coord, oy + h]]
        ticks = [[ox + coord, oy + t] for t in ticks_local]
        crop_region = [
            max(0, ox + coord - tick_search_px - pad_px),
            oy,
            ox + coord + tick_search_px + pad_px,
            oy + h,
        ]
    return {
        "found": bool(found),
        "orientation": orient,
        "line": line if found else None,
        "ticks": ticks,
        "tick_count": len(ticks),
        "crop_region": [int(v) for v in crop_region] if found else (list(region) if region else [0, 0, w, h]),
        "score": {"line_dark_px": int(score), "line_span_px": int(span), "line_span_frac": round(float(frac), 4)},
        "params": {
            "region": list(region) if region else None,
            "requested_orientation": orientation,
            "thresh": thresh,
            "min_line_frac": min_line_frac,
            "min_tick_px": min_tick_px,
            "tick_search_px": tick_search_px,
            "pad_px": pad_px,
        },
        "note": "CV positional prior only; harness vision reads printed values.",
    }


def _empty(region, orientation, thresh, min_line_frac, min_tick_px, tick_search_px, pad_px):
    return {
        "found": False,
        "orientation": orientation,
        "line": None,
        "ticks": [],
        "tick_count": 0,
        "crop_region": list(region) if region else None,
        "params": {
            "region": list(region) if region else None,
            "requested_orientation": orientation,
            "thresh": thresh,
            "min_line_frac": min_line_frac,
            "min_tick_px": min_tick_px,
            "tick_search_px": tick_search_px,
            "pad_px": pad_px,
        },
        "note": "CV positional prior only; no candidate found.",
    }
