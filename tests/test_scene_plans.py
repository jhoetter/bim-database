from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main  # noqa: E402
from api.scene_plan_state import _label_quality_summary, _status_for_state  # noqa: E402


@pytest.fixture
def scene():
    key = "house-zzplantest"
    file = f"{key}-scene.jpg"
    ds_key = api_main.DATASET_DIR / key
    ds_key.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), (255, 255, 255)).save(ds_key / file)
    try:
        yield key, file
    finally:
        shutil.rmtree(ds_key, ignore_errors=True)


def test_scene_plan_template_create_read_task_and_log(scene):
    key, file = scene
    client = TestClient(api_main.app)

    create = client.post(
        f"/datasets/{key}/{file}/plan/template",
        json={"scene_tag": "grundriss", "level_or_orientation": "eg", "created_by": "test-agent"},
    )
    assert create.status_code == 200, create.text
    data = create.json()["data"]
    assert data["exists"] is True
    assert data["status"] == "draft"
    assert "## 2. Silhouette And Masses" in data["markdown"]

    patch = client.patch(
        f"/datasets/{key}/{file}/plan/tasks/A1",
        json={"status": "in_progress", "note": "reviewing outer mass"},
    )
    assert patch.status_code == 200, patch.text
    markdown = patch.json()["data"]["markdown"]
    assert "- [~] A1 analysis: outer wall silhouette and mass decomposition" in markdown
    assert "note: reviewing outer mass" in markdown

    log = client.post(
        f"/datasets/{key}/{file}/plan/log",
        json={
            "mode": "analysis",
            "evidence": "full scene",
            "decision": "main mass plus garage",
            "result": "continue to outer walls",
        },
    )
    assert log.status_code == 200, log.text
    assert "| analysis | full scene | main mass plus garage | continue to outer walls |" in log.json()["data"]["markdown"]

    read = client.get(f"/datasets/{key}/{file}/plan")
    assert read.status_code == 200
    assert read.json()["data"]["version"] == log.json()["data"]["version"]


def test_scene_plan_optimistic_concurrency_rejects_stale_update(scene):
    key, file = scene
    client = TestClient(api_main.app)

    first = client.put(
        f"/datasets/{key}/{file}/plan",
        json={"markdown": "# Scene plan\n\nStatus: draft\n", "create_only": True},
    )
    assert first.status_code == 200, first.text
    version = first.json()["data"]["version"]

    second = client.put(
        f"/datasets/{key}/{file}/plan",
        json={"markdown": "# Scene plan\n\nStatus: active\n", "expected_version": version},
    )
    assert second.status_code == 200, second.text

    stale = client.put(
        f"/datasets/{key}/{file}/plan",
        json={"markdown": "# Scene plan\n\nStatus: blocked\n", "expected_version": version},
    )
    assert stale.status_code == 409
    assert "version conflict" in stale.text


def test_scene_plan_rejects_path_traversal(scene):
    key, file = scene
    client = TestClient(api_main.app)

    r = client.get(f"/datasets/{key}/../bad/plan")

    assert r.status_code in {400, 404}


def test_scene_label_reset_preserves_plan_by_default_and_can_delete(scene):
    key, file = scene
    client = TestClient(api_main.app)

    created = client.post(f"/datasets/{key}/{file}/plan/template", json={"scene_tag": "grundriss"})
    assert created.status_code == 200

    reset = client.delete(f"/labels/dataset/{key}/{file}")
    assert reset.status_code == 200
    assert reset.json()["plan_deleted"] is False
    assert client.get(f"/datasets/{key}/{file}/plan").json()["data"]["exists"] is True

    reset_with_plan = client.delete(f"/labels/dataset/{key}/{file}?reset_plan=true")
    assert reset_with_plan.status_code == 200
    assert reset_with_plan.json()["plan_deleted"] is True
    assert client.get(f"/datasets/{key}/{file}/plan").json()["data"]["exists"] is False


def test_scene_plan_state_template_evaluate_and_markdown(scene):
    key, file = scene
    client = TestClient(api_main.app)

    created = client.post(
        f"/datasets/{key}/{file}/plan-state/template",
        json={"scene_tag": "grundriss", "level_or_orientation": "eg", "created_by": "test-agent"},
    )
    assert created.status_code == 200, created.text
    state = created.json()["data"]["state"]
    assert state["schema_version"] == "scene-plan-state-v1"
    assert any(t["id"] == "ANALYZE_SILHOUETTE" for t in state["tasks"])
    assert "## 2. Open Defects" in created.json()["data"]["markdown"]

    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    put = client.put(f"/labels/dataset/{key}/{file}", json=labels)
    assert put.status_code == 200, put.text

    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    data = evaluated.json()["data"]
    assert data["state"]["status"] == "needs_repair"
    titles = {d["title"] for d in data["state"]["defects"]}
    assert "No wall labels on floorplan" in titles
    assert "No floorplan openings placed" in titles
    assert "No wall labels on floorplan" in data["markdown"]


def test_scene_plan_state_templates_cover_scene_types(scene):
    key, file = scene
    client = TestClient(api_main.app)
    expected = {
        "grundriss": {"TRACE_OUTER_WALLS", "PLACE_OPENINGS"},
        "schnitt": {"READ_HEIGHTS", "TRACE_COMPONENTS", "CALIBRATE_SCENE"},
        "ansicht": {"PLACE_VIEW_OPENINGS", "TRACE_COMPONENTS", "CALIBRATE_SCENE"},
        "sonstiges": {"INSPECT_SCENE", "FINAL_QA"},
    }
    for tag, task_ids in expected.items():
        created = client.post(
            f"/datasets/{key}/{file}/plan-state/template",
            json={"scene_tag": tag, "overwrite": True},
        )
        assert created.status_code == 200, created.text
        actual = {task["id"] for task in created.json()["data"]["state"]["tasks"]}
        assert task_ids <= actual


def test_floorplan_template_orders_measurements_after_openings(scene):
    key, file = scene
    client = TestClient(api_main.app)
    created = client.post(
        f"/datasets/{key}/{file}/plan-state/template",
        json={"scene_tag": "grundriss", "overwrite": True},
    )
    assert created.status_code == 200, created.text
    task_list = created.json()["data"]["state"]["tasks"]
    ids = [task["id"] for task in task_list]
    assert ids.index("ANALYZE_SILHOUETTE") < ids.index("TRACE_OUTER_WALLS")
    assert ids.index("VERIFY_OPENINGS") < ids.index("READ_DIMENSIONS")
    assert ids.index("READ_DIMENSIONS") < ids.index("VERIFY_MEASUREMENTS")
    tasks = {task["id"]: task for task in task_list}
    assert tasks["READ_DIMENSIONS"]["depends_on"] == ["VERIFY_OPENINGS"]
    assert tasks["VERIFY_MEASUREMENTS"]["depends_on"] == ["READ_DIMENSIONS"]
    assert tasks["FINAL_QA"]["depends_on"] == ["VERIFY_MEASUREMENTS"]


