"""WS-F3 (legibility-first-quality tracker): bound chatty score payloads.

A faint scan can yield hundreds of missing/off-ink regions; the raw arrays
were 150KB+ per scoring pass (the "Output too large" spills that drove the
floorplan workers to 300-600K context). Bound them, report the omitted count.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main  # noqa: E402,F401  (load package root first; main<->routes cycle)
from api.routes_plan_state import _MAX_SCORE_REGIONS, _bound_score_lists  # noqa: E402


def test_bounds_large_lists_and_reports_omitted():
    res = {
        "missing_regions": [[i, i, 10, 10, 100] for i in range(120)],
        "off_ink_segments": [[i, i, i + 1, i + 1, 0.2] for i in range(75)],
        "precision": 0.6,
    }
    out = _bound_score_lists(res)
    assert len(out["missing_regions"]) == _MAX_SCORE_REGIONS
    assert out["missing_regions_total"] == 120
    assert out["missing_regions_truncated"] == 120 - _MAX_SCORE_REGIONS
    assert len(out["off_ink_segments"]) == _MAX_SCORE_REGIONS
    assert out["off_ink_segments_truncated"] == 75 - _MAX_SCORE_REGIONS
    assert out["precision"] == 0.6  # untouched


def test_small_lists_untouched():
    res = {"missing_regions": [[1, 1, 2, 2, 4]], "off_ink_segments": []}
    out = _bound_score_lists(res)
    assert out["missing_regions"] == [[1, 1, 2, 2, 4]]
    assert "missing_regions_total" not in out  # no truncation annotation
    assert "off_ink_segments_truncated" not in out
