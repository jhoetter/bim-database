"""V2.5 — height-stack checksum.

A derived `heights` dict must be physically sane: the storey OK-FFB stack
increases bottom→top, and `first_mm` (the roof ridge) is the maximum.
This is the guard that would have caught the house-21 error where the EG
storey height (2.75 m) was mislabelled as the Firsthöhe.
(labeling-correctness-verification-tracker V2.5)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.fact_derivation import check_height_stack  # noqa: E402


# A real, well-formed stack (house-23-like): KG -2470 … Spitzboden +8430,
# ridge above.
GOOD = {
    "ok_ffb_kg_mm": -2470, "ok_ffb_eg_mm": 0, "ok_ffb_og_mm": 2790,
    "ok_ffb_dg_mm": 5630, "ok_ffb_spitzboden_mm": 8430,
    "traufe_mm": 6940, "first_mm": 9050,
}


def test_v2_5_good_stack_passes():
    r = check_height_stack(GOOD)
    assert r["ok"], r["problems"]
    assert "first_mm" in r["checked"]
    assert "ok_ffb_spitzboden_mm" in r["checked"]


def test_v2_5_first_below_top_storey_flagged():
    """The house-21 bug: first_mm is actually the EG storey height (2750),
    far below the upper storeys → must be flagged."""
    bad = {
        "ok_ffb_kg_mm": -2470, "ok_ffb_eg_mm": 0, "ok_ffb_og_mm": 2790,
        "ok_ffb_dg_mm": 5630, "first_mm": 2750,  # WRONG: below OG/DG
    }
    r = check_height_stack(bad)
    assert not r["ok"]
    assert any("first_mm" in p for p in r["problems"]), r["problems"]


def test_v2_5_non_monotonic_stack_flagged():
    bad = {"ok_ffb_eg_mm": 0, "ok_ffb_og_mm": 2790, "ok_ffb_dg_mm": 1000}
    r = check_height_stack(bad)
    assert not r["ok"]
    assert any("not increasing" in p for p in r["problems"]), r["problems"]


def test_v2_5_ridge_below_eaves_flagged():
    bad = {"ok_ffb_eg_mm": 0, "traufe_mm": 6940, "first_mm": 5000}
    r = check_height_stack(bad)
    assert not r["ok"]
    assert any("traufe" in p for p in r["problems"]), r["problems"]


def test_v2_5_partial_stack_ok():
    """Only a couple of keys present → still passes if consistent."""
    r = check_height_stack({"ok_ffb_eg_mm": 0, "first_mm": 7000})
    assert r["ok"], r["problems"]


def test_v2_5_empty_is_ok():
    """No height data → nothing to contradict → ok (vacuously)."""
    assert check_height_stack({})["ok"]


def test_v2_5_tolerance_allows_small_slack():
    """A ridge a few mm under the top storey (drafting rounding) is ok."""
    r = check_height_stack({"ok_ffb_dg_mm": 5630, "first_mm": 5600}, tol_mm=50)
    assert r["ok"], r["problems"]
