from __future__ import annotations

import base64
import json

import mcp_server
from mcp.types import ImageContent, TextContent


def test_image_response_handle_writes_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "IMAGE_ARTIFACT_DIR", tmp_path)
    content = b"fake-png-bytes"

    parts = mcp_server._image_response(
        content,
        "image/png",
        {"image_format": "PNG", "region": "1,2,3,4"},
        started_at=1.0,
        status_code=200,
        delivery="handle",
        artifact_meta={"tool": "test"},
    )

    assert len(parts) == 1
    assert isinstance(parts[0], TextContent)
    env = json.loads(parts[0].text)
    data = env["data"]
    assert data["image_delivery"] == "handle"
    assert data["inline_image_omitted"] is True
    assert data["base64_chars_avoided"] == len(base64.b64encode(content))
    assert data["image_artifact"]["path"].endswith(".png")
    assert data["image_artifact"]["bytes"] == len(content)


def test_image_response_inline_preserves_image_content(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "IMAGE_ARTIFACT_DIR", tmp_path)

    parts = mcp_server._image_response(
        b"fake-jpg-bytes",
        "image/jpeg",
        {"image_format": "JPEG"},
        started_at=1.0,
        status_code=200,
        delivery="inline",
    )

    assert len(parts) == 2
    assert isinstance(parts[0], ImageContent)
    assert isinstance(parts[1], TextContent)
    env = json.loads(parts[1].text)
    assert env["data"]["image_delivery"] == "inline"
    assert "image_artifact" not in env["data"]
