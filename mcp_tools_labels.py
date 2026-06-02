"""Labels MCP tools (extracted from mcp_server.py, H5).

Stable helpers imported by value; HTTP helpers, __file__, and inter-tool
calls go through `mcp_server.` so the test-harness patches still apply.
"""
from __future__ import annotations

import httpx
import time

import mcp_server
from mcp_server import (
    _VALID_LABEL_STATUS,
    _VALID_LEVELS,
    _VALID_ORIENTATIONS,
    _VALID_TAGS,
    _api_unreachable_error,
    _current_action_write_warning,
    _err,
    _http_status_to_error,
    _new_label_id,
    _ok,
    _read_labels,
    _wait_for_api,
    _write_labels,
    mcp,
)


@mcp.tool()
async def get_label(key: str, file: str, label_id: str) -> dict:
    """Full label object — geometry + attributes + relations + notes.

    USE when:
      - About to delete or update a label — confirm the id refers to
        what you think.

    Returns: `data` = the full Label per scene_labels.schema.json.
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_get(f"/labels/dataset/{key}/{file}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get(f"/labels/dataset/{key}/{file}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    target = next((l for l in (body.get("labels") or []) if l.get("id") == label_id), None)
    if target is None:
        return _err("label_not_found", f"no label {label_id!r} on {file!r}", started_at=started)
    return _ok(target, started_at=started, status_code=status)


@mcp.tool()
async def set_scene_tag(
    key: str,
    file: str,
    tag: str,
    idempotency_key: str | None = None,
) -> dict:
    """Set the scene discriminator tag for one scene.

    USE when:
      - The scene's tag is still 'nicht_klassifiziert' after extraction.
      - Earlier tagging was wrong and the human hasn't touched labels.

    DON'T USE when:
      - The scene has labels of types the new tag can't render — call
        `delete_label` for those first.

    Args:
      key: house key.
      file: scene filename.
      tag: one of 'grundriss', 'ansicht', 'schnitt', 'sonstiges',
           'nicht_klassifiziert'.
      idempotency_key: optional driver-supplied key.

    Returns: `data` = {file, scene_tag} from the labels-JSON update.

    Writes ONLY to data/dataset/<key>/labels/<file>.json `scene_tag` —
    that is the workflow predicate's source of truth. The manifest's
    separate `kind` field (floorplan/elevation/section/detail; set by
    extraction) is left alone; use the SPA's edit-attrs popover to
    change it when needed.
    """
    started = time.time()
    if tag not in _VALID_TAGS:
        return _err("schema_invalid", f"unknown tag {tag!r}",
                    hint=f"use one of {sorted(_VALID_TAGS)}", started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    payload["scene_tag"] = tag
    put_status, put_body = await mcp_server._api_put(f"/labels/dataset/{key}/{file}", payload)
    if put_status >= 400:
        return _http_status_to_error(put_status, put_body, started)
    return _ok({"file": file, "scene_tag": tag},
               started_at=started, status_code=put_status)


@mcp.tool()
async def set_scene_orientation(
    key: str,
    file: str,
    orientation: str | None,
    idempotency_key: str | None = None,
) -> dict:
    """Set scene_orientation on one scene's labels JSON.

    USE when:
      - The scene_tag is 'ansicht' or 'schnitt' and you can determine
        the cardinal direction.
      - Pass null to clear.

    Args:
      orientation: 'north' | 'south' | 'east' | 'west' | null
    """
    started = time.time()
    if orientation not in _VALID_ORIENTATIONS:
        return _err("schema_invalid", f"unknown orientation {orientation!r}",
                    hint="use 'north', 'south', 'east', 'west', or null", started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    payload["scene_orientation"] = orientation
    put_status, put_body = await mcp_server._api_put(f"/labels/dataset/{key}/{file}", payload)
    if put_status >= 400:
        return _http_status_to_error(put_status, put_body, started)
    return _ok({"file": file, "scene_orientation": orientation},
               started_at=started, status_code=put_status)


@mcp.tool()
async def set_scene_level(
    key: str,
    file: str,
    level: str | None,
    idempotency_key: str | None = None,
) -> dict:
    """Set scene_level on a Grundriss scene.

    USE when:
      - scene_tag is 'grundriss' — determine which floor.
      - Pass null to clear.

    Args:
      level: 'kg' | 'ug' | 'eg' | 'og' | 'dg' | 'spitzboden' | null
    """
    started = time.time()
    if level not in _VALID_LEVELS:
        return _err("schema_invalid", f"unknown level {level!r}",
                    hint=f"use one of {sorted({lv for lv in _VALID_LEVELS if lv})} or null",
                    started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    payload["scene_level"] = level
    put_status, put_body = await mcp_server._api_put(f"/labels/dataset/{key}/{file}", payload)
    if put_status >= 400:
        return _http_status_to_error(put_status, put_body, started)
    return _ok({"file": file, "scene_level": level},
               started_at=started, status_code=put_status)


@mcp.tool()
async def reset_scene_labels(
    key: str,
    file: str,
    idempotency_key: str | None = None,
) -> dict:
    """Reset ONE scene's labels and scene metadata, keeping the scene image.

    USE when:
      - You want to restart labeling for a single extracted scene.
      - A prior agent run produced bad labels and the extraction itself is OK.

    EFFECT:
      - Writes a clean labels skeleton for the scene.
      - Sets scene_tag='nicht_klassifiziert', clears scene_orientation/level.
      - Removes every saved label for that scene.
      - Rebuilds house_facts from scratch so stale calibration/extent facts
        from deleted labels do not leak into the next run.

    DON'T USE when:
      - You want to remove extracted scenes and return to PDF extraction;
        call `reset_house_dataset` instead.
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_delete(f"/labels/dataset/{key}/{file}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_delete(f"/labels/dataset/{key}/{file}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def reset_house_labeling(
    key: str,
    idempotency_key: str | None = None,
) -> dict:
    """Reset ALL labels for a house while keeping extracted scenes.

    USE when:
      - You want a fresh labeling run on the existing scene crops.
      - The extraction/cropping is good, but the annotations should be purged.

    EFFECT:
      - Replaces every scene labels JSON with an empty skeleton.
      - Clears scene tags/orientations/levels back to unclassified metadata.
      - Rebuilds house_facts from scratch so required phases become pending.
      - Keeps data/dataset/<key> images and manifest intact.

    DON'T USE when:
      - You need to re-extract scenes from the incoming PDF. Use
        `reset_house_dataset` for that stronger reset.
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_delete(f"/datasets/{key}/labels")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_delete(f"/datasets/{key}/labels")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def reset_house_dataset(
    key: str,
    idempotency_key: str | None = None,
) -> dict:
    """Destructive house reset: remove extracted scenes and labels.

    USE when:
      - The scene extraction/cropping itself is bad.
      - You want to return the incoming PDF bundle to the "ready to extract"
        state and start over from W0 extraction.

    EFFECT:
      - Deletes data/dataset/<key>/ entirely.
      - Resets the incoming PDF manifest's extracted_scenes list/state.
      - Keeps data/pdfs/incoming/<key>/ source PDFs.

    This is stronger than `reset_house_labeling` and cannot be undone.
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_delete(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_delete(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok({"key": key, "mode": "dataset_removed_keep_incoming_pdf"},
               started_at=started, status_code=status)


@mcp.tool()
async def upsert_label(
    key: str,
    file: str,
    label: dict,
    idempotency_key: str | None = None,
) -> dict:
    """Create or replace a label by id.

    USE when:
      - Adding a new label (omit `label.id` — server allocates one).
      - Replacing an existing label by its id.

    DON'T USE when:
      - You only want to change attributes — use `update_label_attrs`
        (avoids re-sending geometry; less error-prone).

    Args:
      key: house key.
      file: scene filename.
      label: a Label dict per scene_labels.schema.json. Required:
             `type`, `geometry`. The tool defaults `status='readable'`
             and `attributes={}` if absent.

             Scene-category palette is enforced by the API:
               grundriss: wall, floorplan_opening, dimensioned_distance,
                          dimension_number
               ansicht/schnitt: view_opening, component_line, height_mark,
                                dimensioned_distance, dimension_number
               sonstiges: all label types
             Example: `height_mark` on a `grundriss` is rejected.

             Geometry uses [x, y] ARRAYS, not {x, y} objects:
               wall:                 {start: [x,y], end: [x,y]}
               floorplan_opening:    {quad: [[x,y],[x,y],[x,y],[x,y]]}
               view_opening:         one of
                                       {top_edge: [[x,y],...], bottom_edge: [[x,y],...]}
                                       {circle: {center: [x,y], radius_px: N}}
                                       {polygon: [[x,y],...]}
               component_line:       {points: [[x,y],...]}
               height_mark:          {anchor: [x,y]}
               dimensioned_distance: {start: [x,y], end: [x,y]}
               dimension_number:     {anchor: [x,y]} XOR {bbox: [[x,y]*4]}
      idempotency_key: optional driver-supplied key.

    Returns: `data.label_id` = the (new or existing) label id.
    """
    started = time.time()
    if not isinstance(label, dict) or "type" not in label:
        return _err("schema_invalid", "label must be an object with at least 'type'",
                    hint="see bim-db://schema/scene_labels resource", started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    labels = payload.setdefault("labels", [])
    label_id = label.get("id") or _new_label_id()
    label["id"] = label_id
    # Default required schema fields the agent often forgets.
    label.setdefault("status", "readable")
    label.setdefault("attributes", {})
    existing_idx = next((i for i, l in enumerate(labels) if l.get("id") == label_id), None)
    if existing_idx is not None:
        labels[existing_idx] = label
        action = "replaced"
    else:
        labels.append(label)
        action = "created"
    result = await _write_labels(key, file, payload, started)
    if not result.get("ok"):
        return result
    result["data"]["label_id"] = label_id
    result["data"]["action"] = action
    warning = await _current_action_write_warning(key, file, str(label.get("type") or ""))
    if warning:
        result["data"]["action_scope_warning"] = warning
    if label.get("type") == "wall":
        try:
            status, body = await mcp_server._api_post(
                f"/datasets/{key}/{file}/wall-labels/anchoring-check",
                {"label": label},
            )
            if status < 400 and isinstance(body, dict):
                check = body.get("data") or {}
                result["data"].update({
                    "anchoring_status": check.get("anchoring_status") or check.get("status") or "unchecked",
                    "ink_overlap": check.get("ink_overlap"),
                    "recommended_tool": "upsert_wall_anchored",
                    "must_verify_before_downstream": bool(check.get("must_verify_before_downstream")),
                    "anchoring_check_region": check.get("region"),
                })
        except Exception:  # noqa: BLE001
            result["data"].update({
                "anchoring_status": "unchecked",
                "recommended_tool": "upsert_wall_anchored",
                "must_verify_before_downstream": True,
            })
    return result


@mcp.tool()
async def upsert_wall_anchored(
    key: str,
    file: str,
    candidate: dict,
    anchor: dict | None = None,
    status_if_unanchored: str = "reject",
    evidence_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Create or replace a floorplan wall after snapping/refining it to ink.

    USE instead of raw `upsert_label(type='wall')` when placing Grundriss
    walls. The tool treats `candidate.start/end` as a draft, refines to the
    measured wall band, checks local ink overlap, and persists a readable wall
    only when confidence + overlap pass.

    Args:
      candidate: {"start":[x,y], "end":[x,y], "thickness_mm":300, "id": optional}
      anchor: optional {"search_px":40, "min_confidence":0.82,
              "min_overlap":0.6, "snap_corners":true}
      status_if_unanchored: "reject" (default) or "uncertain". Uncertain
              persistence requires evidence_id so the dataset stays honest.
    """
    started = time.time()
    if not isinstance(candidate, dict):
        return _err("schema_invalid", "candidate must be an object",
                    started_at=started)
    body = {
        "candidate": candidate,
        "anchor": anchor or {},
        "status_if_unanchored": status_if_unanchored,
        "evidence_id": evidence_id,
    }
    try:
        status, resp = await mcp_server._api_post(f"/datasets/{key}/{file}/wall-labels/anchored", body)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, resp = await mcp_server._api_post(f"/datasets/{key}/{file}/wall-labels/anchored", body)
    if status >= 400:
        return _http_status_to_error(status, resp, started)
    data = (resp or {}).get("data") or resp
    warning = await _current_action_write_warning(key, file, "wall")
    if warning and isinstance(data, dict):
        data["action_scope_warning"] = warning
    return _ok(data, started_at=started, status_code=status)


@mcp.tool()
async def delete_label(
    key: str,
    file: str,
    label_id: str,
    idempotency_key: str | None = None,
) -> dict:
    """Delete a label by id.

    USE when:
      - The agent decided a label was wrong and wants a clean slate.
      - You're about to re-tag a scene and the existing labels would
        violate the new tag's tool palette.
    """
    started = time.time()
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    labels = payload.get("labels") or []
    before = len(labels)
    payload["labels"] = [l for l in labels if l.get("id") != label_id]
    if len(payload["labels"]) == before:
        return _err("label_not_found", f"no label {label_id!r} on {file!r}",
                    started_at=started)
    return await _write_labels(key, file, payload, started)


@mcp.tool()
async def update_label_attrs(
    key: str,
    file: str,
    label_id: str,
    attrs_patch: dict,
    idempotency_key: str | None = None,
) -> dict:
    """Partial update on a label's `attributes` dict.

    USE when:
      - Changing a `dimensioned_distance.attributes.value_mm` after
        re-reading the dim text.
      - Flipping `is_reference` after deciding a stroke is/isn't an
        anchor.
      - Tightening `attributes.opening_kind` from default 'window' to
        e.g. 'door'.

    Args:
      attrs_patch: dict of attributes to merge in. Existing attributes
                   not mentioned are preserved.
    """
    started = time.time()
    if not isinstance(attrs_patch, dict) or not attrs_patch:
        return _err("schema_invalid", "attrs_patch must be a non-empty dict",
                    started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    target = next((l for l in (payload.get("labels") or []) if l.get("id") == label_id), None)
    if target is None:
        return _err("label_not_found", f"no label {label_id!r} on {file!r}",
                    started_at=started)
    target.setdefault("attributes", {}).update(attrs_patch)
    return await _write_labels(key, file, payload, started)


@mcp.tool()
async def set_label_status(
    key: str,
    file: str,
    label_id: str,
    status: str,
    idempotency_key: str | None = None,
) -> dict:
    """Set the honesty axis on a label.

    USE when:
      - You labelled a dim but can't read the value confidently — set
        `status='uncertain'` so a human reviewer is alerted.
      - A label is for a feature that's missing in the drawing entirely
        — set `status='missing'`.

    Args:
      status: 'readable' | 'not_readable' | 'missing' | 'uncertain'
    """
    started = time.time()
    if status not in _VALID_LABEL_STATUS:
        return _err("schema_invalid", f"unknown status {status!r}",
                    hint=f"use one of {sorted(_VALID_LABEL_STATUS)}", started_at=started)
    payload, err = await _read_labels(key, file, started)
    if err is not None:
        return err
    target = next((l for l in (payload.get("labels") or []) if l.get("id") == label_id), None)
    if target is None:
        return _err("label_not_found", f"no label {label_id!r}", started_at=started)
    target["status"] = status
    return await _write_labels(key, file, payload, started)


