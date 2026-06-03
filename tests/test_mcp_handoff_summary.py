from __future__ import annotations

import asyncio
import json
from pathlib import Path

import mcp_server


def test_write_handoff_summary_writes_bounded_json(tmp_path: Path, monkeypatch) -> None:
    fake_server = tmp_path / "mcp_server.py"
    fake_server.write_text("# fake")
    monkeypatch.setattr(mcp_server, "__file__", str(fake_server))

    result = asyncio.run(mcp_server.write_handoff_summary(
        key="house-22",
        run_id="run 1",
        file="house-22-floorplan-eg.jpg",
        phase="floorplan",
        status="needs_repair",
        labels_added=2,
        open_defects=["d1", "d2", "d3"],
        evidence_refs=["ev1", "ev2"],
        next_action="repair d1",
        quality={"score_walls_f1": 0.91},
        max_items=2,
    ))

    assert result["ok"], result
    data = result["data"]
    payload = data["summary"]
    assert payload["summary_contract"] == "mcp-context-bloat/handoff-summary-v1"
    assert payload["open_defects"] == ["d1", "d2"]
    assert payload["truncated"] is True
    assert payload["truncation"]["open_defects"]["omitted"] == 1
    json_path = tmp_path / data["json_path"]
    assert json_path.exists()
    assert json.loads(json_path.read_text())["next_action"] == "repair d1"


def test_inspect_agent_run_joins_scene_plan_labels_and_handoff(tmp_path: Path, monkeypatch) -> None:
    fake_server = tmp_path / "mcp_server.py"
    fake_server.write_text("# fake")
    monkeypatch.setattr(mcp_server, "__file__", str(fake_server))

    handoff_dir = tmp_path / "tmp" / "agent-runs" / "run-1" / "handoffs"
    handoff_dir.mkdir(parents=True)
    (handoff_dir / "house-22-floorplan-eg.jpg.json").write_text(json.dumps({
        "summary_contract": "mcp-context-bloat/handoff-summary-v1",
        "key": "house-22",
        "file": "house-22-floorplan-eg.jpg",
        "phase": "floorplan",
        "status": "needs_repair",
        "quality": {"quality_tier": "silver"},
        "evidence_refs": ["EV-1"],
        "next_action": "review wall W1",
        "written_at": "2026-06-03T00:00:00Z",
    }))

    async def fake_get(path: str, params=None):
        if path == "/datasets/house-22":
            return 200, {"drawings": [{"file": "house-22-floorplan-eg.jpg", "kind": "floorplan"}]}
        if path == "/labels/dataset/house-22/house-22-floorplan-eg.jpg":
            return 200, {
                "labels": [
                    {
                        "id": "W1",
                        "type": "wall",
                        "status": "uncertain",
                        "attributes": {
                            "quality_status": "centerline_plausible",
                            "confidence_reason": "faint_double_rail_centerline",
                        },
                    }
                ]
            }
        if path == "/datasets/house-22/house-22-floorplan-eg.jpg/plan-state":
            return 200, {
                "data": {
                    "state": {
                        "status": "verified",
                        "current_state": {
                            "final_qa_summary": {"tier": "silver"},
                        },
                        "defects": [
                            {
                                "id": "DEF-1",
                                "title": "Wall needs review",
                                "category": "wall_off_ink",
                                "severity": "warning",
                                "status": "accepted_risk",
                                "evidence_ids": ["EV-1"],
                            }
                        ],
                        "evidence": [
                            {
                                "id": "EV-1",
                                "kind": "wall_centerline_review",
                                "mode": "verification",
                                "tool": "review_wall_centerline_between_rails",
                                "summary": "W1 is centered between faint rails.",
                                "result": {"wall_id": "W1"},
                                "created_at": "2026-06-03T00:00:00Z",
                            }
                        ],
                    }
                }
            }
        raise AssertionError(path)

    monkeypatch.setattr(mcp_server, "_api_get", fake_get)

    result = asyncio.run(mcp_server.inspect_agent_run(
        key="house-22",
        run_id="run 1",
        file="house-22-floorplan-eg.jpg",
        label_id="W1",
        defect_id="DEF-1",
    ))

    assert result["ok"], result
    data = result["data"]
    assert data["summary_contract"] == "agent-run-inspector/v1"
    assert data["scene_count"] == 1
    scene = data["scenes"][0]
    assert scene["quality_tier"] == "silver"
    assert scene["matching_labels"][0]["quality_status"] == "centerline_plausible"
    assert scene["matching_defects"][0]["id"] == "DEF-1"
    assert scene["recent_evidence"][0]["id"] == "EV-1"
    assert scene["handoffs"][0]["next_action"] == "review wall W1"
