"""Image payload delivery helpers for the BIM MCP server."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from mcp.types import ImageContent, TextContent

IMAGE_ARTIFACT_DIR = Path(os.environ.get("BIM_MCP_IMAGE_ARTIFACT_DIR", Path(__file__).parent / "tmp" / "mcp-images"))
DEFAULT_IMAGE_DELIVERY = os.environ.get("BIM_MCP_IMAGE_DELIVERY_DEFAULT", "inline").strip().lower() or "inline"


def image_response(
    content: bytes,
    ctype: str,
    data: dict,
    *,
    started_at: float,
    status_code: int,
    server_version: str,
    delivery: str | None = None,
    artifact_meta: dict | None = None,
    artifact_dir: Path | None = None,
) -> list[ImageContent | TextContent]:
    """Return image content inline, as a persisted handle, or both."""
    mode = _normalize_image_delivery(delivery)
    image_bytes = len(content)
    data = {
        **data,
        "image_bytes": image_bytes,
        "image_delivery": mode,
    }
    parts: list[ImageContent | TextContent] = []
    if mode in ("handle", "both"):
        artifact = _write_image_artifact(content, ctype, artifact_meta or {}, artifact_dir or IMAGE_ARTIFACT_DIR)
        data["image_artifact"] = artifact
        data["inline_image_omitted"] = mode == "handle"
        data["base64_chars_avoided"] = len(base64.b64encode(content)) if mode == "handle" else 0
    if mode in ("inline", "both"):
        parts.append(ImageContent(
            type="image",
            data=base64.b64encode(content).decode("ascii"),
            mimeType=ctype or "image/png",
        ))
    parts.append(TextContent(
        type="text",
        text=json.dumps(_ok(
            data,
            started_at=started_at,
            status_code=status_code,
            server_version=server_version,
        ), indent=2),
    ))
    return parts


def _ok(
    data: Any,
    *,
    started_at: float | None = None,
    status_code: int | None = None,
    server_version: str,
) -> dict:
    return {
        "ok": True,
        "data": data,
        "next_recommended_tool": None,
        "_meta": _meta(started_at, status_code, server_version),
    }


def _meta(started_at: float | None, status_code: int | None, server_version: str) -> dict:
    return {
        "tool_call_id": f"tc-{int(time.time() * 1000):x}",
        "api_status_code": status_code,
        "latency_ms": int((time.time() - started_at) * 1000) if started_at else None,
        "server_version": server_version,
    }


def _normalize_image_delivery(delivery: str | None) -> str:
    mode = (delivery or DEFAULT_IMAGE_DELIVERY).strip().lower()
    if mode not in {"inline", "handle", "both"}:
        return DEFAULT_IMAGE_DELIVERY if DEFAULT_IMAGE_DELIVERY in {"inline", "handle", "both"} else "inline"
    return mode


def _write_image_artifact(content: bytes, ctype: str, meta: dict, artifact_dir: Path) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    ext = _image_ext_for_content_type(ctype)
    digest = hashlib.sha256(content).hexdigest()[:24]
    image_path = artifact_dir / f"{digest}.{ext}"
    meta_path = artifact_dir / f"{digest}.json"
    if not image_path.exists():
        image_path.write_bytes(content)
    payload = {
        "path": str(image_path),
        "content_type": ctype or "image/png",
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        **meta,
    }
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {**payload, "metadata_path": str(meta_path)}


def _image_ext_for_content_type(ctype: str) -> str:
    ctype = (ctype or "").lower()
    if "jpeg" in ctype or "jpg" in ctype:
        return "jpg"
    if "webp" in ctype:
        return "webp"
    return "png"
