"""V3.1 — required geometry kinds per scene type.

The deliverable is geometry (walls, openings, roof/component lines), not
just facts. This locks the per-scene-type requirement that the honest gate
(V5.1 Wgeo) enforces, so the contract can't silently regress.
(labeling-correctness-verification-tracker V3.1)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.workflow_state import REQUIRED_GEOMETRY, missing_geometry  # noqa: E402


def test_v3_1_grundriss_requires_walls_and_openings():
    assert "wall" in REQUIRED_GEOMETRY["grundriss"]
    assert "floorplan_opening" in REQUIRED_GEOMETRY["grundriss"]


def test_v3_1_schnitt_requires_component_lines():
    assert REQUIRED_GEOMETRY["schnitt"] == ["component_line"]


def test_v3_1_ansicht_requires_view_openings():
    assert REQUIRED_GEOMETRY["ansicht"] == ["view_opening"]


def test_v3_1_all_geometry_scene_types_covered():
    """Every geometry-bearing scene tag has a non-empty requirement; the
    non-geometry tags are intentionally absent (exempt)."""
    assert set(REQUIRED_GEOMETRY) == {"grundriss", "schnitt", "ansicht"}
    for tag, kinds in REQUIRED_GEOMETRY.items():
        assert kinds, f"{tag} has an empty requirement"


def test_v3_1_missing_geometry_reports_each_gap():
    # nothing present → all required kinds missing
    assert missing_geometry("grundriss", []) == ["wall", "floorplan_opening"]
    # one present → only the other missing
    assert missing_geometry("grundriss", ["wall"]) == ["floorplan_opening"]
    # all present → nothing missing
    assert missing_geometry("schnitt", ["component_line"]) == []
    # extra/irrelevant kinds don't satisfy the requirement
    assert missing_geometry("ansicht", ["wall", "height_mark"]) == ["view_opening"]


def test_v3_1_exempt_scene_types_need_no_geometry():
    for tag in ("sonstiges", "detail", "nicht_klassifiziert", None):
        assert missing_geometry(tag, []) == []
