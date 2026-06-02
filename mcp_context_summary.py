"""Compact summary helpers for MCP context-reduction tools."""
from __future__ import annotations

from collections import Counter
from typing import Any


def compact_scene_row(drawing: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": drawing.get("file"),
        "title": drawing.get("title"),
        "extraction_kind": drawing.get("kind"),
        "manifest_floor": drawing.get("floor"),
        "manifest_view": drawing.get("view"),
        "scene_tag": meta.get("scene_tag"),
        "scene_level": meta.get("scene_level"),
        "scene_orientation": meta.get("scene_orientation"),
        "labeled": bool(drawing.get("labeled")),
        "label_count": meta.get("label_count", drawing.get("label_count", 0)),
        "label_types": meta.get("label_types") or [],
    }


def compact_plan_status(plan_status: dict[str, Any] | None, max_blockers: int = 3) -> dict[str, Any]:
    if not plan_status:
        return {
            "exists": False,
            "status": "missing",
            "summary": "No structured scene plan exists.",
            "next_action": None,
            "blocker_count": 0,
            "blockers": [],
            "truncated": False,
        }
    blockers = plan_status.get("blockers") or plan_status.get("blocking_defects") or []
    if not isinstance(blockers, list):
        blockers = []
    return {
        "exists": bool(plan_status.get("exists", True)),
        "status": plan_status.get("status") or plan_status.get("terminality") or plan_status.get("state"),
        "required_complete": bool(plan_status.get("required_complete")),
        "percent_complete": plan_status.get("percent_complete"),
        "summary": plan_status.get("summary") or plan_status.get("current_summary"),
        "next_action": plan_status.get("next_action"),
        "blocker_count": len(blockers),
        "blockers": blockers[:max_blockers],
        "truncated": len(blockers) > max_blockers,
    }


def compact_label(label: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": label.get("id"),
        "type": label.get("type"),
        "status": label.get("status"),
        "summary": label_summary(label),
    }


def label_counts(labels: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(label.get("type") or "unknown") for label in labels))


def label_summary(label: dict[str, Any]) -> str:
    """One-line human description for summary views."""
    label_type = label.get("type")
    attrs = label.get("attributes") or {}
    geom = label.get("geometry") or {}
    if label_type == "wall":
        return f"thickness={attrs.get('thickness_mm')}mm"
    if label_type in ("floorplan_opening", "view_opening"):
        kind = attrs.get("opening_kind")
        return f"{kind} width={attrs.get('width_mm', '?')}mm"
    if label_type == "component_line":
        n = len(geom.get("points") or [])
        return f"{attrs.get('line_kind', 'unknown')} ({n} pts)"
    if label_type == "height_mark":
        return f"value={attrs.get('value_mm')}mm datum={attrs.get('datum')}"
    if label_type == "dimensioned_distance":
        ref = " (REF)" if attrs.get("is_reference") else ""
        return f"value={attrs.get('value_mm')}mm{ref}"
    if label_type == "dimension_number":
        return f"text={attrs.get('text')!r}"
    return ""
