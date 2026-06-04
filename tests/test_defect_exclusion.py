"""Bug2 (2026-06-04 EG run): classifying a wall-score defect as non-wall must
persist a wall-score EXCLUSION, so a re-score doesn't regenerate the same
off-footprint defect (compass rose / title block / dimension text) and force
the agent through ceremonial re-classification every evaluation.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main  # noqa: E402,F401  (load package root first; main<->routes cycle)
from api.routes_plan_state import _NON_WALL_SEMANTIC_CLASSES  # noqa: E402
from api.scene_plan_state import _exclusion_evidence_from_defect  # noqa: E402


def _defect(category="wall_missing_region", region=(2178.0, 921.0, 2334.0, 2052.0)):
    return {"id": "DEF-001", "category": category, "region": list(region)}


def test_nonwall_classification_creates_honored_exclusion():
    for classification, expected in [
        ("false_positive", "ignored_noise"),
        ("dimension_or_annotation", "dimension_annotation"),
        ("site_or_boundary_line", "site_boundary"),
        ("furniture_or_fixture", "furniture_fixture"),
        ("dashed_projection", "hatching_projection"),
        ("separate_structure", "ignored_noise"),
        ("opening_symbol", "opening_symbol"),
    ]:
        ev = _exclusion_evidence_from_defect({"evidence": []}, _defect(), classification)
        assert ev is not None, classification
        assert ev["kind"] == "semantic_ink_region"
        res = ev["result"]
        assert res["semantic_class"] == expected
        # must be a class the exclusion reader actually honors, else it's a no-op
        assert expected in _NON_WALL_SEMANTIC_CLASSES
        assert res["bbox_format"] == "xyxy"
        assert res["bbox_xyxy"] == [2178.0, 921.0, 2334.0, 2052.0]


def test_real_wall_issues_are_not_excluded():
    for classification in ("real_missing_wall", "bad_existing_wall",
                           "duplicate_wall_face_not_centerline", "ambiguous"):
        assert _exclusion_evidence_from_defect({"evidence": []}, _defect(), classification) is None


def test_non_wall_score_category_not_excluded():
    # an opening_relation defect isn't a wall-score region — don't fabricate one
    assert _exclusion_evidence_from_defect(
        {"evidence": []}, _defect(category="opening_relation"), "false_positive"
    ) is None
