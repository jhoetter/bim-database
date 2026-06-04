"""WS-E (legibility-first-quality tracker): conflict adjudication.

E1: EG_munn_mm (absolute müNN) vs bezug_mm (relative ±0.00) are different
frames and must NOT raise a conflict — that was the permanent false "1 Konflikt".
E2: build_global_view honors resolved_conflicts so an adjudicated conflict stops
gating, while still being visible/auditable.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.building_facts import build_global_view, detect_fact_conflicts  # noqa: E402


def _facts(**vals):
    return {k: {"value": v, "unit": "mm"} for k, v in vals.items()}


# ── E1 ───────────────────────────────────────────────────────────────────
def test_e1_munn_vs_bezug_is_not_a_conflict():
    # the real house-22 values: 843.8 m müNN vs ±0.00 relative datum.
    conflicts = detect_fact_conflicts(_facts(EG_munn_mm=843800, bezug_mm=0))
    assert conflicts == [], "absolute müNN vs relative ±0.00 must not conflict"


def test_e1_flags_absolute_value_in_relative_bezug_field():
    # a frame slip: an absolute müNN value mistakenly stored in bezug_mm.
    conflicts = detect_fact_conflicts(_facts(bezug_mm=843800))
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "relative_datum_frame_mistake"


def test_e1_same_fact_divergent_reads_still_conflict():
    facts = {"bezug_mm": {"value": 0, "unit": "mm",
                          "previous_values": [{"value": 3500}]}}
    conflicts = detect_fact_conflicts(facts)
    assert any(c["kind"] == "same_fact_conflicting_readings" for c in conflicts)


# ── E2 ───────────────────────────────────────────────────────────────────
def test_e2_resolved_conflict_stops_gating():
    # high confidence so the CONFLICT is the only thing forcing review.
    bg = {
        "facts": {
            "bezug_mm": {"value": 0, "unit": "mm", "confidence": "high",
                         "provenance_quality": "direct_read",
                         "previous_values": [{"value": 3500}]},
        },
    }
    view = build_global_view(bg, ["s.png"])
    ledger = view["fact_ledger"]
    assert ledger["review_required"] is True
    cid = ledger["conflicts"][0]["id"]

    # resolve it
    bg2 = {**bg, "resolved_conflicts": {cid: {"resolution": "adjudicated",
                                              "rationale": "0.00 is the datum; 3500 was a misread"}}}
    view2 = build_global_view(bg2, ["s.png"])
    l2 = view2["fact_ledger"]
    assert l2["conflicts"] == []
    assert l2["review_required"] is False
    assert len(l2["resolved_conflicts"]) == 1
    assert l2["resolved_conflicts"][0]["resolution"]["resolution"] == "adjudicated"
    # the fact is no longer down-tiered to conflicting
    assert view2["facts"]["bezug_mm"].get("provenance_quality") != "conflicting"
