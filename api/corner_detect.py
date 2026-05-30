"""Wall corner / junction detection helper (classic CV positional prior).

Per project rules, classic CV is allowed ONLY as a positional prior /
cross-check that proposes candidate coordinates; the vision-LLM agent
remains the authoritative judge of which candidates are real walls and
how to connect them.

The core idea: hand-drawn floorplans have THICK wall strokes (~10-18px)
and THIN annotation lines (~1-3px: dimensions, furniture, hatching).
A morphological OPEN with a kernel sized ~min_wall_px erases the thin
lines while preserving thick walls. Polygon-approximated contour
vertices of the surviving wall mask are returned as candidate corners,
in FULL-image source-pixel coordinates.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image

try:
    import cv2
except Exception as exc:  # pragma: no cover - hard dependency
    raise RuntimeError("corner_detect requires opencv (cv2)") from exc


def _to_gray_array(image: Image.Image, region) -> tuple[np.ndarray, int, int]:
    """Return (grayscale uint8 array, offset_x, offset_y) for the working area.

    Works at native resolution within the region (no downscale) so corner
    coordinates stay pixel-accurate.
    """
    if region is not None:
        x0, y0, x1, y1 = region
        x0, x1 = sorted((int(x0), int(x1)))
        y0, y1 = sorted((int(y0), int(y1)))
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(image.width, x1)
        y1 = min(image.height, y1)
        crop = image.crop((x0, y0, x1, y1))
        ox, oy = x0, y0
    else:
        crop = image
        ox, oy = 0, 0
    gray = np.asarray(crop.convert("L"), dtype=np.uint8)
    return gray, ox, oy


def _wall_mask(gray: np.ndarray, *, min_wall_px: int, thresh: Optional[int]) -> np.ndarray:
    """Binary mask (uint8 0/255) of THICK dark wall ink.

    1. Threshold dark ink -> foreground.
    2. Morphological OPEN with a kernel ~min_wall_px to erase thin lines.
    """
    if thresh is None:
        # Otsu: dark ink becomes foreground (THRESH_BINARY_INV).
        _, binary = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )
    else:
        _, binary = cv2.threshold(gray, int(thresh), 255, cv2.THRESH_BINARY_INV)

    # Kernel sized to wall thickness: thin annotation lines (narrower than
    # the kernel) are eroded away; thick walls survive the open.
    k = max(3, int(min_wall_px))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    return opened


def _cluster_points(points: list[tuple[int, int]], radius: int) -> list[tuple[int, int]]:
    """Merge points within `radius` into their centroid. Greedy single-pass."""
    if not points:
        return []
    pts = [np.array([float(x), float(y)]) for x, y in points]
    used = [False] * len(pts)
    clusters: list[tuple[int, int]] = []
    r2 = float(radius) ** 2
    for i in range(len(pts)):
        if used[i]:
            continue
        group = [pts[i]]
        used[i] = True
        for j in range(i + 1, len(pts)):
            if used[j]:
                continue
            # compare to any member of the growing group (chaining)
            if any(float(np.sum((pts[j] - g) ** 2)) <= r2 for g in group):
                group.append(pts[j])
                used[j] = True
        c = np.mean(np.stack(group), axis=0)
        clusters.append((int(round(c[0])), int(round(c[1]))))
    return clusters


def detect_wall_outline(
    image: Image.Image,
    region=None,
    *,
    min_wall_px: int = 8,
    thresh: Optional[int] = None,
    n_outlines: int = 2,
    epsilon_px: float = 8.0,
    min_area_frac: float = 0.02,
    close_px: int | None = None,
) -> list[dict]:
    """Ordered outer-boundary polygon(s) of the thick-wall ink.

    Unlike `detect_wall_corners` (a scattered point cloud the agent must
    connect itself), this returns the OUTER face of each connected wall
    structure as an *ordered* polygon, so each consecutive vertex pair is one
    wall segment. Far more robust for placing outer walls, and it naturally
    separates disjoint structures (the main block vs. the garage wing each
    become their own polygon). Keep `min_wall_px` small (6-10) so faint outer
    walls a large kernel would erase are preserved; furniture blobs are smaller
    than the footprint and so drop out of the top-`n_outlines` largest
    contours.

    Returns up to `n_outlines` dicts, largest area first:
        {"polygon": [[x, y], ...], "area": int, "n_vertices": int}
    Coordinates are integer FULL-image SOURCE pixels tracing the outer face.
    Positional prior only; the vision-LLM judges/prunes furniture bumps and
    decides thickness.
    """
    gray, ox, oy = _to_gray_array(image, region)
    if gray.size == 0:
        return []
    mask = _wall_mask(gray, min_wall_px=min_wall_px, thresh=thresh)
    if int(mask.sum()) == 0:
        return []
    # Door/window openings break the wall ring into disconnected fragments,
    # each too small to be the footprint. A morphological CLOSE bridges those
    # gaps so the outer ring reconnects into one contour per structure. The
    # kernel must exceed the widest opening (a door ~0.9m); default scales
    # generously with wall thickness and is overridable.
    ck = int(close_px) if close_px is not None else max(31, int(min_wall_px) * 12)
    if ck > 0:
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ck, ck)),
        )
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return []
    h, w = mask.shape
    min_area = float(min_area_frac) * float(h) * float(w)
    out: list[dict] = []
    for cnt in sorted(contours, key=cv2.contourArea, reverse=True):
        area = float(cv2.contourArea(cnt))
        if area < min_area:
            continue
        approx = cv2.approxPolyDP(cnt, float(epsilon_px), True)
        poly = [
            [
                int(min(max(0, int(p[0][0]) + ox), image.width - 1)),
                int(min(max(0, int(p[0][1]) + oy), image.height - 1)),
            ]
            for p in approx
        ]
        out.append({"polygon": poly, "area": int(area), "n_vertices": len(poly)})
        if len(out) >= int(n_outlines):
            break
    return out


def detect_wall_corners(
    image: Image.Image,
    region=None,
    *,
    min_wall_px: int = 8,
    thresh: Optional[int] = None,
    max_dim: int = 2318,
) -> list[tuple[int, int]]:
    """Detect candidate wall-corner coordinates in FULL-image source pixels.

    Returns a deduplicated list of (x, y) ints sorted top-to-bottom then
    left-to-right. Returns [] gracefully if nothing is found.

    `max_dim` is accepted for API symmetry; detection runs at native
    resolution within the region for coordinate accuracy and does not
    downscale below source.
    """
    gray, ox, oy = _to_gray_array(image, region)
    if gray.size == 0:
        return []

    mask = _wall_mask(gray, min_wall_px=min_wall_px, thresh=thresh)
    if int(mask.sum()) == 0:
        return []

    h, w = gray.shape

    # Contour vertices via approxPolyDP = architectural corners.
    contours, _ = cv2.findContours(
        mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    raw: list[tuple[int, int]] = []
    # ignore tiny specks: area must be plausible for a wall fragment
    min_area = float(min_wall_px) * float(min_wall_px) * 2.0
    epsilon_px = max(2.0, float(min_wall_px) * 0.6)
    for cnt in contours:
        if cv2.contourArea(cnt) < min_area:
            continue
        approx = cv2.approxPolyDP(cnt, epsilon_px, True)
        for p in approx.reshape(-1, 2):
            raw.append((int(p[0]), int(p[1])))

    # Harris cross-check: collect strong corner responses on the wall mask.
    try:
        harris = cv2.cornerHarris(np.float32(mask), blockSize=max(2, min_wall_px // 2),
                                  ksize=3, k=0.04)
        thr = 0.01 * harris.max() if harris.max() > 0 else 1e9
        ys, xs = np.where(harris > thr)
        # subsample to keep clustering cheap
        step = max(1, len(xs) // 2000)
        for i in range(0, len(xs), step):
            raw.append((int(xs[i]), int(ys[i])))
    except Exception:
        pass

    # clamp into working area
    raw = [(min(max(0, x), w - 1), min(max(0, y), h - 1)) for (x, y) in raw]

    clustered = _cluster_points(raw, radius=max(3, min_wall_px))

    # offset back to full-image source coords + clamp to image bounds
    out = []
    for (x, y) in clustered:
        fx = min(max(0, x + ox), image.width - 1)
        fy = min(max(0, y + oy), image.height - 1)
        out.append((int(fx), int(fy)))

    out.sort(key=lambda p: (p[1], p[0]))
    return out


def check_corner(
    image: Image.Image,
    x: int,
    y: int,
    *,
    search_px: int = 40,
    min_wall_px: int = 8,
) -> dict:
    """Find the nearest detected corner within `search_px` of (x, y).

    Returns a dict with movement hints. dx>0 => true corner is to the
    RIGHT of (x,y); dy>0 => true corner is BELOW (image y grows down).
    Detection runs on a local region around (x,y) for speed.
    """
    x = int(round(x))
    y = int(round(y))
    pad = int(search_px) + 4 * int(min_wall_px)
    region = (x - pad, y - pad, x + pad, y + pad)
    corners = detect_wall_corners(
        image, region=region, min_wall_px=min_wall_px
    )
    if not corners:
        return {
            "found": False,
            "nearest": None,
            "dx": None,
            "dy": None,
            "distance": None,
            "move_hint": "no-corner-found",
        }

    best = None
    best_d = None
    for (cx, cy) in corners:
        d = float(np.hypot(cx - x, cy - y))
        if best_d is None or d < best_d:
            best_d = d
            best = (cx, cy)

    cx, cy = best
    dx = cx - x
    dy = cy - y
    if best_d is None or best_d > float(search_px):
        return {
            "found": False,
            "nearest": [int(cx), int(cy)],
            "dx": int(dx),
            "dy": int(dy),
            "distance": round(best_d, 2),
            "move_hint": "nearest-corner-out-of-range",
        }

    if best_d <= max(2.0, float(min_wall_px) * 0.5):
        hint = "on-corner"
    else:
        parts = []
        if dx > 0:
            parts.append(f"right {dx}")
        elif dx < 0:
            parts.append(f"left {-dx}")
        if dy > 0:
            parts.append(f"down {dy}")
        elif dy < 0:
            parts.append(f"up {-dy}")
        hint = ", ".join(parts) if parts else "on-corner"

    return {
        "found": True,
        "nearest": [int(cx), int(cy)],
        "dx": int(dx),
        "dy": int(dy),
        "distance": round(best_d, 2),
        "move_hint": hint,
    }
