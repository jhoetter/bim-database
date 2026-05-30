"""Measurement-as-oracle cross-checks (pure): collinearity, chain-sum,
tick↔feature alignment. The metric correctness layer over score-walls."""
from api.measure_check import (
    chain_collinear,
    chain_sum_consistent,
    ticks_align_with_features,
    score_chain,
)


# ── collinearity ──────────────────────────────────────────────────────────

def test_collinear_chain_passes():
    res = chain_collinear([900, 902, 899, 901], tol_px=6)
    assert res["collinear"] is True
    assert res["spread_px"] == 3.0


def test_noncollinear_chain_flagged():
    res = chain_collinear([900, 902, 940], tol_px=6)
    assert res["collinear"] is False
    assert res["spread_px"] == 40.0


def test_collinear_trivial_single_tick():
    assert chain_collinear([900])["collinear"] is True


# ── chain sum ─────────────────────────────────────────────────────────────

def test_chain_sum_consistent():
    # 1.50 + 3.63 + 3.50 = 8.63 m
    res = chain_sum_consistent([1500, 3634, 3500], 8634)
    assert res["consistent"] is True
    assert res["delta_mm"] == 0.0


def test_chain_sum_misread_part_flagged():
    # second part misread 3634 -> 363 ; sum 5363 vs total 8634
    res = chain_sum_consistent([1500, 363, 3500], 8634)
    assert res["consistent"] is False
    assert res["delta_mm"] > 3000


def test_chain_sum_within_abs_floor():
    # 20mm off on a big total -> within the 30mm absolute floor
    res = chain_sum_consistent([1500, 3634, 3480], 8634)
    assert res["consistent"] is True


def test_chain_sum_guards_bad_total():
    assert chain_sum_consistent([100], 0)["consistent"] is False


# ── tick ↔ feature alignment ──────────────────────────────────────────────

def test_ticks_align_with_walls():
    ticks = [120, 627, 1163]
    walls = [118, 630, 1160]      # each within 8px
    res = ticks_align_with_features(ticks, walls, tol_px=8)
    assert res["matched"] is True
    assert res["n_matched"] == 3
    assert res["unmatched_ticks"] == []


def test_misplaced_wall_leaves_tick_unmatched():
    ticks = [120, 627, 1163]
    walls = [118, 730, 1160]      # middle wall 103px off the 627 tick
    res = ticks_align_with_features(ticks, walls, tol_px=8)
    assert res["matched"] is False
    assert len(res["unmatched_ticks"]) == 1
    u = res["unmatched_ticks"][0]
    assert u["pos"] == 627.0
    assert u["dist"] > 100


def test_no_features_all_unmatched():
    res = ticks_align_with_features([120, 627], [], tol_px=8)
    assert res["matched"] is False
    assert res["n_matched"] == 0
    assert res["match_frac"] == 0.0


# ── combined ──────────────────────────────────────────────────────────────

def test_score_chain_all_good():
    res = score_chain(
        tick_positions=[120, 627, 1163],
        cross_axis_positions=[900, 901, 902],
        part_values_mm=[1500, 3634, 3500],
        total_mm=8634,
        feature_positions=[118, 630, 1160],
    )
    assert res["ok"] is True


def test_score_chain_fails_on_misplaced_wall():
    res = score_chain(
        tick_positions=[120, 627, 1163],
        cross_axis_positions=[900, 901, 902],
        part_values_mm=[1500, 3634, 3500],
        total_mm=8634,
        feature_positions=[118, 730, 1160],   # middle wall wrong
    )
    assert res["ok"] is False
    assert res["alignment"]["matched"] is False
    # the metric chain itself is fine — only placement is wrong
    assert res["sum"]["consistent"] is True
    assert res["collinear"]["collinear"] is True
