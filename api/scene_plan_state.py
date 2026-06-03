"""Structured per-scene plan state for agentic labeling.

The Markdown plan remains the human surface, but this sidecar is the
authoritative state machine for tasks, gates, defects, evidence, and current
status. It intentionally does not read drawings; the harness vision agent
creates evidence, while deterministic gates keep the work honest.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .geometry_util import as_point as _as_point, wall_segment as _wall_segment
from .persistence import atomic_write_json, atomic_write_text, locked_path

SCHEMA_VERSION = "scene-plan-state-v1"
MARKDOWN_TEMPLATE_VERSION = "scene-plan-v2"
MAX_ACTION_ATTEMPTS = 3

TASK_STATES = {
    "todo",
    "in_progress",
    "blocked",
    "needs_repair",
    "rejected",
    "verified",
    "accepted_incomplete",
}
PLAN_STATUSES = {
    "draft",
    "active",
    "blocked",
    "needs_repair",
    "blocked_external",
    "review",
    "verified",
    "accepted_incomplete",
}
DEFECT_STATUSES = {"open", "in_progress", "fixed", "rejected", "accepted_uncertain", "superseded"}
DEFECT_SEVERITIES = {"blocker", "warning", "info"}
EVIDENCE_KINDS = {
    "scene_view",
    "label_view",
    "score_walls",
    "score_measurements",
    "topology_qa",
    "continuity_check",
    "repair_candidate_decision",
    "human_note",
    "semantic_ink_region",
    "subagent_report",
    "reset",
    "gate_evaluation",
}

REPAIR_CANDIDATE_OUTCOMES = {
    "accepted_applied",
    "rejected_false_positive",
    "rejected_intentional_opening",
    "rejected_would_hurt_score",
    "accepted_uncertain",
    "needs_manual_geometry",
}

OPENING_CANDIDATE_OUTCOMES = {
    "accepted_applied",
    "rejected_false_positive",
    "rejected_not_an_opening",
    "rejected_bad_parent_wall",
    "accepted_uncertain",
    "needs_manual_geometry",
}


class PlanStateConflictError(RuntimeError):
    pass


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _bad_part(value: str) -> bool:
    return "/" in value or "\\" in value or ".." in value or not value


def plan_state_path(dataset_root: Path, key: str, file: str) -> Path:
    if _bad_part(key) or _bad_part(file):
        raise ValueError("bad key or file")
    return dataset_root / key / "plans" / f"{Path(file).stem}.plan.json"


def plan_state_rel_path(dataset_root: Path, key: str, file: str) -> str:
    return str(plan_state_path(dataset_root, key, file).relative_to(dataset_root.parent))


def markdown_path(dataset_root: Path, key: str, file: str) -> Path:
    if _bad_part(key) or _bad_part(file):
        raise ValueError("bad key or file")
    return dataset_root / key / "plans" / f"{Path(file).stem}.md"


def version_for_state(state: dict[str, Any]) -> str:
    body = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _next_id(items: list[dict[str, Any]], prefix: str) -> str:
    highest = 0
    for item in items:
        raw = str(item.get("id") or "")
        if raw.startswith(prefix + "-"):
            try:
                highest = max(highest, int(raw.split("-", 1)[1]))
            except ValueError:
                pass
    return f"{prefix}-{highest + 1:03d}"


def _task(
    task_id: str,
    title: str,
    phase: str,
    category: str,
    *,
    required: bool = True,
    gates: list[str] | None = None,
    depends_on: list[str] | None = None,
    invalidates: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": title,
        "phase": phase,
        "category": category,
        "status": "todo",
        "required": required,
        "blocked_by": [],
        "gates": [
            {"id": gate, "status": "pending", "evidence_ids": [], "waiver_reason": None}
            for gate in (gates or [])
        ],
        "depends_on": depends_on or [],
        "invalidates": invalidates or [],
        "evidence_ids": [],
        "updated_at": _now_iso(),
    }


def _tasks_for(scene_tag: str) -> list[dict[str, Any]]:
    if scene_tag == "grundriss":
        return [
            _task("CLASSIFY_SCENE", "Set scene tag and floor level", "analysis", "classification", gates=["SCENE_CLASSIFIED"]),
            _task("ANALYZE_SILHOUETTE", "Describe outer masses, excluded non-walls, and endpoint rules", "analysis", "walls", gates=["HAS_SILHOUETTE_HYPOTHESIS"]),
            _task("TRACE_OUTER_WALLS", "Place outer structural walls before openings", "editing", "walls", gates=["WALLS_EXIST", "WALL_INK_ANCHORED"], depends_on=["CLASSIFY_SCENE", "ANALYZE_SILHOUETTE"], invalidates=["VERIFY_OUTER_TOPOLOGY", "VERIFY_INTERIOR_TOPOLOGY", "PLACE_OPENINGS", "VERIFY_OPENINGS", "READ_DIMENSIONS", "VERIFY_MEASUREMENTS", "FINAL_QA"]),
            _task("VERIFY_OUTER_TOPOLOGY", "Verify outer wall topology and wall score", "verification", "walls", gates=["TOPOLOGY_REVIEWED", "WALL_SCORE_REVIEWED", "WALL_INK_ANCHORED"], depends_on=["TRACE_OUTER_WALLS"]),
            _task("TRACE_INTERIOR_WALLS", "Place interior structural walls", "editing", "walls", gates=["WALLS_EXIST", "WALL_INK_ANCHORED"], depends_on=["VERIFY_OUTER_TOPOLOGY"], invalidates=["VERIFY_INTERIOR_TOPOLOGY", "PLACE_OPENINGS", "VERIFY_OPENINGS", "READ_DIMENSIONS", "VERIFY_MEASUREMENTS", "FINAL_QA"]),
            _task("VERIFY_INTERIOR_TOPOLOGY", "Verify interior wall topology and score", "verification", "walls", gates=["TOPOLOGY_REVIEWED", "WALL_SCORE_REVIEWED", "WALL_INK_ANCHORED"], depends_on=["TRACE_INTERIOR_WALLS"]),
            _task("PLACE_OPENINGS", "Place doors, windows, passages, and garage doors on parent walls", "editing", "openings", gates=["OPENINGS_HAVE_PARENT_WALL"], depends_on=["VERIFY_INTERIOR_TOPOLOGY"], invalidates=["VERIFY_OPENINGS", "READ_DIMENSIONS", "VERIFY_MEASUREMENTS", "FINAL_QA"]),
            _task("VERIFY_OPENINGS", "Verify opening relations and on-wall placement", "verification", "openings", gates=["OPENINGS_HAVE_PARENT_WALL", "OPENINGS_ON_WALL"], depends_on=["PLACE_OPENINGS"], invalidates=["READ_DIMENSIONS", "VERIFY_MEASUREMENTS", "FINAL_QA"]),
            _task("READ_DIMENSIONS", "Inspect and label readable dimension chains after walls/openings", "analysis", "dimensions", gates=["DIMENSIONS_REVIEWED"], depends_on=["VERIFY_OPENINGS"], invalidates=["VERIFY_MEASUREMENTS", "FINAL_QA"]),
            _task("VERIFY_MEASUREMENTS", "Verify dimension chains, reference dims, and wall/opening tick alignment", "verification", "dimensions", gates=["MEASUREMENTS_REVIEWED"], depends_on=["READ_DIMENSIONS"]),
            _task("FINAL_QA", "Run final scene QA and update remaining blockers", "verification", "qa", gates=["VISUAL_VERIFY_EXISTS", "NO_BLOCKER_DEFECTS"], depends_on=["VERIFY_MEASUREMENTS"]),
        ]
    if scene_tag == "schnitt":
        return [
            _task("CLASSIFY_SCENE", "Set scene tag and orientation if visible", "analysis", "classification", gates=["SCENE_CLASSIFIED"]),
            _task("READ_HEIGHTS", "Read height marks, datum, and roof facts", "editing", "heights", gates=["HEIGHTS_REVIEWED"]),
            _task("TRACE_COMPONENTS", "Trace section component lines", "editing", "components", gates=["STRUCTURE_EXISTS"]),
            _task("PLACE_VIEW_OPENINGS", "Place visible section openings/components", "editing", "view_openings", gates=["VIEW_OPENINGS_REVIEWED"]),
            _task("CALIBRATE_SCENE", "Add reference dimensions or record transferred calibration", "editing", "calibration", gates=["CALIBRATION_REVIEWED"]),
            _task("FINAL_QA", "Run final section QA", "verification", "qa", gates=["VISUAL_VERIFY_EXISTS", "NO_BLOCKER_DEFECTS"]),
        ]
    if scene_tag == "ansicht":
        return [
            _task("CLASSIFY_SCENE", "Set scene tag and facade orientation", "analysis", "classification", gates=["SCENE_CLASSIFIED"]),
            _task("READ_HEIGHTS", "Read or propagate datum and height facts", "editing", "heights", gates=["HEIGHTS_REVIEWED"]),
            _task("TRACE_COMPONENTS", "Trace facade and roof component lines", "editing", "components", gates=["STRUCTURE_EXISTS"]),
            _task("PLACE_VIEW_OPENINGS", "Place facade doors/windows", "editing", "view_openings", gates=["VIEW_OPENINGS_REVIEWED"]),
            _task("CALIBRATE_SCENE", "Add reference dimensions or record transferred calibration", "editing", "calibration", gates=["CALIBRATION_REVIEWED"]),
            _task("FINAL_QA", "Run final elevation QA", "verification", "qa", gates=["VISUAL_VERIFY_EXISTS", "NO_BLOCKER_DEFECTS"]),
        ]
    return [
        _task("CLASSIFY_SCENE", "Classify or confirm this auxiliary scene", "analysis", "classification", required=True, gates=["SCENE_CLASSIFIED"]),
        _task("INSPECT_SCENE", "Record whether the scene contains useful geometry/facts", "analysis", "qa", required=True, gates=["HAS_ANALYSIS_EVIDENCE"]),
        _task("FINAL_QA", "Summarize relevance and blockers", "verification", "qa", required=False, gates=["NO_BLOCKER_DEFECTS"]),
    ]


def _ensure_tasks_match_scene_tag(state: dict[str, Any], scene_tag: str) -> None:
    expected = _tasks_for(scene_tag)
    expected_ids = {str(t.get("id")) for t in expected}
    current = state.get("tasks") or []
    current_ids = {str(t.get("id")) for t in current}
    if current_ids == expected_ids:
        return
    current_by_id = {str(t.get("id")): t for t in current}
    migrated: list[dict[str, Any]] = []
    for template_task in expected:
        task_id = str(template_task.get("id"))
        old = current_by_id.get(task_id)
        if old:
            merged = {**template_task}
            for field in ("status", "blocked_by", "evidence_ids", "updated_at"):
                if field in old:
                    merged[field] = old[field]
            old_gates = {g.get("id"): g for g in old.get("gates") or []}
            for gate in merged.get("gates") or []:
                if gate.get("id") in old_gates:
                    gate.update(old_gates[gate.get("id")])
            migrated.append(merged)
        else:
            migrated.append(template_task)
    removed_ids = sorted(current_ids - expected_ids)
    if removed_ids:
        state.setdefault("decision_log", []).append({
            "time": _now_iso(),
            "mode": "analysis",
            "evidence_ids": [],
            "decision": f"Migrated task template to {scene_tag}",
            "result": "Removed stale task(s): " + ", ".join(removed_ids),
        })
    state["tasks"] = migrated


def create_state_from_template(
    *,
    key: str,
    file: str,
    scene_tag: str = "nicht_klassifiziert",
    level_or_orientation: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "key": key,
        "file": file,
        "scene_tag": scene_tag,
        "level_or_orientation": level_or_orientation,
        "status": "draft",
        "created_by": created_by or "agent",
        "created_at": now,
        "updated_at": now,
        "current_state": {
            "summary": "Scene plan created; analysis not yet complete.",
            "label_counts": {},
            "scores": {},
            "topology": {},
            "blockers": [],
        },
        "tasks": _tasks_for(scene_tag),
        "defects": [],
        "evidence": [],
        "decision_log": [],
        "actions": [],
    }


def read_plan_state(dataset_root: Path, key: str, file: str) -> dict[str, Any]:
    p = plan_state_path(dataset_root, key, file)
    if not p.exists():
        mp = markdown_path(dataset_root, key, file)
        legacy_markdown = mp.read_text(encoding="utf-8") if mp.exists() else ""
        return {
            "exists": False,
            "key": key,
            "file": file,
            "path": plan_state_rel_path(dataset_root, key, file),
            "version": None,
            "state": None,
            "markdown": legacy_markdown,
            "legacy_markdown_exists": bool(legacy_markdown),
            "markdown_path": str(markdown_path(dataset_root, key, file).relative_to(dataset_root.parent)),
            "last_updated": None,
        }
    state = json.loads(p.read_text())
    return {
        "exists": True,
        "key": key,
        "file": file,
        "path": str(p.relative_to(dataset_root.parent)),
        "version": version_for_state(state),
        "state": state,
        "markdown": render_markdown(state),
        "legacy_markdown_exists": False,
        "markdown_path": str(markdown_path(dataset_root, key, file).relative_to(dataset_root.parent)),
        "last_updated": state.get("updated_at"),
    }


def write_plan_state(
    dataset_root: Path,
    state: dict[str, Any],
    *,
    expected_version: str | None = None,
    sync_markdown: bool = True,
) -> dict[str, Any]:
    key = str(state.get("key") or "")
    file = str(state.get("file") or "")
    p = plan_state_path(dataset_root, key, file)
    # C2/H1: hold the lock across the version check AND the write so the
    # optimistic-concurrency check is no longer TOCTOU-racy — two writers
    # with the same expected_version can no longer both pass and clobber.
    with locked_path(p):
        if p.exists() and expected_version is not None:
            current = json.loads(p.read_text())
            if version_for_state(current) != expected_version:
                raise PlanStateConflictError("plan state version conflict")
        state["updated_at"] = _now_iso()
        atomic_write_json(p, state, sort_keys=True, trailing_newline=True)
        if sync_markdown:
            mp = markdown_path(dataset_root, key, file)
            mp.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(mp, render_markdown(state))
    return read_plan_state(dataset_root, key, file)


def create_plan_state_from_template(
    dataset_root: Path,
    key: str,
    file: str,
    *,
    scene_tag: str = "nicht_klassifiziert",
    level_or_orientation: str | None = None,
    created_by: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    p = plan_state_path(dataset_root, key, file)
    if p.exists() and not overwrite:
        raise PlanStateConflictError("plan state already exists")
    state = create_state_from_template(
        key=key,
        file=file,
        scene_tag=scene_tag,
        level_or_orientation=level_or_orientation,
        created_by=created_by,
    )
    return write_plan_state(dataset_root, state, sync_markdown=True)


def _load_or_create(dataset_root: Path, key: str, file: str, *, scene_tag: str = "nicht_klassifiziert") -> dict[str, Any]:
    current = read_plan_state(dataset_root, key, file)
    if current["exists"]:
        return current["state"]
    return create_state_from_template(key=key, file=file, scene_tag=scene_tag)


def _find_task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in state.get("tasks") or []:
        if task.get("id") == task_id:
            return task
    raise KeyError(f"task {task_id!r} not found")


def _find_defect(state: dict[str, Any], defect_id: str) -> dict[str, Any]:
    for defect in state.get("defects") or []:
        if defect.get("id") == defect_id:
            return defect
    raise KeyError(f"defect {defect_id!r} not found")


def add_evidence(
    dataset_root: Path,
    key: str,
    file: str,
    evidence: dict[str, Any],
    *,
    expected_version: str | None = None,
) -> dict[str, Any]:
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    kind = str(evidence.get("kind") or "")
    if kind and kind not in EVIDENCE_KINDS:
        raise ValueError(f"unknown evidence kind {kind!r}")
    if kind == "subagent_report":
        result = evidence.get("result") or {}
        required = {
            "plan_status",
            "task_ids_changed",
            "defect_ids_changed",
            "evidence_ids_created",
            "label_counts",
            "score_deltas",
            "rejected_edits",
            "unresolved_blockers",
        }
        missing = sorted(k for k in required if k not in result)
        if missing:
            raise ValueError("subagent_report missing required field(s): " + ", ".join(missing))
        current_blockers = sorted(
            str(d.get("id"))
            for d in _open_defects(state)
            if d.get("severity") == "blocker"
        )
        reported_blockers = sorted(str(x) for x in (result.get("unresolved_blockers") or []))
        if current_blockers != reported_blockers:
            raise ValueError(
                "subagent_report unresolved_blockers does not match plan state: "
                f"expected {current_blockers}, got {reported_blockers}"
            )
    item = {
        "id": evidence.get("id") or _next_id(state.get("evidence") or [], "EV"),
        "kind": kind or "human_note",
        "mode": evidence.get("mode") or "analysis",
        "summary": evidence.get("summary") or "",
        "tool": evidence.get("tool"),
        "params": evidence.get("params") or {},
        "result": evidence.get("result") or {},
        "observation_id": evidence.get("observation_id"),
        "image_url": evidence.get("image_url"),
        "created_at": evidence.get("created_at") or _now_iso(),
    }
    state.setdefault("evidence", []).append(item)
    for task_id in evidence.get("task_ids") or []:
        try:
            task = _find_task(state, str(task_id))
        except KeyError:
            continue
        task.setdefault("evidence_ids", []).append(item["id"])
        task["updated_at"] = _now_iso()
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def upsert_defect(
    dataset_root: Path,
    key: str,
    file: str,
    defect: dict[str, Any],
    *,
    expected_version: str | None = None,
) -> dict[str, Any]:
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    now = _now_iso()
    defect_id = defect.get("id")
    if defect_id:
        try:
            target = _find_defect(state, str(defect_id))
            target.update({k: v for k, v in defect.items() if k != "id"})
            target["updated_at"] = now
        except KeyError:
            target = None
    else:
        target = None
    if target is None:
        target = {
            "id": defect_id or _next_id(state.get("defects") or [], "DEF"),
            "title": defect.get("title") or "Untitled defect",
            "status": defect.get("status") or "open",
            "severity": defect.get("severity") or "warning",
            "category": defect.get("category") or "qa",
            "region": defect.get("region"),
            "description": defect.get("description") or "",
            "expected_resolution": defect.get("expected_resolution") or "",
            "evidence_ids": defect.get("evidence_ids") or [],
            "created_at": defect.get("created_at") or now,
            "updated_at": now,
        }
        state.setdefault("defects", []).append(target)
    if target["status"] not in DEFECT_STATUSES:
        raise ValueError(f"unknown defect status {target['status']!r}")
    if target["severity"] not in DEFECT_SEVERITIES:
        raise ValueError(f"unknown defect severity {target['severity']!r}")
    if target.get("status") in {"fixed", "rejected", "accepted_uncertain"} and not target.get("evidence_ids"):
        raise ValueError(f"defect cannot be {target.get('status')} without evidence")
    if (
        target.get("status") in {"fixed", "rejected", "accepted_uncertain"}
        and target.get("category") in {"wall_missing_region", "wall_off_ink"}
        and not target.get("classification")
    ):
        raise ValueError("wall score defects must be classified before closure")
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def update_defect(
    dataset_root: Path,
    key: str,
    file: str,
    defect_id: str,
    patch: dict[str, Any],
    *,
    expected_version: str | None = None,
) -> dict[str, Any]:
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    target = _find_defect(state, defect_id)
    target.update(patch)
    target["updated_at"] = _now_iso()
    if target.get("status") not in DEFECT_STATUSES:
        raise ValueError(f"unknown defect status {target.get('status')!r}")
    if target.get("severity") not in DEFECT_SEVERITIES:
        raise ValueError(f"unknown defect severity {target.get('severity')!r}")
    if target.get("status") in {"fixed", "rejected", "accepted_uncertain"} and not target.get("evidence_ids"):
        raise ValueError(f"defect cannot be {target.get('status')} without evidence")
    if (
        target.get("status") in {"fixed", "rejected", "accepted_uncertain"}
        and target.get("category") in {"wall_missing_region", "wall_off_ink"}
        and not target.get("classification")
    ):
        raise ValueError("wall score defects must be classified before closure")
    return write_plan_state(dataset_root, state, expected_version=expected_version)


DEFECT_CLASSIFICATIONS = {
    "real_missing_wall",
    "bad_existing_wall",
    "duplicate_wall_face_not_centerline",
    "opening_symbol",
    "door_swing_or_hint",
    "dashed_projection",
    "furniture_or_fixture",
    "dimension_or_annotation",
    "site_or_boundary_line",
    "separate_structure",
    "false_positive",
    "ambiguous",
}


def classify_defect(
    dataset_root: Path,
    key: str,
    file: str,
    defect_id: str,
    classification: str,
    *,
    evidence_ids: list[str] | None = None,
    note: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    if classification not in DEFECT_CLASSIFICATIONS:
        raise ValueError(f"unknown defect classification {classification!r}")
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    defect = _find_defect(state, defect_id)
    defect["classification"] = classification
    if evidence_ids:
        existing = defect.setdefault("evidence_ids", [])
        for ev_id in evidence_ids:
            if ev_id not in existing:
                existing.append(ev_id)
    defect["updated_at"] = _now_iso()
    state.setdefault("decision_log", []).append({
        "time": _now_iso(),
        "mode": "analysis",
        "evidence_ids": evidence_ids or [],
        "decision": f"Classified {defect_id} as {classification}",
        "result": note or "classification recorded",
    })
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def record_repair_candidate_decision(
    dataset_root: Path,
    key: str,
    file: str,
    candidate: dict[str, Any],
    outcome: str,
    *,
    evidence_ids: list[str] | None = None,
    note: str | None = None,
    simulation: dict[str, Any] | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    if outcome not in REPAIR_CANDIDATE_OUTCOMES:
        raise ValueError(f"unknown repair candidate outcome {outcome!r}")
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    current_state = state.setdefault("current_state", {})
    cluster_fp = str(candidate.get("cluster_fingerprint") or candidate.get("cluster_id") or candidate.get("candidate_id"))
    decision = {
        "candidate_id": candidate.get("candidate_id"),
        "candidate_op": candidate.get("op"),
        "cluster_id": candidate.get("cluster_id"),
        "cluster_fingerprint": candidate.get("cluster_fingerprint"),
        "finding_ids": list(candidate.get("finding_ids") or []),
        "outcome": outcome,
        "evidence_ids": evidence_ids or [],
        "note": note,
        "simulation": simulation or {},
        "updated_at": _now_iso(),
    }
    current_state.setdefault("repair_candidate_decisions", {})[cluster_fp] = decision
    ev_id = _next_id(state.get("evidence") or [], "EV")
    state.setdefault("evidence", []).append({
        "id": ev_id,
        "kind": "repair_candidate_decision",
        "mode": "verification",
        "summary": f"Repair candidate {candidate.get('candidate_id')} decision: {outcome}",
        "tool": "apply_repair_candidate" if outcome == "accepted_applied" else "classify_repair_candidate",
        "params": {"candidate_id": candidate.get("candidate_id"), "candidate_op": candidate.get("op")},
        "result": {
            "candidate_id": candidate.get("candidate_id"),
            "cluster_id": candidate.get("cluster_id"),
            "cluster_fingerprint": candidate.get("cluster_fingerprint"),
            "finding_ids": list(candidate.get("finding_ids") or []),
            "outcome": outcome,
            "simulation": simulation or {},
            "note": note,
        },
        "observation_id": None,
        "image_url": None,
        "created_at": _now_iso(),
    })
    decision.setdefault("evidence_ids", []).append(ev_id)
    state.setdefault("decision_log", []).append({
        "time": _now_iso(),
        "mode": "verification",
        "evidence_ids": decision.get("evidence_ids") or [],
        "decision": f"Repair candidate {candidate.get('candidate_id')} -> {outcome}",
        "result": note or str(candidate.get("expected_gain") or "candidate decision recorded"),
    })
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def set_task_state(
    dataset_root: Path,
    key: str,
    file: str,
    task_id: str,
    status: str,
    *,
    evidence_ids: list[str] | None = None,
    blocked_by: list[str] | None = None,
    gate_updates: list[dict[str, Any]] | None = None,
    note: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    if status not in TASK_STATES:
        raise ValueError(f"unknown task status {status!r}")
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    task = _find_task(state, task_id)
    if status == "accepted_incomplete" and task.get("required"):
        raise ValueError(
            "required task cannot be accepted_incomplete; leave it open, "
            "mark blocked, or verify it with passing gates"
        )
    if evidence_ids is not None:
        task["evidence_ids"] = evidence_ids
    if blocked_by is not None:
        task["blocked_by"] = blocked_by
    if gate_updates:
        gates_by_id = {g.get("id"): g for g in task.get("gates") or []}
        for upd in gate_updates:
            gate_id = upd.get("id")
            if gate_id not in gates_by_id:
                task.setdefault("gates", []).append({
                    "id": gate_id,
                    "status": upd.get("status") or "pending",
                    "evidence_ids": upd.get("evidence_ids") or [],
                    "waiver_reason": upd.get("waiver_reason"),
                })
            else:
                gates_by_id[gate_id].update(upd)
    if note:
        ev = {
            "id": _next_id(state.get("evidence") or [], "EV"),
            "kind": "human_note",
            "mode": task.get("phase") or "analysis",
            "summary": note,
            "tool": None,
            "params": {},
            "result": {},
            "observation_id": None,
            "image_url": None,
            "created_at": _now_iso(),
        }
        state.setdefault("evidence", []).append(ev)
        task.setdefault("evidence_ids", []).append(ev["id"])
    if status == "verified":
        gates = task.get("gates") or []
        passing_statuses = {"passed"} if task.get("required") else {"passed", "waived"}
        not_passing = [
            gate.get("id")
            for gate in gates
            if gate.get("status") not in passing_statuses
        ]
        if not_passing:
            raise ValueError(
                "task cannot be verified until all gates pass"
                + ("; required tasks cannot use waived gates" if task.get("required") else " or are waived")
                + ": "
                + ", ".join(str(g) for g in not_passing)
            )
        if not task.get("evidence_ids"):
            raise ValueError("task cannot be verified without evidence")
    if status in {"rejected", "accepted_incomplete"} and not task.get("evidence_ids"):
        raise ValueError(f"task cannot be {status} without evidence")
    task["status"] = status
    task["updated_at"] = _now_iso()
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def append_decision_log(
    dataset_root: Path,
    key: str,
    file: str,
    *,
    mode: str,
    evidence_ids: list[str] | None = None,
    evidence: str | None = None,
    decision: str,
    result: str,
    expected_version: str | None = None,
) -> dict[str, Any]:
    state = _load_or_create(dataset_root, key, file)
    state.setdefault("decision_log", []).append({
        "time": _now_iso(),
        "mode": mode,
        "evidence_ids": evidence_ids or [],
        "evidence": evidence or "",
        "decision": decision,
        "result": result,
    })
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def start_action(
    dataset_root: Path,
    key: str,
    file: str,
    action_id: str,
    *,
    agent_id: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    action = _action_by_id(state, action_id)
    if not action:
        raise KeyError(f"action {action_id!r} not found")
    task_id = str(action.get("task_id") or action.get("id") or "")
    wall_anchor_blockers = _open_wall_anchoring_blockers(state)
    if task_id in _DOWNSTREAM_WALL_DEPENDENT_TASKS and wall_anchor_blockers:
        first_blocker = str(wall_anchor_blockers[0].get("id"))
        blocker_ids = ", ".join(str(d.get("id")) for d in wall_anchor_blockers[:8])
        raise ValueError(
            "code=wall_ink_anchor_blocked; "
            f"first_blocker={first_blocker}; "
            f"recommended_action_id=ACT-{first_blocker}; "
            "recommended_tools=get_scene_repair_candidates,"
            "get_scene_view_with_repair_candidate,apply_repair_candidate,"
            "upsert_wall_anchored,score_walls; "
            f"finish wall anchoring before downstream {task_id}; blocker(s): {blocker_ids}"
        )
    now = _now_iso()
    action = {**action, "status": "in_progress", "agent_id": agent_id, "started_at": now, "updated_at": now}
    _store_action(state, action)
    state.setdefault("current_state", {})["current_action_id"] = action_id
    if action.get("kind") == "task" and action.get("task_id"):
        try:
            task = _find_task(state, str(action["task_id"]))
            if task.get("status") in {"todo", "blocked", "needs_repair"}:
                task["status"] = "in_progress"
                task["updated_at"] = now
        except KeyError:
            pass
    if action.get("kind") == "defect" and action.get("defect_id"):
        try:
            defect = _find_defect(state, str(action["defect_id"]))
            if defect.get("status") == "open":
                defect["status"] = "in_progress"
                defect["updated_at"] = now
        except KeyError:
            pass
    state["status"] = "active"
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def record_attempt(
    dataset_root: Path,
    key: str,
    file: str,
    action_id: str,
    attempt: dict[str, Any],
    *,
    expected_version: str | None = None,
) -> dict[str, Any]:
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    action = _action_by_id(state, action_id)
    if not action:
        raise KeyError(f"action {action_id!r} not found")
    _assert_attempt_allowed(action, attempt.get("edits") or [])
    attempts = action.setdefault("attempts", [])
    item = {
        "id": attempt.get("id") or _next_id(attempts, "ATT"),
        "hypothesis": attempt.get("hypothesis") or "",
        "edits": attempt.get("edits") or [],
        "evidence_ids": attempt.get("evidence_ids") or [],
        "created_at": attempt.get("created_at") or _now_iso(),
    }
    attempts.append(item)
    action["updated_at"] = _now_iso()
    _store_action(state, action)
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def finish_action(
    dataset_root: Path,
    key: str,
    file: str,
    action_id: str,
    *,
    outcome: str,
    attempt_id: str | None = None,
    evidence_ids: list[str] | None = None,
    reason: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    allowed = {"fixed", "still_open", "rejected", "accepted_uncertain", "regressed", "blocked_external"}
    if outcome not in allowed:
        raise ValueError(f"unknown action outcome {outcome!r}")
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    action = _action_by_id(state, action_id)
    if not action:
        raise KeyError(f"action {action_id!r} not found")
    if outcome == "accepted_uncertain" and action.get("kind") == "task":
        raise ValueError(
            "task actions cannot be accepted_uncertain; keep the task open, "
            "finish as blocked_external with a concrete blocker, or verify it with passing gates"
        )
    attempts = action.get("attempts") or []
    if outcome in {"still_open", "regressed"} and len(attempts) >= MAX_ACTION_ATTEMPTS:
        raise ValueError(
            f"action {action_id!r} has reached {MAX_ACTION_ATTEMPTS} attempts; "
            "finish as fixed, rejected, accepted_uncertain, or blocked_external"
        )
    now = _now_iso()
    action.update({
        "status": outcome,
        "attempt_id": attempt_id,
        "evidence_ids": evidence_ids or action.get("evidence_ids") or [],
        "reason": reason or "",
        "finished_at": now,
        "updated_at": now,
    })
    if action.get("kind") == "defect" and action.get("defect_id"):
        defect = _find_defect(state, str(action["defect_id"]))
        if evidence_ids:
            existing = defect.setdefault("evidence_ids", [])
            for ev_id in evidence_ids:
                if ev_id not in existing:
                    existing.append(ev_id)
        if outcome == "fixed":
            defect["status"] = "fixed"
        elif outcome == "rejected":
            defect["status"] = "rejected"
        elif outcome == "accepted_uncertain":
            defect["status"] = "accepted_uncertain"
        elif outcome in {"still_open", "regressed"}:
            defect["status"] = "open"
        defect["updated_at"] = now
        if defect.get("status") in {"fixed", "rejected", "accepted_uncertain"} and not defect.get("evidence_ids"):
            raise ValueError(f"defect cannot be {defect.get('status')} without evidence")
        if (
            defect.get("status") in {"fixed", "rejected", "accepted_uncertain"}
            and defect.get("category") in {"wall_missing_region", "wall_off_ink"}
            and not defect.get("classification")
        ):
            raise ValueError("wall score defects must be classified before closure")
    if action.get("kind") == "task" and action.get("task_id"):
        task = _find_task(state, str(action["task_id"]))
        if evidence_ids:
            existing = task.setdefault("evidence_ids", [])
            for ev_id in evidence_ids:
                if ev_id not in existing:
                    existing.append(ev_id)
        if outcome == "fixed":
            gates = task.get("gates") or []
            passing_statuses = {"passed"} if task.get("required") else {"passed", "waived"}
            if gates and all(g.get("status") in passing_statuses for g in gates):
                task["status"] = "verified"
            else:
                task["status"] = "in_progress"
        elif outcome == "accepted_uncertain":
            task["status"] = "accepted_incomplete"
        elif outcome in {"still_open", "regressed"}:
            task["status"] = "needs_repair"
        elif outcome == "rejected":
            task["status"] = "rejected"
        elif outcome == "blocked_external":
            task["status"] = "needs_repair"
        task["updated_at"] = now
    state.setdefault("decision_log", []).append({
        "time": now,
        "mode": "verification",
        "evidence_ids": evidence_ids or [],
        "decision": f"Finished {action_id} as {outcome}",
        "result": reason or "",
    })
    state.setdefault("current_state", {})["current_action_id"] = None
    _store_action(state, action)
    status = _status_for_state(state)
    state["status"] = status["status"]
    state.setdefault("current_state", {})["summary"] = status["summary"]
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def reopen_task(
    dataset_root: Path,
    key: str,
    file: str,
    task_id: str,
    *,
    reason: str,
    evidence_ids: list[str] | None = None,
    invalidate_dependents: bool = True,
    expected_version: str | None = None,
) -> dict[str, Any]:
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    task = _find_task(state, task_id)
    task["status"] = "needs_repair"
    task["updated_at"] = _now_iso()
    if evidence_ids:
        existing = task.setdefault("evidence_ids", [])
        for ev_id in evidence_ids:
            if ev_id not in existing:
                existing.append(ev_id)
    stale = set((state.setdefault("current_state", {}).get("stale_evidence") or []))
    stale.add(task_id)
    if invalidate_dependents:
        invalidated = set(task.get("invalidates") or [])
        changed = True
        while changed:
            changed = False
            for other in state.get("tasks") or []:
                deps = set(other.get("depends_on") or [])
                if other.get("id") not in invalidated and deps & invalidated:
                    invalidated.add(str(other.get("id")))
                    changed = True
        for other in state.get("tasks") or []:
            if other.get("id") in invalidated and other.get("status") == "verified":
                other["status"] = "blocked"
                other["updated_at"] = _now_iso()
                other["blocked_by"] = sorted(set((other.get("blocked_by") or []) + [task_id]))
                stale.add(str(other.get("id")))
    state.setdefault("current_state", {})["stale_evidence"] = sorted(stale)
    state["status"] = "needs_repair"
    state.setdefault("decision_log", []).append({
        "time": _now_iso(),
        "mode": "verification",
        "evidence_ids": evidence_ids or [],
        "decision": f"Reopened {task_id}",
        "result": reason,
    })
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def record_opening_candidate_decision(
    dataset_root: Path,
    key: str,
    file: str,
    candidate: dict[str, Any],
    outcome: str,
    *,
    label_id: str | None = None,
    evidence_ids: list[str] | None = None,
    note: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    if outcome not in OPENING_CANDIDATE_OUTCOMES:
        raise ValueError(f"unknown opening candidate outcome {outcome!r}")
    state = _load_or_create(dataset_root, key, file)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")
    now = _now_iso()
    fingerprint = str(candidate.get("candidate_fingerprint") or candidate.get("candidate_id") or "")
    decision = {
        "candidate_id": candidate.get("candidate_id"),
        "candidate_fingerprint": fingerprint,
        "kind": candidate.get("kind"),
        "outcome": outcome,
        "label_id": label_id,
        "parent_wall_id": candidate.get("parent_wall_id"),
        "region": candidate.get("region"),
        "evidence_ids": evidence_ids or [],
        "note": note or "",
        "decided_at": now,
    }
    current = state.setdefault("current_state", {})
    decisions = current.setdefault("opening_candidate_decisions", {})
    decisions[fingerprint] = decision
    state.setdefault("decision_log", []).append({
        "time": now,
        "mode": "verification",
        "evidence_ids": evidence_ids or [],
        "decision": f"Opening candidate {candidate.get('candidate_id')} {outcome}",
        "result": note or "",
    })
    if outcome == "accepted_applied":
        for task in state.get("tasks") or []:
            if task.get("id") in {"PLACE_OPENINGS", "VERIFY_OPENINGS"} and task.get("status") in {"todo", "in_progress", "blocked", "needs_repair"}:
                task["status"] = "in_progress"
                task["updated_at"] = now
    return write_plan_state(dataset_root, state, expected_version=expected_version)


def mark_state_stale_after_reset(dataset_root: Path, key: str, file: str) -> dict[str, Any] | None:
    current = read_plan_state(dataset_root, key, file)
    if not current["exists"]:
        return None
    state = current["state"]
    ev_id = _next_id(state.get("evidence") or [], "EV")
    state.setdefault("evidence", []).append({
        "id": ev_id,
        "kind": "reset",
        "mode": "verification",
        "summary": "Scene labels were reset; label-dependent gates are stale.",
        "tool": "reset_scene_labels",
        "params": {},
        "result": {"labels_reset": True},
        "observation_id": None,
        "image_url": None,
        "created_at": _now_iso(),
    })
    for task in state.get("tasks") or []:
        if task.get("category") in {"walls", "openings", "dimensions", "qa"} and task.get("status") == "verified":
            task["status"] = "blocked"
            task.setdefault("blocked_by", [])
            task["updated_at"] = _now_iso()
            for gate in task.get("gates") or []:
                gate["status"] = "pending"
    state["status"] = "needs_repair"
    state.setdefault("current_state", {})["summary"] = "Labels were reset; plan state preserved but label-dependent gates are stale."
    return write_plan_state(dataset_root, state)


def delete_plan_state_files(dataset_root: Path, key: str, file: str | None = None) -> int:
    plans_dir = dataset_root / key / "plans"
    if not plans_dir.exists():
        return 0
    patterns = [f"{Path(file).stem}.*"] if file else ["*.md", "*.plan.json"]
    deleted = 0
    for pattern in patterns:
        for p in plans_dir.glob(pattern):
            if p.suffix == ".md" or p.name.endswith(".plan.json"):
                p.unlink()
                deleted += 1
    return deleted


def _label_counts(labels_doc: dict[str, Any]) -> dict[str, int]:
    return dict(Counter(str(lab.get("type")) for lab in (labels_doc.get("labels") or []) if isinstance(lab, dict)))


def _walls(labels_doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [lab for lab in (labels_doc.get("labels") or []) if lab.get("type") == "wall"]


def _floorplan_openings(labels_doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [lab for lab in (labels_doc.get("labels") or []) if lab.get("type") == "floorplan_opening"]


def _floorplan_opening_axes(label: dict[str, Any]) -> tuple[
    tuple[tuple[float, float], tuple[float, float]],
    tuple[tuple[float, float], tuple[float, float]],
] | None:
    quad = ((label.get("geometry") or {}).get("quad") or [])
    if not isinstance(quad, list) or len(quad) != 4:
        return None
    pts = [_as_point(p) for p in quad]
    if any(p is None for p in pts):
        return None
    a, b, c, d = pts  # type: ignore[misc]
    along = (
        ((a[0] + d[0]) / 2.0, (a[1] + d[1]) / 2.0),
        ((b[0] + c[0]) / 2.0, (b[1] + c[1]) / 2.0),
    )
    depth = (
        ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0),
        ((d[0] + c[0]) / 2.0, (d[1] + c[1]) / 2.0),
    )
    return along, depth


def _latest_evidence(state: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for ev in reversed(state.get("evidence") or []):
        if ev.get("kind") == kind:
            return ev
    return None


def _latest_evidence_time(state: dict[str, Any], kinds: set[str]) -> dt.datetime | None:
    latest: dt.datetime | None = None
    for ev in state.get("evidence") or []:
        if ev.get("kind") not in kinds:
            continue
        t = _parse_iso(ev.get("created_at"))
        if t and (latest is None or t > latest):
            latest = t
    return latest


def _latest_label_time(labels_doc: dict[str, Any]) -> dt.datetime | None:
    latest: dt.datetime | None = None
    for lab in labels_doc.get("labels") or []:
        if not isinstance(lab, dict):
            continue
        for key in ("updated_at", "created_at"):
            t = _parse_iso(lab.get(key))
            if t and (latest is None or t > latest):
                latest = t
    return latest


def _defect_key(category: str, title: str) -> str:
    return f"{category}:{title.lower().strip()}"


def _region_fingerprint(region: Any) -> str:
    if region is None:
        return ""
    try:
        payload = json.dumps(region, sort_keys=True, separators=(",", ":"))
    except TypeError:
        payload = str(region)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def _open_defects(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        d for d in state.get("defects") or []
        if d.get("status") in {"open", "in_progress"}
    ]


def _open_blockers(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in _open_defects(state) if d.get("severity") == "blocker"]


_WALL_ANCHOR_BLOCK_CATEGORIES = {"wall_missing_region", "wall_off_ink", "missing_geometry"}
_DOWNSTREAM_WALL_DEPENDENT_TASKS = {
    "PLACE_OPENINGS",
    "VERIFY_OPENINGS",
    "READ_DIMENSIONS",
    "VERIFY_MEASUREMENTS",
    "FINAL_QA",
}


def _open_wall_anchoring_blockers(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        d for d in _open_blockers(state)
        if d.get("category") in _WALL_ANCHOR_BLOCK_CATEGORIES
    ]


def _status_for_state(state: dict[str, Any]) -> dict[str, Any]:
    tasks = state.get("tasks") or []
    defects = state.get("defects") or []
    open_defects = _open_defects(state)
    blockers = [d for d in open_defects if d.get("severity") == "blocker"]
    warnings = [d for d in open_defects if d.get("severity") == "warning"]
    required = [t for t in tasks if t.get("required")]
    incomplete_required = [
        t for t in required
        if t.get("status") != "verified"
    ]
    actions = next_actions_from_state(state, limit=1) if state else []
    actionable = bool(actions)
    if blockers and actionable:
        status = "needs_repair"
        terminal = False
        summary = f"{len(blockers)} blocker defect(s) remain; continue repair loop."
    elif blockers:
        status = "blocked_external"
        terminal = True
        summary = f"{len(blockers)} blocker defect(s) remain, but no actionable repair is available."
    elif incomplete_required and actionable:
        status = "active"
        terminal = False
        summary = "Required scene-plan tasks remain open."
    elif incomplete_required:
        status = "blocked_external"
        terminal = True
        summary = "Required scene-plan tasks remain open, but no actionable task is available."
    elif sorted(set((state.get("current_state") or {}).get("stale_evidence") or [])):
        status = "active"
        terminal = False
        summary = "Required scene-plan work needs fresh verification evidence."
    elif any(d.get("status") == "accepted_uncertain" for d in defects) or any(t.get("status") == "accepted_incomplete" for t in tasks):
        status = "accepted_incomplete"
        terminal = True
        summary = "Required work is closed with accepted uncertainty/incompleteness."
    else:
        status = "verified"
        terminal = True
        summary = "All required scene-plan work is verified."
    if terminal:
        actionable = False
        actions = []
    total = len(required) or len(tasks) or 1
    closed = len([
        t for t in required
        if t.get("status") == "verified"
    ])
    stale = sorted(set((state.get("current_state") or {}).get("stale_evidence") or []))
    current_findings = ((state.get("current_state") or {}).get("findings") or {})
    current_clusters = ((state.get("current_state") or {}).get("finding_clusters") or {})
    return {
        "terminal": terminal,
        "status": status,
        "summary": summary,
        "required_complete": not incomplete_required,
        "percent_complete": int(round((closed / total) * 100)),
        "open_blockers": len(blockers),
        "open_warnings": len(warnings),
        "current_finding_count": int(current_findings.get("count") or 0) if isinstance(current_findings, dict) else 0,
        "current_warning_finding_count": int(current_findings.get("warnings") or 0) if isinstance(current_findings, dict) else 0,
        "current_blocker_finding_count": int(current_findings.get("blockers") or 0) if isinstance(current_findings, dict) else 0,
        "current_finding_cluster_count": int(current_clusters.get("count") or 0) if isinstance(current_clusters, dict) else 0,
        "current_action_id": ((state.get("current_state") or {}).get("current_action_id")),
        "final_qa_allowed": not blockers and not incomplete_required and "FINAL_QA" not in stale,
        "stale_evidence": stale,
        "next_action_available": actionable,
        "next_action": actions[0] if actions else None,
        "terminality_reasons": _terminality_reasons(state, blockers, incomplete_required, stale),
    }


def _terminality_reasons(
    state: dict[str, Any],
    blockers: list[dict[str, Any]] | None = None,
    incomplete_required: list[dict[str, Any]] | None = None,
    stale: list[str] | None = None,
) -> list[str]:
    blockers = blockers if blockers is not None else _open_blockers(state)
    tasks = state.get("tasks") or []
    incomplete_required = incomplete_required if incomplete_required is not None else [
        t for t in tasks
        if t.get("required") and t.get("status") != "verified"
    ]
    stale = stale if stale is not None else sorted(set((state.get("current_state") or {}).get("stale_evidence") or []))
    reasons: list[str] = []
    if blockers:
        reasons.append(f"open blocker defects: {', '.join(str(d.get('id')) for d in blockers)}")
    if incomplete_required:
        reasons.append(f"required tasks open: {', '.join(str(t.get('id')) for t in incomplete_required)}")
    if stale:
        reasons.append(f"stale evidence: {', '.join(stale)}")
    return reasons


def plan_status(dataset_root: Path, key: str, file: str) -> dict[str, Any]:
    data = read_plan_state(dataset_root, key, file)
    state = data.get("state")
    if not state:
        return {
            "exists": False,
            "terminal": False,
            "status": "draft",
            "summary": "No structured scene plan exists.",
            "required_complete": False,
            "percent_complete": 0,
            "open_blockers": 0,
            "open_warnings": 0,
            "current_action_id": None,
            "final_qa_allowed": False,
            "stale_evidence": [],
            "next_action_available": False,
            "next_action": None,
            "terminality_reasons": ["missing plan state"],
            "version": data.get("version"),
        }
    status = _status_for_state(state)
    return {"exists": True, "version": data.get("version"), **status}


def evaluate_terminality(dataset_root: Path, key: str, file: str) -> dict[str, Any]:
    return plan_status(dataset_root, key, file)


def _upsert_auto_defect(
    state: dict[str, Any],
    *,
    title: str,
    severity: str,
    category: str,
    description: str,
    expected_resolution: str,
    region: Any = None,
    evidence_ids: list[str] | None = None,
    fingerprint: str | None = None,
) -> str:
    now = _now_iso()
    key = fingerprint or _defect_key(category, title)
    region_fp = _region_fingerprint(region)
    if region_fp and fingerprint is None:
        key = f"{key}:r{region_fp}"
    for defect in state.setdefault("defects", []):
        if (
            defect.get("_auto_key") == key
            or (fingerprint is not None and defect.get("_auto_fingerprint") == fingerprint)
            or _defect_key(str(defect.get("category")), str(defect.get("title"))) == key
        ):
            if defect.get("status") in {"fixed", "rejected", "accepted_uncertain"}:
                defect.setdefault("_auto_key", key)
                if fingerprint:
                    defect["_auto_fingerprint"] = fingerprint
                if evidence_ids:
                    existing = defect.setdefault("evidence_ids", [])
                    for ev_id in evidence_ids:
                        if ev_id not in existing:
                            existing.append(ev_id)
                return str(defect["id"])
            defect.update({
                "severity": severity,
                "category": category,
                "description": description,
                "expected_resolution": expected_resolution,
                "region": region,
                "updated_at": now,
            })
            if evidence_ids:
                existing = defect.setdefault("evidence_ids", [])
                for ev_id in evidence_ids:
                    if ev_id not in existing:
                        existing.append(ev_id)
            defect["_auto_key"] = key
            if fingerprint:
                defect["_auto_fingerprint"] = fingerprint
            return str(defect["id"])
    defect_id = _next_id(state.get("defects") or [], "DEF")
    state.setdefault("defects", []).append({
        "id": defect_id,
        "title": title,
        "status": "open",
        "severity": severity,
        "category": category,
        "region": region,
        "description": description,
        "expected_resolution": expected_resolution,
        "evidence_ids": evidence_ids or [],
        "created_at": now,
        "updated_at": now,
        "_auto_key": key,
        **({"_auto_fingerprint": fingerprint} if fingerprint else {}),
    })
    return defect_id


def _supersede_absent_auto_defects(state: dict[str, Any], current_fingerprints: set[str]) -> None:
    now = _now_iso()
    auto_categories = {
        "wall_missing_region",
        "wall_off_ink",
        "wall_topology",
        "possible_split_wall",
        "wall_continuity",
        "topology_candidate_review",
    }
    for defect in state.get("defects") or []:
        if defect.get("status") not in {"open", "in_progress"}:
            continue
        fp = defect.get("_auto_fingerprint")
        if not fp or fp in current_fingerprints:
            continue
        if defect.get("category") not in auto_categories:
            continue
        defect["status"] = "superseded"
        defect["updated_at"] = now


def _set_gate(task: dict[str, Any], gate_id: str, status: str, evidence_ids: list[str] | None = None, waiver_reason: str | None = None) -> None:
    gates = task.setdefault("gates", [])
    for gate in gates:
        if gate.get("id") == gate_id:
            gate["status"] = status
            if evidence_ids is not None:
                gate["evidence_ids"] = evidence_ids
            if waiver_reason is not None:
                gate["waiver_reason"] = waiver_reason
            return
    gates.append({"id": gate_id, "status": status, "evidence_ids": evidence_ids or [], "waiver_reason": waiver_reason})


def evaluate_gates(
    dataset_root: Path,
    key: str,
    file: str,
    *,
    labels_doc: dict[str, Any],
    score_walls_result: dict[str, Any] | None = None,
    score_measurements_result: dict[str, Any] | None = None,
    topology_result: dict[str, Any] | None = None,
    continuity_result: dict[str, Any] | None = None,
    visual_evidence: bool = False,
    quality_profile: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    scene_tag = labels_doc.get("scene_tag") or "nicht_klassifiziert"
    level_or_orientation = labels_doc.get("scene_level") or labels_doc.get("scene_orientation")
    state = _load_or_create(dataset_root, key, file, scene_tag=scene_tag)
    state["scene_tag"] = scene_tag
    state["level_or_orientation"] = level_or_orientation
    if not state.get("tasks"):
        state["tasks"] = _tasks_for(scene_tag)
    else:
        _ensure_tasks_match_scene_tag(state, scene_tag)
    if expected_version is not None:
        current = read_plan_state(dataset_root, key, file)
        if current["exists"] and current["version"] != expected_version:
            raise PlanStateConflictError("plan state version conflict")

    previous_score_walls = ((_latest_evidence(state, "score_walls") or {}).get("result") or None)
    evidence_ids_by_kind: dict[str, str] = {}
    for kind, result, tool in (
        ("score_walls", score_walls_result, "score_walls"),
        ("score_measurements", score_measurements_result, "score_measurements"),
        ("topology_qa", topology_result, "wall_topology_qa"),
        ("continuity_check", continuity_result, "wall_continuity_check"),
    ):
        if result is None:
            continue
        ev_id = _next_id(state.get("evidence") or [], "EV")
        state.setdefault("evidence", []).append({
            "id": ev_id,
            "kind": kind,
            "mode": "verification",
            "summary": f"{tool} gate evidence",
            "tool": tool,
            "params": {},
            "result": result,
            "observation_id": None,
            "image_url": None,
            "created_at": _now_iso(),
        })
        evidence_ids_by_kind[kind] = ev_id
    if visual_evidence:
        ev_id = _next_id(state.get("evidence") or [], "EV")
        state.setdefault("evidence", []).append({
            "id": ev_id,
            "kind": "label_view",
            "mode": "verification",
            "summary": "Visual verification evidence asserted by caller.",
            "tool": "get_scene_view_with_labels",
            "params": {},
            "result": {"visual_evidence": True},
            "observation_id": None,
            "image_url": None,
            "created_at": _now_iso(),
        })
        evidence_ids_by_kind["visual"] = ev_id

    counts = _label_counts(labels_doc)
    walls = _walls(labels_doc)
    openings = _floorplan_openings(labels_doc)
    labels = [lab for lab in (labels_doc.get("labels") or []) if isinstance(lab, dict)]
    label_by_id = {str(lab.get("id")): lab for lab in labels if lab.get("id") is not None}
    dim_labels = [lab for lab in labels if lab.get("type") == "dimensioned_distance"]
    height_marks = [lab for lab in labels if lab.get("type") == "height_mark"]
    component_lines = [lab for lab in labels if lab.get("type") == "component_line"]
    view_openings = [lab for lab in labels if lab.get("type") == "view_opening"]
    reference_dims = [lab for lab in dim_labels if (lab.get("attributes") or {}).get("is_reference") is True]
    facts_path = dataset_root / key / "house_facts.json"
    scene_calibration: dict[str, Any] = {}
    if facts_path.exists():
        try:
            facts_doc = json.loads(facts_path.read_text())
            scene_calibration = ((facts_doc.get("calibration_per_scene") or {}).get(file) or {})
            if not isinstance(scene_calibration, dict):
                scene_calibration = {}
        except Exception:  # noqa: BLE001
            scene_calibration = {}
    calibration_transferred = scene_calibration.get("status") == "transferred"
    has_analysis_evidence = any((ev.get("mode") == "analysis") for ev in state.get("evidence") or [])
    has_dimension_defect = any(
        d.get("category") == "dimension" and d.get("status") in {"open", "in_progress", "accepted_uncertain", "rejected"}
        for d in state.get("defects") or []
    )
    latest_score_walls = score_walls_result or ((_latest_evidence(state, "score_walls") or {}).get("result") or None)
    latest_measurements = score_measurements_result or ((_latest_evidence(state, "score_measurements") or {}).get("result") or None)
    measurement_label_count = len(dim_labels)
    if isinstance(latest_measurements, dict):
        n_dims = latest_measurements.get("n_dims")
        if isinstance(n_dims, (int, float)):
            measurement_label_count = max(measurement_label_count, int(n_dims))
    latest_topology = topology_result or ((_latest_evidence(state, "topology_qa") or {}).get("result") or None)
    no_openings_accepted = (
        not openings
        and any(
            d.get("category") == "opening_relation"
            and d.get("title") == "No floorplan openings placed"
            and d.get("status") in {"fixed", "rejected", "accepted_uncertain"}
            for d in state.get("defects") or []
        )
    )

    current_state = state.setdefault("current_state", {})
    current_state["label_counts"] = counts
    current_state["scores"] = {}
    if latest_score_walls:
        current_state["scores"]["score_walls"] = latest_score_walls
    if latest_measurements:
        current_state["scores"]["score_measurements"] = latest_measurements
    if latest_topology:
        current_state["topology"] = {
            "wall_count": latest_topology.get("wall_count", 0),
            "endpoint_count": latest_topology.get("endpoint_count", 0),
            "dangling_endpoints": len(latest_topology.get("dangling_endpoints") or []),
            "near_miss_corners": len(latest_topology.get("near_miss_corners") or []),
            "collinear_fragments": len(latest_topology.get("collinear_fragments") or []),
            "short_stubs": len(latest_topology.get("short_stubs") or []),
            "components": len(latest_topology.get("components") or []),
        }
    from .topology_repair import cluster_findings, current_findings_from_results
    current_findings = current_findings_from_results(
        file=file,
        labels_doc=labels_doc,
        score_walls_result=latest_score_walls,
        topology_result=latest_topology,
        continuity_result=continuity_result,
    )
    current_fingerprints = {str(f.get("fingerprint")) for f in current_findings if f.get("fingerprint")}
    current_clusters = cluster_findings(current_findings, labels_doc)
    current_state["findings"] = {
        "count": len(current_findings),
        "blockers": len([f for f in current_findings if f.get("severity") == "blocker"]),
        "warnings": len([f for f in current_findings if f.get("severity") == "warning"]),
        "items": [
            {k: v for k, v in f.items() if k != "payload"}
            for f in current_findings[:100]
        ],
    }
    current_state["finding_clusters"] = {
        "count": len(current_clusters),
        "items": [
            {k: v for k, v in c.items() if k != "findings"}
            for c in current_clusters[:50]
        ],
    }

    latest_label_time = _latest_label_time(labels_doc)
    latest_verify_time = _latest_evidence_time(
        state,
        {"label_view", "score_walls", "score_measurements", "topology_qa", "continuity_check"},
    )
    evidence_stale = bool(latest_label_time and (latest_verify_time is None or latest_verify_time < latest_label_time))
    stale_tasks: set[str] = set(current_state.get("stale_evidence") or [])
    if evidence_stale:
        stale_tasks.add("FINAL_QA")
        _upsert_auto_defect(
            state,
            title="Verification evidence is stale",
            severity="warning",
            category="stale_evidence",
            description="Latest label edit is newer than verification/score/topology evidence.",
            expected_resolution="Re-render labels, re-run relevant QA, add evidence, and evaluate gates again.",
        )
    if latest_label_time and evidence_stale:
        label_types = {str(lab.get("type")) for lab in labels}
        if "wall" in label_types:
            stale_tasks.update({"VERIFY_OUTER_TOPOLOGY", "VERIFY_INTERIOR_TOPOLOGY", "VERIFY_OPENINGS", "READ_DIMENSIONS", "VERIFY_MEASUREMENTS", "FINAL_QA"})
        if "dimensioned_distance" in label_types:
            stale_tasks.update({"READ_DIMENSIONS", "VERIFY_MEASUREMENTS", "FINAL_QA"})
        if "floorplan_opening" in label_types:
            stale_tasks.update({"VERIFY_OPENINGS", "READ_DIMENSIONS", "VERIFY_MEASUREMENTS", "FINAL_QA"})
    elif not evidence_stale:
        stale_tasks.clear()
    current_state["stale_evidence"] = sorted(stale_tasks)

    if scene_tag == "grundriss":
        if not walls:
            _upsert_auto_defect(
                state,
                title="No wall labels on floorplan",
                severity="blocker",
                category="missing_geometry",
                description="A grundriss cannot pass scene QA without wall labels.",
                expected_resolution="Analyze silhouette, place structural wall labels, then verify topology.",
            )
        if not openings:
            _upsert_auto_defect(
                state,
                title="No floorplan openings placed",
                severity="blocker",
                category="opening_relation",
                description="A grundriss opening pass cannot verify with zero doors/windows/passages/garage doors unless explicitly accepted incomplete.",
                expected_resolution="Place openings on verified parent walls, or mark accepted incomplete with evidence.",
            )
        for op in openings:
            parent_ids = [
                rel.get("other_id")
                for rel in (op.get("relations") or [])
                if isinstance(rel, dict) and rel.get("kind") == "belongs_to"
            ]
            if not parent_ids:
                _upsert_auto_defect(
                    state,
                    title=f"Opening {op.get('id')} has no parent wall",
                    severity="blocker",
                    category="opening_relation",
                    description="floorplan_opening labels must belong_to a wall.",
                    expected_resolution="Attach the opening to the correct parent wall or delete/re-place it.",
                )
                continue
            parent_id = next((pid for pid in parent_ids if isinstance(pid, str)), None)
            parent = label_by_id.get(parent_id or "")
            if not parent or parent.get("type") != "wall":
                _upsert_auto_defect(
                    state,
                    title=f"Opening {op.get('id')} parent wall missing",
                    severity="blocker",
                    category="opening_relation",
                    description="floorplan_opening belongs_to target must be an existing wall.",
                    expected_resolution="Attach the opening to an existing wall or delete/re-place it.",
                )
                continue
            axes = _floorplan_opening_axes(op)
            parent_wall = _wall_segment(parent)
            if axes is None or parent_wall is None:
                continue
            from .geometry_checks import floorplan_opening_quality
            quality = floorplan_opening_quality(
                axes[0],
                axes[1],
                parent_wall,
                tol_px=30.0,
                is_garage_door=(op.get("attributes") or {}).get("opening_kind") == "garage_door",
            )
            current_state.setdefault("opening_quality", {})[str(op.get("id"))] = quality
            for defect in quality.get("defects") or []:
                category = str(defect.get("category") or "opening_geometry")
                _upsert_auto_defect(
                    state,
                    title=f"Opening {op.get('id')} {category}",
                    severity="blocker",
                    category=category,
                    description=str(defect.get("message") or "floorplan_opening geometry failed QA."),
                    expected_resolution="Normalize, move, or re-place the opening on its parent wall, then verify again.",
                    region=(op.get("geometry") or {}).get("quad"),
                )
    if latest_score_walls:
        if (
            isinstance(previous_score_walls, dict)
            and isinstance(previous_score_walls.get("f1"), (int, float))
            and isinstance(latest_score_walls.get("f1"), (int, float))
            and float(latest_score_walls["f1"]) + 0.001 < float(previous_score_walls["f1"])
        ):
            _upsert_auto_defect(
                state,
                title="Wall score regression",
                severity="warning",
                category="score_regression",
                description=(
                    f"score_walls f1 regressed from {previous_score_walls.get('f1')} "
                    f"to {latest_score_walls.get('f1')}."
                ),
                expected_resolution="Analyze the edit that caused the regression; revert, reject the attempt, or justify with evidence.",
                evidence_ids=[evidence_ids_by_kind["score_walls"]] if "score_walls" in evidence_ids_by_kind else [],
            )
        score_findings = [f for f in current_findings if f.get("source") == "score_walls"]
        off_ink_wall_ids = {
            str(((f.get("payload") or {}).get("wall_id")))
            for f in score_findings
            if f.get("category") == "off_ink_segment" and ((f.get("payload") or {}).get("wall_id"))
        }
        wall_quality_by_id: dict[str, Any] = {}
        labels_changed = False
        if scene_tag == "grundriss":
            for wall in walls:
                wall_id = str(wall.get("id") or "")
                if not wall_id:
                    continue
                attrs = wall.setdefault("attributes", {})
                if wall_id in off_ink_wall_ids:
                    wall["status"] = "uncertain"
                    attrs["quality_status"] = "off_ink"
                    labels_changed = True
                    wall_quality_by_id[wall_id] = {"quality_status": "off_ink", "reason": "score_walls.off_ink_segment"}
                elif attrs.get("quality_status") == "off_ink":
                    anchoring = attrs.get("anchoring") or {}
                    attrs["quality_status"] = "ink_anchored" if float(anchoring.get("ink_overlap") or 0.0) >= 0.6 else "unanchored"
                    wall["status"] = "readable"
                    labels_changed = True
                    wall_quality_by_id[wall_id] = {"quality_status": attrs["quality_status"], "reason": "latest score no longer reports off-ink segment"}
        if wall_quality_by_id:
            current_state["wall_quality_by_label_id"] = wall_quality_by_id
        if labels_changed:
            label_path = dataset_root / key / "labels" / f"{Path(file).stem}.json"
            if label_path.exists():
                atomic_write_json(label_path, labels_doc)
        for idx, finding in enumerate([f for f in score_findings if f.get("category") == "missing_region"], start=1):
            region = finding.get("region")
            _upsert_auto_defect(
                state,
                title=f"Wall score missing region {idx}",
                severity="blocker",
                category="wall_missing_region",
                region=region,
                description="score_walls reports wall ink not covered by saved wall labels.",
                expected_resolution="Re-read the crop visually; repair/add with upsert_wall_anchored or reject this as non-wall ink with evidence.",
                evidence_ids=[evidence_ids_by_kind["score_walls"]] if "score_walls" in evidence_ids_by_kind else [],
                fingerprint=str(finding.get("fingerprint") or ""),
            )
        if isinstance(latest_score_walls.get("precision"), (int, float)) and float(latest_score_walls["precision"]) < 0.70:
            precision_fp = f"score_walls:low_precision:{file}"
            current_fingerprints.add(precision_fp)
            _upsert_auto_defect(
                state,
                title="Wall score precision below anchoring threshold",
                severity="blocker",
                category="wall_off_ink",
                region=None,
                description=(
                    f"score_walls precision {latest_score_walls.get('precision')} is below the "
                    "floorplan wall-ink threshold 0.70."
                ),
                expected_resolution="Review repair candidates and re-anchor/delete off-ink wall labels before downstream openings or dimensions.",
                evidence_ids=[evidence_ids_by_kind["score_walls"]] if "score_walls" in evidence_ids_by_kind else [],
                fingerprint=precision_fp,
            )
        for idx, finding in enumerate([f for f in score_findings if f.get("category") == "off_ink_segment"], start=1):
            seg = finding.get("region")
            _upsert_auto_defect(
                state,
                title=f"Wall score off-ink segment {idx}",
                severity="blocker",
                category="wall_off_ink",
                region=seg,
                description="score_walls reports a saved wall segment does not sit on wall ink.",
                expected_resolution="Use repair candidates or upsert_wall_anchored to re-locate/refine this wall before placing openings/dimensions; mark uncertain only with visual evidence.",
                evidence_ids=[evidence_ids_by_kind["score_walls"]] if "score_walls" in evidence_ids_by_kind else [],
                fingerprint=str(finding.get("fingerprint") or ""),
            )
    if latest_topology:
        topology_findings = [f for f in current_findings if f.get("source") == "wall_topology_qa"]
        for idx, finding in enumerate([f for f in topology_findings if f.get("category") in {"dangling_endpoint", "near_miss_corner"}], start=1):
            _upsert_auto_defect(
                state,
                title=f"Dangling wall endpoint {idx}",
                severity="warning",
                category="wall_topology",
                region=finding.get("region"),
                description="wall_topology_qa reports a wall endpoint that does not connect to another endpoint.",
                expected_resolution="Classify endpoint reason; connect, repair, reject false positive, or mark uncertain.",
                evidence_ids=[evidence_ids_by_kind["topology_qa"]] if "topology_qa" in evidence_ids_by_kind else [],
                fingerprint=str(finding.get("fingerprint") or ""),
            )
        for idx, finding in enumerate([f for f in topology_findings if f.get("category") == "collinear_fragment"], start=1):
            _upsert_auto_defect(
                state,
                title=f"Possible split wall {idx}",
                severity="warning",
                category="possible_split_wall",
                region=finding.get("region"),
                description="wall_topology_qa reports collinear fragments that may be one continuous wall.",
                expected_resolution="Review in clean overlay; merge only if vision and score agree, otherwise reject.",
                evidence_ids=[evidence_ids_by_kind["topology_qa"]] if "topology_qa" in evidence_ids_by_kind else [],
                fingerprint=str(finding.get("fingerprint") or ""),
            )
        for idx, finding in enumerate([f for f in topology_findings if f.get("category") == "short_stub"], start=1):
            _upsert_auto_defect(
                state,
                title=f"Short wall stub {idx}",
                severity="warning",
                category="wall_topology",
                region=finding.get("region"),
                description="wall_topology_qa reports a short wall stub that may be non-structural or incomplete.",
                expected_resolution="Review in clean overlay; delete/demote only if vision and score agree, otherwise classify.",
                evidence_ids=[evidence_ids_by_kind["topology_qa"]] if "topology_qa" in evidence_ids_by_kind else [],
                fingerprint=str(finding.get("fingerprint") or ""),
            )
    if continuity_result:
        continuity_findings = [f for f in current_findings if f.get("source") == "wall_continuity_check"]
        for idx, finding in enumerate(continuity_findings, start=1):
            _upsert_auto_defect(
                state,
                title=f"Wall continuity candidate {idx}",
                severity="warning",
                category="wall_continuity",
                region=finding.get("region"),
                description="wall_continuity_check reports fragments that may have been split at an opening.",
                expected_resolution="Accept only with visual evidence and score improvement; otherwise reject.",
                evidence_ids=[evidence_ids_by_kind["continuity_check"]] if "continuity_check" in evidence_ids_by_kind else [],
                fingerprint=str(finding.get("fingerprint") or ""),
            )
    _supersede_absent_auto_defects(state, current_fingerprints)

    if quality_profile == "gold":
        from .topology_repair import repair_candidate_report
        report = repair_candidate_report(labels_doc, topology_result=latest_topology, plan_state=state)
        current_state["gold_repair_candidates"] = {
            "cluster_count": report.get("cluster_count"),
            "candidate_count": report.get("candidate_count"),
            "reviewed_cluster_count": report.get("reviewed_cluster_count"),
            "high_confidence_unclassified_count": report.get("high_confidence_unclassified_count"),
        }
        high_conf_unreviewed = []
        for cluster in report.get("clusters") or []:
            if cluster.get("confidence") == "high" and cluster.get("candidates") and not cluster.get("decision"):
                high_conf_unreviewed.append(cluster)
        for idx, cluster in enumerate(high_conf_unreviewed, start=1):
            _upsert_auto_defect(
                state,
                title=f"Gold topology candidate requires review {idx}",
                severity="blocker",
                category="topology_candidate_review",
                region=cluster.get("region"),
                description="Gold quality profile found a high-confidence topology repair candidate that must be accepted or rejected before final QA.",
                expected_resolution="Inspect the candidate overlay; apply the deterministic repair or reject/classify it with evidence.",
                evidence_ids=[evidence_ids_by_kind["topology_qa"]] if "topology_qa" in evidence_ids_by_kind else [],
                fingerprint=f"gold:{cluster.get('cluster_id')}",
            )
    if latest_measurements:
        for idx, item in enumerate(latest_measurements.get("unmatched_ticks") or [], start=1):
            axis = item.get("axis")
            pos = item.get("pos")
            _upsert_auto_defect(
                state,
                title=f"Measurement unmatched tick {idx}",
                severity="blocker",
                category="dimension",
                region=[axis, pos, item.get("nearest"), item.get("dist")],
                description="score_measurements reports a dimension tick that does not align with a saved wall feature.",
                expected_resolution="Classify the tick as wall-face mismatch, opening feature, dimension false positive, or unresolved; repair geometry/dimension labels or accept uncertain with evidence.",
                evidence_ids=[evidence_ids_by_kind["score_measurements"]] if "score_measurements" in evidence_ids_by_kind else [],
            )

    open_blockers = [d for d in _open_defects(state) if d.get("severity") == "blocker"]
    open_defect_ids = {str(d.get("id")) for d in _open_defects(state)}
    wall_blockers = [
        d for d in open_blockers
        if d.get("category") in {"wall_missing_region", "wall_off_ink", "wall_topology", "possible_split_wall", "wall_continuity", "missing_geometry"}
    ]
    wall_anchor_blockers = _open_wall_anchoring_blockers(state)
    current_state["wall_anchoring"] = {
        "status": "failed" if wall_anchor_blockers else "passed" if walls and latest_score_walls else "pending",
        "blocker_ids": [str(d.get("id")) for d in wall_anchor_blockers],
        "off_ink_count": len([d for d in wall_anchor_blockers if d.get("category") == "wall_off_ink"]),
        "missing_region_count": len([d for d in wall_anchor_blockers if d.get("category") == "wall_missing_region"]),
        "precision": latest_score_walls.get("precision") if isinstance(latest_score_walls, dict) else None,
    }
    opening_blockers = [
        d for d in open_blockers
        if str(d.get("category") or "").startswith("opening_")
    ]
    for task in state.get("tasks") or []:
        task["blocked_by"] = [d for d in (task.get("blocked_by") or []) if d in open_defect_ids]
        if task.get("status") == "blocked" and not task["blocked_by"]:
            task["status"] = "todo"
        task_id = task.get("id")
        if task_id == "CLASSIFY_SCENE":
            passed = scene_tag != "nicht_klassifiziert" and (scene_tag != "grundriss" or bool(labels_doc.get("scene_level")))
            _set_gate(task, "SCENE_CLASSIFIED", "passed" if passed else "failed")
            if passed and task.get("status") in {"todo", "in_progress"}:
                task["status"] = "verified"
        elif task_id == "READ_DIMENSIONS":
            passed = bool(dim_labels or has_dimension_defect)
            _set_gate(task, "DIMENSIONS_REVIEWED", "passed" if passed else "pending", [evidence_ids_by_kind["score_measurements"]] if "score_measurements" in evidence_ids_by_kind else [])
            if not passed and task.get("status") == "verified":
                task["status"] = "needs_repair"
        elif task_id == "VERIFY_MEASUREMENTS":
            measurements_have_labeled_dims = bool(latest_measurements and measurement_label_count > 0)
            passed = bool(measurements_have_labeled_dims or has_dimension_defect)
            _set_gate(task, "MEASUREMENTS_REVIEWED", "passed" if passed else "pending", [evidence_ids_by_kind["score_measurements"]] if "score_measurements" in evidence_ids_by_kind else [])
            if not passed and task.get("status") == "verified":
                task["status"] = "needs_repair"
        elif task_id == "ANALYZE_SILHOUETTE":
            _set_gate(task, "HAS_SILHOUETTE_HYPOTHESIS", "passed" if has_analysis_evidence else "pending")
        elif task_id in {"TRACE_OUTER_WALLS", "TRACE_INTERIOR_WALLS"}:
            _set_gate(task, "WALLS_EXIST", "passed" if walls else "failed")
            _set_gate(
                task,
                "WALL_INK_ANCHORED",
                "failed" if wall_anchor_blockers else "passed" if walls and latest_score_walls else "pending",
                [evidence_ids_by_kind["score_walls"]] if "score_walls" in evidence_ids_by_kind else [],
            )
            if wall_anchor_blockers and task.get("status") != "accepted_incomplete":
                task["status"] = "needs_repair"
                task["blocked_by"] = [d["id"] for d in wall_anchor_blockers]
        elif task_id in {"VERIFY_OUTER_TOPOLOGY", "VERIFY_INTERIOR_TOPOLOGY"}:
            _set_gate(
                task,
                "TOPOLOGY_REVIEWED",
                "failed" if wall_blockers else "passed" if latest_topology else "pending",
                [evidence_ids_by_kind["topology_qa"]] if "topology_qa" in evidence_ids_by_kind else [],
            )
            _set_gate(
                task,
                "WALL_SCORE_REVIEWED",
                "failed" if wall_blockers else "passed" if latest_score_walls else "pending",
                [evidence_ids_by_kind["score_walls"]] if "score_walls" in evidence_ids_by_kind else [],
            )
            _set_gate(
                task,
                "WALL_INK_ANCHORED",
                "failed" if wall_anchor_blockers else "passed" if walls and latest_score_walls else "pending",
                [evidence_ids_by_kind["score_walls"]] if "score_walls" in evidence_ids_by_kind else [],
            )
            if wall_blockers and task.get("status") != "accepted_incomplete":
                task["status"] = "needs_repair"
                task["blocked_by"] = [d["id"] for d in wall_blockers]
        elif task_id == "PLACE_OPENINGS":
            opening_parent_ok = bool(openings and all(any((r or {}).get("kind") == "belongs_to" for r in op.get("relations") or []) for op in openings))
            _set_gate(
                task,
                "OPENINGS_HAVE_PARENT_WALL",
                "passed" if opening_parent_ok else "waived" if no_openings_accepted else "failed",
                waiver_reason="No openings accepted incomplete/uncertain with evidence." if no_openings_accepted else None,
            )
            if (wall_blockers or opening_blockers) and task.get("status") not in {"accepted_incomplete", "verified"}:
                task["status"] = "blocked"
                task["blocked_by"] = [d["id"] for d in (wall_blockers + opening_blockers)]
        elif task_id == "VERIFY_OPENINGS":
            opening_parent_ok = bool(openings and all(any((r or {}).get("kind") == "belongs_to" for r in op.get("relations") or []) for op in openings))
            _set_gate(
                task,
                "OPENINGS_HAVE_PARENT_WALL",
                "passed" if opening_parent_ok else "waived" if no_openings_accepted else "failed",
                waiver_reason="No openings accepted incomplete/uncertain with evidence." if no_openings_accepted else None,
            )
            _set_gate(
                task,
                "OPENINGS_ON_WALL",
                "passed" if openings and not opening_blockers else "waived" if no_openings_accepted else "failed",
                [d["id"] for d in opening_blockers] if opening_blockers else None,
                waiver_reason="No openings accepted incomplete/uncertain with evidence." if no_openings_accepted else None,
            )
            if (wall_blockers or opening_blockers) and task.get("status") not in {"accepted_incomplete", "verified"}:
                task["status"] = "blocked"
                task["blocked_by"] = [d["id"] for d in (wall_blockers + opening_blockers)]
        elif task_id == "READ_HEIGHTS":
            _set_gate(task, "HEIGHTS_REVIEWED", "passed" if height_marks or has_analysis_evidence else "pending")
        elif task_id == "TRACE_COMPONENTS":
            _set_gate(task, "STRUCTURE_EXISTS", "passed" if component_lines else "failed")
        elif task_id == "PLACE_VIEW_OPENINGS":
            _set_gate(task, "VIEW_OPENINGS_REVIEWED", "passed" if view_openings or has_analysis_evidence else "pending")
        elif task_id == "CALIBRATE_SCENE":
            homography = labels_doc.get("homography") or {}
            calibrated = bool(reference_dims) or homography.get("status") == "ok" or calibration_transferred
            _set_gate(
                task,
                "CALIBRATION_REVIEWED",
                "passed" if calibrated else "pending",
                waiver_reason=(
                    "Calibration transferred from another scene; no fabricated local reference dimension."
                    if calibration_transferred else None
                ),
            )
        elif task_id == "INSPECT_SCENE":
            _set_gate(task, "HAS_ANALYSIS_EVIDENCE", "passed" if has_analysis_evidence else "pending")
        elif task_id == "FINAL_QA":
            visual_ok = bool((_latest_evidence(state, "label_view") or visual_evidence) and not evidence_stale)
            _set_gate(task, "VISUAL_VERIFY_EXISTS", "passed" if visual_ok else "pending")
            _set_gate(task, "NO_BLOCKER_DEFECTS", "passed" if not open_blockers else "failed", [d["id"] for d in open_blockers])
            if open_blockers and task.get("status") != "accepted_incomplete":
                task["status"] = "blocked"
                task["blocked_by"] = [d["id"] for d in open_blockers]
        if task.get("status") in {"todo", "in_progress", "needs_repair"} and not task.get("blocked_by"):
            gates = task.get("gates") or []
            if gates and all(g.get("status") in {"passed", "waived"} for g in gates):
                if all(g.get("status") == "passed" for g in gates):
                    task["status"] = "verified"
        task["updated_at"] = _now_iso()

    open_defects = _open_defects(state)
    blockers = [d for d in open_defects if d.get("severity") == "blocker"]
    current_state["blockers"] = [d["id"] for d in blockers]
    if blockers:
        state["status"] = "needs_repair" if next_actions_from_state(state, limit=1) else "blocked_external"
        current_state["summary"] = f"{len(blockers)} blocker defect(s) remain; continue repair before Final QA."
    elif any(t.get("status") in {"todo", "in_progress", "blocked"} and t.get("required") for t in state.get("tasks") or []):
        state["status"] = "active"
        current_state["summary"] = "No blocker defects, but required scene tasks remain open."
    elif any(d.get("status") == "accepted_uncertain" for d in state.get("defects") or []):
        state["status"] = "accepted_incomplete"
        current_state["summary"] = "Required gates closed with accepted incomplete/uncertain items."
    else:
        state["status"] = "verified"
        current_state["summary"] = "All required scene-plan gates are verified."

    terminality = _status_for_state(state)
    state["status"] = terminality["status"]
    current_state["summary"] = terminality["summary"]
    wall_anchor_summary = current_state.get("wall_anchoring") or {}
    if wall_anchor_summary.get("status") == "failed":
        current_state["summary"] = (
            "Wall ink anchoring failed: "
            f"{wall_anchor_summary.get('off_ink_count', 0)} readable wall segment(s) are off ink; "
            f"{wall_anchor_summary.get('missing_region_count', 0)} missing wall-ink region(s). "
            + terminality["summary"]
        )
    current_state["terminality"] = terminality
    result = write_plan_state(dataset_root, state, expected_version=expected_version)
    data = result["state"]
    return {
        **result,
        "status": data["status"],
        "open_defects": _open_defects(data),
        "actionable_tasks": next_actions_from_state(data),
        "markdown": render_markdown(data),
    }


def next_actions_from_state(state: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    severity_rank = {"blocker": 0, "warning": 1, "info": 2}
    for defect in sorted(
        _open_defects(state),
        key=lambda d: (
            severity_rank.get(d.get("severity"), 9),
            _defect_category_rank(str(d.get("category") or ""), str(d.get("title") or "")),
            d.get("id", ""),
        ),
    ):
        action_id = f"ACT-{defect.get('id')}"
        category = str(defect.get("category") or "")
        actions.append({
            "kind": "defect",
            "action_id": action_id,
            "mode": "scene-defect-repair",
            "task_id": _task_for_defect_category(category),
            "defect_id": defect.get("id"),
            "mode": "scene-defect-repair",
            "id": defect.get("id"),
            "title": defect.get("title"),
            "severity": defect.get("severity"),
            "category": defect.get("category"),
            "region": defect.get("region"),
            "phase": "analysis",
            "allowed_label_types": _allowed_label_types_for_defect(category),
            "forbidden_label_types": _forbidden_label_types_for_defect(category),
            "allowed_tools": _allowed_tools_for_defect(category),
            "required_evidence": _required_evidence_for_defect(category),
            "success_gates": _success_gates_for_defect(category),
            "recommended_view_mode": _recommended_view_mode_for_action({
                "phase": "analysis",
                "category": defect.get("category"),
            }),
            "instruction": defect.get("expected_resolution") or "Analyze, repair, verify, and update this defect.",
            "rejected_attempts": _rejected_attempts_for_action(state, action_id),
        })
        if len(actions) >= limit:
            return actions
    for task in state.get("tasks") or []:
        if task.get("required") and task.get("status") in {"todo", "in_progress", "blocked", "needs_repair"}:
            action_id = f"ACT-{task.get('id')}"
            mode = "scene-review" if task.get("phase") == "analysis" else "scene-full-pass"
            actions.append({
                "kind": "task",
                "action_id": action_id,
                "mode": mode,
                "task_id": task.get("id"),
                "defect_id": None,
                "id": task.get("id"),
                "title": task.get("title"),
                "phase": task.get("phase"),
                "category": task.get("category"),
                "region": None,
                "allowed_label_types": _allowed_label_types_for_task(task),
                "forbidden_label_types": _forbidden_label_types_for_task(task),
                "allowed_tools": _allowed_tools_for_task(task),
                "required_evidence": _required_evidence_for_task(task),
                "success_gates": [g.get("id") for g in task.get("gates") or []],
                "recommended_view_mode": _recommended_view_mode_for_action(task),
                "instruction": f"Work only on {task.get('id')}: {task.get('title')}. Produce analysis evidence, at most one edit, then verification evidence.",
                "rejected_attempts": _rejected_attempts_for_action(state, action_id),
            })
            if len(actions) >= limit:
                break
    return actions


def _defect_category_rank(category: str, title: str = "") -> int:
    wall_categories = {
        "wall_missing_region",
        "wall_off_ink",
        "wall_topology",
        "possible_split_wall",
        "wall_continuity",
        "missing_geometry",
    }
    if category in wall_categories or "wall" in title.lower() or "topology" in title.lower():
        return 10
    if category in {"dimension", "stale_evidence"} or "measurement" in title.lower() or "dimension" in title.lower():
        return 20
    if category == "opening_relation" or "opening" in title.lower():
        return 30
    return 40


def next_action(dataset_root: Path, key: str, file: str) -> dict[str, Any]:
    data = read_plan_state(dataset_root, key, file)
    state = data.get("state")
    actions = next_actions_from_state(state, limit=1) if state else []
    action = actions[0] if actions else None
    return {"exists": data["exists"], "action": action, "version": data.get("version")}


def _recommended_view_mode_for_action(action: dict[str, Any] | None) -> str:
    if not action:
        return "analysis_view"
    phase = action.get("phase")
    category = action.get("category")
    if category == "openings":
        return "opening_candidate_view" if phase == "editing" else "analysis_view"
    if category == "view_openings":
        return "coordinate_pick_view" if phase == "editing" else "analysis_view"
    if category == "dimensions":
        return "measurement_read_view" if phase in {"analysis", "editing"} else "edit_verify_view"
    if category in {"heights", "calibration"}:
        return "measurement_read_view" if phase == "editing" else "edit_verify_view"
    if category == "components":
        return "coordinate_pick_view" if phase == "editing" else "analysis_view"
    if category == "walls":
        if phase == "analysis":
            return "silhouette_view"
        if phase == "verification":
            return "topology_qa_view"
        return "coordinate_pick_view"
    if phase == "verification":
        return "topology_qa_view"
    if phase == "editing":
        return "coordinate_pick_view"
    return "analysis_view"


def _action_by_id(state: dict[str, Any], action_id: str) -> dict[str, Any] | None:
    for action in state.get("actions") or []:
        if action.get("action_id") == action_id:
            return action
    for action in next_actions_from_state(state, limit=50):
        if action.get("action_id") == action_id or action.get("id") == action_id:
            return action
    return None


def _store_action(state: dict[str, Any], action: dict[str, Any]) -> None:
    action_id = str(action.get("action_id") or action.get("id") or "")
    actions = state.setdefault("actions", [])
    for idx, existing in enumerate(actions):
        if existing.get("action_id") == action_id:
            actions[idx] = action
            return
    actions.append(action)


def _edit_label_types(edits: list[Any]) -> set[str]:
    label_types: set[str] = set()
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        for key in ("label_type", "type"):
            if isinstance(edit.get(key), str) and edit.get(key) in {
                "wall",
                "floorplan_opening",
                "view_opening",
                "dimensioned_distance",
                "dimension_number",
                "component_line",
                "height_mark",
            }:
                label_types.add(str(edit[key]))
        label = edit.get("label")
        if isinstance(label, dict) and isinstance(label.get("type"), str):
            label_types.add(str(label["type"]))
        for key in ("labels", "labels_added", "labels_updated", "upserted_labels"):
            values = edit.get(key)
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict) and isinstance(item.get("type"), str):
                        label_types.add(str(item["type"]))
        if edit.get("op") in {"upsert_label", "add_label", "replace_label"} and not label_types:
            label_types.add("*")
    return label_types


def _assert_attempt_allowed(action: dict[str, Any], edits: list[Any]) -> None:
    label_types = _edit_label_types(edits)
    if not label_types:
        return
    task_id = str(action.get("task_id") or action.get("id") or "")
    if task_id == "CLASSIFY_SCENE":
        raise ValueError("action_scope_violation: CLASSIFY_SCENE cannot record geometry edits")
    allowed = set(action.get("allowed_label_types") or [])
    forbidden = set(action.get("forbidden_label_types") or [])
    concrete = {t for t in label_types if t != "*"}
    if "*" in label_types and not allowed:
        raise ValueError("action_scope_violation: geometry edit recorded under an action with no allowed_label_types")
    bad_forbidden = sorted(concrete & forbidden)
    if bad_forbidden:
        raise ValueError(f"action_scope_violation: label type(s) {bad_forbidden} forbidden for this action")
    if allowed:
        bad = sorted(concrete - allowed)
        if bad:
            raise ValueError(f"action_scope_violation: label type(s) {bad} not allowed for this action; allowed={sorted(allowed)}")


_GEOMETRY_LABEL_TYPES = {
    "wall",
    "floorplan_opening",
    "view_opening",
    "dimensioned_distance",
    "dimension_number",
    "component_line",
    "height_mark",
}


def _record_plan_order_override(
    dataset_root: Path,
    state: dict[str, Any],
    *,
    label_types: set[str],
    tool: str,
    reason: str,
) -> None:
    defects = state.setdefault("defects", [])
    defect = {
        "id": _next_id(defects, "D"),
        "status": "open",
        "severity": "warning",
        "category": "plan_order_override",
        "title": "Out-of-order label write override",
        "description": reason,
        "expected_resolution": "Review the override evidence, then either repair the ordering issue or accept the dependency risk explicitly.",
        "region": None,
        "evidence_ids": [],
        "label_types": sorted(label_types),
        "tool": tool,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    defects.append(defect)
    write_plan_state(dataset_root, state)


def _plan_order_error(
    *,
    reason: str,
    state: dict[str, Any],
    action: dict[str, Any] | None,
    label_types: set[str],
    tool: str,
) -> ValueError:
    action_id = (action or {}).get("action_id")
    allowed = sorted((action or {}).get("allowed_label_types") or [])
    forbidden = sorted((action or {}).get("forbidden_label_types") or [])
    allowed_tools = sorted((action or {}).get("allowed_tools") or [])
    current_action_id = ((state.get("current_state") or {}).get("current_action_id"))
    return ValueError(
        "code=plan_order_blocked; "
        f"reason={reason}; "
        f"tool={tool}; "
        f"label_types={sorted(label_types)}; "
        f"current_action_id={current_action_id}; "
        f"recommended_action_id={action_id}; "
        f"recommended_view_mode={(action or {}).get('recommended_view_mode')}; "
        f"allowed_label_types={allowed}; "
        f"forbidden_label_types={forbidden}; "
        f"allowed_tools={allowed_tools}; "
        "start the recommended action or pass allow_plan_order_override=true with evidence"
    )


def preflight_label_write(
    dataset_root: Path,
    key: str,
    file: str,
    label_types: list[str] | set[str],
    *,
    tool: str,
    allow_override: bool = False,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """Enforce active scene-plan ordering for agent-facing label writes."""
    requested = {str(t) for t in label_types if str(t) in _GEOMETRY_LABEL_TYPES}
    if not requested:
        return {"allowed": True, "reason": "no_geometry_label_types", "label_types": []}
    plan = read_plan_state(dataset_root, key, file)
    if not plan.get("exists"):
        return {
            "allowed": True,
            "reason": "no_scene_plan",
            "label_types": sorted(requested),
            "tool": tool,
        }
    state = plan.get("state") or {}
    current_action_id = ((state.get("current_state") or {}).get("current_action_id"))
    source = "current_action" if current_action_id else "next_action"
    action = _action_by_id(state, str(current_action_id)) if current_action_id else None
    if action is None:
        actions = next_actions_from_state(state, limit=1)
        action = actions[0] if actions else None
    if action is None:
        reason = "scene plan has no active or recommended action for geometry writes"
        if allow_override:
            _record_plan_order_override(dataset_root, state, label_types=requested, tool=tool, reason=override_reason or reason)
            return {"allowed": True, "override": True, "reason": reason, "label_types": sorted(requested), "tool": tool}
        raise _plan_order_error(reason=reason, state=state, action=action, label_types=requested, tool=tool)

    allowed = set(action.get("allowed_label_types") or [])
    forbidden = set(action.get("forbidden_label_types") or [])
    allowed_tools = set(action.get("allowed_tools") or [])
    reasons: list[str] = []
    if tool and allowed_tools and tool not in allowed_tools:
        reasons.append(f"tool {tool!r} is not allowed for action {action.get('action_id')}")
    if requested & forbidden:
        reasons.append(f"label types {sorted(requested & forbidden)} are forbidden for action {action.get('action_id')}")
    if not allowed:
        reasons.append(f"action {action.get('action_id')} is evidence/verification only and allows no label writes")
    elif requested - allowed:
        reasons.append(f"label types {sorted(requested - allowed)} are outside allowed_label_types={sorted(allowed)}")

    wall_anchoring = (state.get("current_state") or {}).get("wall_anchoring") or {}
    if requested & {"floorplan_opening", "view_opening", "dimensioned_distance", "dimension_number"} and wall_anchoring.get("status") == "failed":
        reasons.append("wall ink anchoring is failed; repair wall blockers before downstream opening/dimension writes")

    if reasons:
        reason = "; ".join(reasons)
        if allow_override:
            _record_plan_order_override(dataset_root, state, label_types=requested, tool=tool, reason=override_reason or reason)
            return {
                "allowed": True,
                "override": True,
                "reason": reason,
                "source": source,
                "action": action,
                "label_types": sorted(requested),
                "tool": tool,
            }
        raise _plan_order_error(reason=reason, state=state, action=action, label_types=requested, tool=tool)

    return {
        "allowed": True,
        "source": source,
        "action_id": action.get("action_id"),
        "task_id": action.get("task_id"),
        "recommended_view_mode": action.get("recommended_view_mode"),
        "allowed_label_types": sorted(allowed),
        "allowed_tools": sorted(allowed_tools),
        "label_types": sorted(requested),
        "tool": tool,
    }


def _rejected_attempts_for_action(state: dict[str, Any], action_id: str) -> list[dict[str, Any]]:
    action = _action_by_id_from_history(state, action_id)
    if not action:
        return []
    return [
        att for att in action.get("attempts") or []
        if action.get("status") in {"rejected", "regressed"} or att.get("outcome") in {"rejected", "regressed"}
    ]


def _action_by_id_from_history(state: dict[str, Any], action_id: str) -> dict[str, Any] | None:
    for action in state.get("actions") or []:
        if action.get("action_id") == action_id:
            return action
    return None


def _task_for_defect_category(category: str) -> str | None:
    if category in {"wall_missing_region", "wall_off_ink", "wall_topology", "possible_split_wall", "wall_continuity", "topology_candidate_review"}:
        return "VERIFY_INTERIOR_TOPOLOGY"
    if category in {"opening_relation"}:
        return "VERIFY_OPENINGS"
    if category in {"dimension"}:
        return "VERIFY_MEASUREMENTS"
    if category in {"stale_evidence"}:
        return "FINAL_QA"
    return None


def _allowed_label_types_for_defect(category: str) -> list[str]:
    if category in {"wall_missing_region", "wall_off_ink", "wall_topology", "possible_split_wall", "wall_continuity", "topology_candidate_review"}:
        return ["wall"]
    if category == "opening_relation":
        return ["floorplan_opening"]
    if category == "dimension":
        return ["dimensioned_distance", "dimension_number"]
    return []


def _forbidden_label_types_for_defect(category: str) -> list[str]:
    if category in {"wall_missing_region", "wall_off_ink", "wall_topology", "possible_split_wall", "wall_continuity", "topology_candidate_review"}:
        return ["floorplan_opening"]
    return []


def _allowed_tools_for_defect(category: str) -> list[str]:
    common = ["get_scene_view", "get_scene_view_with_labels", "add_scene_plan_evidence", "evaluate_scene_plan_gates"]
    if category in {"wall_missing_region", "wall_off_ink", "wall_topology", "possible_split_wall", "wall_continuity", "topology_candidate_review"}:
        return common + ["get_scene_repair_candidates", "get_scene_view_with_repair_candidate", "apply_repair_candidate", "decide_repair_candidate", "get_scene_plan_quality_report", "get_scene_topology_snapshot", "wall_topology_qa", "wall_continuity_check", "score_walls", "resolve_scene_point", "upsert_rect_mass", "upsert_stepped_mass", "upsert_wall_anchored", "upsert_label", "delete_label", "classify_plan_defect"]
    if category == "opening_relation":
        return common + ["opening_candidates", "get_scene_view_with_opening_candidate", "apply_opening_candidate", "decide_opening_candidate", "review_opening_candidate", "review_opening_candidates_batch", "upsert_opening_on_wall", "verify_label_placement", "upsert_label", "update_label_attrs"]
    if category == "dimension":
        return common + ["dimension_chain_context", "dimension_station_graph", "dimension_chain_transaction", "reference_dim_review", "score_measurements", "add_reference_dim", "upsert_label"]
    return common


def _required_evidence_for_defect(category: str) -> list[str]:
    if category in {"wall_missing_region", "wall_off_ink"}:
        return ["analysis_crop", "defect_classification", "verification_overlay", "score_walls_after"]
    if category in {"wall_topology", "possible_split_wall", "wall_continuity", "topology_candidate_review"}:
        return ["candidate_overlay", "accept_or_reject_decision", "topology_qa_after", "score_walls_after"]
    if category == "opening_relation":
        return ["opening_parent_crop", "verification_overlay", "opening_relation_check"]
    if category == "dimension":
        return ["dimension_crop", "verification_overlay", "score_measurements_after"]
    return ["analysis_evidence", "verification_evidence"]


def _success_gates_for_defect(category: str) -> list[str]:
    if category in {"wall_missing_region", "wall_off_ink"}:
        return ["WALL_SCORE_REVIEWED", "NO_NEW_SCORE_REGRESSION"]
    if category in {"wall_topology", "possible_split_wall", "wall_continuity", "topology_candidate_review"}:
        return ["TOPOLOGY_REVIEWED", "NO_NEW_SCORE_REGRESSION"]
    if category == "opening_relation":
        return ["OPENINGS_HAVE_PARENT_WALL", "OPENINGS_ON_WALL"]
    if category == "dimension":
        return ["MEASUREMENTS_REVIEWED"]
    return ["VISUAL_VERIFY_EXISTS"]


def _allowed_label_types_for_task(task: dict[str, Any]) -> list[str]:
    if task.get("phase") != "editing":
        return []
    category = task.get("category")
    if category == "walls":
        return ["wall"]
    if category == "openings":
        return ["floorplan_opening"]
    if category == "view_openings":
        return ["view_opening"]
    if category == "dimensions":
        return ["dimensioned_distance", "dimension_number"]
    if category == "components":
        return ["component_line"]
    if category == "heights":
        return ["height_mark", "dimensioned_distance", "dimension_number"]
    if category == "calibration":
        return ["dimensioned_distance", "dimension_number", "height_mark"]
    return []


def _forbidden_label_types_for_task(task: dict[str, Any]) -> list[str]:
    if task.get("phase") in {"analysis", "verification"}:
        return [
            "wall",
            "floorplan_opening",
            "view_opening",
            "dimensioned_distance",
            "dimension_number",
            "component_line",
            "height_mark",
        ]
    if task.get("category") == "walls":
        return ["floorplan_opening", "view_opening"]
    if task.get("category") == "components":
        return ["wall", "floorplan_opening", "view_opening"]
    if task.get("category") == "openings":
        return ["wall", "component_line", "view_opening"]
    if task.get("category") == "view_openings":
        return ["wall", "component_line", "floorplan_opening"]
    if task.get("category") in {"heights", "calibration"}:
        return ["wall", "floorplan_opening", "view_opening", "component_line"]
    return []


def _allowed_tools_for_task(task: dict[str, Any]) -> list[str]:
    phase = task.get("phase")
    if phase == "analysis":
        return ["get_scene_view", "dimension_chain_context", "dimension_station_graph", "opening_candidates", "view_geometry_candidates", "add_scene_plan_evidence", "set_scene_plan_task_state"]
    if phase == "editing":
        category = task.get("category")
        common = ["get_scene_view", "get_scene_view_with_labels", "resolve_scene_point", "add_scene_plan_evidence"]
        if category == "walls":
            return common + ["building_silhouette", "upsert_rect_mass", "upsert_stepped_mass", "upsert_wall_anchored", "upsert_label", "delete_label", "score_walls"]
        if category == "openings":
            return common + ["opening_candidates", "get_scene_view_with_opening_candidate", "apply_opening_candidate", "decide_opening_candidate", "review_opening_candidate", "review_opening_candidates_batch", "upsert_opening_on_wall", "upsert_label", "update_label_attrs", "verify_label_placement"]
        if category == "view_openings":
            return common + ["view_geometry_candidates", "upsert_label", "update_label_attrs", "verify_label_placement"]
        if category == "dimensions":
            return common + ["dimension_chain_context", "dimension_station_graph", "dimension_chain_transaction", "reference_dim_review", "add_reference_dim", "upsert_label", "score_measurements"]
        if category == "components":
            return common + ["view_geometry_candidates", "upsert_label", "delete_label", "verify_label_placement"]
        if category == "heights":
            return common + ["get_building_global_facts", "set_building_global_fact", "view_geometry_candidates", "upsert_label", "update_label_attrs", "verify_label_placement"]
        if category == "calibration":
            return common + ["get_building_global_facts", "add_reference_dim", "recompute_homography", "record_transferred_calibration", "upsert_label", "update_label_attrs", "verify_label_placement"]
        return common + ["upsert_label"]
    return ["get_scene_view_with_labels", "verify_label_placement", "score_walls", "score_measurements", "wall_topology_qa", "evaluate_scene_plan_gates"]


def _required_evidence_for_task(task: dict[str, Any]) -> list[str]:
    phase = task.get("phase")
    if phase == "analysis":
        return ["analysis_evidence"]
    if phase == "editing":
        return ["edit_evidence", "verification_overlay"]
    return ["verification_overlay", "gate_evaluation"]


def render_markdown(state: dict[str, Any]) -> str:
    current = state.get("current_state") or {}
    counts = current.get("label_counts") or {}
    scores = current.get("scores") or {}
    topology = current.get("topology") or {}
    findings = current.get("findings") or {}
    finding_clusters = current.get("finding_clusters") or {}
    open_defects = _open_defects(state)
    actions = next_actions_from_state(state, limit=5)

    lines = [
        f"# Scene plan: {state.get('key')} / {state.get('file')}",
        "",
        f"Status: {state.get('status')}",
        f"Template: {MARKDOWN_TEMPLATE_VERSION}",
        f"Schema: {state.get('schema_version') or SCHEMA_VERSION}",
        f"Scene tag: {state.get('scene_tag')}",
        f"Level/orientation: {state.get('level_or_orientation') or 'unknown'}",
        f"Created by: {state.get('created_by') or 'agent'}",
        f"Created at: {state.get('created_at')}",
        f"Last updated: {state.get('updated_at')}",
        "",
        "## 1. Current State",
        "",
        f"- Summary: {current.get('summary') or ''}",
        f"- Label counts: {', '.join(f'{k}={v}' for k, v in sorted(counts.items())) or 'none'}",
        f"- Score walls: {_score_summary(scores.get('score_walls'))}",
        f"- Score measurements: {_measurement_summary(scores.get('score_measurements'))}",
        f"- Topology: {_topology_summary(topology)}",
        f"- Current findings: count={findings.get('count', 0) if isinstance(findings, dict) else 0}, "
        f"blockers={findings.get('blockers', 0) if isinstance(findings, dict) else 0}, "
        f"warnings={findings.get('warnings', 0) if isinstance(findings, dict) else 0}, "
        f"clusters={finding_clusters.get('count', 0) if isinstance(finding_clusters, dict) else 0}",
        f"- Open blockers: {', '.join(d.get('id', '') for d in open_defects if d.get('severity') == 'blocker') or 'none'}",
        "",
        "## 2. Open Defects / Current Finding Clusters",
        "",
    ]
    cluster_items = finding_clusters.get("items") if isinstance(finding_clusters, dict) else None
    if cluster_items:
        lines += ["| ID | Severity | Confidence | Type | Region | Summary |", "|---|---|---|---|---|---|"]
        for cluster in cluster_items[:25]:
            lines.append(
                f"| {cluster.get('cluster_id')} | {cluster.get('severity')} | {cluster.get('confidence')} | "
                f"{cluster.get('cluster_type')} | `{json.dumps(cluster.get('region'), ensure_ascii=False)}` | "
                f"{cluster.get('summary') or ''} |"
            )
    else:
        lines.append("- No current finding clusters.")
    lines += [
        "",
        "## 3. Open Defects / Action History",
        "",
    ]
    if open_defects:
        lines += ["| ID | Severity | Status | Category | Classification | Region | Title |", "|---|---|---|---|---|---|---|"]
        for defect in sorted(open_defects, key=lambda d: ({"blocker": 0, "warning": 1, "info": 2}.get(d.get("severity"), 9), d.get("id", ""))):
            lines.append(
                f"| {defect.get('id')} | {defect.get('severity')} | {defect.get('status')} | "
                f"{defect.get('category')} | {defect.get('classification') or ''} | "
                f"`{json.dumps(defect.get('region'), ensure_ascii=False)}` | {defect.get('title')} |"
            )
    else:
        lines.append("- No open defects.")
    lines += ["", "## 4. Next Actions", ""]
    if actions:
        for action in actions:
            lines.append(f"- **{action.get('id')}** ({action.get('kind')}): {action.get('instruction')}")
    else:
        lines.append("- No actionable tasks.")
    lines += ["", "## 5. Task Board", ""]
    for task in state.get("tasks") or []:
        mark = {
            "todo": " ",
        "in_progress": "~",
        "blocked": "!",
        "needs_repair": "!",
        "rejected": "x",
        "verified": "x",
        "accepted_incomplete": "!",
        }.get(task.get("status"), " ")
        gate_summary = ", ".join(f"{g.get('id')}={g.get('status')}" for g in task.get("gates") or []) or "no gates"
        lines.append(f"- [{mark}] **{task.get('id')}** {task.get('title')} — `{task.get('status')}`; gates: {gate_summary}")
        if task.get("blocked_by"):
            lines.append(f"  - note: blocked by {', '.join(task.get('blocked_by') or [])}")
    lines += ["", "## 6. Evidence", ""]
    evidence = state.get("evidence") or []
    if evidence:
        lines += ["| ID | Mode | Kind | Tool | Summary |", "|---|---|---|---|---|"]
        for ev in evidence[-25:]:
            lines.append(f"| {ev.get('id')} | {ev.get('mode')} | {ev.get('kind')} | `{ev.get('tool') or ''}` | {ev.get('summary') or ''} |")
    else:
        lines.append("- No evidence recorded.")
    lines += ["", "## 7. Decision Log", ""]
    log = state.get("decision_log") or []
    if log:
        lines += ["| Time | Mode | Evidence | Decision | Result |", "|---|---|---|---|---|"]
        for row in log[-25:]:
            evidence = ", ".join(row.get("evidence_ids") or []) or row.get("evidence") or ""
            lines.append(f"| {row.get('time')} | {row.get('mode')} | {evidence} | {row.get('decision')} | {row.get('result')} |")
    else:
        lines.append("- No decisions logged.")
    lines += ["", "## 8. Final Verification", ""]
    if state.get("status") == "verified":
        lines.append("- Final QA verified by gates.")
    elif state.get("status") == "accepted_incomplete":
        lines.append("- Accepted incomplete with explicit uncertainty/waiver.")
    else:
        lines.append("- Final QA not verified; see open defects and next actions.")
    return "\n".join(lines).rstrip() + "\n"


def _score_summary(score: Any) -> str:
    if not isinstance(score, dict):
        return "not recorded"
    return (
        f"precision={score.get('precision')}, recall={score.get('recall')}, "
        f"f1={score.get('f1')}, missing={len(score.get('missing_regions') or [])}, "
        f"off_ink={len(score.get('off_ink_segments') or [])}"
    )


def _measurement_summary(score: Any) -> str:
    if not isinstance(score, dict):
        return "not recorded"
    return (
        f"ok={score.get('ok')}, dims={score.get('n_dims')}, walls={score.get('n_walls')}, "
        f"match_frac={score.get('match_frac')}"
    )


def _topology_summary(topology: Any) -> str:
    if not isinstance(topology, dict) or not topology:
        return "not recorded"
    return (
        f"walls={topology.get('wall_count')}, dangling={topology.get('dangling_endpoints')}, "
        f"near_miss={topology.get('near_miss_corners')}, fragments={topology.get('collinear_fragments')}, "
        f"stubs={topology.get('short_stubs')}, components={topology.get('components')}"
    )