def test_scene_plan_state_final_qa_cannot_verify_with_blocker(scene):
    key, file = scene
    client = TestClient(api_main.app)
    client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"})
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["labels"] = [
        {
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [20, 20], "end": [200, 20]},
            "attributes": {},
        }
    ]
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

    patch = client.patch(
        f"/datasets/{key}/{file}/plan-state/tasks/FINAL_QA",
        json={"status": "verified", "note": "attempted direct completion"},
    )
    assert patch.status_code == 400
    assert "cannot be verified" in patch.text

    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    final_task = next(t for t in evaluated.json()["data"]["state"]["tasks"] if t["id"] == "FINAL_QA")
    assert final_task["status"] == "blocked"
    assert final_task["blocked_by"]


def test_scene_plan_state_reset_preserves_sidecar_but_marks_stale(scene):
    key, file = scene
    client = TestClient(api_main.app)
    created = client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"})
    assert created.status_code == 200
    assert client.patch(
        f"/datasets/{key}/{file}/plan-state/tasks/TRACE_OUTER_WALLS",
        json={
            "status": "verified",
            "note": "verified before reset",
            "gate_updates": [
                {"id": "WALLS_EXIST", "status": "passed"},
                {"id": "WALL_INK_ANCHORED", "status": "passed"},
            ],
        },
    ).status_code == 200

    reset = client.delete(f"/labels/dataset/{key}/{file}")
    assert reset.status_code == 200

    plan_state = client.get(f"/datasets/{key}/{file}/plan-state").json()["data"]["state"]
    assert plan_state["status"] == "needs_repair"
    trace = next(t for t in plan_state["tasks"] if t["id"] == "TRACE_OUTER_WALLS")
    assert trace["status"] == "blocked"
    assert any(ev["kind"] == "reset" for ev in plan_state["evidence"])


def test_scene_plan_state_defect_closure_requires_evidence(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    defect = client.post(
        f"/datasets/{key}/{file}/plan-state/defects",
        json={
            "title": "Ambiguous line",
            "severity": "warning",
            "category": "wall_topology",
            "description": "Needs review.",
            "expected_resolution": "Reject or fix with evidence.",
        },
    )
    assert defect.status_code == 200, defect.text
    defect_id = defect.json()["data"]["state"]["defects"][0]["id"]

    rejected = client.patch(
        f"/datasets/{key}/{file}/plan-state/defects/{defect_id}",
        json={"status": "rejected"},
    )
    assert rejected.status_code == 400
    assert "without evidence" in rejected.text

    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "analysis", "summary": "Reviewed crop; it is furniture."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][0]["id"]
    rejected = client.patch(
        f"/datasets/{key}/{file}/plan-state/defects/{defect_id}",
        json={"status": "rejected", "evidence_ids": [ev_id]},
    )
    assert rejected.status_code == 200, rejected.text


def test_scene_plan_evidence_preserves_run_agent_provenance(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200

    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={
            "kind": "scene_view",
            "mode": "analysis",
            "summary": "Subagent read the wall pass.",
            "run_id": "run-123",
            "agent_id": "orchestrator",
            "subagent_id": "eg-wall-worker",
        },
    )
    assert evidence.status_code == 200, evidence.text
    ev = evidence.json()["data"]["state"]["evidence"][-1]
    assert ev["run_id"] == "run-123"
    assert ev["agent_id"] == "orchestrator"
    assert ev["subagent_id"] == "eg-wall-worker"


def test_scene_plan_state_task_verified_requires_passed_gates_and_evidence(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200

    no_gate = client.patch(
        f"/datasets/{key}/{file}/plan-state/tasks/TRACE_OUTER_WALLS",
        json={"status": "verified", "note": "looks done"},
    )
    assert no_gate.status_code == 400
    assert "all gates pass" in no_gate.text

    ok = client.patch(
        f"/datasets/{key}/{file}/plan-state/tasks/TRACE_OUTER_WALLS",
        json={
            "status": "verified",
            "note": "verified with label overlay",
            "gate_updates": [
                {"id": "WALLS_EXIST", "status": "passed"},
                {"id": "WALL_INK_ANCHORED", "status": "passed"},
            ],
        },
    )
    assert ok.status_code == 200, ok.text
    task = next(t for t in ok.json()["data"]["state"]["tasks"] if t["id"] == "TRACE_OUTER_WALLS")
    assert task["status"] == "verified"


def test_scene_plan_state_score_regression_creates_defect(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

    first = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_walls": {"precision": 1.0, "recall": 0.9, "f1": 0.95, "missing_regions": [], "off_ink_segments": []},
        },
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_walls": {"precision": 0.8, "recall": 0.8, "f1": 0.7, "missing_regions": [], "off_ink_segments": []},
        },
    )
    assert second.status_code == 200, second.text
    defects = second.json()["data"]["state"]["defects"]
    assert any(d["category"] == "score_regression" for d in defects)


def test_scene_plan_state_stale_evidence_blocks_visual_gate(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    assert client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={
            "kind": "label_view",
            "mode": "verification",
            "summary": "old verify",
            "created_at": "2026-06-01T10:00:00+00:00",
        },
    ).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["labels"] = [
        {
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [20, 20], "end": [200, 20]},
            "attributes": {},
            "updated_at": "2026-06-01T11:00:00+00:00",
        }
    ]
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200
    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    state = evaluated.json()["data"]["state"]
    assert any(d["category"] == "stale_evidence" for d in state["defects"])
    final_task = next(t for t in state["tasks"] if t["id"] == "FINAL_QA")
    visual_gate = next(g for g in final_task["gates"] if g["id"] == "VISUAL_VERIFY_EXISTS")
    assert visual_gate["status"] == "pending"


def test_scene_plan_state_subagent_report_must_match_plan_state(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    defect = client.post(
        f"/datasets/{key}/{file}/plan-state/defects",
        json={
            "title": "Missing wall",
            "severity": "blocker",
            "category": "wall_missing_region",
            "description": "Missing wall.",
            "expected_resolution": "Repair it.",
        },
    )
    assert defect.status_code == 200, defect.text
    defect_id = defect.json()["data"]["state"]["defects"][0]["id"]

    bad = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={
            "kind": "subagent_report",
            "mode": "verification",
            "summary": "bad report",
            "result": {
                "plan_status": "blocked",
                "task_ids_changed": [],
                "defect_ids_changed": [],
                "evidence_ids_created": [],
                "label_counts": {},
                "score_deltas": {},
                "rejected_edits": [],
                "unresolved_blockers": [],
            },
        },
    )
    assert bad.status_code == 400
    assert "unresolved_blockers" in bad.text

    good = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={
            "kind": "subagent_report",
            "mode": "verification",
            "summary": "matched report",
            "result": {
                "plan_status": "blocked",
                "task_ids_changed": [],
                "defect_ids_changed": [defect_id],
                "evidence_ids_created": [],
                "label_counts": {},
                "score_deltas": {},
                "rejected_edits": [],
                "unresolved_blockers": [defect_id],
            },
        },
    )
    assert good.status_code == 200, good.text


