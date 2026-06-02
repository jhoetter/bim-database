from __future__ import annotations

import json
from pathlib import Path

import mcp_server


def test_image_delivery_inline_returns_image_and_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "IMAGE_HANDLE_DIR", tmp_path)

    result = mcp_server._image_delivery_payload(
        content=b"png-bytes",
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
    assert env["data"]["image_bytes"] == len(b"png-bytes")


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
