"""Stage 3 — rectify (deskew + dewarp + crop-to-sheet).

Returns a `Rectified` record per page. The default backend is classical
OpenCV: find the largest 4-point sheet contour, warp it to a flat
rectangle, fall back to a small deskew rotation if no contour is found,
fall back to passthrough if both fail.

The interface is structured so a learned dewarp model can be plugged in
later — just register another method that takes a PIL image + returns a
warped PIL image, and accept its name in the config enum.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass
class Rectified:
    image: Image.Image
    method: str  # "passthrough" | "deskew" | "perspective_contour" | "perspective_lines" | "learned"
    succeeded: bool


def rectify(image: Image.Image, method: str = "perspective_contour", is_native_pdf: bool = False) -> Rectified:
    if is_native_pdf:
        return Rectified(image=image, method="passthrough", succeeded=True)

    if method == "passthrough":
        return Rectified(image=image, method="passthrough", succeeded=True)

    # Try perspective rectification, then fall back to a deskew rotation,
    # then to passthrough. We avoid raising — the caller wants a usable
    # image either way and the per-page manifest records what we did.
    try:
        result = _perspective_contour(image)
        if result is not None:
            return Rectified(image=result, method="perspective_contour", succeeded=True)
    except Exception:  # noqa: BLE001
        pass

    try:
        result = _deskew(image)
        if result is not None:
            return Rectified(image=result, method="deskew", succeeded=True)
    except Exception:  # noqa: BLE001
        pass

    return Rectified(image=image, method="passthrough", succeeded=False)


def _perspective_contour(image: Image.Image) -> Image.Image | None:
    """Largest-quadrilateral perspective warp. Returns None when we can't
    confidently identify a sheet."""
    try:
        import cv2  # type: ignore
    except ImportError:
        return None

    arr = np.array(image.convert("RGB"))
    h, w = arr.shape[:2]
    # Downscale for contour search; warp the original at full res.
    scale = 1024.0 / max(h, w) if max(h, w) > 1024 else 1.0
    small = cv2.resize(arr, (int(w * scale), int(h * scale))) if scale != 1.0 else arr
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 180)
    # Close small gaps.
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    frame_area = small.shape[0] * small.shape[1]
    quad = None
    for c in contours[:8]:
        if cv2.contourArea(c) < frame_area * 0.20:
            break
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            quad = approx.reshape(4, 2).astype(np.float32) / scale
            break
    if quad is None:
        return None

    rect = _order_quad(quad)
    (tl, tr, br, bl) = rect
    w_top = np.linalg.norm(tr - tl)
    w_bot = np.linalg.norm(br - bl)
    h_left = np.linalg.norm(bl - tl)
    h_right = np.linalg.norm(br - tr)
    target_w = int(max(w_top, w_bot))
    target_h = int(max(h_left, h_right))
    if target_w < 50 or target_h < 50:
        return None

    dst = np.array(
        [[0, 0], [target_w - 1, 0], [target_w - 1, target_h - 1], [0, target_h - 1]],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(arr, M, (target_w, target_h))
    return Image.fromarray(warped)


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Order four corner points as (top-left, top-right, bottom-right, bottom-left)."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _deskew(image: Image.Image) -> Image.Image | None:
    """Pure rotation-only deskew via dominant Hough line angle. Used as a
    fallback when the perspective warp can't lock onto a quad — typical
    of scans where the page edge runs off-frame."""
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    arr = np.array(image.convert("L"))
    edges = cv2.Canny(arr, 60, 180)
    lines = cv2.HoughLines(edges, 1, np.pi / 360.0, threshold=150)
    if lines is None or len(lines) == 0:
        return None
    deg = np.degrees(lines[:, 0, 1])
    deg = (deg + 45.0) % 90.0 - 45.0
    angle = float(np.median(deg))
    if abs(angle) < 0.3:
        return image  # already straight
    if abs(angle) > 15:
        return None  # likely a Hough misfire; refuse to rotate that far
    return image.rotate(-angle, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))
