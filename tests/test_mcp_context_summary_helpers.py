from __future__ import annotations

from mcp_context_summary import compact_plan_status, compact_scene_row, label_summary


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


def test_label_summary_covers_reference_dimension() -> None:
    summary = label_summary(
        {
            "type": "dimensioned_distance",
            "attributes": {"value_mm": 1200, "is_reference": True},
        }
    )

    assert summary == "value=1200mm (REF)"
