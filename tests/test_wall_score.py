"""Tests for objective wall-label coverage scoring (self-QA signal)."""
import numpy as np
from PIL import Image

from api.wall_score import score_walls


def _img_rect_walls(w=600, h=400, rect=(120, 90, 470, 320), t=14):
    arr = np.full((h, w), 255, dtype=np.uint8)
    x0, y0, x1, y1 = rect
    arr[y0:y0 + t, x0:x1] = 0
    arr[y1 - t:y1, x0:x1] = 0
    arr[y0:y1, x0:x0 + t] = 0
    arr[y0:y1, x1 - t:x1] = 0
    return Image.fromarray(arr, mode="L").convert("RGB"), rect


def _rect_walls(rect):
    x0, y0, x1, y1 = rect
    return [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
            ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]


def test_perfect_labels_high_scores_no_missing():
    img, rect = _img_rect_walls()
    res = score_walls(img, _rect_walls(rect), min_wall_px=10, tol_px=10)
    assert res["recall"] > 0.9, res
    assert res["precision"] > 0.8, res
    assert res["missing_regions"] == [], res
    assert res["off_ink_segments"] == [], res


def test_missing_wall_is_reported():
    """Drop one of the four walls -> recall falls AND the missing side is
    reported as a region the agent must fill."""
    img, rect = _img_rect_walls()
    walls = _rect_walls(rect)[:3]  # omit the last (left) wall
    res = score_walls(img, walls, min_wall_px=10, tol_px=10)
    assert res["recall"] < 0.85, res
    assert res["missing_regions"], "should report the uncovered wall"
    # the missing region should be on the LEFT edge (x near rect[0]=120)
    x, y, bw, bh, area = res["missing_regions"][0]
    assert abs(x - rect[0]) <= 20, res["missing_regions"]


def test_off_ink_label_is_flagged():
    """A label placed in empty space (far from any ink) is flagged off-ink."""
    img, rect = _img_rect_walls()
    walls = _rect_walls(rect) + [((200, 200), (400, 200))]  # bogus interior
    res = score_walls(img, walls, min_wall_px=10, tol_px=10)
    assert res["off_ink_segments"], "bogus label should be flagged off-ink"
    seg = res["off_ink_segments"][0]
    assert seg[4] < 0.6, res["off_ink_segments"]


def test_empty_labels_zero_recall_all_missing():
    img, rect = _img_rect_walls()
    res = score_walls(img, [], min_wall_px=10, tol_px=10)
    assert res["recall"] == 0.0, res
    assert res["missing_regions"], res


def test_close_px_bridges_intermittent_wall_ink():
    """A wall drawn with a GAP in the middle (intermittent ink) under-scores;
    close_px bridges the gap so a full-length label scores higher."""
    import numpy as np
    from PIL import Image
    from api.wall_score import score_walls
    arr = np.full((200, 400), 255, dtype=np.uint8)
    # horizontal wall y=100, x 40..360, but with a 40px ink GAP in the middle
    arr[94:106, 40:170] = 0
    arr[94:106, 230:360] = 0
    img = Image.fromarray(arr, mode="L").convert("RGB")
    wall = [((40, 100), (360, 100))]
    base = score_walls(img, wall, min_wall_px=8, tol_px=9, close_px=0)
    closed = score_walls(img, wall, min_wall_px=8, tol_px=9, close_px=51)
    # closing the gap raises recall (more of the label's ink span is covered)
    assert closed["recall"] >= base["recall"]
    assert closed["f1"] >= base["f1"]
