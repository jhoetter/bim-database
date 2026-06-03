from __future__ import annotations

import json
from pathlib import Path
import asyncio
import os
import time

import mcp_server
import mcp_tools_geometry
from PIL import Image


def test_image_delivery_inline_returns_image_and_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "IMAGE_HANDLE_DIR", tmp_path)
    png_path = tmp_path / "tiny.png"
    Image.new("RGB", (2, 3), "white").save(png_path)
    content = png_path.read_bytes()

    result = mcp_server._image_delivery_payload(
        content=content,
        ctype="image/png",
        metadata={"region": "1,2,3,4"},
        started_at=0,
        status_code=200,
        image_delivery="inline",
    )

    assert len(result) == 2
    assert result[0].type == "image"
    env = json.loads(result[1].text)
    assert env["ok"]
    assert env["data"]["image_delivery"] == "inline"
    assert env["data"]["image_bytes"] == len(content)
    assert env["data"]["output_image_size_px"] == [2, 3]
    assert env["data"]["context_telemetry"]["payload_strategy"] == "inline_base64"
    assert env["data"]["context_telemetry"]["estimated_inline_tokens"] > 0


def test_image_delivery_handle_omits_inline_base64(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "IMAGE_HANDLE_DIR", tmp_path)

    result = mcp_server._image_delivery_payload(
        content=b"png-bytes",
        ctype="image/png",
        metadata={"region": None},
        started_at=0,
        status_code=200,
        image_delivery="handle",
    )

    assert len(result) == 1
    assert result[0].type == "text"
    env = json.loads(result[0].text)
    assert env["ok"]
    data = env["data"]
    assert data["image_delivery"] == "handle"
    assert "image_handle" in data
    assert data["context_telemetry"]["payload_strategy"] == "file_handle"
    handle_path = Path(data["image_handle"]["path"])
    assert handle_path.exists()
    assert handle_path.read_bytes() == b"png-bytes"
    assert "data" not in data["image_handle"]


def test_image_delivery_auto_uses_handle_above_threshold(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "IMAGE_HANDLE_DIR", tmp_path)
    monkeypatch.setattr(mcp_server, "IMAGE_HANDLE_INLINE_THRESHOLD", 3)

    result = mcp_server._image_delivery_payload(
        content=b"large",
        ctype="image/png",
        metadata={},
        started_at=0,
        status_code=200,
        image_delivery="auto",
    )

    env = json.loads(result[-1].text)
    assert env["data"]["image_delivery"] == "handle"
    assert "data" not in env["data"]


def test_image_delivery_empty_mode_defaults_to_auto_handle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "IMAGE_HANDLE_DIR", tmp_path)
    monkeypatch.setattr(mcp_server, "IMAGE_HANDLE_INLINE_THRESHOLD", 3)

    result = mcp_server._image_delivery_payload(
        content=b"large",
        ctype="image/png",
        metadata={},
        started_at=0,
        status_code=200,
        image_delivery="",
    )

    env = json.loads(result[-1].text)
    assert env["data"]["image_delivery"] == "handle"


def test_response_mode_payload_compact_and_full() -> None:
    full = {
        "state": {
            "key": "house-x",
            "file": "scene.png",
            "version": "v1",
            "current_state": {
                "terminality": {
                    "terminal": False,
                    "percent_complete": 25,
                    "required_complete": False,
                    "summary": "work remains",
                },
                "label_counts": {"wall": 2},
            },
            "tasks": [{"id": "A1", "required": True, "status": "todo", "title": "Analyze"}],
            "defects": [{"id": "D1", "status": "open", "severity": "blocker", "title": "Fix wall"}],
            "evidence": [{"id": "E1"}],
        },
        "actionable_tasks": [{"id": "A1"}],
    }

    compact = mcp_server._response_mode_payload(full, response_mode="compact", action="test")
    assert compact["summary_contract"] == "plan-mutation-summary/v1"
    assert compact["action"] == "test"
    assert compact["key"] == "house-x"
    assert compact["label_counts"] == {"wall": 2}
    assert compact["open_blocker_count"] == 1
    assert compact["latest_evidence_id"] == "E1"

    assert mcp_server._response_mode_payload(full, response_mode="full") == full


def test_opening_candidate_apply_summary_omits_full_candidate_geometry() -> None:
    summary = mcp_tools_geometry._compact_opening_candidate_apply({
        "candidate_id": "OPEN-1",
        "candidate_fingerprint": "fp",
        "persisted": True,
        "label_id": "opening-1",
        "candidate": {
            "kind": "wall_gap",
            "confidence": "high",
            "parent_wall_id": "wall-1",
            "opening_kind": "door",
            "region": [1, 2, 3, 4],
            "geometry": {"quad": [[1, 2], [3, 4], [5, 6], [7, 8]]},
        },
        "decision": {"OPEN-1": {"outcome": "accepted_applied"}},
    })

    assert summary["summary_contract"] == "opening-candidate-apply-summary/v1"
    assert summary["label_id"] == "opening-1"
    assert summary["candidate"]["parent_wall_id"] == "wall-1"
    assert "geometry" not in summary["candidate"]


def test_cleanup_image_handles_removes_old_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "IMAGE_HANDLE_DIR", tmp_path)
    old = tmp_path / "old.png"
    new = tmp_path / "new.png"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    old_time = time.time() - 10
    os.utime(old, (old_time, old_time))

    result = asyncio.run(mcp_server.cleanup_image_handles(max_age_seconds=5))

    assert result["ok"], result
    assert result["data"]["removed_count"] == 1
    assert not old.exists()
    assert new.exists()
