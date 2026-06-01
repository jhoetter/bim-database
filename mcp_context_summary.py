"""Compact summary helpers for MCP context-reduction tools."""
from __future__ import annotations


def compact_scene_row(drawing: dict, meta: dict) -> dict:
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


def compact_plan_status(plan_status: dict, max_blockers: int = 3) -> dict:
    blockers = plan_status.get("blockers") or plan_status.get("blocking_defects") or []
    if not isinstance(blockers, list):
        blockers = []
    return {
        "exists": bool(plan_status.get("exists", True)),
        "status": plan_status.get("status") or plan_status.get("terminality") or plan_status.get("state"),
        "summary": plan_status.get("summary") or plan_status.get("current_summary"),
        "next_action": plan_status.get("next_action"),
        "blocker_count": len(blockers),
        "blockers": blockers[:max_blockers],
        "truncated": len(blockers) > max_blockers,
    }


def label_summary(label: dict) -> str:
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
