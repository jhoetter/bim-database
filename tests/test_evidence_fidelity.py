"""WS-C (legibility-first-quality tracker): evidence pointers + fidelity gate.

A readable value read off a downscaled/grid SURVEY view (no read/zoom evidence
pointer) must not count toward gold. Persisting the pointer — not the pixels —
is what makes context summarization harmless.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.evidence_fidelity import (  # noqa: E402
    is_high_fidelity,
    is_low_fidelity_value,
    needs_high_fidelity,
    normalize_evidence,
)
from api.scene_plan_state import _label_quality_summary, _quality_for_state  # noqa: E402


def _read_ptr(fid="read"):
    return {"scene": "s.png", "region_bbox": "10,10,200,80", "dpi": "native", "fidelity": fid}


# ── pure classifier ──────────────────────────────────────────────────────
def test_high_fidelity_only_for_read_and_zoom():
    assert is_high_fidelity(_read_ptr("read")) is True
    assert is_high_fidelity(_read_ptr("zoom_read")) is True
    assert is_high_fidelity(_read_ptr("survey")) is False
    assert is_high_fidelity(None) is False
    assert is_high_fidelity({"scene": "s"}) is False  # unknown tier -> survey


def test_normalize_defaults_unknown_to_survey():
    n = normalize_evidence({"region": "1,2,3,4"})
    assert n["fidelity"] == "survey"
    assert n["region_bbox"] == "1,2,3,4"


def test_needs_high_fidelity_only_for_readable_values():
    assert needs_high_fidelity({"type": "dimension_number", "status": "readable"})
    assert needs_high_fidelity({"type": "height_mark", "status": "readable"})
    # walls are graded by the anchoring path, not the value gate
    assert not needs_high_fidelity({"type": "wall", "status": "readable"})
    # an honestly-uncertain value is already downgraded — not double-counted
    assert not needs_high_fidelity({"type": "dimension_number", "status": "uncertain"})


def test_is_low_fidelity_value():
    assert is_low_fidelity_value({"type": "dimension_number", "status": "readable"})
    # canonical: evidence at the label TOP LEVEL (attributes is strict schema).
    good = {"type": "dimension_number", "status": "readable", "evidence": _read_ptr("read")}
    assert not is_low_fidelity_value(good)
    # back-compat: older records that stashed it under attributes still read.
    good_attrs = {"type": "dimension_number", "status": "readable",
                  "attributes": {"evidence": _read_ptr("zoom_read")}}
    assert not is_low_fidelity_value(good_attrs)
    survey = {"type": "dimension_number", "status": "readable", "evidence": _read_ptr("survey")}
    assert is_low_fidelity_value(survey)


# ── summary counter ──────────────────────────────────────────────────────
def test_summary_counts_low_fidelity_values():
    labels = [
        {"type": "dimension_number", "status": "readable"},                      # low
        {"type": "dimension_number", "status": "readable",
         "attributes": {"evidence": _read_ptr("zoom_read")}},                    # ok
        {"type": "wall", "status": "readable"},                                  # not gated
        {"type": "height_mark", "status": "uncertain"},                          # not gated
    ]
    s = _label_quality_summary(labels)
    assert s["low_fidelity_value_total"] == 1


# ── the gate: low-fidelity values block gold ─────────────────────────────
def _state_with(low_fidelity: int) -> dict:
    return {
        "tasks": [], "defects": [],
        "current_state": {"label_quality": {
            "uncertain_total": 0, "missing_total": 0, "not_readable_total": 0,
            "low_fidelity_value_total": low_fidelity,
        }},
    }


def _grade(low_fidelity: int) -> str:
    q = _quality_for_state(
        _state_with(low_fidelity), status="done",
        blockers=[], warnings=[], incomplete_required=[], stale=[],
    )
    return q["quality_tier"]


def test_clean_scene_is_gold_but_low_fidelity_blocks_it():
    assert _grade(0) == "gold"
    assert _grade(2) == "silver"