def test_scene_plan_status_distinguishes_needs_repair_from_terminal(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    defect = client.post(
        f"/datasets/{key}/{file}/plan-state/defects",
        json={
            "title": "Missing wall band",
            "severity": "blocker",
            "category": "wall_missing_region",
            "description": "A wall region is uncovered.",
            "expected_resolution": "Classify the crop, repair or reject with evidence.",
            "region": [10, 20, 30, 40],
        },
    )
    assert defect.status_code == 200, defect.text

    status = client.get(f"/datasets/{key}/{file}/plan-state/status")
    assert status.status_code == 200, status.text
    data = status.json()["data"]
    assert data["status"] == "needs_repair"
    assert data["terminal"] is False
    assert data["next_action_available"] is True
    assert data["next_action"]["mode"] == "scene-defect-repair"


def _quality_state(
    *,
    task_status: str = "verified",
    task_required: bool = True,
    defect: dict | None = None,
    label_quality: dict | None = None,
    transferred: bool = False,
    source_unreadable: bool = False,
) -> dict:
    state = {
        "schema_version": "scene-plan-state-v1",
        "key": "house-quality",
        "file": "scene.jpg",
        "scene_tag": "ansicht",
        "status": "verified",
        "tasks": [
            {
                "id": "FINAL_QA",
                "title": "Final QA",
                "phase": "verification",
                "category": "qa",
                "required": task_required,
                "status": task_status,
                "blocked_by": [],
                "gates": [{"id": "NO_BLOCKER_DEFECTS", "status": "passed"}],
            }
        ],
        "defects": [defect] if defect else [],
        "current_state": {"label_quality": label_quality or _label_quality_summary([])},
    }
    if transferred:
        state["current_state"]["transferred_facts"] = [{
            "kind": "calibration",
            "file": "scene.jpg",
            "source_scene": "section.jpg",
            "transfer_kind": "section_scale",
            "review_required": True,
        }]
    if source_unreadable:
        state["current_state"]["source_unreadable"] = [{
            "evidence_id": "EV-001",
            "decision": "source_unreadable",
            "chain_region": [10, 20, 120, 60],
            "orientation": "horizontal",
            "readable_values": [],
            "unreadable_fragments": ["washed out perimeter dimension values"],
            "reason": "Values are too faint after zoom and enhancement.",
        }]
    return state


def test_scene_plan_quality_tier_gold_for_clean_verified_state():
    status = _status_for_state(_quality_state())
    assert status["quality_tier"] == "gold"
    assert status["completion_state"] == "verified_high_confidence"
    assert status["review_debt"] == 0
    assert status["final_qa_summary"]["human_review_required"] is False


def test_scene_plan_quality_tier_silver_for_uncertainty_or_transfer():
    labels = [
        {"type": "wall", "status": "readable", "attributes": {}},
        {
            "type": "wall",
            "status": "uncertain",
            "attributes": {"confidence_reason": "faint_double_rail_centerline"},
        },
    ]
    status = _status_for_state(_quality_state(label_quality=_label_quality_summary(labels), transferred=True))
    assert status["quality_tier"] == "silver"
    assert status["completion_state"] == "verified_with_uncertainty"
    assert status["review_debt"] > 0
    assert "faint_double_rail_centerline" in status["final_qa_summary"]["uncertainty_reasons"]
    assert status["final_qa_summary"]["transferred_facts"][0]["source_scene"] == "section.jpg"


def test_scene_plan_quality_tier_bronze_for_accepted_incomplete():
    status = _status_for_state(_quality_state(task_status="accepted_incomplete", task_required=False))
    assert status["quality_tier"] == "bronze"
    assert status["completion_state"] == "accepted_incomplete"
    assert status["final_qa_summary"]["human_review_required"] is True


def test_scene_plan_quality_tier_bronze_for_source_unreadable_dimensions():
    status = _status_for_state(_quality_state(source_unreadable=True))
    assert status["quality_tier"] == "bronze"
    assert status["completion_state"] == "accepted_incomplete"
    assert status["review_debt"] >= 6
    assert status["final_qa_summary"]["human_review_required"] is True
    assert status["final_qa_summary"]["source_unreadable"][0]["evidence_id"] == "EV-001"
    assert "source-unreadable dimension chain(s)" in status["final_qa_summary"]["uncertainties"][0]


def test_scene_plan_quality_tier_blocked_for_blocker_defect():
    status = _status_for_state(_quality_state(defect={
        "id": "DEF-001",
        "title": "Missing wall",
        "status": "open",
        "severity": "blocker",
        "category": "wall_missing_region",
    }))
    assert status["quality_tier"] == "blocked"
    assert status["completion_state"] == "blocked_quality_regression"
    assert status["final_qa_summary"]["human_review_required"] is True


def test_scene_plan_singular_action_attempt_and_finish(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    assert client.post(
        f"/datasets/{key}/{file}/plan-state/defects",
        json={
            "title": "Topology gap",
            "severity": "warning",
            "category": "wall_topology",
            "description": "Two endpoints nearly meet.",
            "expected_resolution": "Inspect and close or reject.",
        },
    ).status_code == 200

    action = client.get(f"/datasets/{key}/{file}/plan-state/next-action")
    assert action.status_code == 200, action.text
    action_id = action.json()["data"]["action"]["action_id"]

    started = client.post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/start",
        json={"run_id": "run-456", "agent_id": "orchestrator", "subagent_id": "subagent-test"},
    )
    assert started.status_code == 200, started.text
    state = started.json()["data"]["state"]
    assert state["current_state"]["current_action_id"] == action_id
    started_action = next(a for a in state["actions"] if a["action_id"] == action_id)
    assert started_action["run_id"] == "run-456"
    assert started_action["agent_id"] == "orchestrator"
    assert started_action["subagent_id"] == "subagent-test"

    attempt = client.post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/attempts",
        json={
            "hypothesis": "Endpoint is a false positive",
            "edits": [],
            "evidence_ids": [],
            "run_id": "run-456",
            "agent_id": "orchestrator",
            "subagent_id": "subagent-test",
        },
    )
    assert attempt.status_code == 200, attempt.text
    attempted_action = next(a for a in attempt.json()["data"]["state"]["actions"] if a["action_id"] == action_id)
    assert attempted_action["attempts"][-1]["run_id"] == "run-456"
    assert attempted_action["attempts"][-1]["agent_id"] == "orchestrator"
    assert attempted_action["attempts"][-1]["subagent_id"] == "subagent-test"

    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "verification", "summary": "Reviewed crop."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]

    finished = client.post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/finish",
        json={
            "outcome": "rejected",
            "evidence_ids": [ev_id],
            "reason": "Not a structural wall endpoint.",
            "run_id": "run-456",
            "agent_id": "orchestrator",
            "subagent_id": "subagent-test",
        },
    )
    assert finished.status_code == 200, finished.text
    finished_state = finished.json()["data"]["state"]
    finished_action = next(a for a in finished_state["actions"] if a["action_id"] == action_id)
    assert finished_action["run_id"] == "run-456"
    assert finished_action["agent_id"] == "orchestrator"
    assert finished_action["subagent_id"] == "subagent-test"
    assert finished_state["decision_log"][-1]["run_id"] == "run-456"
    assert finished_state["decision_log"][-1]["agent_id"] == "orchestrator"
    assert finished_state["decision_log"][-1]["subagent_id"] == "subagent-test"
    defect = finished_state["defects"][0]
    assert defect["status"] == "rejected"
    assert finished_state["current_state"]["current_action_id"] is None


