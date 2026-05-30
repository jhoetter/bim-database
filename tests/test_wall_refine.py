"""Tests for sub-pixel, angle-aware wall-band refinement."""
import numpy as np
from PIL import Image

from api.wall_refine import refine_segment, line_intersection


def _img_with_band(w=600, h=400, p0=(80, 200), p1=(520, 200), thickness=14):
    """White canvas with a thick dark BAND from p0 to p1 (a wall)."""
    arr = np.full((h, w), 255, dtype=np.uint8)
    x0, y0 = p0
    x1, y1 = p1
    length = int(np.hypot(x1 - x0, y1 - y0))
    for i in range(length + 1):
        t = i / length
        cx = x0 + (x1 - x0) * t
        cy = y0 + (y1 - y0) * t
        rr = thickness // 2
        yy0, yy1 = int(cy - rr), int(cy + rr + 1)
        xx0, xx1 = int(cx - rr), int(cx + rr + 1)
        arr[max(0, yy0):yy1, max(0, xx0):xx1] = 0
    return Image.fromarray(arr, mode="L").convert("RGB")


def test_refine_snaps_offset_horizontal_band():
    """A horizontal band at y=200; a guess 8px off must snap back to ~200."""
    img = _img_with_band(p0=(80, 200), p1=(520, 200), thickness=14)
    res = refine_segment(img, (80, 208), (520, 208), search_px=22, n_samples=25)
    assert res["confidence"] > 0.6, res
    assert abs(res["start"][1] - 200) <= 2, res
    assert abs(res["end"][1] - 200) <= 2, res
    assert 10 <= res["thickness_px"] <= 18, res


def test_refine_recovers_tilt_angle():
    """A band tilted ~4 degrees; an axis-aligned guess must be corrected to
    follow the true tilt (this is the key fix for skewed scans)."""
    import math
    ang = math.radians(4.0)
    cx0, cy0 = 80, 200
    L = 440
    p1 = (cx0 + L * math.cos(ang), cy0 + L * math.sin(ang))
    img = _img_with_band(p0=(cx0, cy0), p1=(int(p1[0]), int(p1[1])), thickness=14)
    # guess a FLAT horizontal segment over the same x-extent
    res = refine_segment(img, (cx0, cy0), (int(p1[0]), cy0),
                         search_px=26, n_samples=25)
    assert res["confidence"] > 0.6, res
    assert res["angle_deg"] is not None, res
    # recovered angle should be near +4 deg (within 1.5 deg)
    assert abs(res["angle_deg"] - 4.0) <= 1.5, res
    # far endpoint must have moved DOWN toward the true tilted band
    assert res["end"][1] > cy0 + 8, res


def test_refine_no_band_low_confidence():
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    res = refine_segment(img, (50, 150), (350, 150))
    assert res["confidence"] == 0.0, res
    assert res["n_found"] == 0, res


def test_line_intersection_basic():
    # x=10 vertical meets y=20 horizontal at (10,20)
    p = line_intersection((10, 0), (10, 100), (0, 20), (100, 20))
    assert p == (10, 20), p
    # parallel -> None
    assert line_intersection((0, 0), (10, 0), (0, 5), (10, 5)) is None
