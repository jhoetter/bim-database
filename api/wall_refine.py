"""Sub-pixel wall-segment refinement (classic-CV positional prior).

Corner snapping gets a wall *roughly* onto the ink. To be "painfully
accurate" we must measure the THICK ink BAND itself: a wall is a dark band
~10-18px wide, not a 1px line. This module samples perpendicular intensity
profiles ALONG a candidate segment, locates the dark band in each slice, and
returns the band's measured CENTERLINE (and thickness) as corrected
endpoints. The vision-LLM remains the judge of which segments are walls and
how they connect; this only measures where the ink truly is.

Pipeline for one segment:
  1. direction d, unit normal n.
  2. sample K points along the segment (skip the ends so corners/openings
     don't bias the measurement).
  3. at each point, read the grayscale profile along n in [-search, +search];
     find the dark run nearest the current line; record its centre offset
     and width.
  4. robust-aggregate (median) the offsets -> shift the whole segment by
     median_offset * n; median width -> measured thickness in px.
Returns the shifted endpoints, measured thickness, and a confidence (fraction
of slices that actually found a band).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

try:
    import cv2
except Exception as exc:  # pragma: no cover
    raise RuntimeError("wall_refine requires opencv (cv2)") from exc


def _gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.uint8)


def _dark_run_offset(
    profile: np.ndarray, offsets: np.ndarray, thresh: float
) -> tuple[float, float] | None:
    """Given a 1-D intensity profile sampled at `offsets` (signed px from the
    current line, 0 = on the line), return (centre_offset, width) of the dark
    run closest to offset 0. None if no dark pixels."""
    dark = profile < thresh
    if not dark.any():
        return None
    # find contiguous dark runs
    idx = np.where(dark)[0]
    splits = np.where(np.diff(idx) > 1)[0]
    groups = np.split(idx, splits + 1)
    best = None
    best_dist = None
    for g in groups:
        if g.size == 0:
            continue
        c = float(offsets[g].mean())
        w = float(offsets[g].max() - offsets[g].min()) + 1.0
        dist = abs(c)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = (c, w)
    return best


def refine_segment(
    image: Image.Image,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    search_px: int = 22,
    n_samples: int = 25,
    skip_frac: float = 0.12,
    thresh: int | None = None,
) -> dict:
    """Measure the ink band along [start, end] and return corrected endpoints
    snapped to the band centerline.

    Returns:
        {
          "start": [x, y], "end": [x, y],      # shifted to band centerline
          "thickness_px": float,                # median measured band width
          "offset_px": float,                   # signed shift applied (along n)
          "confidence": float,                  # frac of slices with a band
          "n_found": int, "n_samples": int,
        }
    """
    gray = _gray(image)
    h, w = gray.shape
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    dx, dy = ex - sx, ey - sy
    length = float(np.hypot(dx, dy))
    if length < 1.0:
        return {
            "start": [int(round(sx)), int(round(sy))],
            "end": [int(round(ex)), int(round(ey))],
            "thickness_px": 0.0, "offset_px": 0.0,
            "confidence": 0.0, "n_found": 0, "n_samples": 0,
        }
    ux, uy = dx / length, dy / length        # unit direction
    nx, ny = -uy, ux                          # unit normal
    offsets = np.arange(-search_px, search_px + 1, dtype=np.float32)

    if thresh is None:
        # Otsu over a band around the segment to pick a dark/light cutoff.
        x0 = max(0, int(min(sx, ex)) - search_px)
        x1 = min(w, int(max(sx, ex)) + search_px + 1)
        y0 = max(0, int(min(sy, ey)) - search_px)
        y1 = min(h, int(max(sy, ey)) + search_px + 1)
        patch = gray[y0:y1, x0:x1]
        if patch.size:
            t, _ = cv2.threshold(
                np.ascontiguousarray(patch), 0, 255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU,
            )
            thresh_val = float(t)
            # Otsu degenerates (returns ~0 or ~255) on near-uniform patches;
            # fall back to a robust midpoint between the dark ink and the light
            # background so faint pencil is still separated.
            pmin = float(patch.min())
            pmax = float(patch.max())
            if thresh_val <= pmin + 1 or thresh_val >= pmax - 1:
                thresh_val = 0.5 * (pmin + pmax)
        else:
            thresh_val = 128.0
    else:
        thresh_val = float(thresh)

    lo = skip_frac
    hi = 1.0 - skip_frac
    ts = np.linspace(lo, hi, n_samples)
    centres: list[float] = []
    widths: list[float] = []
    cpts: list[tuple[float, float]] = []   # world-space band-centre points
    for t in ts:
        px = sx + ux * length * t
        py = sy + uy * length * t
        xs = (px + nx * offsets).astype(np.float32)
        ys = (py + ny * offsets).astype(np.float32)
        # nearest-pixel sampling (clamp into bounds)
        xi = np.clip(np.round(xs).astype(int), 0, w - 1)
        yi = np.clip(np.round(ys).astype(int), 0, h - 1)
        profile = gray[yi, xi].astype(np.float32)
        res = _dark_run_offset(profile, offsets, thresh_val)
        if res is not None:
            c_off, c_w = res
            centres.append(c_off)
            widths.append(c_w)
            cpts.append((px + nx * c_off, py + ny * c_off))

    n_found = len(centres)
    if n_found == 0:
        return {
            "start": [int(round(sx)), int(round(sy))],
            "end": [int(round(ex)), int(round(ey))],
            "thickness_px": 0.0, "offset_px": 0.0, "angle_deg": None,
            "confidence": 0.0, "n_found": 0, "n_samples": int(n_samples),
            "fit_line": None,
        }

    offset = float(np.median(centres))
    thickness = float(np.median(widths))

    # ANGLE-AWARE FIT: total-least-squares (PCA) line through the measured
    # band-centre points. This captures the wall's TRUE tilt (scans/drawings
    # are rarely perfectly axis-aligned) instead of forcing the original
    # direction. Falls back to the offset-only shift if too few points or a
    # degenerate fit. The corrected endpoints are the ORIGINAL endpoints
    # PROJECTED onto the fitted line, so the segment keeps its length/extent
    # but follows the real ink orientation.
    pts = np.asarray(cpts, dtype=np.float64)
    fit_line = None
    angle_deg = None
    use_fit = n_found >= max(5, n_samples // 3)
    if use_fit:
        centroid = pts.mean(axis=0)
        u, s, vt = np.linalg.svd(pts - centroid, full_matrices=False)
        direction = vt[0]
        # spread along principal axis must dominate (else it's a blob, bail)
        if s[0] > 1e-6 and (s.size < 2 or s[0] >= 3.0 * s[1]):
            dnorm = direction / (np.linalg.norm(direction) + 1e-12)

            def _proj(pt):
                v = np.array(pt, dtype=np.float64) - centroid
                t = float(np.dot(v, dnorm))
                p = centroid + t * dnorm
                return (p[0], p[1])

            ns_xy = _proj((sx, sy))
            ne_xy = _proj((ex, ey))
            nsx, nsy = ns_xy
            nex, ney = ne_xy
            angle_deg = round(float(np.degrees(np.arctan2(dnorm[1], dnorm[0]))), 2)
            fit_line = [[round(float(centroid[0]), 1), round(float(centroid[1]), 1)],
                        [round(float(dnorm[0]), 4), round(float(dnorm[1]), 4)]]
        else:
            use_fit = False
    if not use_fit:
        nsx = sx + nx * offset
        nsy = sy + ny * offset
        nex = ex + nx * offset
        ney = ey + ny * offset

    return {
        "start": [int(round(nsx)), int(round(nsy))],
        "end": [int(round(nex)), int(round(ney))],
        "thickness_px": round(thickness, 1),
        "offset_px": round(offset, 1),
        "angle_deg": angle_deg,
        "confidence": round(n_found / float(n_samples), 2),
        "n_found": n_found,
        "n_samples": int(n_samples),
        "fit_line": fit_line,
    }


def line_intersection(
    a0: tuple[float, float], a1: tuple[float, float],
    b0: tuple[float, float], b1: tuple[float, float],
) -> tuple[int, int] | None:
    """Intersection of infinite lines (a0->a1) and (b0->b1). None if parallel.

    Used to make adjacent refined walls meet at an EXACT shared corner instead
    of leaving a gap/overshoot where two independently-shifted edges cross."""
    x1, y1 = a0
    x2, y2 = a1
    x3, y3 = b0
    x4, y4 = b1
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
    return (int(round(px)), int(round(py)))
