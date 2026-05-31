"""Tests for api/wall_geometry.py — the proven labeling patterns as real tools
(bim-agent tracker B2.1/B2.2/B2.3). Pure-geometry tests need no image; the
image-dependent tools use small synthetic drawings.
"""
import math

from PIL import Image, ImageDraw

from api.wall_geometry import (
    apply_candidate,
    building_silhouette,
    connect_corners,
    propose_wall_edit,
    rectilinearize,
    _intersect,
    _line_through,
    _poly_area,
)

TEST_PARAMS = {"min_wall_px": 5, "tol_px": 8, "close_px": 0}


# --------------------------- pure geometry --------------------------- #
def test_intersect_basic():
    l1 = _line_through((0, 0), (10, 0))   # y = 0
    l2 = _line_through((5, -3), (5, 7))   # x = 5
    assert _intersect(l1, l2) == (5.0, 0.0)


def test_intersect_parallel_returns_none():
    l1 = _line_through((0, 0), (10, 0))
    l2 = _line_through((0, 4), (10, 4))
    assert _intersect(l1, l2) is None


def test_connect_corners_closed_square_from_overshooting_edges():
    # Four edges that OVERSHOOT (don't meet exactly); corners must be the
    # intersections, producing an exact closed unit-ish square 0..100.
    edges = [
        ((-5, 0), (105, 0)),      # bottom, y=0
        ((100, -5), (100, 105)),  # right,  x=100
        ((105, 100), (-5, 100)),  # top,    y=100
        ((0, 105), (0, -5)),      # left,   x=0
    ]
    walls = connect_corners(edges, closed=True)
    assert len(walls) == 4
    corners = [w[0] for w in walls]
    assert (0.0, 0.0) in [(round(x), round(y)) for x, y in corners]
    assert (100.0, 0.0) in [(round(x), round(y)) for x, y in corners]
    assert (100.0, 100.0) in [(round(x), round(y)) for x, y in corners]
    assert (0.0, 100.0) in [(round(x), round(y)) for x, y in corners]
    # closed by construction: each wall's end == next wall's start
    for i in range(4):
        assert walls[i][1] == walls[(i + 1) % 4][0]


def test_connect_corners_honors_tilt_not_equal_y():
    # A tilted bottom edge: the corner is the line intersection, NOT equal-y.
    edges = [
        ((0, 0), (100, 10)),       # tilted bottom
        ((100, 5), (100, 100)),    # right vertical
        ((100, 100), (0, 100)),    # top
        ((0, 100), (0, 0)),        # left
    ]
    walls = connect_corners(edges, closed=True)
    # corner between tilted-bottom and right-vertical sits on x=100 at y≈10
    c = walls[1][0]
    assert abs(c[0] - 100.0) < 1e-6
    assert abs(c[1] - 10.0) < 1.0  # tilt honored, not snapped to 0


def test_connect_corners_open_keeps_free_ends():
    edges = [((0, 0), (50, 0)), ((40, 0), (40, 50))]
    walls = connect_corners(edges, closed=False)
    assert walls[0][0] == (0.0, 0.0)        # first free end kept
    assert walls[-1][1] == (40.0, 50.0)     # last free end kept


def test_rectilinearize_cleans_stepped_polygon():
    # Noisy L-shape (corners off by a few px, edges slightly tilted) → clean steps.
    noisy = [
        (2, 1), (98, -2), (101, 49), (51, 52),
        (48, 98), (-1, 101),
    ]
    clean = rectilinearize(noisy, angle_tol_deg=20)
    assert len(clean) == 6
    # every consecutive edge is axis-aligned (dx≈0 or dy≈0)
    n = len(clean)
    for i in range(n):
        x0, y0 = clean[i]
        x1, y1 = clean[(i + 1) % n]
        assert abs(x1 - x0) < 1.0 or abs(y1 - y0) < 1.0


def test_rectilinearize_keeps_real_diagonal():
    # A genuine 45° edge must NOT be snapped to an axis.
    tri = [(0, 0), (100, 0), (0, 100)]
    out = rectilinearize(tri, angle_tol_deg=18)
    # the hypotenuse from (100,0)->(0,100) stays diagonal
    assert any(abs((b[1] - a[1])) > 5 and abs((b[0] - a[0])) > 5
               for a, b in zip(out, out[1:] + out[:1]))


def test_poly_area_square():
    assert abs(abs(_poly_area([(0, 0), (10, 0), (10, 10), (0, 10)])) - 100.0) < 1e-6


def test_apply_candidate_add_move_delete():
    walls = [((0, 0), (10, 0))]
    added = apply_candidate(walls, {"op": "add", "wall": [[10, 0], [10, 10]]})
    assert len(added) == 2 and added[1] == ((10, 0), (10, 10))
    moved = apply_candidate(added, {"op": "move", "index": 0, "wall": [[0, 0], [20, 0]]})
    assert moved[0] == ((0, 0), (20, 0))
    deleted = apply_candidate(moved, {"op": "delete", "index": 1})
    assert len(deleted) == 1


# --------------------------- image-backed --------------------------- #
def _square_walls_image(n=400, m=40, t=8):
    """White image with a black square ring [m, n-m] of stroke width t."""
    img = Image.new("RGB", (n, n), "white")
    d = ImageDraw.Draw(img)
    a, b = m, n - m
    for (x0, y0, x1, y1) in [(a, a, b, a), (b, a, b, b), (b, b, a, b), (a, b, a, a)]:
        d.line([(x0, y0), (x1, y1)], fill="black", width=t)
    return img, [((a, a), (b, a)), ((b, a), (b, b)), ((b, b), (a, b)), ((a, b), (a, a))]


def test_propose_wall_edit_add_missing_improves():
    img, full = _square_walls_image()
    three = full[:3]                       # one wall missing
    cand = {"op": "add", "wall": [[full[3][0][0], full[3][0][1]],
                                  [full[3][1][0], full[3][1][1]]]}
    res = propose_wall_edit(img, three, cand, params=TEST_PARAMS)
    assert res["applied"] is True
    assert res["after"]["recall"] > res["before"]["recall"]
    assert len(res["walls_after"]) == 4


def test_propose_wall_edit_delete_real_wall_rejected():
    # Deleting a REAL wall lowers recall → must be rejected (never delete-to-win).
    img, full = _square_walls_image()
    res = propose_wall_edit(img, full, {"op": "delete", "index": 0}, params=TEST_PARAMS)
    assert res["applied"] is False
    assert res["gain"] <= 0


def test_building_silhouette_separates_two_masses():
    # Two filled black boxes with a clear gap → two masses, rectilinear polygons.
    img = Image.new("RGB", (600, 300), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 240, 240], outline="black", width=14)
    d.rectangle([360, 80, 540, 240], outline="black", width=14)
    res = building_silhouette(img, min_wall_px=6, angle_tol_deg=20)
    assert res["count"] == 2
    for mass in res["masses"]:
        assert len(mass["polygon"]) >= 4
        assert mass["area"] > 0
    # largest first
    assert res["masses"][0]["area"] >= res["masses"][1]["area"]
