"""Discovery MCP tools (extracted from mcp_server.py, H5).

Stable helpers imported by value; HTTP helpers, __file__, and inter-tool
calls go through `mcp_server.` so the test-harness patches still apply.
"""
from __future__ import annotations

import httpx
import time

import mcp_server
from mcp_server import (
    _api_unreachable_error,
    _derive_workflow_state,
    _http_status_to_error,
    _load_facts_and_scene_meta,
    _ok,
    _recommended_scene_plan_action,
    _wait_for_api,
    mcp,
)


@mcp.tool()
async def list_houses() -> dict:
    """List every house in the corpus with a compact workflow summary.

    USE when:
      - The agent doesn't know which houses exist yet.
      - It needs to pick the next unlabeled house (`--next` flow).

    DON'T USE when:
      - You already have the key — call `get_house` instead for full
        detail.

    Returns: `data.houses` is a list of compact records:
      {key, intake_only, page_count, scenes_count, workflow_phase,
       exportable, has_labels}

    Example:
      list_houses() → {"ok": true, "data": {"houses": [...]}, ...}
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_get("/datasets")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get("/datasets")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    rows = []
    for h in body or []:
        drawings = h.get("drawings") or []
        labeled = sum(1 for d in drawings if d.get("labeled"))
        rows.append({
            "key": h.get("key"),
            "intake_only": bool(h.get("intake_only")),
            "page_count": h.get("intake_page_count") or h.get("page_count"),
            "scenes_count": len(drawings),
            "labeled_scenes": labeled,
            "has_labels": labeled > 0,
            "model": h.get("model"),
        })
    return _ok({"houses": rows}, started_at=started, status_code=status)


@mcp.tool()
async def get_house(key: str) -> dict:
    """Full dataset manifest for one house, with house_facts merged in.

    USE when:
      - You need the per-scene list with current labeled / label_count.
      - You're about to call `set_scene_tag` / `upsert_label` / etc.

    DON'T USE when:
      - You only need workflow status — call `get_workflow_state`.

    Args:
      key: house key, e.g. "house-22".

    Returns:
      `data` contains the dataset manifest plus a `house_facts` field
      (null if not yet populated).
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_get(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    facts_status, facts_body = await mcp_server._api_get(f"/datasets/{key}/house_facts")
    body["house_facts"] = facts_body if facts_status == 200 else None
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def get_workflow_state(key: str) -> dict:
    """Per-phase status (W0–W5) derived from on-disk facts.

    USE when:
      - At the start of a labeling run to see where the agent picks up.
      - After every phase to confirm the predicate flipped to `done`.
      - When deciding whether to call `export_house`.

    DON'T USE when:
      - You're just listing scenes — `get_house` gives that.

    Returns:
      `data` = {phase: {status: "done"|"in_progress"|"pending",
                        predicate_value: ...,
                        blockers: [...]},
                next_phase: "W4",
                exportable: bool,
                blockers: [...]}

    Implementation note (v0.1): workflow predicates live in the
    frontend's `ui/src/lib/workflow.ts`. This tool computes a
    server-side approximation from the dataset manifest + house_facts.
    The skill should still consult the SPA for ground truth when the
    agent's behavior diverges from expected. A future task moves the
    predicate set into a shared schema both consume.
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_get(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    facts, scene_meta_by_file = await _load_facts_and_scene_meta(key, body or {})
    state = _derive_workflow_state(body or {}, facts, scene_meta_by_file)
    next_tool = None
    if not state.get("exportable") and state.get("next_phase"):
        next_tool = {
            "name": "get_recommended_next_action",
            "args": {"key": key},
            "reason": f"phase {state['next_phase']} is the next to advance",
        }
    return _ok(state, next_tool=next_tool, started_at=started, status_code=status)


@mcp.tool()
async def get_recommended_next_action(key: str) -> dict:
    """Convenience wrapper: derives the next thing the agent should do
    from the workflow state, returning a tool call template.

    USE when:
      - You're starting an iteration loop and want a single source of
        truth for "what now?".

    DON'T USE when:
      - You're already mid-phase — re-call your own playbook step. This
        tool is for orientation, not constant lookup.

    Returns: `data` = {phase, suggested_tool, suggested_args, reason}
    or {done: true} when the house exports cleanly.
    """
    started = time.time()
    state_env = await get_workflow_state(key=key)
    if not state_env.get("ok"):
        return state_env
    state = state_env["data"]
    if state.get("exportable") and not state.get("blockers_total"):
        return _ok({"done": True, "reason": "all phases done; ready to export"}, started_at=started)
    phase = state.get("next_phase") or "W0"
    if phase == "Wgeo":
        groundfloor_only = bool((state["phases"].get("Wgeo") or {}).get("groundfloor_blockers"))
        plan_action = await _recommended_scene_plan_action(key, groundfloor_only=groundfloor_only)
        if plan_action:
            return _ok(plan_action, started_at=started)
    suggestions = {
        "W0": ("get_house", {"key": key}, "list scenes + their current tags; then set_scene_tag for each untagged"),
        "W1": ("get_house", {"key": key}, "pick an Ansicht with visible bezug + ridge; label height_marks; set_house_facts heights"),
        "W2": ("get_house", {"key": key}, "pick EG-Grundriss; add_reference_dim horizontal + vertical; set_house_facts extent + wall_thickness"),
        "W3": ("get_house", {"key": key}, "pick EG-Grundriss; identify north wall; set_house_facts orientation"),
        "W4": ("get_house", {"key": key}, "for each uncalibrated Ansicht/Schnitt: add_reference_dim h+v, recompute_homography"),
        "Wgeo": ("get_house_context_summary", {"key": key, "include_plan_status": True}, "create missing scene plans; then drive each geometry scene via get_scene_plan_next_action"),
        "W5": ("get_workflow_state", {"key": key}, "W5 is opt-in; if --with-detail, label view_openings + component_lines"),
    }
    tool_name, tool_args, reason = suggestions[phase]
    return _ok({
        "phase": phase,
        "suggested_tool": tool_name,
        "suggested_args": tool_args,
        "reason": reason,
        "blockers_in_phase": state["phases"][phase].get("blockers", []),
    }, started_at=started)


