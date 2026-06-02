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
    _parse_label_render_style,
    _plan_http_error,
    _scene_image_path,
    _scene_px_per_mm,
    get_labels,
    put_labels,
)

router = APIRouter()


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
