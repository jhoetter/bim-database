"""Pure workflow and geometry completeness contracts.

This module is intentionally transport-free: FastAPI routes, MCP tools,
export checks, and tests should import these rules instead of treating the MCP
server as the source of truth.
"""
from __future__ import annotations

from typing import Any


# Geometry the honest gate requires, per scene type. A scene is
# geometry-complete when it carries at least one label of each required kind.
REQUIRED_GEOMETRY: dict[str, list[str]] = {
    "grundriss": ["wall", "floorplan_opening"],
    "schnitt": ["component_line"],
    "ansicht": ["view_opening"],
}


def missing_geometry(scene_tag: str | None, label_types) -> list[str]:
    """Return required geometry kinds missing from the scene."""
    req = REQUIRED_GEOMETRY.get(scene_tag or "", [])
    have = set(label_types or [])
    return [kind for kind in req if kind not in have]


def derive_workflow_state(dataset: dict[str, Any], facts: dict[str, Any], scene_meta: dict[str, dict]) -> dict[str, Any]:
    """Server-side approximation of ui/src/lib/workflow.ts predicates.

    Keep deliberately conservative: when in doubt, return ``pending`` and let
    the labeling workflow fill the gaps. Status flips only on clear observable
    conditions from the dataset manifest, labels metadata, and house facts.
    """
    drawings = dataset.get("drawings") or []
    scenes_by_file = {d.get("file"): d for d in drawings}

    inventory_blockers: list[str] = []
    if not drawings:
        inventory_blockers.append("no scenes extracted yet")
    for d in drawings:
        f = d.get("file")
        meta = scene_meta.get(f, {})
        tag = meta.get("scene_tag")
        if tag in (None, "nicht_klassifiziert"):
            inventory_blockers.append(f"{f}: untagged")
            continue
        if tag == "grundriss" and not meta.get("scene_level"):
            inventory_blockers.append(f"{f}: missing level")
    inventory_status = "done" if drawings and not inventory_blockers else "pending"

    has_scenes = bool(drawings)

    def class_blockers(tag: str) -> tuple[bool, list[str]]:
        blockers: list[str] = []
        targets = [
            (d.get("file"), scene_meta.get(d.get("file"), {}))
            for d in drawings
            if scene_meta.get(d.get("file"), {}).get("scene_tag") == tag
        ]
        for f, meta in targets:
            missing = missing_geometry(tag, meta.get("label_types"))
            if missing:
                blockers.append(f"{f}: missing geometry {missing}")
        return bool(targets), blockers

    has_floorplans, floorplan_blockers = class_blockers("grundriss")
    has_sections, section_blockers = class_blockers("schnitt")
    has_elevations, elevation_blockers = class_blockers("ansicht")

    if inventory_status != "done":
        floorplan_status = "pending"
        floorplan_blockers = ["inventory incomplete"] + floorplan_blockers
    else:
        floorplan_status = "done" if has_floorplans and not floorplan_blockers else "pending"
        if not has_floorplans:
            floorplan_blockers = ["no grundriss scenes classified"]

    if floorplan_status != "done":
        section_status = "pending"
        section_blockers = ["floorplans incomplete"] + section_blockers
    else:
        section_status = "done" if not has_sections or not section_blockers else "pending"

    if section_status != "done":
        elevation_status = "pending"
        elevation_blockers = ["sections incomplete"] + elevation_blockers
    else:
        elevation_status = "done" if not has_elevations or not elevation_blockers else "pending"

    cps = facts.get("calibration_per_scene") or {}
    assumed_isotropic: list[str] = []
    for d in drawings:
        f = d.get("file")
        tag = scene_meta.get(f, {}).get("scene_tag")
        if tag in ("ansicht", "schnitt"):
            calib = cps.get(f)
            if isinstance(calib, dict) and calib.get("single_ref_assumed_isotropic"):
                assumed_isotropic.append(f)

    wf = facts.get("workflow") or {}
    review_status = "done" if has_scenes and (
        (wf.get("phase_completed_at") or {}).get("review")
        or (wf.get("phase_completed_at") or {}).get("detail")
        or (wf.get("user_skipped") or {}).get("review")
        or (wf.get("user_skipped") or {}).get("detail")
    ) else "pending"

    phases = {
        "inventory": {"status": inventory_status, "blockers": inventory_blockers},
        "floorplans": {"status": floorplan_status, "blockers": [] if floorplan_status == "done" else floorplan_blockers},
        "sections": {"status": section_status, "blockers": [] if section_status == "done" else section_blockers},
        "elevations": {"status": elevation_status, "blockers": [] if elevation_status == "done" else elevation_blockers},
        "review": {"status": review_status, "blockers": ["review not marked complete"] if review_status != "done" else []},
    }
    next_phase = None
    for phase in ("inventory", "floorplans", "sections", "elevations"):
        if phases[phase]["status"] != "done":
            next_phase = phase
            break
    required_phase_names = ("inventory", "floorplans", "sections", "elevations")
    blocker_count = sum(len(phases[phase]["blockers"]) for phase in required_phase_names)
    labeled_count = sum(1 for d in scenes_by_file.values() if d.get("labeled"))
    return {
        "phases": phases,
        "next_phase": next_phase,
        "exportable": bool(drawings) and labeled_count > 0,
        "blockers_total": blocker_count,
        "scenes_total": len(drawings),
        "labeled_scenes": labeled_count,
        "assumed_isotropic_scenes": assumed_isotropic,
    }
