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