def test_scene_plan_task_state_preserves_run_agent_provenance(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200

    updated = client.patch(
        f"/datasets/{key}/{file}/plan-state/tasks/TRACE_OUTER_WALLS",
        json={
            "status": "blocked",
            "note": "Waiting on a cleaner silhouette pass.",
            "run_id": "run-789",
            "agent_id": "orchestrator",
            "subagent_id": "wall-worker",
        },
    )
    assert updated.status_code == 200, updated.text
    state = updated.json()["data"]["state"]
    task = next(t for t in state["tasks"] if t["id"] == "TRACE_OUTER_WALLS")
    note = state["evidence"][-1]
    assert task["run_id"] == "run-789"
    assert task["agent_id"] == "orchestrator"
    assert task["subagent_id"] == "wall-worker"
    assert note["run_id"] == "run-789"
    assert note["agent_id"] == "orchestrator"
    assert note["subagent_id"] == "wall-worker"


def test_scene_plan_attempt_retry_policy_requires_terminal_outcome(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    assert client.post(
        f"/datasets/{key}/{file}/plan-state/defects",
        json={
            "title": "Persistent topology gap",
            "severity": "warning",
            "category": "wall_topology",
            "description": "Repeated repair attempts fail.",
            "expected_resolution": "Resolve or accept uncertainty after retries.",
        },
    ).status_code == 200
    action_id = client.get(f"/datasets/{key}/{file}/plan-state/next-action").json()["data"]["action"]["action_id"]
    assert client.post(f"/datasets/{key}/{file}/plan-state/actions/{action_id}/start", json={}).status_code == 200
    for idx in range(3):
        attempt = client.post(
            f"/datasets/{key}/{file}/plan-state/actions/{action_id}/attempts",
            json={"hypothesis": f"attempt {idx + 1}", "edits": [], "evidence_ids": []},
        )
        assert attempt.status_code == 200, attempt.text
    still_open = client.post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/finish",
        json={"outcome": "still_open", "reason": "no progress after retries"},
    )
    assert still_open.status_code == 400
    assert "reached 3 attempts" in still_open.text

    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "verification", "summary": "Accepted uncertainty after retries."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]
    accepted = client.post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/finish",
        json={"outcome": "accepted_uncertain", "evidence_ids": [ev_id], "reason": "Ambiguous after three attempts."},
    )
    assert accepted.status_code == 200, accepted.text
    defect = accepted.json()["data"]["state"]["defects"][0]
    assert defect["status"] == "accepted_uncertain"


def test_scene_plan_warning_can_close_as_source_limited_without_remaining_open_warning(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "ansicht"}).status_code == 200
    assert client.post(
        f"/datasets/{key}/{file}/plan-state/defects",
        json={
            "title": "Plan order override reviewed",
            "severity": "warning",
            "category": "plan_order_override",
            "description": "Legitimate historical override.",
            "expected_resolution": "Close with evidence once reviewed.",
        },
    ).status_code == 200
    action_id = client.get(f"/datasets/{key}/{file}/plan-state/next-action").json()["data"]["action"]["action_id"]
    assert client.post(f"/datasets/{key}/{file}/plan-state/actions/{action_id}/start", json={}).status_code == 200
    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "verification", "summary": "Reviewed; source-limited but acceptable."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]
    finished = client.post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/finish",
        json={
            "outcome": "accepted_source_limited",
            "evidence_ids": [ev_id],
            "reason": "Faint source, reviewed and accepted as source-limited.",
        },
    )
    assert finished.status_code == 200, finished.text
    defect = finished.json()["data"]["state"]["defects"][0]
    assert defect["status"] == "accepted_source_limited"
    assert defect["terminal_reason"].startswith("Faint source")

    status = client.get(f"/datasets/{key}/{file}/plan-state/status").json()["data"]
    assert status["open_warnings"] == 0
    assert status["terminal_warning_decisions"] == 1
    assert status["review_debt"] > 0


def test_scene_plan_batch_closes_warning_class_with_shared_evidence(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "ansicht"}).status_code == 200
    for idx in range(3):
        assert client.post(
            f"/datasets/{key}/{file}/plan-state/defects",
            json={
                "title": f"Override warning {idx}",
                "severity": "warning",
                "category": "plan_order_override",
                "description": "Repeated reviewed warning.",
                "expected_resolution": "Batch close after review.",
            },
        ).status_code == 200
    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "verification", "summary": "All overrides reviewed as false positives."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]
    closed = client.post(
        f"/datasets/{key}/{file}/plan-state/defects/batch-close-warnings",
        json={
            "status": "rejected_false_positive",
            "category": "plan_order_override",
            "evidence_ids": [ev_id],
            "reason": "Scene-specific template now permits this write class.",
        },
    )
    assert closed.status_code == 200, closed.text
    assert len(closed.json()["data"]["closed_defect_ids"]) == 3
    status = client.get(f"/datasets/{key}/{file}/plan-state/status").json()["data"]
    assert status["open_warnings"] == 0
    assert status["terminal_warning_decisions"] == 3


