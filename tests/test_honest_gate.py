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


def _meta(eg_types, aa_types):
    return {
        "p-eg.jpg": {"scene_tag": "grundriss", "scene_level": "eg",
                     "has_height_mark": True, "label_types": eg_types},
        "s-aa.jpg": {"scene_tag": "schnitt", "scene_orientation": "south",
                     "has_height_mark": True, "label_types": aa_types},
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
    """Required polygons present on both scenes → Wgeo done."""
    state = _derive_workflow_state(
        _dataset(), _facts_complete(),
        _meta(eg_types=["wall", "floorplan_opening"], aa_types=["component_line"]),
    )
    assert state["phases"]["Wgeo"]["status"] == "done", state["phases"]["Wgeo"]
    assert state["phases"]["Wgeo"]["blockers"] == []


def test_v5_1_partial_geometry_still_pending():
    """Grundriss has walls but no openings → still pending, names the gap."""
    state = _derive_workflow_state(
        _dataset(), _facts_complete(),
        _meta(eg_types=["wall"], aa_types=["component_line"]),
    )
    assert state["phases"]["Wgeo"]["status"] == "pending"
    blockers = state["phases"]["Wgeo"]["blockers"]
    assert any("p-eg.jpg" in b and "floorplan_opening" in b for b in blockers), blockers


def test_v5_1_no_scenes_wgeo_pending():
    state = _derive_workflow_state({"drawings": []}, {}, {})
    assert state["phases"]["Wgeo"]["status"] == "pending"
