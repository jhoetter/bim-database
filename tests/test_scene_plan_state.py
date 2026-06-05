"""M6 (code-quality-tracker): dedicated unit tests for api/scene_plan_state.

Covers the version hash, the defect/task action ordering that drives the
agent's next-step selection, and the template create→read→version-conflict
round-trip (which also exercises the C2 locked write path).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.scene_plan_state import (  # noqa: E402
    PlanStateConflictError,
    create_plan_state_from_template,
    create_state_from_template,
    evaluate_gates,
    next_actions_from_state,
    read_plan_state,
    version_for_state,
    write_plan_state,
)


# ── version_for_state ──────────────────────────────────────────────────────


def test_version_is_stable_and_changes_on_mutation():
    state = create_state_from_template(key="h", file="f.jpg", scene_tag="grundriss")
    v1 = version_for_state(state)
    assert v1 == version_for_state(dict(state))  # order-independent / stable
    state["status"] = "in_progress"
    assert version_for_state(state) != v1


# ── next_actions_from_state ────────────────────────────────────────────────


def test_template_state_yields_required_task_actions():
    state = create_state_from_template(key="h", file="f.jpg", scene_tag="grundriss")
    actions = next_actions_from_state(state, limit=3)
    assert actions, "a fresh grundriss plan should have required tasks"
    assert all(a["kind"] == "task" for a in actions)
    assert len(actions) <= 3


def test_open_defects_take_priority_over_tasks_and_sort_by_severity():
    state = create_state_from_template(key="h", file="f.jpg", scene_tag="grundriss")
    state["defects"] = [
        {"id": "D-warn", "status": "open", "severity": "warning",
         "category": "dimension", "title": "warn"},
        {"id": "D-block", "status": "open", "severity": "blocker",
         "category": "wall_topology", "title": "block"},
        {"id": "D-done", "status": "resolved", "severity": "blocker",
         "category": "wall_topology", "title": "ignored"},
    ]
    actions = next_actions_from_state(state, limit=5)
    # First action is the blocker defect; resolved defect is excluded.
    assert actions[0]["kind"] == "defect"
    assert actions[0]["id"] == "D-block"
    defect_ids = [a["id"] for a in actions if a["kind"] == "defect"]
    assert defect_ids == ["D-block", "D-warn"]
    assert "D-done" not in defect_ids


def test_limit_is_respected():
    state = create_state_from_template(key="h", file="f.jpg", scene_tag="grundriss")
    assert len(next_actions_from_state(state, limit=1)) == 1


# ── create → read → conflict round-trip (also exercises C2 locking) ─────────


def test_create_read_and_version_conflict(tmp_path):
    key, file = "house-x", "house-x-eg.jpg"
    created = create_plan_state_from_template(tmp_path, key, file, scene_tag="grundriss")
    assert created["state"]["key"] == key

    loaded = read_plan_state(tmp_path, key, file)
    assert loaded["exists"] is True
    assert loaded["state"]["scene_tag"] == "grundriss"

    # Re-create without overwrite must conflict.
    with pytest.raises(PlanStateConflictError):
        create_plan_state_from_template(tmp_path, key, file, scene_tag="grundriss")

    # A write with a stale expected_version must be rejected (optimistic lock).
    with pytest.raises(PlanStateConflictError):
        write_plan_state(tmp_path, loaded["state"], expected_version="deadbeef")


def test_write_with_correct_version_succeeds(tmp_path):
    key, file = "house-y", "house-y-eg.jpg"
    create_plan_state_from_template(tmp_path, key, file, scene_tag="grundriss")
    loaded = read_plan_state(tmp_path, key, file)
    state = loaded["state"]
    state["status"] = "in_progress"
    out = write_plan_state(tmp_path, state, expected_version=loaded["version"])
    assert out["state"]["status"] == "in_progress"
    assert out["version"] != loaded["version"]


# ── F-09 homography-health gate for grundriss ──────────────────────────────


def _grundriss_doc(homography_status):
    """A floorplan labels_doc with one wall and an optional homography snapshot."""
    doc = {
        "scene_tag": "grundriss",
        "scene_level": "eg",
        "labels": [
            {
                "id": "w1",
                "type": "wall",
                "status": "readable",
                "geometry": {"start": [0.0, 0.0], "end": [100.0, 0.0]},
                "attributes": {},
            }
        ],
    }
    if homography_status is not None:
        doc["homography"] = {"status": homography_status}
    return doc


def _open_categories(result):
    return {d.get("category") for d in result.get("open_defects", [])}


def test_grundriss_homography_health_blocks_single_axis(tmp_path):
    # Single-axis / "H fehlt" homography must raise a blocker (the house-22 bug).
    create_plan_state_from_template(tmp_path, "h", "f.png", scene_tag="grundriss")
    res = evaluate_gates(tmp_path, "h", "f.png",
                         labels_doc=_grundriss_doc("insufficient_references"))
    assert "calibration_health" in _open_categories(res)


def test_grundriss_homography_health_blocks_when_absent(tmp_path):
    # No persisted homography at all (transferred scale only) also blocks.
    create_plan_state_from_template(tmp_path, "h", "f.png", scene_tag="grundriss")
    res = evaluate_gates(tmp_path, "h", "f.png", labels_doc=_grundriss_doc(None))
    assert "calibration_health" in _open_categories(res)


def test_grundriss_homography_health_clears_when_ok(tmp_path):
    # A positionable homography (status ok, via H+V refs or assume_isotropic) passes.
    create_plan_state_from_template(tmp_path, "h", "f.png", scene_tag="grundriss")
    res = evaluate_gates(tmp_path, "h", "f.png", labels_doc=_grundriss_doc("ok"))
    assert "calibration_health" not in _open_categories(res)
