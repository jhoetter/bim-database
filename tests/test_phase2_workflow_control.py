from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main  # noqa: E402
from api.scene_plan_state import read_plan_state, write_plan_state  # noqa: E402


def _seed_floorplan(key: str) -> tuple[Path, str]:
    file = f"{key}-eg.png"
    root = api_main.DATASET_DIR / key
    shutil.rmtree(root, ignore_errors=True)
    (root / "labels").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 220), "white").save(root / file)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["image_size_px"] = [320, 220]
    (root / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    return root, file


def _seed_elevation(key: str) -> tuple[Path, str]:
    file = f"{key}-east.png"
    root = api_main.DATASET_DIR / key
    shutil.rmtree(root, ignore_errors=True)
    (root / "labels").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 220), "white").save(root / file)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "ansicht"
    labels["scene_orientation"] = "east"
    labels["image_size_px"] = [320, 220]
    (root / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    return root, file


def _create_plan(client: TestClient, key: str, file: str) -> None:
    created = client.post(
        f"/datasets/{key}/{file}/plan-state/template",
        json={"scene_tag": "grundriss", "level_or_orientation": "eg"},
    )
    assert created.status_code == 200, created.text


def _create_elevation_plan(client: TestClient, key: str, file: str) -> None:
    created = client.post(
        f"/datasets/{key}/{file}/plan-state/template",
        json={"scene_tag": "ansicht", "level_or_orientation": "east"},
    )
    assert created.status_code == 200, created.text


def _force_task_status(key: str, file: str, task_id: str, status: str = "verified") -> None:
    loaded = read_plan_state(api_main.DATASET_DIR, key, file)
    state = loaded["state"]
    for task in state["tasks"]:
        if task["id"] == task_id:
            task["status"] = status
            for gate in task.get("gates") or []:
                gate["status"] = "passed"
            break
    else:
        raise AssertionError(f"task {task_id!r} not found")
    write_plan_state(api_main.DATASET_DIR, state)


def test_next_action_exposes_view_mode_and_readonly_analysis_policy() -> None:
    key = "house-zzphase2-next"
    root, file = _seed_floorplan(key)
    try:
        client = TestClient(api_main.app)
        _create_plan(client, key, file)

        r = client.get(f"/datasets/{key}/{file}/plan-state/next-action")
        assert r.status_code == 200, r.text
        action = r.json()["data"]["action"]
        assert action["recommended_view_mode"] == "analysis_view"
        assert action["allowed_tools"]
        assert action["allowed_label_types"] == []
        assert "wall" in action["forbidden_label_types"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_elevation_plan_allows_scene_specific_label_writes_without_override() -> None:
    key = "house-zzphase2-elevation-policy"
    root, file = _seed_elevation(key)
    try:
        client = TestClient(api_main.app)
        _create_elevation_plan(client, key, file)
        _force_task_status(key, file, "CLASSIFY_SCENE")

        next_action = client.get(f"/datasets/{key}/{file}/plan-state/next-action").json()["data"]["action"]
        assert next_action["task_id"] == "READ_HEIGHTS"
        assert "height_mark" in next_action["allowed_label_types"]
        assert next_action["recommended_view_mode"] == "measurement_read_view"

        height_ok = client.post(
            f"/datasets/{key}/{file}/plan-state/preflight-label-write",
            json={"label_types": ["height_mark"], "tool": "upsert_label"},
        )
        assert height_ok.status_code == 200, height_ok.text
        assert height_ok.json()["data"].get("override") is not True

        _force_task_status(key, file, "READ_HEIGHTS")
        component_action = client.get(f"/datasets/{key}/{file}/plan-state/next-action").json()["data"]["action"]
        assert component_action["task_id"] == "TRACE_COMPONENTS"
        assert component_action["allowed_label_types"] == ["component_line"]

        component_ok = client.post(
            f"/datasets/{key}/{file}/plan-state/preflight-label-write",
            json={"label_types": ["component_line"], "tool": "upsert_label"},
        )
        assert component_ok.status_code == 200, component_ok.text
        assert component_ok.json()["data"].get("override") is not True

        _force_task_status(key, file, "TRACE_COMPONENTS")
        opening_action = client.get(f"/datasets/{key}/{file}/plan-state/next-action").json()["data"]["action"]
        assert opening_action["task_id"] == "PLACE_VIEW_OPENINGS"
        assert opening_action["allowed_label_types"] == ["view_opening"]

        _force_task_status(key, file, "PLACE_VIEW_OPENINGS")
        calibration_action = client.get(f"/datasets/{key}/{file}/plan-state/next-action").json()["data"]["action"]
        assert calibration_action["task_id"] == "CALIBRATE_SCENE"
        assert {"dimensioned_distance", "dimension_number"} <= set(calibration_action["allowed_label_types"])
        assert "record_transferred_calibration" in calibration_action["allowed_tools"]

        dim_ok = client.post(
            f"/datasets/{key}/{file}/plan-state/preflight-label-write",
            json={"label_types": ["dimensioned_distance"], "tool": "add_reference_dim"},
        )
        assert dim_ok.status_code == 200, dim_ok.text
        assert dim_ok.json()["data"].get("override") is not True
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_wall_write_before_silhouette_hypothesis_is_blocked_and_override_is_audited() -> None:
    key = "house-zzphase2-wall"
    root, file = _seed_floorplan(key)
    try:
        client = TestClient(api_main.app)
        _create_plan(client, key, file)
        _force_task_status(key, file, "CLASSIFY_SCENE")

        blocked = client.post(
            f"/datasets/{key}/{file}/plan-state/preflight-label-write",
            json={"label_types": ["wall"], "tool": "upsert_wall_anchored"},
        )
        assert blocked.status_code == 400
        assert "plan_order_blocked" in blocked.text
        assert "ACT-ANALYZE_SILHOUETTE" in blocked.text

        override = client.post(
            f"/datasets/{key}/{file}/plan-state/preflight-label-write",
            json={
                "label_types": ["wall"],
                "tool": "upsert_wall_anchored",
                "allow_override": True,
                "override_reason": "test override",
            },
        )
        assert override.status_code == 200, override.text
        assert override.json()["data"]["override"] is True

        plan = client.get(f"/datasets/{key}/{file}/plan-state").json()["data"]["state"]
        assert any(d.get("category") == "plan_order_override" for d in plan["defects"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_opening_write_before_topology_verification_is_blocked() -> None:
    key = "house-zzphase2-opening"
    root, file = _seed_floorplan(key)
    try:
        client = TestClient(api_main.app)
        _create_plan(client, key, file)
        for task_id in ("CLASSIFY_SCENE", "ANALYZE_SILHOUETTE", "TRACE_OUTER_WALLS"):
            _force_task_status(key, file, task_id)

        next_action = client.get(f"/datasets/{key}/{file}/plan-state/next-action").json()["data"]["action"]
        assert next_action["task_id"] == "VERIFY_OUTER_TOPOLOGY"
        assert next_action["recommended_view_mode"] == "topology_qa_view"

        blocked = client.post(
            f"/datasets/{key}/{file}/plan-state/preflight-label-write",
            json={"label_types": ["floorplan_opening"], "tool": "apply_opening_candidate"},
        )
        assert blocked.status_code == 400
        assert "plan_order_blocked" in blocked.text
        assert "floorplan_opening" in blocked.text
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_mass_tool_allowed_under_wall_editing_action() -> None:
    key = "house-zzphase3-mass-policy"
    root, file = _seed_floorplan(key)
    try:
        client = TestClient(api_main.app)
        _create_plan(client, key, file)
        _force_task_status(key, file, "CLASSIFY_SCENE")
        _force_task_status(key, file, "ANALYZE_SILHOUETTE")

        started = client.post(f"/datasets/{key}/{file}/plan-state/actions/ACT-TRACE_OUTER_WALLS/start")
        assert started.status_code == 200, started.text

        ok = client.post(
            f"/datasets/{key}/{file}/plan-state/preflight-label-write",
            json={"label_types": ["wall"], "tool": "upsert_rect_mass"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["action_id"] == "ACT-TRACE_OUTER_WALLS"
    finally:
        shutil.rmtree(root, ignore_errors=True)
