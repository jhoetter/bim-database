"""Tests for the wall-corner detection helper (classic-CV positional prior)."""
import numpy as np
from PIL import Image

from api.corner_detect import detect_wall_corners, check_corner


def _make_rect_image(
    w=600, h=400, rect=(120, 90, 470, 320), thickness=12, thin_line=True
):
    """White canvas with a black RECTANGLE OUTLINE of known thickness.

    Optionally adds a 1px thin line to prove thin-line rejection.
    """
    arr = np.full((h, w), 255, dtype=np.uint8)
    x0, y0, x1, y1 = rect
    t = thickness
    # four thick sides
    arr[y0:y0 + t, x0:x1] = 0          # top
    arr[y1 - t:y1, x0:x1] = 0          # bottom
    arr[y0:y1, x0:x0 + t] = 0          # left
    arr[y0:y1, x1 - t:x1] = 0          # right
    if thin_line:
        # a 1px-wide line crossing the whole image (a "dimension" line)
        arr[h // 2:h // 2 + 1, :] = 0
        arr[:, w // 4:w // 4 + 1] = 0
    return Image.fromarray(arr, mode="L").convert("RGB")


def _nearest(corners, target):
    return min(
        (((cx - target[0]) ** 2 + (cy - target[1]) ** 2) ** 0.5, (cx, cy))
        for (cx, cy) in corners
    )


def test_detect_rectangle_four_corners():
    rect = (120, 90, 470, 320)
    img = _make_rect_image(rect=rect, thickness=12, thin_line=True)
    corners = detect_wall_corners(img, min_wall_px=10)
    assert corners, "expected at least the 4 rectangle corners"

    # the four true outer corners
    truth = [
        (rect[0], rect[1]),
        (rect[2], rect[1]),
        (rect[0], rect[3]),
        (rect[2], rect[3]),
    ]
    for t in truth:
        d, near = _nearest(corners, t)
        assert d <= 10 + 4, f"no corner within tolerance of {t}; nearest {near} d={d:.1f}"


def test_thin_lines_do_not_spawn_corners():
    """A canvas with ONLY thin (1px) lines must yield no wall corners."""
    arr = np.full((400, 600), 255, dtype=np.uint8)
    arr[200:201, :] = 0          # horizontal 1px
    arr[:, 150:151] = 0          # vertical 1px
    arr[100:101, 50:550] = 0     # another thin line
    img = Image.fromarray(arr, mode="L").convert("RGB")
    corners = detect_wall_corners(img, min_wall_px=10)
    assert corners == [], f"thin lines should not produce corners, got {corners}"


def test_thin_crossing_line_does_not_add_corners_near_center():
    """With a thick rect + thin cross, the thin-cross intersection should
    NOT generate a spurious corner away from the rectangle ink."""
    rect = (120, 90, 470, 320)
    img = _make_rect_image(rect=rect, thickness=12, thin_line=True)
    corners = detect_wall_corners(img, min_wall_px=10)
    # the thin cross intersection is at (w//4=150, h//2=200) — interior,
    # not on any thick wall. assert nothing lands right there.
    for (cx, cy) in corners:
        d = ((cx - 150) ** 2 + (cy - 200) ** 2) ** 0.5
        assert d > 20, f"spurious corner {cx,cy} near thin-line crossing"


def test_check_corner_sign_and_distance():
    rect = (120, 90, 470, 320)
    img = _make_rect_image(rect=rect, thickness=12, thin_line=False)
    # The morphological open shrinks the mask by ~kernel radius, so the
    # detected top-left corner sits a few px INSIDE the true (120,90)
    # ink corner — around (126,96). Probe from clearly down-right of any
    # plausible detected position so the dx/dy sign is unambiguous.
    probe = (150, 120)
    res = check_corner(img, probe[0], probe[1], search_px=60, min_wall_px=10)
    assert res["found"] is True, res
    # the corner is LEFT and UP of the probe -> dx<0, dy<0
    assert res["dx"] < 0, res
    assert res["dy"] < 0, res
    assert res["distance"] is not None and res["distance"] < 60, res
    # move_hint should mention left + up
    assert "left" in res["move_hint"] and "up" in res["move_hint"], res


def test_check_corner_far_reports_not_found():
    rect = (120, 90, 470, 320)
    img = _make_rect_image(rect=rect, thickness=12, thin_line=False)
    # dead center of the rectangle interior: far from any wall corner
    res = check_corner(img, 295, 205, search_px=30, min_wall_px=10)
    assert res["found"] is False, res


def test_empty_image_returns_empty():
    img = Image.new("RGB", (300, 200), (255, 255, 255))
    assert detect_wall_corners(img, min_wall_px=10) == []
