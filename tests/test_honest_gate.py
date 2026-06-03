"""V5.1 — the honest export gate requires real geometry, not just facts.

The overnight-drive failure: a scene tagged + given an assumed orientation
+ facts (heights/extent/calibration) but ZERO geometry polygons passed as
export-ready. This locks the fix: a geometry-bearing scene (grundriss /
schnitt / ansicht) must carry the required polygon kinds, or Wgeo is
pending and the scene is not ready.
(labeling-correctness-verification-tracker V5.1)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp_server import _derive_workflow_state, _missing_geometry  # noqa: E402


def _facts_complete():
    """Facts that satisfy W1–W4 for a grundriss+schnitt house."""
    return {
        "heights": {"bezug_mm": 0, "first_mm": 9050},
        "extent": {"width_mm": 11490, "depth_mm": 9240},
        "wall_thickness": {"outer_mm": 365},
        "orientation": {"north_angle_deg": 0, "assumed": True},
        "calibration_per_scene": {
            "p-eg.jpg": {"px_per_mm": 0.16},
            "s-aa.jpg": {"px_per_mm": 0.065},
        },
    }


def _dataset():
    return {"drawings": [{"file": "p-eg.jpg", "labeled": True},
                         {"file": "s-aa.jpg", "labeled": True}]}


def _meta(eg_types, aa_types, *, plan_state_exists: bool = False, plan_required_complete: bool = False):
    return {
        "p-eg.jpg": {"scene_tag": "grundriss", "scene_level": "eg",
                     "has_height_mark": True, "label_types": eg_types,
                     "plan_state_exists": plan_state_exists,
                     "plan_required_complete": plan_required_complete},
        "s-aa.jpg": {"scene_tag": "schnitt", "scene_orientation": "south",
                     "has_height_mark": True, "label_types": aa_types,
                     "plan_state_exists": plan_state_exists,
                     "plan_required_complete": plan_required_complete},
    }


# ── _missing_geometry helper ──────────────────────────────────────────

def test_v5_missing_geometry_grundriss():
    assert _missing_geometry("grundriss", []) == ["wall", "floorplan_opening"]
    assert _missing_geometry("grundriss", ["wall"]) == ["floorplan_opening"]
    assert _missing_geometry("grundriss", ["wall", "floorplan_opening"]) == []


def test_v5_missing_geometry_exempt_types():
    """sonstiges / detail / untagged require no geometry."""
    assert _missing_geometry("sonstiges", []) == []
    assert _missing_geometry(None, []) == []


# ── Wgeo phase in the derived workflow state ──────────────────────────

def test_v5_1_facts_only_scene_is_not_geometry_complete():
    """Facts complete but ZERO geometry → Wgeo pending (the bug)."""
    state = _derive_workflow_state(_dataset(), _facts_complete(),
                                   _meta(eg_types=[], aa_types=[]))
    assert state["phases"]["W1"]["status"] == "done"
    assert state["phases"]["W2"]["status"] == "done"
    assert state["phases"]["Wgeo"]["status"] == "pending"
    assert any("missing geometry" in b for b in state["phases"]["Wgeo"]["blockers"])


def test_v5_1_full_geometry_scene_is_complete():
    """Required polygons + completed scene plans → Wgeo done."""
    state = _derive_workflow_state(
        _dataset(), _facts_complete(),
        _meta(
            eg_types=["wall", "floorplan_opening"],
            aa_types=["component_line"],
            plan_state_exists=True,
            plan_required_complete=True,
        ),
    )
    assert state["phases"]["Wgeo"]["status"] == "done", state["phases"]["Wgeo"]
    assert state["phases"]["Wgeo"]["blockers"] == []


def test_v5_1_geometry_without_plan_is_not_complete():
    """Labels without plan state are legacy/unverified, not Wgeo-complete."""
    state = _derive_workflow_state(
        _dataset(), _facts_complete(),
        _meta(eg_types=["wall", "floorplan_opening"], aa_types=["component_line"]),
    )
    assert state["phases"]["Wgeo"]["status"] == "pending"
    blockers = state["phases"]["Wgeo"]["blockers"]
    assert any("p-eg.jpg" in b and "missing scene plan state" in b for b in blockers), blockers
    assert any("s-aa.jpg" in b and "missing scene plan state" in b for b in blockers), blockers


def test_v5_1_geometry_with_draft_plan_is_not_complete():
    """Minimal labels plus draft plan tasks are not an honest Wgeo pass."""
    state = _derive_workflow_state(
        _dataset(), _facts_complete(),
        _meta(
            eg_types=["wall", "floorplan_opening"],
            aa_types=["component_line"],
            plan_state_exists=True,
            plan_required_complete=False,
        ),
    )
    assert state["phases"]["Wgeo"]["status"] == "pending"
    blockers = state["phases"]["Wgeo"]["blockers"]
    assert any("p-eg.jpg" in b and "scene plan incomplete" in b for b in blockers), blockers
    assert any("s-aa.jpg" in b and "scene plan incomplete" in b for b in blockers), blockers
    assert state["next_phase"] == "Wgeo"


def test_v5_1_partial_geometry_still_pending():
    """Grundriss has walls but no openings → still pending, names the gap."""
    state = _derive_workflow_state(
        _dataset(), _facts_complete(),
        _meta(
            eg_types=["wall"],
            aa_types=["component_line"],
            plan_state_exists=True,
            plan_required_complete=True,
        ),
    )
    assert state["phases"]["Wgeo"]["status"] == "pending"
    blockers = state["phases"]["Wgeo"]["blockers"]
    assert any("p-eg.jpg" in b and "floorplan_opening" in b for b in blockers), blockers


def test_v5_1_no_scenes_wgeo_pending():
    state = _derive_workflow_state({"drawings": []}, {}, {})
    assert state["phases"]["Wgeo"]["status"] == "pending"


def test_w4_transferred_calibration_counts_as_done_with_review_debt():
    ds = {"drawings": [{"file": "north.jpg"}, {"file": "section.jpg"}]}
    facts = {
        "calibration_per_scene": {
            "north.jpg": {
                "status": "transferred",
                "computed_from": "transferred",
                "source_scene": "section.jpg",
                "transfer_kind": "section_scale",
                "confidence": "medium",
                "reason": "north elevation has no readable local dimension chain",
                "review_required": True,
            },
            "section.jpg": {"px_per_mm": 0.08, "computed_from": "M1-both"},
        }
    }
    sm = {
        "north.jpg": {"scene_tag": "ansicht"},
        "section.jpg": {"scene_tag": "schnitt"},
    }
    state = _derive_workflow_state(ds, facts, sm)
    w4 = state["phases"]["W4"]
    assert w4["status"] == "done"
    assert w4["blockers"] == []
    assert w4["transferred_calibrations"] == [
        {
            "file": "north.jpg",
            "source_scene": "section.jpg",
            "transfer_kind": "section_scale",
            "confidence": "medium",
            "review_required": True,
            "reason": "north elevation has no readable local dimension chain",
        }
    ]
