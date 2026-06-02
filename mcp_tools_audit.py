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
    get_house_facts,
    get_workflow_state,
    mcp,
)


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
    wf = await get_workflow_state(key=key)
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
    facts_env = await get_house_facts(key=key)
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
            lbl_status, lbl = await mcp_server._api_get(f"/labels/dataset/{key}/{f}")
            if lbl_status != 200 or not isinstance(lbl, dict):
                continue
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
    wf_env = await get_workflow_state(key=key)
    if not wf_env.get("ok"):
        return wf_env
    state = wf_env["data"]
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


