"""Audit MCP tools (extracted from mcp_server.py, H5).

Tool definitions moved out of the mcp_server.py god file. They register on the
shared `mcp` instance imported from mcp_server; monkeypatched HTTP helpers
(_api_*) are called as `mcp_server._api_*` so the test harness's
`mcp_server._http` / `_api_get` patches still apply. Stable helpers are
imported by value.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import mcp_server
from mcp_server import (
    _err,
    _ok,
    mcp,
)


def _bbox_from_label(label: dict) -> list[float] | None:
    geom = label.get("geometry") or {}
    pts = []
    if label.get("type") == "wall":
        for key in ("start", "end"):
            p = geom.get(key)
            if isinstance(p, list) and len(p) >= 2:
                pts.append(p)
    elif label.get("type") == "floorplan_opening":
        pts.extend(p for p in (geom.get("quad") or []) if isinstance(p, list) and len(p) >= 2)
    elif label.get("type") in {"dimensioned_distance", "component_line"}:
        if isinstance(geom.get("points"), list):
            pts.extend(p for p in geom.get("points") if isinstance(p, list) and len(p) >= 2)
        for key in ("start", "end"):
            p = geom.get(key)
            if isinstance(p, list) and len(p) >= 2:
                pts.append(p)
    elif label.get("type") in {"height_mark", "dimension_number"}:
        p = geom.get("anchor")
        if isinstance(p, list) and len(p) >= 2:
            pts.append(p)
        bbox = geom.get("bbox")
        if isinstance(bbox, list):
            pts.extend(p for p in bbox if isinstance(p, list) and len(p) >= 2)
    if not pts:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


@mcp.tool()
async def list_anomalies(key: str) -> dict:
    """List validator-flagged issues for a house — everything blocking
    a clean export plus per-phase predicate failures, server-side
    derivation warnings, and any assumed/uncertain rows the agent
    or human flagged.

    USE when:
      - Triaging a failed `export_house`: which blockers must be cleared?
      - Pre-flight before committing a labeling pass: how clean is the
        house?
      - Looking for the agent's "I guessed" markers (assumed: true on
        orientation, status: uncertain on labels) before exporting.

    DON'T USE when:
      - The agent already knows the current phase's blockers from
        `get_workflow_state`; this tool aggregates across all phases.

    Returns: `data.anomalies = [{phase, kind, message, severity}]`
    where severity ∈ {"blocker", "warning", "info"}.

    Augmented per agentic-labeling-followups-tracker §G5-2 to include
    server-side derivation warnings (G1) + assumed-orientation rows
    (G4-3) + uncertain labels (B6).
    """
    started = time.time()
    wf = await mcp_server.get_workflow_state(key=key)
    if not wf.get("ok"):
        return wf
    state = wf["data"]
    anomalies: list[dict] = []
    for phase, ph in state["phases"].items():
        for b in ph.get("blockers", []):
            anomalies.append({
                "phase": phase, "kind": "phase_blocker",
                "message": b, "severity": "blocker",
            })
    if not state.get("exportable"):
        anomalies.append({
            "phase": "export", "kind": "export_blocker",
            "message": "no labeled scenes yet", "severity": "blocker",
        })

    # G5-2: derivation warnings from fact_derivation.recompute_…
    # (HOUSE_FACTS_STRICT mode drops fields, surfaces them here).
    facts_env = await mcp_server.get_house_facts(key=key)
    facts = (facts_env or {}).get("data") or {}
    for w in facts.get("_derivation_warnings", []) or []:
        anomalies.append({
            "phase": "facts", "kind": "derivation_warning",
            "message": w, "severity": "warning",
        })

    # G5-2: assumed orientation surfaces here so reviewers prioritize.
    orient = facts.get("orientation") or {}
    if orient.get("assumed") is True:
        msg = "orientation is assumed (no compass mark on drawing)"
        if isinstance(orient.get("north_angle_deg"), (int, float)):
            msg += f" — north_angle_deg={orient['north_angle_deg']}"
        anomalies.append({
            "phase": "W3", "kind": "assumed_orientation",
            "message": msg, "severity": "warning",
        })

    # G5-2 + H3: per-scene anomalies — uncertain labels + missing
    # orientation on ansicht/schnitt.
    try:
        ds_status, ds_body = await mcp_server._api_get(f"/datasets/{key}")
        for d in (ds_body or {}).get("drawings") or []:
            f = d.get("file")
            if not f:
                continue
            for warning in d.get("crop_warnings") or []:
                anomalies.append({
                    "phase": "W0",
                    "kind": warning.get("kind") or "crop_warning",
                    "message": f"{f}: {warning.get('message') or 'crop warning'}",
                    "severity": warning.get("severity") or "warning",
                    "details": {"file": f, **warning},
                })
            lbl_status, lbl = await mcp_server._api_get(f"/labels/dataset/{key}/{f}")
            if lbl_status != 200 or not isinstance(lbl, dict):
                continue
            image_size = None
            try:
                from PIL import Image as PILImage
                img_path = Path(mcp_server.__file__).parent / "data" / "dataset" / key / f
                with PILImage.open(img_path) as img:
                    image_size = (int(img.width), int(img.height))
            except Exception:  # noqa: BLE001
                image_size = None
            uncertain = sum(
                1 for lab in (lbl.get("labels") or [])
                if lab.get("status") == "uncertain"
            )
            if uncertain:
                anomalies.append({
                    "phase": "labels", "kind": "uncertain_labels",
                    "message": f"{f}: {uncertain} label(s) marked uncertain",
                    "severity": "info",
                    "details": {"file": f, "count": uncertain},
                })
            for lab in lbl.get("labels") or []:
                attrs = lab.get("attributes") or {}
                if attrs.get("mass_id") and lab.get("status") == "uncertain":
                    anomalies.append({
                        "phase": "labels",
                        "kind": "uncertain_mass_edge",
                        "message": f"{f}: mass edge {lab.get('id')} is uncertain"
                                   + (f" (confidence={attrs.get('edge_confidence')})" if attrs.get("edge_confidence") is not None else ""),
                        "severity": "warning",
                        "details": {"file": f, "label_id": lab.get("id"), "mass_id": attrs.get("mass_id"), "edge_confidence": attrs.get("edge_confidence")},
                    })
                if image_size is not None:
                    bbox = _bbox_from_label(lab)
                    if bbox and (bbox[0] < 0 or bbox[1] < 0 or bbox[2] > image_size[0] or bbox[3] > image_size[1]):
                        anomalies.append({
                            "phase": "labels",
                            "kind": "label_out_of_bounds",
                            "message": f"{f}: label {lab.get('id')} extends outside scene bounds",
                            "severity": "blocker",
                            "details": {"file": f, "label_id": lab.get("id"), "bbox_xyxy": bbox, "image_size_px": list(image_size)},
                        })
            plan_status, plan_body = await mcp_server._api_get(f"/datasets/{key}/{f}/plan-state")
            if plan_status == 200 and isinstance(plan_body, dict):
                plan_state = ((plan_body.get("data") or {}).get("state") or {})
                for ev in plan_state.get("evidence") or []:
                    if ev.get("kind") != "semantic_ink_region":
                        continue
                    result = ev.get("result") or {}
                    bbox = result.get("bbox_xyxy")
                    if not (isinstance(bbox, list) and len(bbox) >= 4):
                        anomalies.append({
                            "phase": "plans",
                            "kind": "semantic_region_missing_normalized_bbox",
                            "message": f"{f}: semantic evidence {ev.get('id')} has no normalized bbox_xyxy",
                            "severity": "warning",
                            "details": {"file": f, "evidence_id": ev.get("id"), "region": result.get("region"), "bbox_format": result.get("bbox_format")},
                        })
                        continue
                    if image_size is not None and (bbox[0] < 0 or bbox[1] < 0 or bbox[2] > image_size[0] or bbox[3] > image_size[1]):
                        anomalies.append({
                            "phase": "plans",
                            "kind": "semantic_region_out_of_bounds",
                            "message": f"{f}: semantic evidence {ev.get('id')} extends outside scene bounds",
                            "severity": "warning",
                            "details": {"file": f, "evidence_id": ev.get("id"), "bbox_xyxy": bbox, "image_size_px": list(image_size)},
                        })
            # H3: missing orientation on ansicht/schnitt is now a warning,
            # not a W0 blocker. Surface for reviewer triage.
            tag = lbl.get("scene_tag")
            if tag in ("ansicht", "schnitt") and not lbl.get("scene_orientation"):
                anomalies.append({
                    "phase": "W0", "kind": "missing_orientation",
                    "message": f"{f}: scene_orientation not set (was previously a blocker; now a warning so reviewers can spot-check)",
                    "severity": "warning",
                    "details": {"file": f, "scene_tag": tag},
                })
    except Exception:  # noqa: BLE001
        pass

    # G5-2: surface the agent's run marker so reviewers see it.
    wf_obj = facts.get("workflow") or {}
    if wf_obj.get("driven_by"):
        anomalies.append({
            "phase": "review", "kind": "agent_labeled",
            "message": (
                f"labeled by {wf_obj['driven_by']!r}"
                + (f", run {wf_obj.get('driven_by_run_id')!r}"
                   if wf_obj.get("driven_by_run_id") else "")
                + " — needs human spot-check"
            ),
            "severity": "info",
        })

    counts = {
        "blocker": sum(1 for a in anomalies if a["severity"] == "blocker"),
        "warning": sum(1 for a in anomalies if a["severity"] == "warning"),
        "info": sum(1 for a in anomalies if a["severity"] == "info"),
    }
    return _ok({"anomalies": anomalies, "count": len(anomalies), "by_severity": counts},
               started_at=started)


@mcp.tool()
async def dump_run_summary(key: str, run_id: str, notes: str = "") -> dict:
    """Write a Markdown run summary to tmp/agent-runs/<run-id>/<key>.md.

    USE when:
      - Driver finishes a phase or a whole run and wants to capture a
        human-readable record.

    Args:
      key: house key.
      run_id: any short string. Driver convention:
              `YYYYMMDD-HHMM-<key>` (e.g. `20260530-1142-house-22`).
      notes: optional free-text to append after the auto-generated body.
    """
    started = time.time()
    safe_run = "".join(c for c in run_id if c.isalnum() or c in "-_")
    if not safe_run:
        return _err("schema_invalid", "run_id must be non-empty alphanumeric",
                    started_at=started)
    wf_env = await mcp_server.get_workflow_state(key=key)
    if not wf_env.get("ok"):
        return wf_env
    state = wf_env["data"]
    anomalies_env = await list_anomalies(key=key)
    guardrail_anomalies = []
    if anomalies_env.get("ok"):
        guardrail_anomalies = [
            a for a in ((anomalies_env.get("data") or {}).get("anomalies") or [])
            if a.get("kind") in {
                "crop_warning",
                "crop_regression",
                "possible_context_loss",
                "semantic_region_missing_normalized_bbox",
                "semantic_region_out_of_bounds",
                "label_out_of_bounds",
                "uncertain_mass_edge",
            }
        ]
    out_dir = Path(mcp_server.__file__).parent / "tmp" / "agent-runs" / safe_run
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{key}.md"
    body = ["# Run summary",
            f"- house: `{key}`",
            f"- run_id: `{safe_run}`",
            f"- generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            f"- exportable: {state.get('exportable')}",
            f"- next_phase: {state.get('next_phase')}",
            f"- scenes_total: {state.get('scenes_total')}",
            f"- labeled_scenes: {state.get('labeled_scenes')}",
            "",
            "## Phases"]
    for p, ph in state["phases"].items():
        body.append(f"- **{p}** — {ph['status']}")
        for b in ph.get("blockers", []):
            body.append(f"    - blocker: {b}")
    body.extend(["", "## Guardrails"])
    if guardrail_anomalies:
        for item in guardrail_anomalies[:20]:
            body.append(f"- {item.get('severity', 'warning')}: {item.get('kind')} — {item.get('message')}")
        omitted = max(0, len(guardrail_anomalies) - 20)
        if omitted:
            body.append(f"- omitted: {omitted} additional guardrail anomalies")
    else:
        body.append("- none")
    if notes:
        body.extend(["", "## Notes", notes])
    out_path.write_text("\n".join(body) + "\n")
    return _ok({"path": str(out_path.relative_to(Path(mcp_server.__file__).parent)),
                "bytes": out_path.stat().st_size},
               started_at=started)


@mcp.tool()
async def write_handoff_summary(
    key: str,
    run_id: str,
    file: str | None = None,
    phase: str = "scene",
    status: str = "needs_repair",
    labels_added: int = 0,
    labels_changed: int = 0,
    open_defects: list[str] | None = None,
    uncertain_labels: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    next_action: str | None = None,
    notes: str = "",
    quality: dict | None = None,
    calibration: dict | None = None,
    max_items: int = 20,
) -> dict:
    """Write a compact scene/phase handoff summary for context reduction.

    USE when:
      - A scene or phase worker is finished or pausing and the parent
        should receive durable state without inheriting the worker's full
        image/tool transcript.

    DON'T USE when:
      - You still need to perform visual verification. Write the handoff
        only after labels, defects, and evidence have been updated.
    """
    started = time.time()
    safe_run = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-") or "run"
    base = Path(mcp_server.__file__).parent / "tmp" / "agent-runs" / safe_run / "handoffs"
    base.mkdir(parents=True, exist_ok=True)
    target = file or phase
    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "-", target).strip("-") or "handoff"
    max_items = max(0, int(max_items))

    def bounded(values: list[str] | None) -> tuple[list[str], dict[str, int]]:
        values = values or []
        return values[:max_items], {
            "total": len(values),
            "returned": min(len(values), max_items),
            "omitted": max(0, len(values) - max_items),
        }

    defects, defect_counts = bounded(open_defects)
    uncertain, uncertain_counts = bounded(uncertain_labels)
    evidence, evidence_counts = bounded(evidence_refs)
    payload = {
        "summary_contract": "mcp-context-bloat/handoff-summary-v1",
        "key": key,
        "file": file,
        "phase": phase,
        "status": status,
        "labels_added": labels_added,
        "labels_changed": labels_changed,
        "open_defects": defects,
        "uncertain_labels": uncertain,
        "calibration": calibration or {},
        "quality": quality or {},
        "evidence_refs": evidence,
        "next_action": next_action,
        "notes": notes,
        "truncated": any(c["omitted"] for c in (defect_counts, uncertain_counts, evidence_counts)),
        "truncation": {
            "open_defects": defect_counts,
            "uncertain_labels": uncertain_counts,
            "evidence_refs": evidence_counts,
        },
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    json_path = base / f"{safe_target}.json"
    md_path = base / f"{safe_target}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    md_path.write_text("\n".join([
        f"# Handoff {key}",
        "",
        f"- File: {file or ''}",
        f"- Phase: {phase}",
        f"- Status: {status}",
        f"- Labels added: {labels_added}",
        f"- Labels changed: {labels_changed}",
        f"- Open defects: {len(open_defects or [])}",
        f"- Uncertain labels: {len(uncertain_labels or [])}",
        f"- Next action: {next_action or ''}",
        "",
        notes,
    ]) + "\n")
    return _ok({
        "json_path": str(json_path.relative_to(Path(mcp_server.__file__).parent)),
        "markdown_path": str(md_path.relative_to(Path(mcp_server.__file__).parent)),
        "bytes": json_path.stat().st_size,
        "summary": payload,
    }, started_at=started)
