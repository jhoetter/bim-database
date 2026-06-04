"""Facts MCP tools (extracted from mcp_server.py, H5).

Stable helpers imported by value; HTTP helpers, __file__, and inter-tool
calls go through `mcp_server.` so the test-harness patches still apply.
"""
from __future__ import annotations

import httpx
import os
import time

import mcp_server
from mcp_server import (
    _api_unreachable_error,
    _deep_merge,
    _err,
    _http_status_to_error,
    _ok,
    _wait_for_api,
    mcp,
)


@mcp.tool()
async def get_house_facts(key: str) -> dict:
    """Full HouseFacts for a house — extent, heights, wall_thickness,
    orientation, calibration_per_scene, scene_metadata, workflow pointer.

    USE when:
      - Reading the current phase predicates before deciding the next
        write. Cheap (single GET).
      - Verifying a `set_house_facts` patch landed.

    DON'T USE when:
      - You only need to know which phase is next — `get_workflow_state`
        is more targeted.

    Args:
      key: house key.

    Returns: full HouseFacts dict, or `data: null` if no
    `data/dataset/<key>/house_facts.json` exists yet (a brand-new house
    surfaces as null until the first `set_house_facts` call).
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_get(f"/datasets/{key}/house_facts")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get(f"/datasets/{key}/house_facts")
    if status == 404:
        return _ok(None, started_at=started, status_code=status)
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def set_house_facts(
    key: str,
    patch: dict,
    idempotency_key: str | None = None,
) -> dict:
    """Deep-merge patch into HouseFacts (server-side replace by default;
    this tool reads-merges-writes to give patch semantics on top).

    USE when:
      - W1: set `heights = {bezug_mm, first_mm}`.
      - W2: set `extent = {width_mm, depth_mm}`, `wall_thickness = {outer_mm}`.
      - W3: set `orientation = {north_edge_label_id} or {north_angle_deg}`.
      - W4: the per-scene `calibration_per_scene[file]` is auto-populated
        by `add_reference_dim` + `recompute_homography`; do not set it
        manually.

    Args:
      patch: partial HouseFacts. Top-level keys merge (other keys
             preserved); nested objects deep-merge one level. Lists are
             replaced atomically.
    """
    started = time.time()
    if not isinstance(patch, dict) or not patch:
        return _err("schema_invalid", "patch must be a non-empty dict",
                    started_at=started)
    # Read current
    cur_status, current = await mcp_server._api_get(f"/datasets/{key}/house_facts")
    if cur_status == 404:
        current = {"schema_version": "1.0"}
    elif cur_status >= 400:
        return _http_status_to_error(cur_status, current, started)

    # G4-3 (followups-tracker): force assumed:true when north_angle_deg
    # is set without a north_edge_label_id. Catches the agent's "I
    # guessed but said I knew" failure mode (B3).
    warnings: list[str] = []
    auto_corrections: list[str] = []
    orient_patch = patch.get("orientation") if isinstance(patch.get("orientation"), dict) else None
    if orient_patch is not None:
        existing_orient = current.get("orientation") if isinstance(current.get("orientation"), dict) else {}
        # Merge patch + existing to see the post-merge state.
        merged_orient = {**(existing_orient or {}), **orient_patch}
        has_angle = isinstance(merged_orient.get("north_angle_deg"), (int, float))
        has_edge = bool(merged_orient.get("north_edge_label_id"))
        if has_angle and not has_edge:
            if merged_orient.get("assumed") is not True:
                # Inject the correction into the patch we're about to apply.
                if not isinstance(patch.get("orientation"), dict):
                    patch["orientation"] = {}
                patch["orientation"]["assumed"] = True
                auto_corrections.append(
                    "orientation.assumed forced to true (north_angle_deg set "
                    "without north_edge_label_id — see §G4-3)"
                )

    # G4-4 (followups-tracker): warn when heights.{bezug_mm, first_mm}
    # is set without matching height_mark labels. Block in
    # HOUSE_FACTS_STRICT mode (per §8 decision 2).
    heights_patch = patch.get("heights") if isinstance(patch.get("heights"), dict) else None
    if heights_patch is not None and any(k in heights_patch for k in ("bezug_mm", "first_mm")):
        # Check whether any scene's labels contain a matching height_mark.
        try:
            ds_status, ds_body = await mcp_server._api_get(f"/datasets/{key}")
            scenes = (ds_body or {}).get("drawings") or []
            need_bezug = "bezug_mm" in heights_patch
            need_first = "first_mm" in heights_patch
            saw_bezug_label = False
            saw_first_label = False
            for d in scenes:
                f = d.get("file")
                if not f:
                    continue
                lbl_status, lbl = await mcp_server._api_get(f"/labels/dataset/{key}/{f}")
                if lbl_status != 200 or not isinstance(lbl, dict):
                    continue
                for lab in (lbl.get("labels") or []):
                    if lab.get("type") != "height_mark":
                        continue
                    attrs = lab.get("attributes") or {}
                    if need_bezug and attrs.get("value_mm") == 0:
                        saw_bezug_label = True
                    if need_first and attrs.get("datum") == "first":
                        saw_first_label = True
                if (not need_bezug or saw_bezug_label) and (not need_first or saw_first_label):
                    break
            missing = []
            if need_bezug and not saw_bezug_label:
                missing.append("bezug_mm (need a height_mark with value_mm == 0)")
            if need_first and not saw_first_label:
                missing.append("first_mm (need a height_mark with datum == 'first')")
            if missing:
                strict = os.environ.get("HOUSE_FACTS_STRICT", "0").strip() not in ("", "0", "false")
                msg = "heights set without matching height_mark labels: " + "; ".join(missing)
                if strict:
                    return _err(
                        "heights_without_labels",
                        msg,
                        hint=(
                            "drop the height_mark labels first (via upsert_label "
                            "or follow the W1-height-anchor MCP prompt). "
                            "HOUSE_FACTS_STRICT=1 blocks this write."
                        ),
                        retry=False,
                        started_at=started,
                    )
                else:
                    warnings.append(msg + " (would block in strict mode)")
        except Exception:  # noqa: BLE001
            # Lookup failure is non-fatal — the heights write itself still goes through.
            pass

    merged = _deep_merge(current or {}, patch)
    merged.setdefault("schema_version", "1.0")
    put_status, put_body = await mcp_server._api_put(f"/datasets/{key}/house_facts", merged)
    if put_status >= 400:
        return _http_status_to_error(put_status, put_body, started)
    envelope = _ok(merged, started_at=started, status_code=put_status)
    if warnings:
        envelope["_meta"]["warnings"] = warnings
    if auto_corrections:
        envelope["_meta"]["auto_corrections"] = auto_corrections
    return envelope


@mcp.tool()
async def record_transferred_calibration(
    key: str,
    file: str,
    source_scene: str,
    transfer_kind: str,
    reason: str,
    confidence: str = "medium",
    source_fact_ids: list[str] | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Record an honest transferred calibration for an Ansicht/Schnitt.

    USE when:
      - The current scene has no readable local reference dimensions.
      - A scale/datum can be transferred from a calibrated section/elevation
        or building-global facts without fabricating a local dimension.

    DON'T USE when:
      - A local horizontal/vertical reference dimension is readable. Prefer
        `add_reference_dim` + `recompute_homography` for measured calibration.

    Args:
      file: target scene receiving the transferred calibration.
      source_scene: calibrated scene or best-source scene the transfer came from.
      transfer_kind: short enum-like string, e.g. `section_scale`,
        `building_global_datum`, or `matched_facade_extent`.
      reason: concise human-readable reason local calibration is unavailable
        and why the transfer is acceptable.
      confidence: low | medium | high.
      source_fact_ids: optional building-global fact names/ids used.

    Returns: updated calibration_per_scene[file] entry.
    """
    started = time.time()
    if not file:
        return _err("schema_invalid", "file is required", started_at=started)
    if not source_scene:
        return _err("missing_provenance", "source_scene is required", started_at=started)
    if not reason:
        return _err("missing_reason", "reason is required", started_at=started)
    if confidence not in {"low", "medium", "high"}:
        return _err("bad_confidence", "confidence must be one of low, medium, high", started_at=started)
    if transfer_kind not in {
        "section_scale",
        "building_global_datum",
        "matched_facade_extent",
        "matched_storey_height",
        "manual_review_transfer",
    }:
        return _err(
            "bad_transfer_kind",
            "transfer_kind must be one of section_scale, building_global_datum, "
            "matched_facade_extent, matched_storey_height, manual_review_transfer",
            started_at=started,
        )
    cur_status, current = await mcp_server._api_get(f"/datasets/{key}/house_facts")
    current_facts = current if cur_status == 200 and isinstance(current, dict) else {}
    calibration_per_scene = current_facts.get("calibration_per_scene") if isinstance(current_facts.get("calibration_per_scene"), dict) else {}
    source_calibration = calibration_per_scene.get(source_scene) if isinstance(calibration_per_scene.get(source_scene), dict) else {}
    source_has_direct_scale = bool(
        source_calibration
        and source_calibration.get("status") != "transferred"
        and (
            source_calibration.get("px_per_mm") is not None
            or source_calibration.get("status") == "ok"
            or source_calibration.get("computed_from") not in {None, "transferred"}
        )
    )
    building_global = current_facts.get("building_global") if isinstance(current_facts.get("building_global"), dict) else {}
    building_facts = building_global.get("facts") if isinstance(building_global.get("facts"), dict) else {}
    missing_source_facts = [
        fact_id for fact_id in (source_fact_ids or [])
        if fact_id not in building_facts
    ]
    has_source_facts = bool(source_fact_ids) and not missing_source_facts
    if missing_source_facts:
        return _err(
            "missing_provenance",
            "source_fact_ids are not present in building_global.facts: " + ", ".join(missing_source_facts),
            hint="record the building-global facts with set_building_global_fact first, or cite a directly calibrated source_scene",
            started_at=started,
        )
    if not source_has_direct_scale and not has_source_facts:
        return _err(
            "missing_provenance",
            "transferred calibration requires a directly calibrated source_scene or existing source_fact_ids",
            hint="run add_reference_dim/recompute_homography on the source scene, or record building-global facts before transferring scale",
            started_at=started,
        )
    entry = {
        "status": "transferred",
        "computed_from": "transferred",
        "source_scene": source_scene,
        "transfer_kind": transfer_kind,
        "confidence": confidence,
        "reason": reason,
        "review_required": True,
        "source_fact_ids": source_fact_ids or [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    res = await set_house_facts(
        key=key,
        patch={"calibration_per_scene": {file: entry}},
        idempotency_key=idempotency_key,
    )
    if not res.get("ok"):
        return res
    calibration = ((res.get("data") or {}).get("calibration_per_scene") or {}).get(file)
    return _ok(
        {"file": file, "calibration": calibration or entry},
        started_at=started,
        status_code=200,
    )


@mcp.tool()
async def record_source_unreadable_calibration(
    key: str,
    file: str,
    reason: str,
    evidence_ids: list[str] | None = None,
    review_required: bool = True,
    idempotency_key: str | None = None,
) -> dict:
    """Record that an Ansicht/Schnitt cannot be locally calibrated from source.

    USE when:
      - You inspected the local dimension/datum sources for a calibration scene.
      - The source is genuinely unreadable or absent, and no honest transferred
        calibration is available yet.

    DON'T USE when:
      - A local reference dimension is readable. Use `add_reference_dim`.
      - A defensible transfer exists. Use `record_transferred_calibration`.

    Returns: updated calibration_per_scene[file] entry with an explicit W4
    source-unreadable blocker.
    """
    started = time.time()
    if not file:
        return _err("schema_invalid", "file is required", started_at=started)
    if not reason:
        return _err("missing_reason", "reason is required", started_at=started)
    entry = {
        "status": "unavailable_source_unreadable",
        "computed_from": "source_unreadable",
        "reason": reason,
        "evidence_ids": evidence_ids or [],
        "review_required": bool(review_required),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    res = await set_house_facts(
        key=key,
        patch={"calibration_per_scene": {file: entry}},
        idempotency_key=idempotency_key,
    )
    if not res.get("ok"):
        return res
    calibration = ((res.get("data") or {}).get("calibration_per_scene") or {}).get(file)
    return _ok(
        {"file": file, "calibration": calibration or entry},
        started_at=started,
        status_code=200,
    )


@mcp.tool()
async def set_building_global_fact(
    key: str,
    fact: str,
    value: float,
    source_scene: str,
    source_label_id: str | None = None,
    confidence: str = "medium",
    unit: str = "mm",
    notes: str | None = None,
) -> dict:
    """Record a BUILDING-GLOBAL fact with provenance (issue #8).

    Höhenkoten (FH/TH/DG/EG/UG/Bezug), the müNN datum, roof pitch and
    Kniestock are properties of the *building*, not of one view — identical
    on every facade. Read each one ONCE from its best source (usually the
    Schnitt) and record it here; it is then available on every scene of
    the house. Each value stores which scene + label it came from and a
    confidence, so the cross-scene propagation is auditable.

    USE when:
      - You read a height/datum/roof value that holds for the whole
        building (not a facade-specific dimension). Cite the scene + the
        label it came from.

    DON'T USE when:
      - The value is facade-specific (a wall width on one Ansicht) — that
        belongs in the per-scene labels / extent, not here.

    Args:
      fact:            one of the recognized names — UG_mm, EG_mm, OG_mm,
                       DG_mm, TH_mm, FH_mm (relative to EG ±0.00),
                       EG_munn_mm (müNN datum), bezug_mm, first_mm,
                       roof_pitch_deg, kniestock_mm, ridge_munn_mm.
      value:           numeric value in `unit`.
      source_scene:    the scene file the value was read from (required —
                       provenance is the point).
      source_label_id: the label it was read from, when there is one.
      confidence:      low | medium | high.
      unit:            mm (default) or deg for roof_pitch_deg.
      notes:           optional free text.

    Returns: `data` = {fact, entry, building_global_facts}. Call
    `get_building_global_facts` to see the propagated + derived view.
    """
    started = time.time()
    from api.building_facts import (
        CONFIDENCE_LEVELS, KNOWN_FACTS, SCHEMA, make_fact,
    )
    if not source_scene:
        return _err("missing_provenance",
                    "source_scene is required — building-global facts must cite where they were read",
                    started_at=started)
    if fact not in KNOWN_FACTS:
        return _err("unknown_fact", f"{fact!r} is not a recognized building-global fact",
                    hint=f"known facts: {sorted(KNOWN_FACTS)}", started_at=started)
    if confidence not in CONFIDENCE_LEVELS:
        return _err("bad_confidence", f"confidence must be one of {sorted(CONFIDENCE_LEVELS)}",
                    started_at=started)
    if not isinstance(value, (int, float)):
        return _err("bad_value", "value must be numeric", started_at=started)
    try:
        entry = make_fact(
            float(value), source_scene=source_scene, source_label_id=source_label_id,
            confidence=confidence, unit=unit, notes=notes,
        )
    except ValueError as e:
        return _err("bad_fact", str(e), started_at=started)
    cur_status, current = await mcp_server._api_get(f"/datasets/{key}/house_facts")
    current_facts = current if cur_status == 200 and isinstance(current, dict) else {}
    existing = (
        ((current_facts.get("building_global") or {}).get("facts") or {}).get(fact)
        if isinstance(current_facts.get("building_global"), dict)
        else None
    )
    if (
        isinstance(existing, dict)
        and isinstance(existing.get("value"), (int, float))
        and abs(float(existing["value"]) - float(value)) > 100
    ):
        previous_values = []
        if isinstance(existing.get("previous_values"), list):
            previous_values.extend(v for v in existing["previous_values"] if isinstance(v, dict))
        previous_values.append({
            "value": existing.get("value"),
            "unit": existing.get("unit"),
            "source": existing.get("source"),
            "confidence": existing.get("confidence"),
            "provenance_quality": existing.get("provenance_quality") or "direct_read",
            "notes": existing.get("notes"),
        })
        entry["previous_values"] = previous_values[-5:]
        entry["provenance_quality"] = "conflicting"
        entry["review_required"] = True
        entry["conflict_note"] = (
            f"Existing {fact} value {existing.get('value')} {existing.get('unit') or 'mm'} "
            f"differs from new value {value} {unit}."
        )
    patch = {"building_global": {"schema": SCHEMA, "facts": {fact: entry}}}
    res = await set_house_facts(key=key, patch=patch)
    if not res.get("ok"):
        return res
    bg = ((res.get("data") or {}).get("building_global") or {})
    return _ok(
        {"fact": fact, "entry": entry, "building_global_facts": bg.get("facts", {})},
        started_at=started, status_code=200,
    )


@mcp.tool()
async def get_building_global_facts(key: str) -> dict:
    """Read the building-global facts tier + deterministic derivations.

    USE when:
      - At the start of labeling any Ansicht/Schnitt: pull the shared
        heights/datum/roof so you don't re-read what's already known.
      - Before W1/W4 to see which building-wide anchors exist and which
        derived values follow from them.

    Returns: `data` = {
      facts:        stored values, each with {value, unit, confidence,
                    source:{scene,label_id}, provenance_quality,
                    review_required, conflicts},
      derived:      deterministically computed facts (math, not OCR), each
                    flagged derived:true + needs_cross_check:true — e.g.
                    <X>_munn_mm = EG_munn_mm + <X>_mm; storey heights from
                    level deltas; roof rise from pitch (+ extent depth),
      fact_ledger:  conflict/review summary for cross-scene consumers,
      propagation:  {applies_to_scenes:[...]} — these hold on every scene.
    }
    """
    started = time.time()
    from api.building_facts import build_global_view
    try:
        f_status, facts = await mcp_server._api_get(f"/datasets/{key}/house_facts")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        f_status, facts = await mcp_server._api_get(f"/datasets/{key}/house_facts")
    if f_status == 404:
        facts = {}
    elif f_status >= 400:
        return _http_status_to_error(f_status, facts, started)
    ds_status, ds = await mcp_server._api_get(f"/datasets/{key}")
    if ds_status >= 400:
        return _http_status_to_error(ds_status, ds, started)
    scene_files = [d.get("file") for d in ((ds or {}).get("drawings") or []) if d.get("file")]
    view = build_global_view(
        (facts or {}).get("building_global"), scene_files,
        extent=(facts or {}).get("extent"),
    )
    return _ok(view, started_at=started, status_code=200)


@mcp.tool()
async def resolve_fact_conflict(
    key: str,
    conflict_id: str,
    rationale: str,
    resolution: str = "adjudicated",
    chosen_fact: str | None = None,
    chosen_value: float | None = None,
) -> dict:
    """Adjudicate a building-global fact conflict so it stops gating gold.

    Conflicts (from `get_building_global_facts.fact_ledger.conflicts`) are
    surfaced, not auto-resolved — multiple divergent readings of one fact need a
    human/agent decision. This records that decision: the conflict moves out of
    `review_required` into `fact_ledger.resolved_conflicts` (still visible +
    auditable) and no longer down-tiers its fact.

    USE when:
      - A surfaced conflict is a FALSE positive or you have decided which
        reading is correct, with a reason (cite the evidence/scene).

    DON'T USE to silence a real disagreement you haven't actually checked — the
    rationale is recorded and the prior values stay in the ledger.

    Args:
      conflict_id:  the `id` from the ledger (e.g. a same-fact conflict id).
      rationale:    REQUIRED — why this is resolved / which reading wins and
                    on what evidence.
      resolution:   short tag: adjudicated | false_positive | chose_value.
      chosen_fact/chosen_value: optionally correct the fact at the same time
                    (records the winning value; cite it in the rationale).

    Returns: `data` = the refreshed building-global view (the conflict now under
    `fact_ledger.resolved_conflicts`).
    """
    import datetime as _dt
    started = time.time()
    if not conflict_id:
        return _err("schema_invalid", "conflict_id is required", started_at=started)
    if not rationale or not str(rationale).strip():
        return _err("schema_invalid",
                    "rationale is required — record why the conflict is resolved",
                    started_at=started)
    record = {
        "resolution": resolution,
        "rationale": str(rationale).strip(),
        "resolved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    if chosen_fact is not None:
        record["chosen_fact"] = chosen_fact
    if chosen_value is not None:
        record["chosen_value"] = float(chosen_value)
    patch = {"building_global": {"resolved_conflicts": {conflict_id: record}}}
    res = await set_house_facts(key=key, patch=patch)
    if not res.get("ok"):
        return res
    # Optionally correct the winning fact value in the same step.
    if chosen_fact is not None and chosen_value is not None:
        upd = await set_building_global_fact(
            key=key, fact=chosen_fact, value=float(chosen_value),
            source_scene="conflict_resolution", confidence="high",
            notes=f"resolved {conflict_id}: {rationale}"[:240],
        )
        if not upd.get("ok"):
            return upd
    return await get_building_global_facts(key=key)