def test_scene_plan_reopen_task_invalidates_dependents(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    for task_id, gate_updates in [
        ("TRACE_OUTER_WALLS", [
            {"id": "WALLS_EXIST", "status": "passed"},
            {"id": "WALL_INK_ANCHORED", "status": "passed"},
        ]),
        ("VERIFY_OPENINGS", [
            {"id": "OPENINGS_HAVE_PARENT_WALL", "status": "passed"},
            {"id": "OPENINGS_ON_WALL", "status": "passed"},
        ]),
        ("READ_DIMENSIONS", [{"id": "DIMENSIONS_REVIEWED", "status": "passed"}]),
        ("VERIFY_MEASUREMENTS", [{"id": "MEASUREMENTS_REVIEWED", "status": "passed"}]),
        ("FINAL_QA", [
            {"id": "VISUAL_VERIFY_EXISTS", "status": "passed"},
            {"id": "NO_BLOCKER_DEFECTS", "status": "passed"},
        ]),
    ]:
        r = client.patch(
            f"/datasets/{key}/{file}/plan-state/tasks/{task_id}",
            json={
                "status": "verified",
                "note": f"{task_id} was verified",
                "gate_updates": gate_updates,
            },
        )
        assert r.status_code == 200, r.text

    reopened = client.post(
        f"/datasets/{key}/{file}/plan-state/tasks/TRACE_OUTER_WALLS/reopen",
        json={"reason": "Later opening check invalidated parent wall."},
    )
    assert reopened.status_code == 200, reopened.text
    tasks = {t["id"]: t for t in reopened.json()["data"]["state"]["tasks"]}
    assert tasks["TRACE_OUTER_WALLS"]["status"] == "needs_repair"
    assert tasks["VERIFY_OPENINGS"]["status"] == "blocked"
    assert tasks["READ_DIMENSIONS"]["status"] == "blocked"
    assert tasks["VERIFY_MEASUREMENTS"]["status"] == "blocked"
    assert tasks["FINAL_QA"]["status"] == "blocked"
    stale = reopened.json()["data"]["state"]["current_state"]["stale_evidence"]
    assert "READ_DIMENSIONS" in stale
    assert "VERIFY_MEASUREMENTS" in stale
    assert "FINAL_QA" in stale


def test_scene_plan_wall_score_defect_requires_classification_before_closure(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    defect = client.post(
        f"/datasets/{key}/{file}/plan-state/defects",
        json={
            "title": "Wall score missing region 1",
            "severity": "blocker",
            "category": "wall_missing_region",
            "description": "Uncovered ink.",
            "expected_resolution": "Classify and repair/reject.",
        },
    )
    assert defect.status_code == 200, defect.text
    defect_id = defect.json()["data"]["state"]["defects"][0]["id"]
    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "analysis", "summary": "Crop reviewed."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]

    rejected = client.patch(
        f"/datasets/{key}/{file}/plan-state/defects/{defect_id}",
        json={"status": "rejected", "evidence_ids": [ev_id]},
    )
    assert rejected.status_code == 400
    assert "classified" in rejected.text

    classified = client.post(
        f"/datasets/{key}/{file}/plan-state/defects/{defect_id}/classify",
        json={"classification": "false_positive", "evidence_ids": [ev_id], "note": "Dimension stroke, not wall."},
    )
    assert classified.status_code == 200, classified.text
    rejected = client.patch(
        f"/datasets/{key}/{file}/plan-state/defects/{defect_id}",
        json={"status": "rejected", "evidence_ids": [ev_id]},
    )
    assert rejected.status_code == 200, rejected.text


def test_scene_plan_evaluate_migrates_template_after_scene_classification(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "nicht_klassifiziert"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    tasks = {t["id"] for t in evaluated.json()["data"]["state"]["tasks"]}
    assert "INSPECT_SCENE" not in tasks
    assert {"ANALYZE_SILHOUETTE", "TRACE_OUTER_WALLS", "PLACE_OPENINGS"} <= tasks


def test_scene_plan_rejected_false_positive_score_defect_does_not_reopen(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200
    first = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_walls": {
                "precision": 0.9,
                "recall": 0.9,
                "f1": 0.9,
                "missing_regions": [],
                "off_ink_segments": [[10, 20, 30, 20, 0.5]],
            },
        },
    )
    assert first.status_code == 200, first.text
    defect = next(d for d in first.json()["data"]["state"]["defects"] if d["category"] == "wall_off_ink")
    defect_id = defect["id"]
    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "analysis", "summary": "Visual crop shows this is false positive."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]
    assert client.post(
        f"/datasets/{key}/{file}/plan-state/defects/{defect_id}/classify",
        json={"classification": "false_positive", "evidence_ids": [ev_id]},
    ).status_code == 200
    assert client.patch(
        f"/datasets/{key}/{file}/plan-state/defects/{defect_id}",
        json={"status": "rejected", "evidence_ids": [ev_id]},
    ).status_code == 200

    second = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_walls": {
                "precision": 0.9,
                "recall": 0.9,
                "f1": 0.9,
                "missing_regions": [],
                "off_ink_segments": [[10, 20, 30, 20, 0.5]],
            },
        },
    )
    assert second.status_code == 200, second.text
    defect = next(d for d in second.json()["data"]["state"]["defects"] if d["id"] == defect_id)
    assert defect["status"] == "rejected"
    assert defect["classification"] == "false_positive"


def test_scene_plan_blocks_downstream_actions_when_wall_anchoring_failed(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["labels"] = [
        {
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [10, 20], "end": [200, 20]},
            "attributes": {},
        }
    ]
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200
    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_walls": {
                "precision": 0.1,
                "recall": 0.5,
                "f1": 0.16,
                "missing_regions": [[0, 50, 220, 20, 1200]],
                "off_ink_segments": [[10, 20, 200, 20, 0.0]],
            },
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    tasks = {t["id"]: t for t in evaluated.json()["data"]["state"]["tasks"]}
    verify_gates = {g["id"]: g["status"] for g in tasks["VERIFY_INTERIOR_TOPOLOGY"]["gates"]}
    assert verify_gates["WALL_INK_ANCHORED"] == "failed"
    updated_labels = client.get(f"/labels/dataset/{key}/{file}").json()["labels"]
    assert updated_labels[0]["status"] == "uncertain"
    assert updated_labels[0]["attributes"]["quality_status"] == "off_ink"
    candidates = client.get(f"/datasets/{key}/{file}/plan-state/repair-candidates").json()["data"]
    ops = {
        cand["op"]
        for cluster in candidates["clusters"]
        for cand in cluster.get("candidates") or []
    }
    assert "move_off_ink_wall_to_score_region" in ops
    assert "reanchor_off_ink_wall" in ops
    assert "delete_off_ink_wall_if_false_positive" in ops
    start = client.post(f"/datasets/{key}/{file}/plan-state/actions/ACT-PLACE_OPENINGS/start", json={})
    assert start.status_code == 400
    assert "wall_ink_anchor_blocked" in start.text


