"""Plan MCP tools (extracted from mcp_server.py, H5).

Stable helpers imported by value; HTTP helpers, __file__, and inter-tool
calls go through `mcp_server.` so the test-harness patches still apply.
"""
from __future__ import annotations

from mcp.types import ImageContent
from mcp.types import TextContent
import httpx
import json
import time

import mcp_server
from mcp_server import (
    _api_unreachable_error,
    _err,
    _http_status_to_error,
    _image_delivery_payload,
    _is_file_groundfloor,
    _ok,
    _response_mode_payload,
    _scene_order_guard,
    _wait_for_api,
    _wrap_text,
    mcp,
)


@mcp.tool()
async def create_scene_plan_state_from_template(
    key: str,
    file: str,
    scene_tag: str = "nicht_klassifiziert",
    level_or_orientation: str | None = None,
    created_by: str | None = "bim-agent",
    overwrite: bool = False,
) -> dict:
    """Create authoritative plan-state JSON plus rendered Markdown.

    USE when:
      - Inventory/extraction has produced a classified scene and a
        subagent is about to place geometry labels.
      - A legacy scene has labels but no plan state, and you need to
        bring it back under the analyze → edit → verify workflow.
      This writes the structured `*.plan.json` sidecar and syncs the
      human-readable `plan.md`.

    DON'T USE to overwrite a plan with human edits unless the user
    explicitly asked for a reset; leave `overwrite=false` for normal runs.

    Args:
      key/file: scene identifier.
      scene_tag: current workflow scene tag (`grundriss`, `ansicht`,
                 `schnitt`, `sonstiges`, or `nicht_klassifiziert`).
      level_or_orientation: EG/DG/etc for Grundriss or cardinal face for
                            Ansicht/Schnitt when known.
      created_by: provenance marker stored in the plan state.
      overwrite: replace an existing plan state when true.
    """
    started = time.time()
    body = {
        "scene_tag": scene_tag,
        "level_or_orientation": level_or_orientation,
        "created_by": created_by,
        "overwrite": overwrite,
    }
    try:
        status, res = await mcp_server._api_post(f"/datasets/{key}/{file}/plan-state/template", body)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, res = await mcp_server._api_post(f"/datasets/{key}/{file}/plan-state/template", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    return _ok(res.get("data") if isinstance(res, dict) else res, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_plan_state(key: str, file: str) -> dict:
    """Read the structured per-scene plan state sidecar.

    USE when:
      - Starting or resuming a scene subagent and you need the full
        task/defect/evidence state.

    DON'T USE when:
      - You only need routing status; prefer `get_scene_plan_status` or
        `get_scene_context_summary` because they are much smaller.
    """
    started = time.time()
    status, body = await mcp_server._api_get(f"/datasets/{key}/{file}/plan-state")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_plan_status(key: str, file: str) -> dict:
    """Return concise terminality/progress status for a scene plan.

    USE when:
      - Before spawning or resuming a scene subagent.
      - Checking whether a legacy labeled scene is missing its plan.

    DON'T USE when:
      - You need the full task/evidence body; call `get_scene_plan_state`.
      If `exists=false`, create the plan before placing geometry labels.
    """
    started = time.time()
    status, body = await mcp_server._api_get(f"/datasets/{key}/{file}/plan-state/status")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_plan_next_action(key: str, file: str) -> dict:
    """Return exactly one blocker-first, subagent-ready next action.

    USE when:
      - Inside the scene analyze → edit → verify loop and ready to work
        on one focused blocker or task.
      - You accept global scene priority. If EG required geometry is still
        open, non-EG requests return `code='groundfloor_first_blocked'`.

    DON'T USE when:
      - The scene plan is missing; create it first. Claim the returned
        action with `start_scene_plan_action`, record attempts/evidence,
        close it, and re-run `evaluate_scene_plan_gates`.
      - You are trying to skip the global recommender. Resume long runs
        from `get_recommended_next_action`.
    """
    started = time.time()
    guard = await _scene_order_guard(key, file)
    if guard:
        return _ok(guard, started_at=started)
    status, body = await mcp_server._api_get(f"/datasets/{key}/{file}/plan-state/next-action")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    data = body.get("data") if isinstance(body, dict) else body
    if isinstance(data, dict):
        data.setdefault("scene_order_blocked", False)
        data.setdefault("global_recommended_file", file)
        data.setdefault("scene_priority", "groundfloor-first" if await _is_file_groundfloor(key, file) else "scene-order")
    return _ok(data, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_plan_next_actions(key: str, file: str, limit: int = 3) -> dict:
    """Return blocker-first, subagent-ready next actions for one scene.

    USE when:
      - A parent/orchestrator needs a compact queue preview before
        assigning scene work.

    DON'T USE when:
      - A worker is already editing a scene; use the singular
        `get_scene_plan_next_action` to stay focused.
    """
    started = time.time()
    guard = await _scene_order_guard(key, file)
    if guard:
        return _ok({"exists": True, "actions": [], **guard}, started_at=started)
    status, body = await mcp_server._api_get(
        f"/datasets/{key}/{file}/plan-state/next-actions",
        params={"limit": int(limit)},
    )
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_workbench_state(key: str, file: str) -> dict:
    """Return the compact scene workbench state for one agent loop.

    USE when:
      - Starting or resuming a scene worker iteration.
      - You need the current task, recommended view mode, allowed tools,
        blockers, label counts, recent evidence, and candidate queue summary
        without loading the full plan state or Markdown.

    DON'T USE when:
      - You need the full audit body; call `get_scene_plan_state`.
    """
    started = time.time()
    guard = await _scene_order_guard(key, file)
    if guard:
        return _ok({"workbench_contract": "scene-workbench-state/v1", **guard}, started_at=started)
    status, body = await mcp_server._api_get(f"/datasets/{key}/{file}/plan-state/workbench")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def start_scene_plan_action(
    key: str,
    file: str,
    action_id: str,
    run_id: str | None = None,
    agent_id: str | None = "bim-agent",
    subagent_id: str | None = None,
    expected_version: str | None = None,
    response_mode: str = "compact",
) -> dict:
    """Claim a scene-plan action and mark its task/defect in progress.

    USE when:
      - Before making edits for a plan action.

    DON'T USE when:
      - You have not fetched a next action from the current plan state.
      This prevents a resumed worker from silently working stale state
      and gives reviewers a clear current action in the plan.
    """
    started = time.time()
    guard = await _scene_order_guard(key, file)
    if guard:
        return _err(
            "groundfloor_first_blocked",
            guard["reason"],
            hint="Call get_recommended_next_action and work the recommended EG scene first.",
            retry=False,
            details=guard,
            started_at=started,
        )
    body = {
        "run_id": run_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
        "expected_version": expected_version,
    }
    status, res = await mcp_server._api_post(f"/datasets/{key}/{file}/plan-state/actions/{action_id}/start", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    try:
        payload = _response_mode_payload(data, response_mode=response_mode, action="start_action")
    except ValueError as e:
        return _err("bad_response_mode", str(e), started_at=started, status_code=400)
    return _ok(payload, started_at=started, status_code=status)


@mcp.tool()
async def record_scene_plan_attempt(
    key: str,
    file: str,
    action_id: str,
    hypothesis: str,
    edits: list[dict] | None = None,
    evidence_ids: list[str] | None = None,
    attempt_id: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    subagent_id: str | None = None,
    expected_version: str | None = None,
    response_mode: str = "compact",
) -> dict:
    """Record one coherent edit/review attempt for the current action.

    USE when:
      - After an edit or review pass, before finishing the action.

    DON'T USE when:
      - You only have a vague transcript note; add concrete evidence
        first. Keep the hypothesis short and cite evidence IDs rather
        than pasting visual transcript details.
    """
    started = time.time()
    body = {
        "hypothesis": hypothesis,
        "edits": edits or [],
        "evidence_ids": evidence_ids or [],
        "attempt_id": attempt_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
        "expected_version": expected_version,
    }
    status, res = await mcp_server._api_post(f"/datasets/{key}/{file}/plan-state/actions/{action_id}/attempts", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    try:
        payload = _response_mode_payload(data, response_mode=response_mode, action="record_attempt")
    except ValueError as e:
        return _err("bad_response_mode", str(e), started_at=started, status_code=400)
    return _ok(payload, started_at=started, status_code=status)


@mcp.tool()
async def finish_scene_plan_action(
    key: str,
    file: str,
    action_id: str,
    outcome: str,
    reason: str | None = None,
    evidence_ids: list[str] | None = None,
    attempt_id: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    subagent_id: str | None = None,
    expected_version: str | None = None,
    response_mode: str = "compact",
) -> dict:
    """Finish one plan action after verification.

    USE when:
      - The attempted edit has been verified, rejected, or clearly
        blocked, with evidence or a blocker reason.

    DON'T USE when:
      - The edit has not been visually/algorithmically checked yet.
      Valid outcomes are `fixed`, `still_open`, `rejected`,
      `rejected_false_positive`, `accepted_uncertain`,
      `accepted_risk`, `accepted_source_limited`, `regressed`, and
      `blocked_external`.
      For task actions, `accepted_uncertain` is rejected by the API:
      keep working, finish as `blocked_external`, or verify with passing
      gates. Required tasks are complete only when `verified`.
    """
    started = time.time()
    body = {
        "outcome": outcome,
        "reason": reason,
        "evidence_ids": evidence_ids or [],
        "attempt_id": attempt_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
        "expected_version": expected_version,
    }
    status, res = await mcp_server._api_post(f"/datasets/{key}/{file}/plan-state/actions/{action_id}/finish", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    try:
        payload = _response_mode_payload(data, response_mode=response_mode, action="finish_action")
    except ValueError as e:
        return _err("bad_response_mode", str(e), started_at=started, status_code=400)
    return _ok(payload, started_at=started, status_code=status)


@mcp.tool()
async def batch_close_scene_plan_warnings(
    key: str,
    file: str,
    status: str,
    evidence_ids: list[str],
    category: str | None = None,
    defect_ids: list[str] | None = None,
    classification: str | None = None,
    reason: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    subagent_id: str | None = None,
    expected_version: str | None = None,
    response_mode: str = "compact",
) -> dict:
    """Close a reviewed batch of warning defects with shared evidence.

    USE when:
      - Several warnings of the same class have been inspected together.
      - The correct terminal outcome is shared, e.g.
        `rejected_false_positive`, `accepted_risk`, or
        `accepted_source_limited`.

    DON'T USE when:
      - Any selected item is a blocker or still unresolved. Work those as
        focused defect actions.
      - You do not have evidence for the shared decision.
    """
    started = time.time()
    body = {
        "status": status,
        "evidence_ids": evidence_ids or [],
        "category": category,
        "defect_ids": defect_ids or [],
        "classification": classification,
        "reason": reason,
        "run_id": run_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
        "expected_version": expected_version,
    }
    status_code, res = await mcp_server._api_post(
        f"/datasets/{key}/{file}/plan-state/defects/batch-close-warnings",
        body,
    )
    if status_code >= 400:
        return _http_status_to_error(status_code, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    try:
        payload = _response_mode_payload(data, response_mode=response_mode, action="batch_close_warnings")
    except ValueError as e:
        return _err("bad_response_mode", str(e), started_at=started, status_code=400)
    return _ok(payload, started_at=started, status_code=status_code)


@mcp.tool()
async def record_dimension_chain_review(
    key: str,
    file: str,
    chain_region: list,
    orientation: str,
    decision: str,
    readable_values: list | None = None,
    unreadable_fragments: list | None = None,
    reason: str | None = None,
    enhance: str | None = None,
    task_ids: list[str] | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    subagent_id: str | None = None,
    expected_version: str | None = None,
    response_mode: str = "compact",
) -> dict:
    """Record a structured review of a dimension chain.

    USE when:
      - A floorplan/elevation/section dimension chain has been inspected.
      - Values were readable, partially readable, or source-unreadable and
        should affect measurement/calibration gates.

    For `decision='source_unreadable'`, include the crop region,
    enhancement mode if used, visible/unreadable fragments, and a reason.
    This lets gates close honestly without inventing dimension values.
    """
    started = time.time()
    body = {
        "kind": "dimension_chain_review",
        "mode": "analysis",
        "summary": reason or f"Dimension chain review: {decision}",
        "tool": "record_dimension_chain_review",
        "task_ids": task_ids or [],
        "run_id": run_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
        "result": {
            "chain_region": chain_region,
            "orientation": orientation,
            "decision": decision,
            "readable_values": readable_values or [],
            "unreadable_fragments": unreadable_fragments or [],
            "reason": reason or "",
            "enhance": enhance,
        },
        "expected_version": expected_version,
    }
    status, res = await mcp_server._api_post(f"/datasets/{key}/{file}/plan-state/evidence", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    try:
        payload = _response_mode_payload(data, response_mode=response_mode, action="record_dimension_chain_review")
    except ValueError as e:
        return _err("bad_response_mode", str(e), started_at=started, status_code=400)
    return _ok(payload, started_at=started, status_code=status)


@mcp.tool()
async def classify_plan_defect(
    key: str,
    file: str,
    defect_id: str,
    classification: str,
    evidence_ids: list[str] | None = None,
    note: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    subagent_id: str | None = None,
    expected_version: str | None = None,
    response_mode: str = "compact",
) -> dict:
    """Classify one scene-plan defect before closing it.

    USE when:
      - A wall-score missing/off-ink defect has been visually reviewed and is
        not automatically repairable.
      - You need to close a score defect as rejected/accepted_uncertain/fixed;
        wall_missing_region and wall_off_ink defects require this first.

    Classifications:
      real_missing_wall, bad_existing_wall, duplicate_wall_face_not_centerline,
      opening_symbol, door_swing_or_hint, dashed_projection,
      furniture_or_fixture, dimension_or_annotation, site_or_boundary_line,
      separate_structure, false_positive, ambiguous.
    """
    started = time.time()
    body = {
        "classification": classification,
        "evidence_ids": evidence_ids or [],
        "note": note,
        "run_id": run_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
        "expected_version": expected_version,
    }
    status, res = await mcp_server._api_post(
        f"/datasets/{key}/{file}/plan-state/defects/{defect_id}/classify",
        body,
    )
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    try:
        payload = _response_mode_payload(data, response_mode=response_mode, action="classify_defect")
    except ValueError as e:
        return _err("bad_response_mode", str(e), started_at=started, status_code=400)
    return _ok(payload, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_repair_candidates(key: str, file: str, limit: int = 20) -> dict:
    """Return clustered current topology findings with deterministic repair candidates.

    USE when:
      - A scene has many topology/wall warning defects.
      - You need the ranked accept/reject queue instead of manually inspecting
        duplicate dangling endpoint cards.

    The response is bounded and includes candidate ids for
    `get_scene_view_with_repair_candidate` and `apply_repair_candidate`.
    """
    started = time.time()
    status, body = await mcp_server._api_get(
        f"/datasets/{key}/{file}/plan-state/repair-candidates",
        params={"limit": int(limit)},
    )
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_view_with_repair_candidate(
    key: str,
    file: str,
    candidate_id: str,
    max_dim: int = 1600,
    clean: bool = True,
    style: str = "ink_compare",
    image_delivery: str = "auto",
) -> list[ImageContent | TextContent]:
    """Render current labels plus one proposed repair candidate.

    USE before accepting/rejecting a candidate from
    `get_scene_repair_candidates`. The overlay is visual evidence only; it does
    not mutate labels.
    """
    started = time.time()
    status, content, ctype = await mcp_server._api_get_bytes(
        f"/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/overlay",
        params={"max_dim": int(max_dim), "clean": bool(clean), "style": style},
    )
    if status >= 400:
        try:
            body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            body = {}
        return _wrap_text(_http_status_to_error(status, body, started))
    return _image_delivery_payload(
        content=content,
        ctype=ctype,
        metadata={
            "key": key,
            "file": file,
            "candidate_id": candidate_id,
            "image_format": "PNG",
            "max_dim": max_dim,
            "clean": clean,
            "style": style,
        },
        started_at=started,
        status_code=status,
        image_delivery=image_delivery,
    )


@mcp.tool()
async def apply_repair_candidate(
    key: str,
    file: str,
    candidate_id: str,
    expected_candidate_op: str | None = None,
    evidence_ids: list[str] | None = None,
    expected_version: str | None = None,
    note: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    subagent_id: str | None = None,
) -> dict:
    """Apply one precomputed deterministic repair candidate.

    USE only after visually inspecting the candidate overlay. No-edit
    classification candidates return `persisted=false`; geometry candidates
    persist labels through the same server validation path as normal label
    writes.
    """
    started = time.time()
    body = {
        "expected_candidate_op": expected_candidate_op,
        "evidence_ids": evidence_ids or [],
        "expected_version": expected_version,
        "note": note,
        "run_id": run_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
    }
    status, res = await mcp_server._api_post(
        f"/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/apply",
        body,
    )
    if status >= 400:
        return _http_status_to_error(status, res, started)
    return _ok(res.get("data") if isinstance(res, dict) else res, started_at=started, status_code=status)


@mcp.tool()
async def decide_repair_candidate(
    key: str,
    file: str,
    candidate_id: str,
    outcome: str,
    expected_candidate_op: str | None = None,
    evidence_ids: list[str] | None = None,
    expected_version: str | None = None,
    note: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    subagent_id: str | None = None,
) -> dict:
    """Record an accept/reject/manual decision for a repair candidate.

    USE after visually inspecting a candidate overlay when the correct outcome
    is not a geometry application. Allowed outcomes are accepted_applied,
    rejected_false_positive, rejected_intentional_opening,
    rejected_would_hurt_score, accepted_uncertain, and needs_manual_geometry.
    """
    started = time.time()
    body = {
        "outcome": outcome,
        "expected_candidate_op": expected_candidate_op,
        "evidence_ids": evidence_ids or [],
        "expected_version": expected_version,
        "note": note,
        "run_id": run_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
    }
    status, res = await mcp_server._api_post(
        f"/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/decision",
        body,
    )
    if status >= 400:
        return _http_status_to_error(status, res, started)
    return _ok(res.get("data") if isinstance(res, dict) else res, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_plan_quality_report(key: str, file: str) -> dict:
    """Return the compact current topology quality report for a scene plan.

    USE before handoff/export claims. This reports current findings, candidate
    decisions, superseded historical defects, accepted repairs, rejected
    candidates, and unresolved high-confidence warning clusters.
    """
    started = time.time()
    status, body = await mcp_server._api_get(f"/datasets/{key}/{file}/plan-state/quality-report")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_topology_snapshot(key: str, file: str) -> dict:
    """Return the deterministic non-binary topology regression snapshot.

    USE for handoff/CI-style quality checks. The snapshot contains topology
    counts, cluster count, candidate count/types, reviewed clusters, decision
    outcomes, and unresolved high-confidence warnings.
    """
    started = time.time()
    status, body = await mcp_server._api_get(f"/datasets/{key}/{file}/plan-state/topology-snapshot")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body.get("data") if isinstance(body, dict) else body, started_at=started, status_code=status)


@mcp.tool()
async def add_scene_plan_evidence(
    key: str,
    file: str,
    kind: str,
    summary: str,
    mode: str = "analysis",
    tool: str | None = None,
    params: dict | None = None,
    result: dict | None = None,
    image_url: str | None = None,
    task_ids: list[str] | None = None,
    observation_id: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    subagent_id: str | None = None,
    expected_version: str | None = None,
    response_mode: str = "compact",
) -> dict:
    """Add evidence to the structured scene plan.

    USE when:
      - Recording the observation, crop, score, or verification result
        that justifies a task/defect state change.
      - Preserving run/session provenance for post-run inspection. Pass
        run_id plus agent_id/subagent_id when the caller knows them.

    DON'T USE when:
      - You are trying to store large image payloads or full transcripts.
      Store compact observations and tool result summaries instead.
    """
    started = time.time()
    body = {
        "kind": kind,
        "summary": summary,
        "mode": mode,
        "tool": tool,
        "params": params or {},
        "result": result or {},
        "image_url": image_url,
        "task_ids": task_ids or [],
        "observation_id": observation_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
        "expected_version": expected_version,
    }
    status, res = await mcp_server._api_post(f"/datasets/{key}/{file}/plan-state/evidence", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    try:
        payload = _response_mode_payload(data, response_mode=response_mode, action="add_evidence")
    except ValueError as e:
        return _err("bad_response_mode", str(e), started_at=started, status_code=400)
    return _ok(payload, started_at=started, status_code=status)


@mcp.tool()
async def set_scene_plan_task_state(
    key: str,
    file: str,
    task_id: str,
    status: str,
    evidence_ids: list[str] | None = None,
    blocked_by: list[str] | None = None,
    gate_updates: list[dict] | None = None,
    note: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    subagent_id: str | None = None,
    expected_version: str | None = None,
    response_mode: str = "compact",
) -> dict:
    """Set one structured scene-plan task state.

    USE when:
      - A plan action's allowed tools require marking a task verified,
        accepted incomplete, blocked, rejected, or back to in progress.
      - You have evidence IDs and gate updates that justify the state.

    DON'T USE when:
      - You are trying to bypass scene work. `accepted_incomplete` is
        only an honest blocker marker and does NOT satisfy readiness or
        export for required tasks. Normal progress requires `verified`
        with passed gates plus evidence; required tasks cannot use waived
        gates as a completion shortcut.

    Valid statuses: `todo`, `in_progress`, `blocked`, `rejected`,
    `verified`, `accepted_incomplete`. Required scene-plan tasks must
    end as `verified` for `required_complete=true`.
    """
    started = time.time()
    body = {
        "status": status,
        "evidence_ids": evidence_ids or [],
        "blocked_by": blocked_by or [],
        "gate_updates": gate_updates or [],
        "note": note,
        "run_id": run_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
        "expected_version": expected_version,
    }
    patch_error = await mcp_server._api_patch(f"/datasets/{key}/{file}/plan-state/tasks/{task_id}", body, started)
    if patch_error is not None:
        return patch_error
    status_code, res = await mcp_server._api_get(f"/datasets/{key}/{file}/plan-state")
    if status_code >= 400:
        return _http_status_to_error(status_code, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    try:
        payload = _response_mode_payload(data, response_mode=response_mode, action="set_task_state")
    except ValueError as e:
        return _err("bad_response_mode", str(e), started_at=started, status_code=400)
    return _ok(payload, started_at=started, status_code=status_code)


@mcp.tool()
async def evaluate_scene_plan_gates(
    key: str,
    file: str,
    run_score_walls: bool = True,
    run_score_measurements: bool = True,
    run_topology_qa: bool = True,
    visual_evidence: bool = False,
    run_continuity_check: bool = False,
    expected_version: str | None = None,
    response_mode: str = "compact",
) -> dict:
    """Evaluate deterministic plan gates and update defects/tasks/status.

    USE when:
      - After edits and before declaring a scene action complete.

    DON'T USE when:
      - You have not yet placed or inspected the relevant labels. This is
        the verification half of the scene loop: it runs server-side QA
        and records resulting blockers in plan state.
    """
    started = time.time()
    body = {
        "run_score_walls": run_score_walls,
        "run_score_measurements": run_score_measurements,
        "run_topology_qa": run_topology_qa,
        "visual_evidence": visual_evidence,
        "run_continuity_check": run_continuity_check,
        "expected_version": expected_version,
    }
    status, res = await mcp_server._api_post(f"/datasets/{key}/{file}/plan-state/evaluate-gates", body)
    if status >= 400:
        return _http_status_to_error(status, res, started)
    data = res.get("data") if isinstance(res, dict) else res
    try:
        payload = _response_mode_payload(data, response_mode=response_mode, action="evaluate_gates")
    except ValueError as e:
        return _err("bad_response_mode", str(e), started_at=started, status_code=400)
    return _ok(payload, started_at=started, status_code=status)
