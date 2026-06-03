"""Compact summary helpers for MCP context-reduction tools."""
from __future__ import annotations

from collections import Counter
from typing import Any


QUALITY_TIERS = ("gold", "silver", "bronze", "blocked")


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
            "quality_tier": "blocked",
            "completion_state": "blocked_tooling",
            "review_debt": 20,
            "human_review_required": True,
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
        "quality_tier": plan_status.get("quality_tier"),
        "completion_state": plan_status.get("completion_state"),
        "review_debt": int(plan_status.get("review_debt") or 0),
        "human_review_required": bool(((plan_status.get("final_qa_summary") or {}).get("human_review_required"))),
        "summary": plan_status.get("summary") or plan_status.get("current_summary"),
        "next_action": plan_status.get("next_action"),
        "blocker_count": len(blockers),
        "blockers": blockers[:max_blockers],
        "truncated": len(blockers) > max_blockers,
    }


def aggregate_house_quality(scene_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate compact scene plan quality into a house-level dashboard."""
    tier_counts: Counter[str] = Counter()
    total_review_debt = 0
    review_required_scenes: list[str] = []
    blocked_scenes: list[str] = []
    missing_plan_scenes: list[str] = []
    for row in scene_rows:
        file_name = str(row.get("file") or "")
        plan = row.get("plan") if isinstance(row.get("plan"), dict) else None
        if not plan or not plan.get("exists"):
            tier = "blocked"
            missing_plan_scenes.append(file_name)
        else:
            tier = str(plan.get("quality_tier") or "blocked")
            if tier not in QUALITY_TIERS:
                tier = "blocked"
            total_review_debt += int(plan.get("review_debt") or 0)
            if plan.get("human_review_required"):
                review_required_scenes.append(file_name)
        tier_counts[tier] += 1
        if tier == "blocked":
            blocked_scenes.append(file_name)
    scene_count = len(scene_rows)
    high_confidence_complete = scene_count > 0 and tier_counts.get("gold", 0) == scene_count and total_review_debt == 0
    return {
        "scene_count": scene_count,
        "tier_counts": {tier: int(tier_counts.get(tier, 0)) for tier in QUALITY_TIERS},
        "total_review_debt": total_review_debt,
        "human_review_required": bool(review_required_scenes or tier_counts.get("silver") or tier_counts.get("bronze")),
        "review_required_scenes": review_required_scenes,
        "blocked_scenes": blocked_scenes,
        "missing_plan_scenes": missing_plan_scenes,
        "high_confidence_complete": high_confidence_complete,
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
