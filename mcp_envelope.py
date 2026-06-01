"""Shared MCP response envelope helpers."""
from __future__ import annotations

import json
import time
from typing import Any

from mcp.types import TextContent

_server_version = "unknown"


def configure_envelope(server_version: str) -> None:
    global _server_version
    _server_version = server_version


def ok(
    data: Any,
    *,
    next_tool: dict | None = None,
    started_at: float | None = None,
    status_code: int | None = None,
) -> dict:
    return {
        "ok": True,
        "data": data,
        "next_recommended_tool": next_tool,
        "_meta": meta(started_at, status_code),
    }


def err(
    code: str,
    message: str,
    *,
    hint: str = "",
    retry: bool = False,
    details: dict | None = None,
    started_at: float | None = None,
    status_code: int | None = None,
) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
            "retry_advisable": retry,
            "details": details or {},
        },
        "_meta": meta(started_at, status_code),
    }


def meta(started_at: float | None, status_code: int | None) -> dict:
    return {
        "tool_call_id": f"tc-{int(time.time() * 1000):x}",
        "api_status_code": status_code,
        "latency_ms": int((time.time() - started_at) * 1000) if started_at else None,
        "server_version": _server_version,
    }


def http_status_to_error(status: int, body: Any, started_at: float) -> dict:
    detail = body
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
    if status == 404:
        return err("not_found", str(detail), retry=False, started_at=started_at, status_code=status)
    if status == 409:
        return err(
            "conflict",
            str(detail),
            hint="re-fetch state and retry",
            retry=False,
            started_at=started_at,
            status_code=status,
        )
    if status == 422 or status == 400:
        return err(
            "schema_invalid",
            str(detail),
            hint="fix the payload",
            retry=False,
            started_at=started_at,
            status_code=status,
        )
    if 400 <= status < 500:
        return err(f"http_{status}", str(detail), retry=False, started_at=started_at, status_code=status)
    return err("api_5xx", str(detail), retry=True, started_at=started_at, status_code=status)


def wrap_text(envelope: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(envelope, indent=2))]
