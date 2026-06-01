"""V5.1 — the honest export gate requires real geometry, not just facts.

The overnight-drive failure: a scene tagged + given an assumed orientation
+ facts (heights/extent/calibration) but ZERO geometry polygons passed as
export-ready. This locks the fix: a geometry-bearing scene (grundriss /
schnitt / ansicht) must carry the required polygon kinds, or the relevant
scene-class workflow phase is pending and the scene is not ready.
(labeling-correctness-verification-tracker V5.1)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.workflow_state import derive_workflow_state, missing_geometry  # noqa: E402


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


def _meta(eg_types, aa_types):
    return {
        "p-eg.jpg": {"scene_tag": "grundriss", "scene_level": "eg",
                     "has_height_mark": True, "label_types": eg_types},
        "s-aa.jpg": {"scene_tag": "schnitt", "scene_orientation": "south",
                     "has_height_mark": True, "label_types": aa_types},
    }


# ── missing_geometry helper ───────────────────────────────────────────

def test_v5_missing_geometry_grundriss():
    assert missing_geometry("grundriss", []) == ["wall", "floorplan_opening"]
    assert missing_geometry("grundriss", ["wall"]) == ["floorplan_opening"]
    assert missing_geometry("grundriss", ["wall", "floorplan_opening"]) == []


def test_v5_missing_geometry_exempt_types():
    """sonstiges / detail / untagged require no geometry."""
    assert missing_geometry("sonstiges", []) == []
    assert missing_geometry(None, []) == []


# ── geometry in the derived workflow state ────────────────────────────

def test_v5_1_facts_only_scene_is_not_geometry_complete():
    """Facts complete but ZERO geometry → floorplans pending (the bug)."""
    state = derive_workflow_state(_dataset(), _facts_complete(),
                                  _meta(eg_types=[], aa_types=[]))
    assert state["phases"]["inventory"]["status"] == "done"
    assert state["phases"]["floorplans"]["status"] == "pending"
    assert any("missing geometry" in b for b in state["phases"]["floorplans"]["blockers"])


def test_v5_1_full_geometry_scene_is_complete():
    """Required polygons present on both scenes → class phases done."""
    state = derive_workflow_state(
        _dataset(), _facts_complete(),
        _meta(eg_types=["wall", "floorplan_opening"], aa_types=["component_line"]),
    )
    assert state["phases"]["floorplans"]["status"] == "done", state["phases"]["floorplans"]
    assert state["phases"]["sections"]["status"] == "done", state["phases"]["sections"]


def test_v5_1_partial_geometry_still_pending():
    """Grundriss has walls but no openings → still pending, names the gap."""
    state = derive_workflow_state(
        _dataset(), _facts_complete(),
        _meta(eg_types=["wall"], aa_types=["component_line"]),
    )
    assert state["phases"]["floorplans"]["status"] == "pending"
    blockers = state["phases"]["floorplans"]["blockers"]
    assert any("p-eg.jpg" in b and "floorplan_opening" in b for b in blockers), blockers


def test_v5_1_no_scenes_wgeo_pending():
    state = derive_workflow_state({"drawings": []}, {}, {})
    assert state["phases"]["inventory"]["status"] == "pending"
    assert state["phases"]["floorplans"]["status"] == "pending"