def test_scene_plan_rejects_geometry_attempt_under_classify_action(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    r = client.post(
        f"/datasets/{key}/{file}/plan-state/actions/ACT-CLASSIFY_SCENE/attempts",
        json={
            "hypothesis": "wrong action",
            "edits": [{"op": "upsert_label", "label": {"type": "wall"}}],
        },
    )
    assert r.status_code == 400
    assert "CLASSIFY_SCENE cannot record geometry edits" in r.text


def test_opening_cannot_use_off_ink_parent_wall(scene):
    key, file = scene
    client = TestClient(api_main.app)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["labels"] = [
        {
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [20, 40], "end": [200, 40]},
            "attributes": {"quality_status": "off_ink"},
        },
        {
            "id": "op-1",
            "type": "floorplan_opening",
            "status": "readable",
            "geometry": {"quad": [[70, 30], [120, 30], [120, 50], [70, 50]]},
            "attributes": {"opening_kind": "door"},
            "relations": [{"kind": "belongs_to", "other_id": "wall-1"}],
        },
    ]
    r = client.put(f"/labels/dataset/{key}/{file}", json=labels)
    assert r.status_code == 422
    assert "quality_status='off_ink'" in r.text


def test_opening_can_use_centerline_plausible_parent_wall(scene):
    key, file = scene
    client = TestClient(api_main.app)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["labels"] = [
        {
            "id": "wall-1",
            "type": "wall",
            "status": "uncertain",
            "geometry": {"start": [20, 40], "end": [200, 40]},
            "attributes": {
                "quality_status": "centerline_plausible",
                "confidence_reason": "faint_double_rail_centerline",
                "review_required": True,
            },
        },
        {
            "id": "op-1",
            "type": "floorplan_opening",
            "status": "readable",
            "geometry": {"quad": [[70, 30], [120, 30], [120, 50], [70, 50]]},
            "attributes": {"opening_kind": "door"},
            "relations": [{"kind": "belongs_to", "other_id": "wall-1"}],
        },
    ]
    r = client.put(f"/labels/dataset/{key}/{file}", json=labels)
    assert r.status_code == 200, r.text


def test_wall_centerline_review_closes_matching_off_ink_blocker(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["labels"] = [
        {
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [10, 20], "end": [200, 20]},
            "attributes": {},
        }
    ]
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

    first = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_walls": {
                "precision": 0.9,
                "recall": 0.9,
                "f1": 0.9,
                "missing_regions": [],
                "off_ink_segments": [[10, 20, 200, 20, 0.0]],
            },
        },
    )
    assert first.status_code == 200, first.text
    defect = next(d for d in first.json()["data"]["state"]["defects"] if d["category"] == "wall_off_ink")
    assert defect["payload"]["wall_id"] == "wall-1"

    review = client.post(
        f"/datasets/{key}/{file}/walls/wall-1/centerline-review",
        json={
            "review_region": [0, 0, 220, 50],
            "rail_evidence": ["upper rail y~12", "lower rail y~28", "saved centerline y~20"],
            "reason": "Detail crop shows a faint double-rail wall; saved wall is the intended centerline.",
            "confidence": "high",
        },
    )
    assert review.status_code == 200, review.text
    assert review.json()["data"]["closed_defect_ids"] == [defect["id"]]

    second = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_walls": {
                "precision": 0.9,
                "recall": 0.9,
                "f1": 0.9,
                "missing_regions": [],
                "off_ink_segments": [[10, 20, 200, 20, 0.0]],
            },
        },
    )
    assert second.status_code == 200, second.text
    state = second.json()["data"]["state"]
    defects = {d["id"]: d for d in state["defects"]}
    assert defects[defect["id"]]["status"] == "accepted_source_limited"
    assert defects[defect["id"]]["classification"] == "centerline_plausible_double_rail"
    labels_after = client.get(f"/labels/dataset/{key}/{file}").json()["labels"]
    wall = labels_after[0]
    assert wall["status"] == "uncertain"
    assert wall["attributes"]["quality_status"] == "centerline_plausible"
    tasks = {t["id"]: t for t in state["tasks"]}
    trace_gates = {g["id"]: g["status"] for g in tasks["TRACE_OUTER_WALLS"]["gates"]}
    assert trace_gates["WALL_INK_ANCHORED"] == "passed"
    assert state["current_state"]["wall_anchoring"]["centerline_plausible_count"] == 1
    assert state["current_state"]["label_quality"]["quality_statuses"]["centerline_plausible"] == 1


def test_scene_plan_closed_opening_absence_waives_opening_tasks(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["labels"] = [
        {
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [20, 20], "end": [200, 20]},
            "attributes": {},
        }
    ]
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200
    first = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
        },
    )
    assert first.status_code == 200, first.text
    defect = next(d for d in first.json()["data"]["state"]["defects"] if d["title"] == "No floorplan openings placed")
    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "verification", "summary": "No openings accepted for this test."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]
    accepted = client.patch(
        f"/datasets/{key}/{file}/plan-state/defects/{defect['id']}",
        json={"status": "accepted_uncertain", "evidence_ids": [ev_id]},
    )
    assert accepted.status_code == 200, accepted.text
    second = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
        },
    )
    assert second.status_code == 200, second.text
    tasks = {t["id"]: t for t in second.json()["data"]["state"]["tasks"]}
    assert tasks["PLACE_OPENINGS"]["status"] in {"todo", "in_progress", "needs_repair"}
    assert tasks["VERIFY_OPENINGS"]["status"] in {"todo", "in_progress", "needs_repair"}
    assert all(g["status"] == "waived" for g in tasks["VERIFY_OPENINGS"]["gates"])


def test_scene_plan_terminal_status_has_no_next_action(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "sonstiges"}).status_code == 200
    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "analysis", "summary": "Auxiliary scene inspected."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]
    for task_id, gate_id in [
        ("CLASSIFY_SCENE", "SCENE_CLASSIFIED"),
        ("INSPECT_SCENE", "HAS_ANALYSIS_EVIDENCE"),
    ]:
        r = client.patch(
            f"/datasets/{key}/{file}/plan-state/tasks/{task_id}",
            json={
                "status": "verified",
                "evidence_ids": [ev_id],
                "gate_updates": [{"id": gate_id, "status": "passed"}],
            },
        )
        assert r.status_code == 200, r.text
    final = client.patch(
        f"/datasets/{key}/{file}/plan-state/tasks/FINAL_QA",
        json={
            "status": "verified",
            "evidence_ids": [ev_id],
            "gate_updates": [{"id": "NO_BLOCKER_DEFECTS", "status": "passed"}],
        },
    )
    assert final.status_code == 200, final.text
    status = client.get(f"/datasets/{key}/{file}/plan-state/status")
    assert status.status_code == 200, status.text
    data = status.json()["data"]
    assert data["terminal"] is True
    assert data["next_action_available"] is False
    assert data["next_action"] is None


