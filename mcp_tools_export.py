"""Export MCP tools (extracted from mcp_server.py, H5).

Stable helpers imported by value; HTTP helpers, __file__, and inter-tool
calls go through `mcp_server.` so the test-harness patches still apply.
"""
from __future__ import annotations

import httpx
import time

import mcp_server
from mcp_server import (
    _REQUIRED_GEOMETRY,
    _api_unreachable_error,
    _derive_workflow_state,
    _err,
    _http_status_to_error,
    _load_facts_and_scene_meta,
    _ok,
    _wait_for_api,
    mcp,
)


@mcp.tool()
async def validate_export_readiness(key: str) -> dict:
    """Server-side gate: is the house HONESTLY complete enough to export?

    USE when:
      - Before calling export_house, to surface blockers without
        committing to the (expensive) export pipeline.
      - As the autonomous label driver's stop-condition. `ready:true`
        now means the substantive ground-truth phases exist — not just
        that the server's minimal sanity gate would accept the bytes.

    Why this is stricter than the export pipeline's own gate (issue #6):
    `export_house`'s sanity check (api/main._sanity_check_house) only
    requires ≥1 drawing + ≥1 labeled scene. Because every scene gets the
    `labeled` flag at W0 tagging time, a house with W0 tags + an assumed
    orientation and ZERO geometry (no heights, no extent, no calibration)
    used to pass `ready:true` — inviting an honest agent to export an
    empty dataset. `ready` now reflects honest completeness instead.

    Required phases for `ready`/`honest_complete`:
      - W0 (every scene tagged; grundriss carry a level)
      - W1 (heights: bezug_mm == 0 and first_mm set)
      - W2 (extent width+depth and wall_thickness.outer)
      - W3 (orientation set — assumed is fine, absent is not)
      - W4 (calibration per ansicht/schnitt) — only when the house has
        any ansicht/schnitt scenes; skipped for floorplan-only houses.
    W5 (detail) is optional and never blocks.

    Returns: `data` = {
      ready: bool,                # == honest_complete
      honest_complete: bool,      # all required phases done
      minimal_export_ok: bool,    # the permissive gate export_house enforces
      blockers: [str, …],         # missing required phases + their reasons
      phase_completeness: {Wn: {status, required, blockers}},
      required_phases: [str, …],
      scenes_total, labeled_scenes,
    }
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
    drawings = body.get("drawings") or []
    facts, scene_meta = await _load_facts_and_scene_meta(key, body or {})
    state = _derive_workflow_state(body or {}, facts, scene_meta)
    phases = state["phases"]

    # W4 only applies when the house actually has scenes that need
    # calibration. Floorplan-only houses have nothing to calibrate, so
    # requiring W4 there would make `ready` unreachable. This mirrors the
    # has_calibration_targets gate inside _derive_workflow_state.
    has_calibration_targets = any(
        scene_meta.get(d.get("file"), {}).get("scene_tag") in ("ansicht", "schnitt")
        for d in drawings
    )
    required = ["W0", "W1", "W2", "W3"]
    if has_calibration_targets:
        required.append("W4")
    # V5.1: require real geometry whenever the house has any
    # geometry-bearing scene (grundriss/schnitt/ansicht). This is the
    # honest-gate fix — facts present but zero polygons is NOT ready.
    has_geometry_targets = any(
        scene_meta.get(d.get("file"), {}).get("scene_tag") in _REQUIRED_GEOMETRY
        for d in drawings
    )
    if has_geometry_targets:
        required.append("Wgeo")

    phase_completeness = {
        p: {
            "status": phases[p]["status"],
            "required": p in required,
            "blockers": phases[p]["blockers"],
        }
        for p in ("W0", "W1", "W2", "W3", "W4", "Wgeo", "W5")
    }

    # Honest blockers: every required phase that isn't done, with its own
    # predicate reasons spelled out so callers see the missing geometry.
    honest_blockers: list[str] = []
    for p in required:
        if phases[p]["status"] != "done":
            reason = "; ".join(phases[p]["blockers"]) or "incomplete"
            honest_blockers.append(f"{p} incomplete: {reason}")

    # The permissive gate the export pipeline actually enforces. Surfaced
    # so callers understand a dishonest export would still be ACCEPTED
    # (and so they don't gate on it).
    minimal_blockers: list[str] = []
    if not drawings:
        minimal_blockers.append("house has zero drawings")
    elif not any(d.get("labeled") for d in drawings):
        minimal_blockers.append("no annotated scenes")

    all_blockers = list(dict.fromkeys(honest_blockers + minimal_blockers))
    honest_complete = not all_blockers

    # Issue #27: surface scenes whose W4 calibration rests on the
    # single-ref isotropic (square-pixel) assumption — they count as
    # calibrated, but an honest export should record the assumption.
    assumed_isotropic_scenes = phases["W4"].get("assumed_isotropic_scenes") or []
    approximate_calibrations = phases["W4"].get("approximate_calibrations") or []
    transferred_calibrations = phases["W4"].get("transferred_calibrations") or []

    return _ok({
        "ready": honest_complete,
        "honest_complete": honest_complete,
        "minimal_export_ok": not minimal_blockers,
        "blockers": all_blockers,
        "phase_completeness": phase_completeness,
        "required_phases": required,
        "scenes_total": len(drawings),
        "labeled_scenes": sum(1 for d in drawings if d.get("labeled")),
        "calibration_assumptions": {
            "single_ref_assumed_isotropic": assumed_isotropic_scenes,
            "approximate_calibrations": approximate_calibrations,
            "transferred_calibrations": transferred_calibrations,
        },
    }, started_at=started, status_code=status)


@mcp.tool()
async def export_house(
    key: str,
    force: bool = False,
    idempotency_key: str | None = None,
) -> dict:
    """Render the Set A / Set B export for one house.

    USE when:
      - Workflow is complete (all required phases done, ≥1 labeled
        scene). This is the "done" signal of an agent run.

    DON'T USE when:
      - `validate_export_readiness` returns ready=false — fix the
        blockers first.

    Args:
      key: house key.
      force: if True, bypass the sanity gate. Default false.
    """
    started = time.time()
    if not force:
        readiness = await validate_export_readiness(key=key)
        if not readiness.get("ok"):
            return readiness
        data = readiness.get("data") or {}
        if not data.get("ready"):
            return _err(
                "export_blocked",
                "honest readiness gate blocked the export",
                hint="fix error.details.blockers or call export_house(force=true) only for an explicit debug export",
                retry=True,
                details={"blockers": data.get("blockers") or []},
                started_at=started,
            )
    try:
        status, body = await mcp_server._api_post(
            f"/exports/{key}",
            params={"force": "true" if force else "false"},
        )
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_post(
            f"/exports/{key}",
            params={"force": "true" if force else "false"},
        )
    if status == 409:
        detail = body.get("detail") if isinstance(body, dict) else body
        anomalies = (detail or {}).get("anomalies") if isinstance(detail, dict) else None
        return _err("export_blocked",
                    "sanity gate blocked the export",
                    hint="see error.details.blockers; pass force=true to bypass",
                    retry=True,
                    details={"blockers": anomalies or []},
                    started_at=started, status_code=status)
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)
