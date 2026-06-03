"""Per-scene plan + plan-state + repair-candidate routes (H5).

Extracted verbatim from api/main.py to shrink that god file. These ~30
routes form one cohesive subsystem (the labeling workflow state machine).
They are registered on an APIRouter that api/main.py includes — before the
SPA catch-all — so behavior and URL shapes are unchanged. Shared helpers and
config stay in api.main and are imported here; main.py imports this module
last (after those helpers are defined), so there is no import cycle.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from .main import (
    DATASET_DIR,
    _ensure_dataset_scene,
    _load_dataset_manifest,
    _parse_label_render_style,
    _plan_http_error,
    _scene_image_path,
    _scene_px_per_mm,
    get_labels,
    put_labels,
)
from .region_contract import normalize_bbox_region

router = APIRouter()

_NON_WALL_SEMANTIC_CLASSES = {
    "opening_symbol",
    "dimension_annotation",
    "site_boundary",
    "furniture_fixture",
    "hatching_projection",
    "landscape_vehicle",
    "ignored_noise",
}


def _semantic_exclusion_regions_for_plan(key: str, file: str) -> list[dict[str, Any]]:
    from .scene_plan_state import read_plan_state
    try:
        plan = read_plan_state(DATASET_DIR, key, file)
    except Exception:  # noqa: BLE001
        return []
    state = plan.get("state") or {}
    out = []
    for evidence in state.get("evidence") or []:
        if evidence.get("kind") != "semantic_ink_region":
            continue
        result = evidence.get("result") or {}
        if result.get("semantic_class") not in _NON_WALL_SEMANTIC_CLASSES:
            continue
        region = result.get("region")
        if isinstance(region, list) and len(region) >= 4:
            bbox_xyxy = result.get("bbox_xyxy")
            bbox_format = result.get("bbox_format") or "xywh"
            if not isinstance(bbox_xyxy, list) or len(bbox_xyxy) < 4:
                try:
                    bbox_xyxy = normalize_bbox_region(
                        region,
                        bbox_format=bbox_format,
                        reject_out_of_bounds=False,
                    ).bbox_xyxy
                except ValueError:
                    continue
            out.append({
                "region": bbox_xyxy[:4],
                "bbox_format": "xyxy",
                "bbox_xyxy": bbox_xyxy[:4],
                "semantic_class": result.get("semantic_class"),
                "evidence_id": evidence.get("id"),
            })
    return out


@router.get("/datasets/{key}/{file}/plan", tags=["dataset"])
def get_scene_plan(key: str, file: str) -> dict:
    """Read the per-scene Markdown plan used by the labeling agent."""
    _ensure_dataset_scene(key, file)
    from .scene_plans import read_plan
    return {"ok": True, "data": read_plan(DATASET_DIR, key, file)}


@router.post("/datasets/{key}/{file}/plan/template", tags=["dataset"])
def create_scene_plan_from_template_route(key: str, file: str, body: dict[str, Any] = Body(default={})) -> dict:
    """Create a scene plan from the standard template. Rejects overwrite unless
    `overwrite:true` is passed."""
    _ensure_dataset_scene(key, file)
    from .scene_plans import create_plan_from_template
    try:
        data = create_plan_from_template(
            DATASET_DIR,
            key,
            file,
            scene_tag=str(body.get("scene_tag") or "nicht_klassifiziert"),
            level_or_orientation=body.get("level_or_orientation"),
            created_by=body.get("created_by"),
            overwrite=bool(body.get("overwrite", False)),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.put("/datasets/{key}/{file}/plan", tags=["dataset"])
def put_scene_plan(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    """Create/update a scene plan. `expected_version` enables optimistic
    concurrency; `create_only:true` rejects overwrite."""
    _ensure_dataset_scene(key, file)
    markdown = body.get("markdown")
    if not isinstance(markdown, str):
        raise HTTPException(status_code=400, detail="markdown must be a string")
    from .scene_plans import write_plan
    try:
        data = write_plan(
            DATASET_DIR,
            key,
            file,
            markdown,
            expected_version=body.get("expected_version"),
            create_only=bool(body.get("create_only", False)),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan/log", tags=["dataset"])
def append_scene_plan_log_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plans import append_log
    try:
        data = append_log(
            DATASET_DIR,
            key,
            file,
            mode=str(body.get("mode") or ""),
            evidence=str(body.get("evidence") or ""),
            decision=str(body.get("decision") or ""),
            result=str(body.get("result") or ""),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.patch("/datasets/{key}/{file}/plan/tasks/{task_id}", tags=["dataset"])
def patch_scene_plan_task_route(key: str, file: str, task_id: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plans import set_task_status
    try:
        data = set_task_status(
            DATASET_DIR,
            key,
            file,
            task_id=task_id,
            status=str(body.get("status") or ""),
            note=body.get("note"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


# ── per-scene structured plan state ──────────────────────────────────────

@router.get("/datasets/{key}/{file}/plan-state", tags=["dataset"])
def get_scene_plan_state_route(key: str, file: str) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import read_plan_state
    return {"ok": True, "data": read_plan_state(DATASET_DIR, key, file)}


@router.get("/datasets/{key}/{file}/plan-state/status", tags=["dataset"])
def get_scene_plan_status_route(key: str, file: str) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import plan_status
    return {"ok": True, "data": plan_status(DATASET_DIR, key, file)}


@router.post("/datasets/{key}/{file}/plan-state/template", tags=["dataset"])
def create_scene_plan_state_from_template_route(key: str, file: str, body: dict[str, Any] = Body(default={})) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import create_plan_state_from_template
    try:
        data = create_plan_state_from_template(
            DATASET_DIR,
            key,
            file,
            scene_tag=str(body.get("scene_tag") or "nicht_klassifiziert"),
            level_or_orientation=body.get("level_or_orientation"),
            created_by=body.get("created_by"),
            overwrite=bool(body.get("overwrite", False)),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.put("/datasets/{key}/{file}/plan-state", tags=["dataset"])
def put_scene_plan_state_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    state = body.get("state")
    if not isinstance(state, dict):
        raise HTTPException(status_code=400, detail="state must be an object")
    state["key"] = key
    state["file"] = file
    from .scene_plan_state import write_plan_state
    try:
        data = write_plan_state(
            DATASET_DIR,
            state,
            expected_version=body.get("expected_version"),
            sync_markdown=bool(body.get("sync_markdown", True)),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan-state/evidence", tags=["dataset"])
def add_scene_plan_evidence_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import add_evidence
    try:
        data = add_evidence(
            DATASET_DIR,
            key,
            file,
            body,
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan-state/defects", tags=["dataset"])
def upsert_scene_plan_defect_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import upsert_defect
    try:
        data = upsert_defect(
            DATASET_DIR,
            key,
            file,
            body,
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.patch("/datasets/{key}/{file}/plan-state/defects/{defect_id}", tags=["dataset"])
def update_scene_plan_defect_route(key: str, file: str, defect_id: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import update_defect
    try:
        data = update_defect(
            DATASET_DIR,
            key,
            file,
            defect_id,
            body,
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.patch("/datasets/{key}/{file}/plan-state/tasks/{task_id}", tags=["dataset"])
def set_scene_plan_task_state_route(key: str, file: str, task_id: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import set_task_state
    try:
        data = set_task_state(
            DATASET_DIR,
            key,
            file,
            task_id,
            str(body.get("status") or ""),
            evidence_ids=body.get("evidence_ids"),
            blocked_by=body.get("blocked_by"),
            gate_updates=body.get("gate_updates"),
            note=body.get("note"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


def _compute_plan_state_gate_inputs(key: str, file: str, body: dict[str, Any]) -> dict[str, Any]:
    labels_doc = get_labels("dataset", key, file)
    score_walls_result = body.get("score_walls")
    if score_walls_result is None and bool(body.get("run_score_walls", True)):
        img_path = _scene_image_path("dataset", key, file)
        walls = []
        for lab in (labels_doc.get("labels") or []):
            if lab.get("type") != "wall":
                continue
            g = lab.get("geometry") or {}
            s, e = g.get("start"), g.get("end")
            if s and e:
                walls.append(((float(s[0]), float(s[1])), (float(e[0]), float(e[1]))))
        from PIL import Image as PILImage
        from .wall_score import score_walls
        min_wall_px = int(body.get("min_wall_px", 16))
        tol_px = int(body.get("tol_px", 18))
        close_px = int(body.get("close_px", 82))
        thin_aware = bool(body.get("thin_aware", False))
        with PILImage.open(img_path) as src:
            score_walls_result = score_walls(
                src.convert("RGB"),
                walls,
                min_wall_px=min_wall_px,
                tol_px=tol_px,
                close_px=close_px,
                thin_aware=thin_aware,
                exclusion_regions=_semantic_exclusion_regions_for_plan(key, file),
            )
        score_walls_result["n_walls"] = len(walls)
        score_walls_result["profile"] = body.get("score_profile") or (
            "faint_scan_thin_aware" if thin_aware
            else "final_scene" if (min_wall_px, tol_px, close_px) == (16, 18, 82)
            else "local_defect_tight"
        )
        score_walls_result["profile_params"] = {
            "min_wall_px": min_wall_px,
            "tol_px": tol_px,
            "close_px": close_px,
            "thin_aware": thin_aware,
        }
    score_measurements_result = body.get("score_measurements")
    if score_measurements_result is None and bool(body.get("run_score_measurements", True)):
        walls, dims = [], []
        for lab in (labels_doc.get("labels") or []):
            g = lab.get("geometry") or {}
            s, e = g.get("start"), g.get("end")
            if not s or not e:
                continue
            if lab.get("type") == "wall":
                walls.append({"start": s, "end": e})
            elif lab.get("type") == "dimensioned_distance":
                attrs = lab.get("attributes") or {}
                dims.append({"start": s, "end": e, "value_mm": attrs.get("value_mm")})
        from .measure_check import score_measurements_from_labels
        score_measurements_result = score_measurements_from_labels(
            walls,
            dims,
            tol_px=float(body.get("measurement_tol_px", 8)),
            axis_tol_px=float(body.get("axis_tol_px", 14)),
        )
    topology_result = body.get("topology_qa")
    if topology_result is None and bool(body.get("run_topology_qa", True)):
        from .wall_topology import wall_topology_qa
        topology_result = wall_topology_qa(labels_doc.get("labels") or [])
    continuity_result = body.get("continuity_check")
    if continuity_result is None and bool(body.get("run_continuity_check", True)):
        from .wall_topology import wall_continuity_check
        continuity_result = wall_continuity_check(labels_doc.get("labels") or [])
    return {
        "labels_doc": labels_doc,
        "score_walls_result": score_walls_result,
        "score_measurements_result": score_measurements_result,
        "topology_result": topology_result,
        "continuity_result": continuity_result,
    }


@router.post("/datasets/{key}/{file}/plan-state/evaluate-gates", tags=["dataset"])
def evaluate_scene_plan_gates_route(key: str, file: str, body: dict[str, Any] = Body(default={})) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import evaluate_gates
    try:
        inputs = _compute_plan_state_gate_inputs(key, file, body)
        data = evaluate_gates(
            DATASET_DIR,
            key,
            file,
            labels_doc=inputs["labels_doc"],
            score_walls_result=inputs["score_walls_result"],
            score_measurements_result=inputs["score_measurements_result"],
            topology_result=inputs["topology_result"],
            continuity_result=inputs["continuity_result"],
            visual_evidence=bool(body.get("visual_evidence", False)),
            quality_profile=body.get("quality_profile"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.get("/datasets/{key}/{file}/plan-state/next-actions", tags=["dataset"])
def get_scene_plan_next_actions_route(key: str, file: str, limit: int = 3) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import next_actions_from_state, read_plan_state
    data = read_plan_state(DATASET_DIR, key, file)
    state = data.get("state")
    return {"ok": True, "data": {"exists": data["exists"], "actions": next_actions_from_state(state, limit=limit) if state else []}}


@router.get("/datasets/{key}/{file}/plan-state/next-action", tags=["dataset"])
def get_scene_plan_next_action_route(key: str, file: str) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import next_action
    return {"ok": True, "data": next_action(DATASET_DIR, key, file)}


def _workbench_view_mode(action: dict[str, Any] | None) -> str:
    if not action:
        return "analysis_view"
    phase = action.get("phase")
    category = action.get("category")
    if category == "openings":
        return "opening_candidate_view" if phase == "editing" else "analysis_view"
    if category == "dimensions":
        return "measurement_read_view" if phase in {"analysis", "editing"} else "edit_verify_view"
    if category == "walls":
        return "silhouette_view" if phase == "analysis" else ("topology_qa_view" if phase == "verification" else "coordinate_pick_view")
    if phase == "verification":
        return "topology_qa_view"
    if phase == "editing":
        return "coordinate_pick_view"
    return "analysis_view"


def _label_type_counts(labels_doc: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in labels_doc.get("labels") or []:
        if isinstance(label, dict):
            label_type = str(label.get("type") or "unknown")
            counts[label_type] = counts.get(label_type, 0) + 1
    return counts


def _mass_group_summary(labels_doc: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for lab in labels_doc.get("labels") or []:
        if not isinstance(lab, dict) or lab.get("type") != "wall":
            continue
        attrs = lab.get("attributes") or {}
        mass_id = attrs.get("mass_id")
        if not mass_id:
            continue
        group = groups.setdefault(str(mass_id), {
            "mass_id": str(mass_id),
            "mass_kind": attrs.get("mass_kind") or "other",
            "mass_tool": attrs.get("mass_tool"),
            "wall_count": 0,
            "label_ids": [],
            "edge_confidence_min": None,
        })
        group["wall_count"] += 1
        group["label_ids"].append(lab.get("id"))
        conf = attrs.get("edge_confidence")
        if isinstance(conf, (int, float)):
            current = group.get("edge_confidence_min")
            group["edge_confidence_min"] = conf if current is None else min(float(current), float(conf))
    return sorted(groups.values(), key=lambda g: (str(g.get("mass_kind")), str(g.get("mass_id"))))


def _transaction_summary(labels_doc: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    tx: dict[str, dict[str, Any]] = {}
    for lab in labels_doc.get("labels") or []:
        if not isinstance(lab, dict):
            continue
        attrs = lab.get("attributes") or {}
        transaction_id = attrs.get("transaction_id") or attrs.get("mass_id")
        if not transaction_id:
            continue
        row = tx.setdefault(str(transaction_id), {
            "transaction_id": str(transaction_id),
            "label_types": {},
            "label_ids": [],
            "tools": set(),
            "qa_statuses": set(),
        })
        label_type = str(lab.get("type") or "unknown")
        row["label_types"][label_type] = row["label_types"].get(label_type, 0) + 1
        row["label_ids"].append(lab.get("id"))
        for key in ("mass_tool",):
            if attrs.get(key):
                row["tools"].add(str(attrs[key]))
        if attrs.get("qa_status"):
            row["qa_statuses"].add(str(attrs["qa_status"]))
    for ev in state.get("evidence") or []:
        if not isinstance(ev, dict):
            continue
        result = ev.get("result") or {}
        transaction_id = result.get("transaction_id") or (ev.get("params") or {}).get("transaction_id")
        if not transaction_id:
            continue
        row = tx.setdefault(str(transaction_id), {
            "transaction_id": str(transaction_id),
            "label_types": {},
            "label_ids": [],
            "tools": set(),
            "qa_statuses": set(),
        })
        if ev.get("tool"):
            row["tools"].add(str(ev["tool"]))
        row["evidence_id"] = ev.get("id")
        row["summary"] = ev.get("summary")
    out = []
    for row in tx.values():
        out.append({
            **row,
            "tools": sorted(row.get("tools") or []),
            "qa_statuses": sorted(row.get("qa_statuses") or []),
            "label_count": len(row.get("label_ids") or []),
        })
    return sorted(out, key=lambda r: str(r.get("transaction_id")))[:20]


def _opening_candidate_summary(key: str, file: str, action: dict[str, Any] | None) -> dict[str, Any]:
    category = (action or {}).get("category")
    allowed = set((action or {}).get("allowed_tools") or [])
    if category != "openings" and "opening_candidates" not in allowed:
        return {"included": False, "reason": "next action is not opening-related"}
    try:
        img_path = _scene_image_path("dataset", key, file)
        labels_doc = get_labels("dataset", key, file)
        from PIL import Image as PILImage
        from .opening_candidates import opening_candidate_report
        with PILImage.open(img_path) as src:
            report = opening_candidate_report(src.convert("RGB"), labels_doc, limit=8)
    except Exception as e:  # noqa: BLE001
        return {"included": False, "error": str(e)}
    candidates = report.get("candidates") or []
    by_kind: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    compact = []
    for candidate in candidates[:5]:
        kind = str(candidate.get("kind") or "unknown")
        confidence = str(candidate.get("confidence") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_confidence[confidence] = by_confidence.get(confidence, 0) + 1
        compact.append({
            "candidate_id": candidate.get("candidate_id"),
            "candidate_fingerprint": candidate.get("candidate_fingerprint"),
            "kind": candidate.get("kind"),
            "confidence": candidate.get("confidence"),
            "parent_wall_id": candidate.get("parent_wall_id"),
            "opening_kind": candidate.get("opening_kind"),
            "region": candidate.get("region"),
        })
    return {
        "included": True,
        "count": int(report.get("count") or len(candidates)),
        "by_kind": by_kind,
        "by_confidence": by_confidence,
        "candidates": compact,
        "truncated": len(candidates) > len(compact),
    }


@router.get("/datasets/{key}/{file}/plan-state/workbench", tags=["dataset"])
def get_scene_workbench_state_route(key: str, file: str) -> dict:
    """Return the compact scene workbench state for one agent loop iteration."""
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import next_action, plan_status, read_plan_state
    labels_doc = get_labels("dataset", key, file)
    status = plan_status(DATASET_DIR, key, file)
    action_env = next_action(DATASET_DIR, key, file)
    action = action_env.get("action") if isinstance(action_env, dict) else None
    plan = read_plan_state(DATASET_DIR, key, file)
    state = plan.get("state") or {}
    current = state.get("current_state") or {}
    manifest = _load_dataset_manifest(key) or {}
    scene_entry = next((d for d in manifest.get("drawings") or [] if d.get("file") == file), {})
    recent_evidence = []
    for item in (state.get("evidence") or [])[-5:]:
        if isinstance(item, dict):
            recent_evidence.append({
                "id": item.get("id"),
                "kind": item.get("kind"),
                "mode": item.get("mode"),
                "summary": item.get("summary"),
                "task_ids": item.get("task_ids") or [],
            })
    data = {
        "workbench_contract": "scene-workbench-state/v1",
        "key": key,
        "file": file,
        "plan": {
            "exists": status.get("exists"),
            "version": status.get("version"),
            "status": status.get("status"),
            "summary": status.get("summary"),
            "quality_tier": status.get("quality_tier"),
            "completion_state": status.get("completion_state"),
            "review_debt": status.get("review_debt"),
            "final_qa_summary": status.get("final_qa_summary"),
            "terminal": status.get("terminal"),
            "required_complete": status.get("required_complete"),
            "percent_complete": status.get("percent_complete"),
            "open_blockers": status.get("open_blockers"),
            "open_warnings": status.get("open_warnings"),
            "terminality_reasons": status.get("terminality_reasons") or [],
        },
        "current_task": (action or {}).get("task_id"),
        "phase": (action or {}).get("phase") or "analysis",
        "recommended_view_mode": _workbench_view_mode(action),
        "recommended_region": (action or {}).get("region"),
        "next_action": action,
        "allowed_tools": (action or {}).get("allowed_tools") or [],
        "forbidden_writes": (action or {}).get("forbidden_label_types") or [],
        "required_evidence": (action or {}).get("required_evidence") or [],
        "crop_warnings": scene_entry.get("crop_warnings") or [],
        "labels_summary": {
            "total": len(labels_doc.get("labels") or []),
            "by_type": _label_type_counts(labels_doc),
            "mass_groups": _mass_group_summary(labels_doc),
        },
        "blocker_summary": {
            "open_blockers": status.get("open_blockers"),
            "open_warnings": status.get("open_warnings"),
            "reasons": status.get("terminality_reasons") or [],
        },
        "semantic_exclusions_summary": {
            "available": True,
            "count": len(_semantic_exclusion_regions_for_plan(key, file)),
            "regions": _semantic_exclusion_regions_for_plan(key, file)[:8],
        },
        "candidate_queue_summary": _opening_candidate_summary(key, file, action),
        "transaction_history": _transaction_summary(labels_doc, state),
        "recent_evidence": recent_evidence,
        "quality": {
            "label_counts": current.get("label_counts") or {},
            "current_findings": current.get("findings") or {},
            "finding_clusters": current.get("finding_clusters") or {},
        },
    }
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan-state/preflight-label-write", tags=["dataset"])
def preflight_scene_plan_label_write_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    """Validate a pending label write against the active scene-plan action."""
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import preflight_label_write
    try:
        data = preflight_label_write(
            DATASET_DIR,
            key,
            file,
            body.get("label_types") or [],
            tool=str(body.get("tool") or ""),
            allow_override=bool(body.get("allow_override", False)),
            override_reason=body.get("override_reason"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan-state/actions/{action_id}/start", tags=["dataset"])
def start_scene_plan_action_route(key: str, file: str, action_id: str, body: dict[str, Any] = Body(default={})) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import start_action
    try:
        data = start_action(
            DATASET_DIR,
            key,
            file,
            action_id,
            agent_id=body.get("agent_id"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan-state/actions/{action_id}/attempts", tags=["dataset"])
def record_scene_plan_attempt_route(key: str, file: str, action_id: str, body: dict[str, Any] = Body(default={})) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import record_attempt
    try:
        data = record_attempt(
            DATASET_DIR,
            key,
            file,
            action_id,
            body,
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan-state/actions/{action_id}/finish", tags=["dataset"])
def finish_scene_plan_action_route(key: str, file: str, action_id: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import finish_action
    try:
        data = finish_action(
            DATASET_DIR,
            key,
            file,
            action_id,
            outcome=str(body.get("outcome") or ""),
            attempt_id=body.get("attempt_id"),
            evidence_ids=body.get("evidence_ids"),
            reason=body.get("reason"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan-state/tasks/{task_id}/reopen", tags=["dataset"])
def reopen_scene_plan_task_route(key: str, file: str, task_id: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import reopen_task
    try:
        data = reopen_task(
            DATASET_DIR,
            key,
            file,
            task_id,
            reason=str(body.get("reason") or ""),
            evidence_ids=body.get("evidence_ids"),
            invalidate_dependents=bool(body.get("invalidate_dependents", True)),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan-state/defects/{defect_id}/classify", tags=["dataset"])
def classify_scene_plan_defect_route(key: str, file: str, defect_id: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import classify_defect
    try:
        data = classify_defect(
            DATASET_DIR,
            key,
            file,
            defect_id,
            str(body.get("classification") or ""),
            evidence_ids=body.get("evidence_ids"),
            note=body.get("note"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan-state/evaluate-terminality", tags=["dataset"])
def evaluate_scene_plan_terminality_route(key: str, file: str) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import evaluate_terminality
    return {"ok": True, "data": evaluate_terminality(DATASET_DIR, key, file)}


@router.get("/datasets/{key}/{file}/plan-state/repair-candidates", tags=["dataset"])
def get_scene_repair_candidates_route(key: str, file: str, limit: int = 20) -> dict:
    _ensure_dataset_scene(key, file)
    labels_doc = get_labels("dataset", key, file)
    from .topology_repair import repair_candidate_report
    from .scene_plan_state import read_plan_state
    try:
        plan = read_plan_state(DATASET_DIR, key, file)
        data = repair_candidate_report(labels_doc, limit=limit, plan_state=plan.get("state"))
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


def _find_repair_candidate(labels_doc: dict[str, Any], candidate_id: str, key: str | None = None, file: str | None = None) -> dict[str, Any]:
    from .topology_repair import repair_candidate_report
    plan_state = None
    if key and file:
        from .scene_plan_state import read_plan_state
        plan_state = (read_plan_state(DATASET_DIR, key, file).get("state") or None)
    report = repair_candidate_report(labels_doc, limit=200, plan_state=plan_state)
    for cluster in report.get("clusters") or []:
        for cand in cluster.get("candidates") or []:
            if cand.get("candidate_id") == candidate_id:
                return cand
    raise KeyError(f"repair candidate {candidate_id!r} not found")


@router.post("/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/apply", tags=["dataset"])
def apply_repair_candidate_route(key: str, file: str, candidate_id: str, body: dict[str, Any] = Body(default={})) -> dict:
    _ensure_dataset_scene(key, file)
    labels_doc = get_labels("dataset", key, file)
    try:
        candidate = _find_repair_candidate(labels_doc, candidate_id, key, file)
        if body.get("expected_candidate_op") and body.get("expected_candidate_op") != candidate.get("op"):
            raise ValueError("candidate op changed; refresh repair candidates")
        from .topology_repair import apply_candidate_to_labels, simulate_candidate
        from .scene_plan_state import PlanStateConflictError, read_plan_state, record_repair_candidate_decision
        if body.get("expected_version"):
            current_plan = read_plan_state(DATASET_DIR, key, file)
            if current_plan.get("exists") and current_plan.get("version") != body.get("expected_version"):
                raise PlanStateConflictError("plan state version conflict")
        simulation = simulate_candidate(labels_doc, candidate)
        new_doc = apply_candidate_to_labels(labels_doc, candidate)
        persisted = False
        if candidate.get("op") != "no_edit_classification":
            put_labels("dataset", key, file, new_doc)
            persisted = True
        decision = record_repair_candidate_decision(
            DATASET_DIR,
            key,
            file,
            candidate,
            "accepted_applied" if persisted else str(body.get("outcome") or "accepted_uncertain"),
            evidence_ids=body.get("evidence_ids"),
            note=body.get("note"),
            simulation=simulation,
            expected_version=body.get("expected_version"),
        )
        data = {
            "candidate_id": candidate_id,
            "candidate": candidate,
            "simulation": simulation,
            "persisted": persisted,
            "labels_changed": persisted,
            "decision": ((decision.get("state") or {}).get("current_state") or {}).get("repair_candidate_decisions", {}),
        }
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/decision", tags=["dataset"])
def decide_repair_candidate_route(key: str, file: str, candidate_id: str, body: dict[str, Any] = Body(...)) -> dict:
    _ensure_dataset_scene(key, file)
    labels_doc = get_labels("dataset", key, file)
    try:
        candidate = _find_repair_candidate(labels_doc, candidate_id, key, file)
        if body.get("expected_candidate_op") and body.get("expected_candidate_op") != candidate.get("op"):
            raise ValueError("candidate op changed; refresh repair candidates")
        outcome = str(body.get("outcome") or "")
        from .topology_repair import simulate_candidate
        from .scene_plan_state import record_repair_candidate_decision
        simulation = simulate_candidate(labels_doc, candidate)
        data = record_repair_candidate_decision(
            DATASET_DIR,
            key,
            file,
            candidate,
            outcome,
            evidence_ids=body.get("evidence_ids"),
            note=body.get("note"),
            simulation=simulation,
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.get("/datasets/{key}/{file}/plan-state/quality-report", tags=["dataset"])
def get_scene_plan_quality_report_route(key: str, file: str) -> dict:
    _ensure_dataset_scene(key, file)
    labels_doc = get_labels("dataset", key, file)
    try:
        from .scene_plan_state import read_plan_state
        from .topology_repair import quality_report, repair_candidate_report
        plan = read_plan_state(DATASET_DIR, key, file)
        state = plan.get("state") or {}
        candidates = repair_candidate_report(labels_doc, limit=200, plan_state=state)
        data = quality_report(state, candidates)
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.get("/datasets/{key}/{file}/plan-state/topology-snapshot", tags=["dataset"])
def get_scene_plan_topology_snapshot_route(key: str, file: str) -> dict:
    _ensure_dataset_scene(key, file)
    labels_doc = get_labels("dataset", key, file)
    try:
        from .scene_plan_state import read_plan_state
        from .topology_repair import repair_candidate_report, topology_regression_snapshot
        plan = read_plan_state(DATASET_DIR, key, file)
        state = plan.get("state") or {}
        candidates = repair_candidate_report(labels_doc, limit=200, plan_state=state)
        data = topology_regression_snapshot(state, candidates)
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.get("/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/overlay", tags=["dataset"])
def render_repair_candidate_overlay_route(
    key: str,
    file: str,
    candidate_id: str,
    max_dim: int = 1600,
    clean: bool = True,
    style: str | None = "ink_compare",
):
    _ensure_dataset_scene(key, file)
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    labels_doc = get_labels("dataset", key, file)
    try:
        candidate = _find_repair_candidate(labels_doc, candidate_id, key, file)
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    region = candidate.get("region")
    parsed_region = None
    if isinstance(region, list) and len(region) >= 4:
        x0, y0, x1, y1 = [int(round(float(v))) for v in region[:4]]
        pad = 40
        parsed_region = (max(0, x0 - pad), max(0, y0 - pad), max(x1 + pad, x0 + pad), max(y1 + pad, y0 + pad))
    from PIL import Image as PILImage, ImageDraw
    from .label_render import render_grid_with_labels
    with PILImage.open(img_path) as src:
        overlay = render_grid_with_labels(
            src.convert("RGB"),
            labels_doc.get("labels") or [],
            tiers=("finer",),
            region=parsed_region,
            max_dim=max_dim,
            clean=bool(clean),
            style=_parse_label_render_style(style),
            background_opacity=0.2,
            background_opacity_explicit=True,
            contrast="high",
            px_per_mm=_scene_px_per_mm(key, file),
            show_relations="required",
        )
    draw = ImageDraw.Draw(overlay, "RGBA")
    if parsed_region is not None:
        rx0, ry0, rx1, ry1 = parsed_region
    else:
        rx0, ry0 = 0, 0
        with PILImage.open(img_path) as src:
            rx1, ry1 = src.size
    scale = min(max_dim / max(1, rx1 - rx0), max_dim / max(1, ry1 - ry0), 1.0)

    def to_out(pt: Any) -> tuple[float, float] | None:
        if not (isinstance(pt, list) and len(pt) == 2):
            return None
        return ((float(pt[0]) - rx0) * scale, (float(pt[1]) - ry0) * scale)

    for edit in candidate.get("edits") or []:
        if edit.get("to"):
            pt = to_out(edit.get("to"))
            if pt:
                r = 7
                draw.ellipse((pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r), outline=(236, 72, 153, 255), width=3)
        if edit.get("wall"):
            a = to_out(edit["wall"][0])
            b = to_out(edit["wall"][1])
            if a and b:
                draw.line((a[0], a[1], b[0], b[1]), fill=(236, 72, 153, 255), width=5)
    labels_by_id = {str(l.get("id")): l for l in labels_doc.get("labels") or [] if isinstance(l, dict)}
    for edit in candidate.get("edits") or []:
        lab = labels_by_id.get(str(edit.get("label_id") or ""))
        if lab and edit.get("to"):
            g = lab.get("geometry") or {}
            other_key = "start" if edit.get("endpoint") == "end" else "end"
            a = to_out(g.get(other_key))
            b = to_out(edit.get("to"))
            if a and b:
                draw.line((a[0], a[1], b[0], b[1]), fill=(6, 182, 212, 255), width=4)
    import io
    buf = io.BytesIO()
    overlay.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.post("/datasets/{key}/{file}/plan-state/render-markdown", tags=["dataset"])
def render_scene_plan_markdown_route(key: str, file: str, body: dict[str, Any] = Body(default={})) -> dict:
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import read_plan_state, render_markdown, write_plan_state
    try:
        data = read_plan_state(DATASET_DIR, key, file)
        if not data["exists"]:
            raise FileNotFoundError("plan state does not exist")
        markdown = render_markdown(data["state"])
        if bool(body.get("sync", True)):
            data = write_plan_state(DATASET_DIR, data["state"], expected_version=body.get("expected_version"), sync_markdown=True)
            markdown = data["markdown"]
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": {"markdown": markdown, "path": data.get("markdown_path"), "version": data.get("version")}}
