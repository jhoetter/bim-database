from __future__ import annotations

from api.wall_topology import ambiguous_line_context, wall_continuity_check, wall_topology_qa


def _wall(label_id: str, start, end):
    return {
        "id": label_id,
        "type": "wall",
        "geometry": {"start": list(start), "end": list(end)},
        "status": "readable",
        "attributes": {},
    }


def _opening(label_id: str, quad, parent_id: str | None = None):
    relations = [] if parent_id is None else [{"kind": "belongs_to", "other_id": parent_id}]
    return {
        "id": label_id,
        "type": "floorplan_opening",
        "geometry": {"quad": quad},
        "status": "readable",
        "attributes": {"opening_kind": "window"},
        "relations": relations,
    }


def test_wall_topology_qa_flags_dangling_near_miss_fragments_and_stubs():
    labels = [
        _wall("w1", (0, 0), (100, 0)),
        _wall("w2", (112, 3), (220, 3)),  # near-miss + collinear fragment from w1
        _wall("stub", (300, 0), (330, 0)),
    ]

    qa = wall_topology_qa(labels, endpoint_tol_px=6, near_miss_px=20, collinear_gap_px=40, short_stub_px=50)

    assert qa["wall_count"] == 3
    assert any(i["wall_id"] == "stub" for i in qa["short_stubs"])
    fragment = next(i for i in qa["collinear_fragments"] if set(i["wall_ids"]) == {"w1", "w2"})
    assert fragment["suggested_repair"]["candidate_wall"] == [[0.0, 0.0], [220.0, 3.0]]
    assert any(i["wall_id"] in {"w1", "w2"} for i in qa["near_miss_corners"])
    assert len(qa["components"]) == 3


def test_wall_continuity_check_flags_wall_split_by_opening_gap():
    labels = [
        _wall("left", (0, 0), (100, 0)),
        _wall("right", (180, 0), (300, 0)),
        _opening(
            "win",
            [[115, -10], [165, -10], [165, 10], [115, 10]],
            parent_id="left",
        ),
    ]

    result = wall_continuity_check(labels, gap_px=120, line_tol_px=12, opening_near_px=80)

    assert result["candidate_count"] == 1
    candidate = result["candidates"][0]
    assert set(candidate["wall_ids"]) == {"left", "right"}
    assert candidate["near_openings"][0]["opening_id"] == "win"
    assert candidate["suggested_repair"] == {
        "action": "merge_or_extend_wall_unless_gap_is_a_real_endpoint",
        "candidate_wall": [[0.0, 0.0], [300.0, 0.0]],
        "replace_wall_ids": ["left", "right"],
    }


def test_ambiguous_line_context_returns_classification_checklist_and_nearby_labels():
    labels = [_wall("w1", (0, 0), (100, 0))]

    result = ambiguous_line_context(labels, line=[[10, 0], [80, 0]], pad_px=20)

    assert "door_swing_or_hint" in result["classification_checklist"]
    assert "dimension_or_annotation" in result["classification_checklist"]
    assert result["nearby_labels"] == [{"id": "w1", "type": "wall", "status": "readable"}]


def test_house22_style_regression_split_opening_and_separate_garage_mass():
    labels = [
        # Main house top wall split by a window/opening gap.
        _wall("house-top-left", (100, 100), (220, 100)),
        _wall("house-top-right", (300, 100), (430, 100)),
        _opening("top-window", [[232, 90], [288, 90], [288, 110], [232, 110]], parent_id="house-top-left"),
        # Main mass and separate garage mass should appear as separate components.
        _wall("house-right", (430, 100), (430, 320)),
        _wall("garage-left", (560, 140), (560, 360)),
        _wall("garage-bottom", (560, 360), (760, 360)),
        # Bad historical failure: a short diagonal/connector-like segment.
        _wall("bad-connector", (430, 320), (500, 350)),
    ]

    continuity = wall_continuity_check(labels, gap_px=130, line_tol_px=14, opening_near_px=90)
    topology = wall_topology_qa(labels, short_stub_px=90)

    assert any(
        set(candidate["wall_ids"]) == {"house-top-left", "house-top-right"}
        and candidate["near_openings"]
        for candidate in continuity["candidates"]
    )
    assert any(stub["wall_id"] == "bad-connector" for stub in topology["short_stubs"])
    assert len(topology["components"]) >= 2
