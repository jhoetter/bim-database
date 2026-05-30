"""V3.3 scale-consistency + V3.4 closure/sanity — pure geometry predicates."""
from api.geometry_checks import (
    scale_consistent,
    segment_length_px,
    chain_is_closed,
    point_in_polygon,
    opening_on_wall,
    ridge_within_footprint,
)


# ── V3.3 ─────────────────────────────────────────────────────────────────

def test_scale_consistent_matches_printed():
    # 1000 px at 0.1 px/mm -> 10000 mm; printed 10.0 m
    res = scale_consistent(1000.0, 0.1, 10000.0)
    assert res["consistent"] is True
    assert res["derived_mm"] == 10000.0
    assert res["rel_err"] == 0.0


def test_scale_inconsistent_flagged():
    # 1000 px / 0.1 = 10000 mm but the chain says 6500 mm -> way off
    res = scale_consistent(1000.0, 0.1, 6500.0)
    assert res["consistent"] is False
    assert res["rel_err"] > 0.05


def test_scale_within_tolerance():
    # 3% off, default 5% tol -> still consistent
    res = scale_consistent(1030.0, 0.1, 10000.0)
    assert res["consistent"] is True


def test_scale_guards_bad_inputs():
    assert scale_consistent(100.0, 0.0, 1000.0)["consistent"] is False
    assert scale_consistent(100.0, 0.1, 0.0)["consistent"] is False


def test_segment_length():
    assert segment_length_px((0, 0), (3, 4)) == 5.0


# ── V3.4 closure ─────────────────────────────────────────────────────────

def test_chain_closed_explicit_and_implicit():
    # A bare square ring whose last vertex (0,100) does NOT return to the
    # first (0,0) reads as NOT closed by its endpoints.
    sq = [(0, 0), (100, 0), (100, 100), (0, 100)]
    assert chain_is_closed(sq) is False
    # Repeating the first vertex closes it.
    closed = sq + [(0, 0)]
    assert chain_is_closed(closed) is True
    # Near-closed within tolerance (last vertex ~3.6px from first).
    almost = [(0, 0), (100, 0), (100, 100), (0, 100), (3, 2)]
    assert chain_is_closed(almost, tol_px=6) is True


def test_chain_too_few_points():
    assert chain_is_closed([(0, 0), (1, 1)]) is False


def test_point_in_polygon():
    sq = [(0, 0), (100, 0), (100, 100), (0, 100)]
    assert point_in_polygon((50, 50), sq) is True
    assert point_in_polygon((150, 50), sq) is False
    assert point_in_polygon((-5, 50), sq) is False


def test_opening_on_wall_true():
    walls = [((0, 0), (200, 0)), ((200, 0), (200, 200))]
    door = ((80, 2), (120, 2))  # sits on the first (horizontal) wall
    res = opening_on_wall(door, walls, tol_px=10)
    assert res["on_wall"] is True
    assert res["wall_index"] == 0


def test_opening_off_wall_flagged():
    walls = [((0, 0), (200, 0)), ((200, 0), (200, 200))]
    floating = ((80, 90), (120, 90))  # 90px from any wall
    res = opening_on_wall(floating, walls, tol_px=10)
    assert res["on_wall"] is False
    assert res["wall_index"] is None
    assert res["max_endpoint_dist"] >= 80


def test_ridge_within_footprint_true():
    fp = [(0, 0), (200, 0), (200, 200), (0, 200), (0, 0)]
    ridge = ((50, 100), (150, 100))
    assert ridge_within_footprint(ridge, fp)["within"] is True


def test_ridge_outside_footprint_flagged():
    fp = [(0, 0), (200, 0), (200, 200), (0, 200), (0, 0)]
    ridge = ((50, 100), (300, 100))  # end pokes outside
    res = ridge_within_footprint(ridge, fp)
    assert res["within"] is False
    assert res["points"]["end"] is False
