"""V5.2 per-scene scorecard + V5.3 zero-observation defect (pure helpers)."""
from api.export_gate import (
    score_scene_completeness,
    score_house_completeness,
    unobserved_labeled_scenes,
    export_readiness,
)


def _complete_grundriss():
    # grundriss requires BOTH wall AND floorplan_opening (mcp_server contract)
    return {
        "scene_tag": "grundriss",
        "file": "eg.jpg",
        "labels": [{"type": "wall"}, {"type": "floorplan_opening"}],
        "calibrated": True,
        "orientation": "north",
    }


# ── V5.2 ──────────────────────────────────────────────────────────────────

def test_complete_grundriss_full_score():
    card = score_scene_completeness(_complete_grundriss(), bezug_mm=0.0)
    assert card["score"] == card["max"] == 4
    assert card["complete"] is True
    assert card["missing"] == []


def test_missing_geometry_and_compass_flagged():
    sc = _complete_grundriss()
    sc["labels"] = [{"type": "wall"}]  # missing floorplan_opening
    sc["orientation"] = None           # compass not set
    card = score_scene_completeness(sc, bezug_mm=0.0)
    assert card["complete"] is False
    assert "geometry" in card["missing"]
    assert "compass" in card["missing"]
    assert card["score"] == 2  # dims + bezug still pass
    assert card["max"] == 4


def test_unoriented_scene_compass_and_bezug_are_na():
    sc = {"scene_tag": "lageplan", "file": "site.jpg",
          "labels": [], "calibrated": True}
    card = score_scene_completeness(sc, bezug_mm=None)
    # lageplan: no geometry contract, no datum, no compass -> only dims counts
    assert card["checks"]["geometry"] is None
    assert card["checks"]["compass"] is None
    assert card["checks"]["bezug"] is None
    assert card["max"] == 1 and card["score"] == 1
    assert card["complete"] is True


def test_no_bezug_flags_datum():
    card = score_scene_completeness(_complete_grundriss(), bezug_mm=None)
    assert "bezug" in card["missing"]
    assert card["complete"] is False


def test_house_rollup():
    scenes = [
        _complete_grundriss(),
        {"scene_tag": "ansicht", "file": "nord.jpg",
         "labels": [{"type": "view_opening"}], "calibrated": True,
         "orientation": "north"},
        {"scene_tag": "schnitt", "file": "aa.jpg",
         "labels": [], "calibrated": False, "orientation": None},
    ]
    res = score_house_completeness(scenes, bezug_mm=0.0)
    assert res["scene_count"] == 3
    assert res["complete_scenes"] == 2          # grundriss + ansicht
    assert res["house_max"] == 12               # 4 + 4 + 4
    # schnitt: geometry F, dims F, bezug T, compass F -> 1
    assert res["house_score"] == 4 + 4 + 1


def test_empty_scene_zero():
    card = score_scene_completeness(
        {"scene_tag": "grundriss", "file": "x.jpg", "labels": [],
         "calibrated": False, "orientation": None}, bezug_mm=None)
    assert card["score"] == 0
    assert card["complete"] is False


# ── V5.3 ──────────────────────────────────────────────────────────────────

def test_labeled_scene_without_observations_blocks_export():
    scenes = [{
        "scene_tag": "grundriss", "file": "eg.jpg",
        "labels": [{"type": "wall"}, {"type": "floorplan_opening"}],
        "observation_count": 0,
    }]
    bad = unobserved_labeled_scenes(scenes)
    assert bad and bad[0]["file"] == "eg.jpg"
    res = export_readiness(scenes)
    assert res["ready"] is False
    assert any(b["missing"] == ["observations"] for b in res["blockers"])


def test_observed_complete_scene_ready():
    scenes = [{
        "scene_tag": "grundriss", "file": "eg.jpg",
        "labels": [{"type": "wall"}, {"type": "floorplan_opening"}],
        "observation_count": 5,
    }]
    res = export_readiness(scenes)
    assert res["ready"] is True
    assert res["silent_scenes"] == []


def test_unlabeled_scene_not_a_silent_defect():
    scenes = [{"scene_tag": "grundriss", "file": "eg.jpg",
               "labels": [], "observation_count": 0}]
    assert unobserved_labeled_scenes(scenes) == []


def test_observation_count_omitted_is_backward_compatible():
    scene = {"scene_tag": "grundriss", "file": "eg.jpg",
             "labels": [{"type": "wall"}, {"type": "floorplan_opening"}]}
    res = export_readiness([scene])
    assert res["ready"] is True
    assert res["silent_scenes"] == []
