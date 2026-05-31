"""Objective wall-labeling QA: compare placed wall labels against the ink.

The agent needs a signal that not only says "am I right yet" but WHERE it is
wrong, so it can self-correct without a human. This computes, in source
pixels:

  - precision: fraction of LABELLED wall length that actually sits on ink
    (low precision => some labels are off the ink / wrong).
  - recall: fraction of INK wall mask that is covered by some label
    (low recall => walls are MISSING).
  - missing_regions: bounding boxes of ink-wall blobs that NO label covers
    (= "there is an unlabelled wall here"). These are the agent's to-do list.
  - off_ink_segments: labels (or label pieces) that do NOT lie on ink
    (= "this label is in the wrong place").

Classic CV positional prior only: it MEASURES agreement between the label
set and the thick-wall ink mask. The vision-LLM decides what to do about it.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

try:
    import cv2
except Exception as exc:  # pragma: no cover
    raise RuntimeError("wall_score requires opencv (cv2)") from exc

from .corner_detect import _to_gray_array, _wall_mask


def _label_mask(shape, walls, thick_px: int) -> np.ndarray:
    """Rasterize wall segments into a uint8 mask (255 on the drawn stroke)."""
    h, w = shape
    m = np.zeros((h, w), dtype=np.uint8)
    for seg in walls:
        (x0, y0), (x1, y1) = seg
        cv2.line(m, (int(round(x0)), int(round(y0))),
                 (int(round(x1)), int(round(y1))), 255, thickness=max(1, thick_px))
    return m


def _thin_wall_mask(gray, *, thresh, min_len_px: int = 40, max_thick_px: int = 7):
    """Mask of FAINT, LINE-LIKE wall ink that the thick-wall OPEN erases.

    On faint scans some real walls are drawn 1-3px thin; `_wall_mask`'s OPEN
    (kernel ~min_wall_px) removes them, so a correctly-placed faint wall scores
    as off-ink. This recovers them WITHOUT pulling in blobby furniture/text:
    threshold dark ink, then keep only connected components that are
    ELONGATED (long axis >= min_len_px AND aspect >= ~5) — i.e. strokes, not
    blobs. Returned mask is meant to be UNIONed with the thick-wall mask.
    """
    if thresh is None:
        _, binary = cv2.threshold(gray, 0, 255,
                                  cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    else:
        _, binary = cv2.threshold(gray, int(thresh), 255, cv2.THRESH_BINARY_INV)
    # bridge tiny gaps along strokes so a dashed/broken wall reads as one run
    binary = cv2.morphologyEx(
        binary, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    out = np.zeros_like(binary)
    for i in range(1, n):
        bw, bh, area = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], stats[i, cv2.CC_STAT_AREA]
        long_axis = max(bw, bh)
        short_axis = max(1, min(bw, bh))
        if long_axis < min_len_px:
            continue
        if short_axis > max_thick_px:
            continue  # thick handled by the normal mask; keep this for thin only
        if long_axis / short_axis < 5.0:
            continue  # blobby (furniture/text) -> drop
        out[labels == i] = 255
    return out


def score_walls(
    image: Image.Image,
    walls: list[tuple[tuple[float, float], tuple[float, float]]],
    *,
    region: tuple[int, int, int, int] | None = None,
    min_wall_px: int = 8,
    thresh: int | None = None,
    tol_px: int = 9,
    min_missing_area: int = 400,
    thin_aware: bool = False,
) -> dict:
    """Score a wall-label set against the thick-ink wall mask.

    Args:
      walls: list of ((x0,y0),(x1,y1)) in FULL-image source px.
      region: optional area to score within (results offset back to full img).
      tol_px: how far (px) a label may be from ink and still count as "on it"
              (dilation radius when matching) — also the label stroke width.
      min_missing_area: ignore ink blobs smaller than this when reporting
              missing_regions (drops specks/short dimension ticks).

    Returns dict: precision, recall, f1, ink_px, label_px, covered_label_px,
      covered_ink_px, missing_regions [[x,y,w,h,area],...],
      off_ink_segments [[x0,y0,x1,y1,on_frac],...].
    """
    gray, ox, oy = _to_gray_array(image, region)
    if gray.size == 0:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "ink_px": 0,
                "label_px": 0, "missing_regions": [], "off_ink_segments": []}
    ink = _wall_mask(gray, min_wall_px=min_wall_px, thresh=thresh)
    if thin_aware:
        # union in faint line-like wall ink the thick OPEN erased
        ink = cv2.bitwise_or(ink, _thin_wall_mask(gray, thresh=thresh))
    h, w = ink.shape

    # shift label coords into the (possibly cropped) working frame
    local_walls = [((x0 - ox, y0 - oy), (x1 - ox, y1 - oy))
                   for ((x0, y0), (x1, y1)) in walls]
    lab = _label_mask((h, w), local_walls, thick_px=tol_px)

    k = max(3, 2 * int(tol_px) + 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    ink_d = cv2.dilate(ink, kernel)
    lab_d = cv2.dilate(lab, kernel)

    ink_px = int((ink > 0).sum())
    label_px = int((lab > 0).sum())
    # precision: label pixels that fall on (dilated) ink
    covered_label = int(((lab > 0) & (ink_d > 0)).sum())
    # recall: ink pixels that fall under (dilated) labels
    covered_ink = int(((ink > 0) & (lab_d > 0)).sum())
    precision = covered_label / label_px if label_px else 0.0
    recall = covered_ink / ink_px if ink_px else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)

    # MISSING: ink not covered by any label -> connected components -> bboxes
    missing = ((ink > 0) & (lab_d == 0)).astype(np.uint8) * 255
    # close small gaps so a wall reads as one region
    missing = cv2.morphologyEx(
        missing, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (min_wall_px, min_wall_px)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(missing, connectivity=8)
    missing_regions = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_missing_area:
            continue
        missing_regions.append([int(x + ox), int(y + oy), int(bw), int(bh), int(area)])
    missing_regions.sort(key=lambda r: -r[4])

    # OFF-INK: per label, fraction of its stroke on ink; flag the low ones
    off_ink_segments = []
    for ((lx0, ly0), (lx1, ly1)), ((ox0, oy0), (ox1, oy1)) in zip(local_walls, walls):
        seg = _label_mask((h, w), [((lx0, ly0), (lx1, ly1))], thick_px=tol_px)
        seg_px = int((seg > 0).sum())
        if seg_px == 0:
            continue
        on = int(((seg > 0) & (ink_d > 0)).sum())
        frac = on / seg_px
        if frac < 0.6:
            off_ink_segments.append([int(ox0), int(oy0), int(ox1), int(oy1),
                                     round(frac, 2)])

    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "ink_px": ink_px,
        "label_px": label_px,
        "covered_label_px": covered_label,
        "covered_ink_px": covered_ink,
        "missing_regions": missing_regions,
        "off_ink_segments": off_ink_segments,
        "params": {"tol_px": tol_px, "min_wall_px": min_wall_px,
                   "min_missing_area": min_missing_area},
    }
