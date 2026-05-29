"""PII flagging for customer-submitted drawings.

Title blocks / letterheads on architectural drawings frequently carry
the owner's name, the property address, the architect's stamp, and
sometimes financing notes. Before promoting a customer submission into
the corpus, those regions must be reviewed and (optionally) redacted.

This module flags a candidate bounding box; redaction is a hook the
caller can choose to apply (set a constant-colour fill, or blank the
region in the consolidated PDF).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass
class PIIFlag:
    title_block_suspected: bool
    title_block_bbox_px: tuple[float, float, float, float] | None
    """[x0, y0, x1, y1] in pixel coordinates of the rectified page image."""


def flag_title_block(image: Image.Image) -> PIIFlag:
    """Architectural drawings put the title block in the bottom-right or
    right-edge column 80% of the time. We don't try OCR — we look for a
    dense-text rectangle near those corners and return the bbox.

    Cheap heuristic on purpose: this is review bait, not detection
    truth. The developer-side promote UI shows the bbox highlighted and
    asks "redact?" before the bundle lands in incoming/.
    """
    arr = np.array(image.convert("L"), dtype=np.float32)
    h, w = arr.shape
    if h < 100 or w < 100:
        return PIIFlag(title_block_suspected=False, title_block_bbox_px=None)

    # Use a small box-mean derivative to locate dense-text regions: text
    # has low local mean compared to surrounding paper (lots of dark
    # strokes packed together).
    pad = 4
    win = 24
    p = np.pad(arr, pad, mode="edge")
    # Strided box mean approximation via cumulative sum.
    cs = p.cumsum(axis=0).cumsum(axis=1)
    A = cs[: -2 * pad, : -2 * pad]
    B = cs[2 * pad:, : -2 * pad]
    C = cs[: -2 * pad, 2 * pad:]
    D = cs[2 * pad:, 2 * pad:]
    local_mean = (D - B - C + A) / ((2 * pad) ** 2)
    # "Text density" — invert + threshold.
    text_density = 255.0 - local_mean
    threshold = float(np.percentile(text_density, 92))
    dense = text_density >= threshold

    # Bottom-right quadrant first (80%+ of the time), then right-strip.
    candidates = [
        (h // 2, h, w // 2, w),       # bottom-right quadrant
        (0, h, int(w * 0.75), w),     # right strip
        (h // 2, h, 0, w),            # bottom strip
    ]
    for y0, y1, x0, x1 in candidates:
        region = dense[y0:y1, x0:x1]
        if region.size == 0:
            continue
        coverage = float(region.mean())
        if coverage < 0.10:
            continue
        # Fit a tighter bbox around the dense pixels.
        ys, xs = np.where(region)
        if len(ys) < 50 or len(xs) < 50:
            continue
        bx0 = float(x0 + xs.min() - win)
        by0 = float(y0 + ys.min() - win)
        bx1 = float(x0 + xs.max() + win)
        by1 = float(y0 + ys.max() + win)
        return PIIFlag(
            title_block_suspected=True,
            title_block_bbox_px=(max(bx0, 0.0), max(by0, 0.0), min(bx1, float(w)), min(by1, float(h))),
        )
    return PIIFlag(title_block_suspected=False, title_block_bbox_px=None)


def redact_region(image: Image.Image, bbox_px: tuple[float, float, float, float]) -> Image.Image:
    """Blank the given pixel rectangle with solid white. Returns a fresh
    PIL image; caller is responsible for persisting it back into the
    consolidated PDF before promotion."""
    x0, y0, x1, y1 = (int(v) for v in bbox_px)
    out = image.copy()
    from PIL import ImageDraw
    draw = ImageDraw.Draw(out)
    draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255))
    return out


def page_has_dimension_text(image: Image.Image) -> bool:
    """Conservative proxy for 'this page contains numeric dimensions or
    label text'. Gates generative enhancers — the cost of a missed
    restoration is far lower than the cost of a hallucinated digit on a
    training sample, so this returns True unless we're confident the
    page is content-free (e.g. solid colour, blank scan).
    """
    arr = np.array(image.convert("L"), dtype=np.float32)
    if arr.size == 0:
        return True
    # If the image is nearly uniform (very low std), it's not a drawing.
    if float(arr.std()) < 8.0:
        return False
    # Otherwise: assume text-bearing. This is a coarse safeguard, not a
    # detector; the per-page metadata records the decision.
    return True