def test_scene_plan_rejects_accepted_incomplete_required_task(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "analysis", "summary": "Cannot verify in test."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]

    r = client.patch(
        f"/datasets/{key}/{file}/plan-state/tasks/CLASSIFY_SCENE",
        json={"status": "accepted_incomplete", "evidence_ids": [ev_id]},
    )
    assert r.status_code == 400
    assert "required task cannot be accepted_incomplete" in r.text

    status = client.get(f"/datasets/{key}/{file}/plan-state/status")
    assert status.status_code == 200, status.text
    data = status.json()["data"]
    assert data["required_complete"] is False
    assert data["status"] == "active"
    assert any("required tasks open" in r for r in data["terminality_reasons"])


def test_scene_plan_required_task_cannot_verify_with_waived_gate(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "analysis", "summary": "Waiver attempt."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]

    r = client.patch(
        f"/datasets/{key}/{file}/plan-state/tasks/ANALYZE_SILHOUETTE",
        json={
            "status": "verified",
            "evidence_ids": [ev_id],
            "gate_updates": [{"id": "HAS_SILHOUETTE_HYPOTHESIS", "status": "waived"}],
        },
    )
    assert r.status_code == 400
    assert "required tasks cannot use waived gates" in r.text


def test_scene_plan_task_action_rejects_accepted_uncertain_shortcut(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    action = client.get(f"/datasets/{key}/{file}/plan-state/next-action")
    assert action.status_code == 200
    action_id = action.json()["data"]["action"]["action_id"]
    started = client.post(f"/datasets/{key}/{file}/plan-state/actions/{action_id}/start", json={})
    assert started.status_code == 200

    evidence = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={"kind": "human_note", "mode": "analysis", "summary": "Shortcut attempt."},
    )
    assert evidence.status_code == 200
    ev_id = evidence.json()["data"]["state"]["evidence"][-1]["id"]
    attempt = client.post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/attempts",
        json={"hypothesis": "try shortcut", "evidence_ids": [ev_id]},
    )
    assert attempt.status_code == 200

    finish = client.post(
        f"/datasets/{key}/{file}/plan-state/actions/{action_id}/finish",
        json={"outcome": "accepted_uncertain", "evidence_ids": [ev_id]},
    )
    assert finish.status_code == 400
    assert "task actions cannot be accepted_uncertain" in finish.text


def test_scene_plan_measurement_unmatched_ticks_create_dimension_defects(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200
    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_measurements": {
                "ok": False,
                "n_dims": 1,
                "n_walls": 1,
                "total_ticks": 2,
                "matched_ticks": 1,
                "match_frac": 0.5,
                "unmatched_ticks": [{"axis": "x", "pos": 100.0, "nearest": 140.0, "dist": 40.0}],
                "chains": [],
            },
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    defects = evaluated.json()["data"]["state"]["defects"]
    defect = next(d for d in defects if d["title"] == "Measurement unmatched tick 1")
    assert defect["severity"] == "blocker"
    assert defect["category"] == "dimension"


def test_scene_plan_zero_dimension_measurement_result_does_not_verify_dimension_tasks(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_measurements": {
                "ok": True,
                "n_dims": 0,
                "n_walls": 1,
                "total_ticks": 0,
                "matched_ticks": 0,
                "match_frac": 1.0,
                "unmatched_ticks": [],
                "chains": [],
            },
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    tasks = {t["id"]: t for t in evaluated.json()["data"]["state"]["tasks"]}
    assert tasks["READ_DIMENSIONS"]["status"] != "verified"
    assert tasks["VERIFY_MEASUREMENTS"]["status"] != "verified"
    assert tasks["READ_DIMENSIONS"]["gates"][0]["status"] == "pending"
    assert tasks["VERIFY_MEASUREMENTS"]["gates"][0]["status"] == "pending"


def test_scene_plan_rejects_source_unreadable_dimension_review_without_fragments(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200

    review = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={
            "kind": "dimension_chain_review",
            "mode": "analysis",
            "summary": "Perimeter chain is unreadable.",
            "result": {
                "chain_region": [10, 20, 120, 60],
                "orientation": "horizontal",
                "decision": "source_unreadable",
                "readable_values": [],
                "unreadable_fragments": [],
            },
        },
    )
    assert review.status_code == 400
    assert "unreadable_fragments" in review.text


def test_scene_plan_source_unreadable_dimension_review_closes_measurement_gates(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

    review = client.post(
        f"/datasets/{key}/{file}/plan-state/evidence",
        json={
            "kind": "dimension_chain_review",
            "mode": "analysis",
            "summary": "Horizontal perimeter chain is source-unreadable after enhanced zoom.",
            "tool": "record_dimension_chain_review",
            "result": {
                "chain_region": [10, 20, 120, 60],
                "orientation": "horizontal",
                "decision": "source_unreadable",
                "readable_values": [],
                "unreadable_fragments": ["dimension text washed out", "ticks visible but numbers illegible"],
                "reason": "Values remain illegible after threshold enhancement.",
                "enhance": "threshold",
            },
        },
    )
    assert review.status_code == 200, review.text
    ev_id = review.json()["data"]["state"]["evidence"][-1]["id"]

    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    state = evaluated.json()["data"]["state"]
    tasks = {t["id"]: t for t in state["tasks"]}
    read_gate = tasks["READ_DIMENSIONS"]["gates"][0]
    verify_gate = tasks["VERIFY_MEASUREMENTS"]["gates"][0]
    assert read_gate["status"] == "passed"
    assert read_gate["evidence_ids"] == [ev_id]
    assert read_gate["waiver_reason"] == "Readable dimension chain source is documented as unreadable."
    assert verify_gate["status"] == "passed"
    assert verify_gate["evidence_ids"] == [ev_id]
    assert verify_gate["waiver_reason"] == "Measurement verification closed from source-unreadable chain review."
    assert state["current_state"]["source_unreadable"][0]["evidence_id"] == ev_id
    assert state["current_state"]["quality_tier"] == "blocked"
    assert state["current_state"]["review_debt"] >= 6
    assert state["current_state"]["final_qa_summary"]["source_unreadable"][0]["evidence_id"] == ev_id


def test_scene_plan_wall_blockers_keep_topology_tasks_in_repair(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["labels"] = [
        {
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [20, 20], "end": [200, 20]},
            "attributes": {},
        }
    ]
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_walls": {
                "precision": 0.8,
                "recall": 0.8,
                "f1": 0.8,
                "missing_regions": [[10, 20, 30, 40, 1200]],
                "off_ink_segments": [],
            },
            "topology_qa": {
                "wall_count": 1,
                "endpoint_count": 2,
                "dangling_endpoints": [],
                "near_miss_corners": [],
                "collinear_fragments": [],
                "short_stubs": [],
                "components": [],
            },
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    tasks = {t["id"]: t for t in evaluated.json()["data"]["state"]["tasks"]}
    for task_id in ("VERIFY_OUTER_TOPOLOGY", "VERIFY_INTERIOR_TOPOLOGY"):
        assert tasks[task_id]["status"] == "needs_repair"
        assert {g["id"]: g["status"] for g in tasks[task_id]["gates"]} == {
            "TOPOLOGY_REVIEWED": "failed",
            "WALL_SCORE_REVIEWED": "failed",
            "WALL_INK_ANCHORED": "failed",
        }


