"""Unit tests for api/building_facts.py (issue #8).

Covers the building-global fact entry shape, deterministic derivation
(müNN ↔ relative, storey heights, roof geometry), and the assembled view.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.building_facts import (  # noqa: E402
    build_global_view,
    derive_building_geometry,
    make_fact,
)


def _facts(**vals):
    """Build a {name: entry} mapping from name=value kwargs."""
    return {
        k: make_fact(v, source_scene="s.jpg", source_label_id="lab-x", confidence="high")
        for k, v in vals.items()
    }


# ── fact entry shape ──────────────────────────────────────────────────────


def test_make_fact_records_provenance_and_confidence():
    e = make_fact(7210, source_scene="schnitt.jpg", source_label_id="hm:lab-1", confidence="high")
    assert e["value"] == 7210
    assert e["unit"] == "mm"
    assert e["confidence"] == "high"
    assert e["source"] == {"scene": "schnitt.jpg", "label_id": "hm:lab-1"}


def test_make_fact_rejects_bad_confidence():
    with pytest.raises(ValueError):
        make_fact(1, confidence="maybe")


# ── müNN ↔ relative derivation (issue example) ────────────────────────────


def test_derive_munn_from_eg_datum():
    # EG = 843.80 müNN (±0.00), FH relative +7.21 m -> FH = 851.01 müNN.
    facts = _facts(EG_munn_mm=843800, FH_mm=7210)
    derived = {d["name"]: d for d in derive_building_geometry(facts)}
    assert "FH_munn_mm" in derived
    assert derived["FH_munn_mm"]["value"] == 851010
    assert derived["FH_munn_mm"]["derived"] is True
    assert derived["FH_munn_mm"]["needs_cross_check"] is True
    assert derived["FH_munn_mm"]["inputs"] == ["EG_munn_mm", "FH_mm"]


def test_no_munn_derivation_without_datum():
    facts = _facts(FH_mm=7210)
    names = {d["name"] for d in derive_building_geometry(facts)}
    assert not any(n.endswith("_munn_mm") for n in names)


# ── storey heights from level deltas ──────────────────────────────────────


def test_derive_storey_heights():
    # UG -2860, EG 0, DG 2970 -> UG→EG = 2860, EG→DG = 2970.
    facts = _facts(UG_mm=-2860, EG_mm=0, DG_mm=2970)
    derived = {d["name"]: d["value"] for d in derive_building_geometry(facts)}
    assert derived["storey_ug_eg_mm"] == 2860
    assert derived["storey_eg_dg_mm"] == 2970


def test_storey_uses_implicit_eg_zero_when_munn_present():
    # EG_mm not stored, but EG_munn datum present -> EG treated as 0.
    facts = _facts(EG_munn_mm=843800, DG_mm=2970)
    derived = {d["name"]: d["value"] for d in derive_building_geometry(facts)}
    assert derived["storey_eg_dg_mm"] == 2970


# ── roof geometry ─────────────────────────────────────────────────────────


def test_derive_roof_rise_per_m():
    facts = {"roof_pitch_deg": make_fact(45, source_scene="s.jpg", unit="deg")}
    derived = {d["name"]: d["value"] for d in derive_building_geometry(facts)}
    # tan(45) = 1 -> 1000 mm rise per metre of run.
    assert derived["roof_rise_per_m_mm"] == pytest.approx(1000.0, abs=0.5)


def test_derive_roof_ridge_rise_uses_depth():
    facts = {
        "roof_pitch_deg": make_fact(45, source_scene="s.jpg", unit="deg"),
        "depth_mm": {"value": 9000},
    }
    derived = {d["name"]: d["value"] for d in derive_building_geometry(facts)}
    # (9000/2) * tan(45) = 4500.
    assert derived["roof_ridge_rise_mm"] == pytest.approx(4500.0, abs=0.5)


# ── assembled view ────────────────────────────────────────────────────────


def test_build_global_view_propagates_to_all_scenes():
    bg = {"schema": 1, "facts": _facts(FH_mm=7210, EG_munn_mm=843800)}
    scenes = ["a-east.jpg", "a-north.jpg", "schnitt.jpg"]
    view = build_global_view(bg, scenes)
    assert view["propagation"]["applies_to_scenes"] == scenes
    assert "FH_mm" in view["facts"]
    names = {d["name"] for d in view["derived"]}
    assert "FH_munn_mm" in names


def test_build_global_view_threads_extent_depth_for_roof():
    bg = {"facts": {"roof_pitch_deg": make_fact(45, source_scene="s.jpg", unit="deg")}}
    view = build_global_view(bg, ["s.jpg"], extent={"depth_mm": 9000})
    names = {d["name"] for d in view["derived"]}
    assert "roof_ridge_rise_mm" in names


def test_build_global_view_empty_is_safe():
    view = build_global_view(None, ["s.jpg"])
    assert view["facts"] == {}
    assert view["derived"] == []
    assert view["propagation"]["applies_to_scenes"] == ["s.jpg"]
