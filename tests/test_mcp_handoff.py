from __future__ import annotations

from pathlib import Path

from mcp_handoff import list_house_handoffs, read_scene_handoff, write_scene_handoff


def test_scene_handoff_roundtrip(tmp_path: Path) -> None:
    result = write_scene_handoff(
        "house-x",
        "house-x-floorplan-eg.jpg",
        {
            "phase": "floorplan",
            "status": "verified",
            "summary": "outer walls verified",
            "labels_added": 3,
            "labels_changed": 1,
            "open_defects": [],
            "uncertain_labels": [],
            "evidence_refs": ["EV-1"],
        },
        dataset_dir=tmp_path,
    )

    assert result["bytes"] > 0
    handoff = read_scene_handoff("house-x", "house-x-floorplan-eg.jpg", dataset_dir=tmp_path)
    assert handoff is not None
    assert handoff["schema_version"] == "mcp-handoff-v1"
    assert handoff["summary"] == "outer walls verified"
    assert handoff["evidence_refs"] == ["EV-1"]


def test_list_house_handoffs_is_compact(tmp_path: Path) -> None:
    write_scene_handoff(
        "house-x",
        "scene.jpg",
        {
            "phase": "elevation",
            "status": "needs_repair",
            "summary": "missing one window",
            "open_defects": [{"id": "DEF-1"}],
            "uncertain_labels": ["L1", "L2"],
        },
        dataset_dir=tmp_path,
    )

    rows = list_house_handoffs("house-x", dataset_dir=tmp_path)

    assert rows == [
        {
            "path": str(tmp_path / "house-x" / "handoffs" / "scene.jpg.json"),
            "bytes": rows[0]["bytes"],
            "key": "house-x",
            "file": "scene.jpg",
            "phase": "elevation",
            "status": "needs_repair",
            "summary": "missing one window",
            "open_defect_count": 1,
            "uncertain_label_count": 2,
            "updated_at_ms": rows[0]["updated_at_ms"],
        }
    ]