def test_scene_plan_wall_score_missing_region_uses_review_bbox_not_xywh(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["labels"] = [
        {
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [20, 20], "end": [200, 20]},
            "attributes": {},
        }
    ]
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": False,
            "run_continuity_check": False,
            "score_walls": {
                "precision": 0.8,
                "recall": 0.8,
                "f1": 0.8,
                "missing_regions": [[10, 20, 30, 40, 1200]],
                "off_ink_segments": [],
            },
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    state = evaluated.json()["data"]["state"]
    defect = next(d for d in state["defects"] if d["category"] == "wall_missing_region")
    assert defect["region"] == [10, 20, 40, 60]
    finding = next(f for f in state["current_state"]["findings"]["items"] if f["category"] == "missing_region")
    assert finding["region"] == [10, 20, 40, 60]


def _put_near_miss_wall_labels(client: TestClient, key: str, file: str) -> None:
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["labels"] = [
        {
            "id": "wall-a",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [20, 100], "end": [100, 100]},
            "attributes": {},
        },
        {
            "id": "wall-b",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [118, 112], "end": [118, 190]},
            "attributes": {},
        },
    ]
    assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200


def test_scene_plan_current_findings_dedupe_repeated_topology_evaluation(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    _put_near_miss_wall_labels(client, key, file)

    body = {
        "run_score_walls": False,
        "run_score_measurements": False,
        "run_topology_qa": True,
        "run_continuity_check": False,
    }
    first = client.post(f"/datasets/{key}/{file}/plan-state/evaluate-gates", json=body)
    assert first.status_code == 200, first.text
    second = client.post(f"/datasets/{key}/{file}/plan-state/evaluate-gates", json=body)
    assert second.status_code == 200, second.text

    state = second.json()["data"]["state"]
    current = state["current_state"]
    open_topology = [
        d for d in state["defects"]
        if d["status"] in {"open", "in_progress"} and d["category"] in {"wall_topology", "possible_split_wall"}
    ]
    fingerprints = [d.get("_auto_fingerprint") for d in open_topology]
    assert len(fingerprints) == len(set(fingerprints))
    assert current["findings"]["warnings"] >= 1
    assert current["finding_clusters"]["count"] >= 1


def test_repair_candidate_report_overlay_and_apply(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    _put_near_miss_wall_labels(client, key, file)

    report = client.get(f"/datasets/{key}/{file}/plan-state/repair-candidates")
    assert report.status_code == 200, report.text
    data = report.json()["data"]
    assert data["cluster_count"] >= 1
    candidates = [c for cluster in data["clusters"] for c in cluster["candidates"]]
    candidate = next(c for c in candidates if c["op"] == "snap_endpoint_to_endpoint")
    candidate_id = candidate["candidate_id"]

    overlay = client.get(f"/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/overlay")
    assert overlay.status_code == 200, overlay.text
    assert overlay.headers["content-type"].startswith("image/png")

    applied = client.post(f"/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/apply", json={})
    assert applied.status_code == 200, applied.text
    assert applied.json()["data"]["persisted"] is True

    labels = client.get(f"/labels/dataset/{key}/{file}").json()
    by_id = {lab["id"]: lab for lab in labels["labels"]}
    assert by_id["wall-a"]["geometry"]["end"] == by_id["wall-b"]["geometry"]["start"]

    quality = client.get(f"/datasets/{key}/{file}/plan-state/quality-report")
    assert quality.status_code == 200, quality.text
    quality_data = quality.json()["data"]
    assert quality_data["candidate_repairs_accepted"] == 1
    assert quality_data["candidate_decision_count"] == 1
    assert quality_data["visual_crop_inspections_per_accepted_repair"][candidate_id] >= 1

    snapshot = client.get(f"/datasets/{key}/{file}/plan-state/topology-snapshot")
    assert snapshot.status_code == 200, snapshot.text
    snapshot_data = snapshot.json()["data"]
    assert "dangling_endpoints" in snapshot_data["topology"]
    assert snapshot_data["candidate_count"] >= 0
    assert "accepted_applied" in snapshot_data["decision_outcomes"]


def test_gold_quality_profile_escalates_high_confidence_topology_candidate(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    _put_near_miss_wall_labels(client, key, file)

    evaluated = client.post(
        f"/datasets/{key}/{file}/plan-state/evaluate-gates",
        json={
            "run_score_walls": False,
            "run_score_measurements": False,
            "run_topology_qa": True,
            "run_continuity_check": False,
            "quality_profile": "gold",
        },
    )
    assert evaluated.status_code == 200, evaluated.text
    defects = evaluated.json()["data"]["state"]["defects"]
    assert any(
        d["category"] == "topology_candidate_review" and d["severity"] == "blocker"
        for d in defects
    )


def test_repair_candidate_decision_clears_gold_high_confidence_blocker(scene):
    key, file = scene
    client = TestClient(api_main.app)
    assert client.post(f"/datasets/{key}/{file}/plan-state/template", json={"scene_tag": "grundriss"}).status_code == 200
    _put_near_miss_wall_labels(client, key, file)

    body = {
        "run_score_walls": False,
        "run_score_measurements": False,
        "run_topology_qa": True,
        "run_continuity_check": False,
        "quality_profile": "gold",
    }
    first = client.post(f"/datasets/{key}/{file}/plan-state/evaluate-gates", json=body)
    assert first.status_code == 200, first.text
    report = client.get(f"/datasets/{key}/{file}/plan-state/repair-candidates")
    assert report.status_code == 200, report.text
    candidate = next(
        c
        for cluster in report.json()["data"]["clusters"]
        for c in cluster["candidates"]
        if c["op"] == "snap_endpoint_to_endpoint"
    )

    decided = client.post(
        f"/datasets/{key}/{file}/plan-state/repair-candidates/{candidate['candidate_id']}/decision",
        json={"outcome": "rejected_false_positive", "note": "test classification"},
    )
    assert decided.status_code == 200, decided.text

    second = client.post(f"/datasets/{key}/{file}/plan-state/evaluate-gates", json=body)
    assert second.status_code == 200, second.text
    defects = second.json()["data"]["state"]["defects"]
    open_gold = [
        d for d in defects
        if d["category"] == "topology_candidate_review" and d["status"] in {"open", "in_progress"}
    ]
    assert open_gold == []

    quality = client.get(f"/datasets/{key}/{file}/plan-state/quality-report")
    assert quality.status_code == 200, quality.text
    quality_data = quality.json()["data"]
    assert quality_data["candidate_rejections_false_positive"] == 1
    assert quality_data["final_unclassified_high_confidence_warning_count"] == 0
