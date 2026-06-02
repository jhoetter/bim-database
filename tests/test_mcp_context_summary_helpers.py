from __future__ import annotations

from mcp_context_summary import compact_label, compact_plan_status, compact_scene_row, label_counts, label_summary


def test_compact_scene_row_prefers_workflow_meta() -> None:
    row = compact_scene_row(
        {"file": "eg.jpg", "kind": "floorplan", "floor": "eg", "view": None, "labeled": True, "label_count": 99},
        {"scene_tag": "grundriss", "scene_level": "eg", "label_count": 3, "label_types": ["wall"]},
    )

    assert row["file"] == "eg.jpg"
    assert row["extraction_kind"] == "floorplan"
    assert row["scene_tag"] == "grundriss"
    assert row["label_count"] == 3
    assert row["label_types"] == ["wall"]


def test_compact_plan_status_truncates_blockers() -> None:
    status = compact_plan_status({"status": "needs_repair", "blockers": [1, 2, 3]}, max_blockers=2)

    assert status["status"] == "needs_repair"
    assert status["blocker_count"] == 3
    assert status["blockers"] == [1, 2]
    assert status["truncated"] is True


def test_compact_plan_status_handles_missing_plan() -> None:
    status = compact_plan_status(None)

    assert status["exists"] is False
    assert status["status"] == "missing"
    assert status["truncated"] is False


def test_label_summary_and_counts_are_geometry_free() -> None:
    labels = [
        {
            "id": "dim-1",
            "type": "dimensioned_distance",
            "attributes": {"value_mm": 1200, "is_reference": True},
            "geometry": {"start": [0, 0], "end": [10, 0]},
        },
        {"id": "wall-1", "type": "wall", "attributes": {"thickness_mm": 365}},
    ]

    assert label_summary(labels[0]) == "value=1200mm (REF)"
    assert label_counts(labels) == {"dimensioned_distance": 1, "wall": 1}
    compact = compact_label(labels[0])
    assert compact == {
        "id": "dim-1",
        "type": "dimensioned_distance",
        "status": None,
        "summary": "value=1200mm (REF)",
    }
    assert "geometry" not in compact
