# Context Bloat Reduction Tracker

**Status:** 2026-06-02. Implementation pass in progress on `main`.
Mitigation work is counted complete only when it is implemented, measured,
tested, and shown not to reduce labeling quality.

**Owner:** jhoetter

**Scope:** BIM labeling agent context pressure from MCP tool schemas,
inline image payloads, repeated state reads, long scene histories, plan-state
payloads, and orchestration patterns.

**Non-goal:** This is not a plan to make the agent look less. The drawings are
the ground truth. The goal is to make the agent look at the right pixels at
the right time, write down the decision durably, and stop carrying stale
pixels and full-state dumps through unrelated future work.

---

## 1. Mission

The house-labeling workflow currently asks the model to do visually precise
work across many drawings: identify scene roles, read dimensions, trace walls,
verify labels, resolve defects, and export honest results. That requires real
visual inspection. Context bloat reduction must therefore optimize the
workflow around visibility, not away from it.

The target outcome is:

- the agent can run longer in one session without losing focus;
- the agent sees enough image detail to make high-quality geometry decisions;
- routine routing uses compact state instead of full JSON/Markdown payloads;
- old visual context is converted into durable evidence and summaries;
- every optimization is measured against both context size and label quality.

The quality bar is unchanged:

- coordinates must still be visually justified;
- wall/opening/dimension labels must still be verified after placement;
- uncertain or incomplete work must still be recorded honestly;
- export readiness must not pass because payloads were compressed or omitted.

---

## 2. Diagnosis

The context bloat problem has four distinct sources. They should be solved in
priority order based on observed impact.

### 2.1 Inline Visual Payloads

Visual tools are essential, but full-scene renders and full labeled QA renders
can be very large. In prior house-22 analysis, the biggest individual result
rows were image payloads in the multi-megabyte range. Repeating those calls
causes the model context to fill with old images that are no longer needed for
the next decision.

This is the highest-severity problem because it combines:

- high byte volume per call;
- repeated calls during verify-after-write loops;
- images retained in transcript history after the coordinate or QA decision
  has already been made;
- loss of focus when the active context contains unrelated previous scene
  imagery.

Required principle: visual tools remain first-class, but full inline images
must be intentional. The default verification path should be tight crops and
compact envelopes.

### 2.2 Repeated Full State Reads

Agents often need to know "what next?" but full state reads answer that with
too much data:

- full house manifests;
- full labels with all geometry;
- full scene-plan JSON;
- full rendered Markdown;
- complete evidence history;
- all scene status in one unbounded pull.

This is especially harmful on active floorplans, where plan state and evidence
can grow large. The result is not only token cost; it also makes the model
reason over stale or irrelevant details.

Required principle: routing uses summaries. Full state is available for
debugging or targeted repair, but not as the default.

### 2.3 Whole-House Conversation Accumulation

The house workflow spans multiple scenes. If the parent context retains every
overview image, every crop, every failed edit, and every full-state read from
previous scenes, the later scene worker starts overloaded before it sees the
current drawing.

Required principle: scene and phase workers should hand back concise durable
summaries. The next worker fetches fresh visual context only for current open
questions.

### 2.4 Tool Schema Bloat

The MCP tool surface is broad. Long tool docstrings and many rarely used tools
consume context before the agent has done any useful work. Current client-side
deferred loading reduces this, but not every runtime will behave the same.

Required principle: keep the available tool surface relevant to the worker
role, and move long procedural guidance out of tool descriptions into
load-on-demand workflow docs or skills.

---

## 3. Quality Guardrails

Any bloat reduction that makes agents faster but less accurate is a regression.
The following guardrails are mandatory.

### Q1. Visual Sufficiency

Every geometry-bearing label must still have a visual verification path.

Accepted verification modes:

- tight crop around the edited label;
- targeted crop around a score/topology defect;
- full labeled QA view for global topology or final scene review;
- full overview when scene orientation or mass decomposition is unclear.

Rejected optimization:

- replacing visual verification with text-only summaries for new geometry;
- hiding or suppressing image access because images are expensive;
- using low-resolution thumbnails for coordinate decisions.

### Q2. Durable Evidence

After visual inspection, the agent must record the result in compact durable
form:

- coordinate values;
- label IDs touched;
- defect ID resolved or rejected;
- pass/fail outcome;
- uncertainty status;
- render metadata or evidence reference, not the full image payload.

The next turn should reason from this evidence and fetch pixels again only
when the evidence is insufficient.

### Q3. Bounded Defaults With Escape Hatches

Default tools should return compact, bounded payloads. But the agent must be
able to request full detail explicitly when needed.

Examples:

- `summary_only=true` by default for plan/state routing;
- `max_items`, `max_defects`, and `include_geometry=false` defaults;
- `image_delivery=handle` or compact crop mode by default where supported;
- explicit `image_delivery=inline` and full-state tools for visual/debug work.

### Q4. No Silent Truncation

If a tool omits data to stay compact, the response must say so:

- `truncated: true`;
- omitted counts;
- IDs or cursors for fetching omitted details;
- enough summary to decide which detail to fetch next.

### Q5. Verification Metrics Include Quality

Context metrics alone are insufficient. Every change must also track:

- wall score precision/recall/F1 where applicable;
- topology QA blocker counts;
- measurement QA unmatched ticks;
- export readiness blockers;
- uncertain/missing label counts;
- human-review flags.

---

## 4. Measurement Baseline

Before mitigation work is considered successful, add a repeatable harness that
can measure context pressure from agent transcripts and server logs.

Minimum report fields:

- MCP tool count and estimated schema size;
- tool-result byte totals by tool;
- inline image/base64 result count, max, average, and total bytes;
- largest individual result rows;
- repeated HTTP request counts;
- full-state read counts;
- per-scene tool-call counts;
- prompt/input/output token usage when transcript rows expose usage metadata;
- cache creation/read token totals when present;
- plan-state and labels payload sizes;
- wall/measurement/topology quality indicators if available.

Reference benchmark:

- `house-22` remains the first hard case.
- The harness must accept local JSONL transcripts and local MCP/server logs.
- It must not require uploading drawings or transcripts.

Success means the report can compare before/after runs in Markdown or JSON and
show both efficiency and quality deltas.

---

## 5. Target Operating Model

### 5.1 Visual Modes

Use three visual modes instead of treating every image call the same.

**Overview**

- Purpose: understand drawing role, orientation, massing, or global topology.
- Frequency: once per scene and when global uncertainty is real.
- Payload: small/medium render, usually no labels.
- Quality rule: enough to choose regions, not enough for final coordinates.

**Targeted Crop**

- Purpose: read a dimension, snap a coordinate, verify one label, inspect one
  defect.
- Frequency: common.
- Payload target: roughly 100-250 KB when inline.
- Quality rule: high enough fidelity for the local visual decision.

**Full Labeled QA**

- Purpose: final pass, topology/system review, broad relation checks.
- Frequency: rare and deliberate.
- Payload: allowed to be large, but measured and logged as intentional.
- Quality rule: not a substitute for local coordinate crops when detail is
  needed.

### 5.2 State Modes

Use state at three granularities.

**Routing Summary**

- One compact call answers what scene/task/defect should be worked next.
- Includes counts, statuses, current blocker IDs, next action IDs, recent
  evidence summaries, and calibration status.
- Target size: under 5 KB.

**Targeted Detail**

- Fetch one label, one defect, one action, one evidence entry, or one cropped
  visual region by ID.
- Used immediately before or after a focused edit.

**Debug Full State**

- Full labels, full plan JSON, rendered Markdown, full evidence history.
- Used for debugging, audits, or explicit human review.
- Not used in routine routing loops.

### 5.3 Worker Handoff

Every scene/phase worker should end with a concise handoff:

```json
{
  "key": "house-22",
  "file": "house-22-floorplan-eg.jpg",
  "phase": "floorplan",
  "status": "needs_repair",
  "labels_added": 12,
  "labels_changed": 4,
  "open_defects": ["def-wall-003"],
  "uncertain_labels": [],
  "calibration": {"status": "ok"},
  "quality": {
    "score_walls_f1": 0.93,
    "topology_blockers": 1,
    "measurement_unmatched_ticks": 0
  },
  "evidence_refs": ["ev-20260602-001"],
  "next_action": "Repair def-wall-003 with targeted crop",
  "notes": "North wall verified; garage connector remains uncertain."
}
```

The parent should not need the worker's image transcript to continue.

---

## 6. Work Items

### MCB0 - Measurement Harness

**Severity:** Blocker

Create `scripts/mcp_context_report.py` or equivalent.

Inputs:

- Claude/Codex JSONL transcripts;
- subagent transcript directories;
- MCP server logs;
- optional API/server logs;
- optional `data/dataset/<key>` for plan/label payload size sampling.

Outputs:

- Markdown report for humans;
- JSON report for automated before/after comparison.

Acceptance:

- reports image payload totals by tool;
- reports repeated state-read counts;
- reports largest rows and largest tools;
- reports per-scene calls when scene filenames are visible;
- can run on house-22 reference data;
- includes quality fields when score/evaluation results appear in the logs.

Quality protection:

- no optimization is declared successful without a before/after report;
- report must distinguish "fewer images" from "same quality with smaller or
  more targeted images."

### MCB1 - Crop-First Verification Defaults

**Severity:** High

Change agent guidance and tool ergonomics so verify-after-write normally uses
`verify_label_placement` or another tight crop, not broad
`get_scene_view_with_labels`.

Implementation targets:

- lower default `max_dim` for auto-crop verification where safe;
- preserve source-pixel coordinate reporting;
- ensure crop envelopes include enough metadata to correct by delta;
- mark full-scene labeled QA calls as global/final QA usage.

Acceptance:

- normal label verification produces compact inline payloads;
- full labeled views are still available and used for topology/final QA;
- tests cover auto-crop region calculation and label inclusion;
- house-22 metrics show fewer MB-scale verification rows.

Quality protection:

- coordinate precision must not regress;
- if an auto-crop is too small to judge context, the agent must request a
  wider crop or full QA view;
- the workflow docs must explicitly allow larger visual payloads when needed.

### MCB2 - Compact Routing Summaries

**Severity:** High

Add compact summary endpoints/tools for routine state routing.

Candidate tools:

- `get_house_context_summary(key)`;
- `get_scene_context_summary(key, file)`;
- `get_scene_plan_status(key, file)`;
- `get_scene_plan_next_action(key, file)`;
- bounded `list_scene_labels` summaries.

Some of these already exist in partial form; this work item is complete only
when they are the documented default path and payload size is measured.

Acceptance:

- a worker can decide the next scene/task without fetching full labels, full
  plan state, and full Markdown;
- summary payloads are typically under 5 KB;
- full-detail tools remain available by explicit request;
- tests assert bounded response shapes.

Quality protection:

- summaries must include blockers, uncertainty, and stale-evidence flags;
- summaries must not hide defects merely because details are omitted.

### MCB3 - Image Handle Delivery

**Severity:** High

Support a delivery mode for image-producing tools that stores the render on
disk and returns metadata plus a handle/resource URI instead of inline base64.

Candidate tools:

- `get_scene_view`;
- `get_scene_view_with_labels`;
- `verify_label_placement`;
- `get_pdf_page_view`;
- `dimension_chain_context`.

Return metadata:

- handle/URI/path;
- source scene and crop region;
- dimensions;
- byte size;
- render parameters;
- expiry or garbage-collection policy;
- optional tiny thumbnail only when useful.

Acceptance:

- handle mode reduces full-scene tool-result size by at least 80%;
- inline mode remains available for clients that need pixels in the model
  turn;
- render artifacts can be cleaned by age or run ID.

Quality protection:

- handle mode must not prevent visual inspection in clients that support
  resources;
- if the active client cannot inspect handles, inline crop mode remains the
  default for decision-critical visual work.

### MCB4 - Bounded Large Results

**Severity:** Medium-high

Add explicit budgets to large-list tools and QA tools.

Parameters:

- `max_items`;
- `max_defects`;
- `max_regions`;
- `include_geometry`;
- `include_markdown`;
- `include_evidence`;
- `summary_only`;
- `cursor` or targeted ID fetch where needed.

Affected payloads:

- labels;
- plan state;
- evidence;
- wall scoring;
- wall topology QA;
- measurement QA;
- anomalies;
- export readiness details.

Acceptance:

- default responses are bounded;
- truncation metadata is present;
- one-detail fetch is available for omitted items.

Quality protection:

- blocker counts and highest-severity blockers must always be visible;
- truncation cannot make a failed QA gate appear passed.

### MCB5 - Scene/Phase Handoff Summaries

**Severity:** High

Define and persist handoff summaries for scene and phase work.

Storage options:

- `tmp/agent-runs/<run-id>/<key>.md` for human-readable summaries;
- structured JSON sidecars for machine-readable handoff;
- scene-plan evidence entries for durable task-level decisions.

Acceptance:

- parent agent receives compact handoff instead of full visual transcript;
- house run can resume from summaries;
- summaries include open blockers and next action;
- summary size is bounded.

Quality protection:

- unresolved uncertainty is explicit;
- evidence refs are traceable;
- final summary cannot claim verified status unless plan gates agree.

### MCB6 - Repeated State-Read Fan-Out Cleanup

**Severity:** Medium-high

Audit loops and tools that repeatedly fetch the same full state.

Targets:

- house dashboard/status;
- export readiness;
- scene chip bars;
- plan state evaluation;
- label summaries;
- house facts/calibration checks.

Acceptance:

- one compact house status call can replace repeated manifest/facts/labels
  fan-out in routing;
- repeated full-state reads drop by at least 50% in the house-22 report;
- API and MCP paths are cache-aware where appropriate.

Quality protection:

- cache invalidation must respect label writes, plan updates, calibration
  recompute, and scene reset;
- stale summaries must be marked stale, not silently reused.

### MCB7 - Toolset and Description Compression

**Severity:** Medium

Reduce static schema cost after result bloat is under control.

Options:

- phase-specific MCP servers or environment filters;
- progressive tool discovery profile;
- shorter docstrings;
- move long procedures to `spec/` or skills;
- load detailed tool help on demand.

Acceptance:

- normal scene worker schema is under 5k-10k tokens before JSON Schema
  overhead;
- `bim-database` MCP docstring chars drop materially;
- debug profile can still expose all tools.

Quality protection:

- critical validation rules stay in schemas/tests;
- shortened descriptions must still guide correct tool choice;
- phase filters must not hide escape-hatch tools needed for recovery.

### MCB8 - Visual Payload Policy

**Severity:** Medium

Document and enforce the operating policy.

Policy:

- one overview per scene unless global uncertainty remains;
- tight crops for coordinate reads and label verification;
- full labeled views for topology/global/final QA;
- after a visual decision, write compact evidence;
- never loop full-scene renders without a narrowed hypothesis;
- ask for larger visuals when the crop is insufficient.

Acceptance:

- policy appears in agent workflow docs/prompts;
- metrics flag repeated full-scene render loops;
- smoke tests or report fixtures include policy-violation detection.

Quality protection:

- policy is guidance, not a hard ban;
- final QA can still use full visual context when needed.

---

## 7. Implementation Priority

1. **MCB0 Measurement Harness**
   Establish before/after numbers and quality metrics.

2. **MCB1 Crop-First Verification Defaults**
   Attack the largest observed payload source while preserving visual checks.

3. **MCB2 Compact Routing Summaries**
   Reduce repeated full labels/plan/house reads.

4. **MCB5 Scene/Phase Handoff Summaries**
   Stop carrying old scene context into later scene work.

5. **MCB3 Image Handle Delivery**
   Large gain when client/runtime can inspect handles.

6. **MCB6 Repeated Fan-Out Cleanup**
   Remove orchestration-level duplicate reads.

7. **MCB4 Bounded Large Results**
   Make all large tools safe by default.

8. **MCB7 Toolset and Description Compression**
   Reduce schema cost once result bloat is controlled.

9. **MCB8 Visual Payload Policy**
   Keep policy visible and measurable throughout.

---

## 8. Success Metrics

Use house-22 as the first benchmark.

Context metrics:

- max prompt-ish row/token total;
- total image result bytes;
- MB-scale image result row count;
- normal verification payload size;
- full-scene render count per scene;
- repeated full labels/plan/house reads;
- tool schema size estimate;
- number of turns before compaction/context exhaustion.

Quality metrics:

- scene-plan terminality;
- open blocker defect count;
- wall score precision/recall/F1;
- topology QA defects;
- measurement unmatched ticks;
- uncertain/missing label count;
- export readiness blockers;
- human-review flags.

Targets after MCB0-MCB3 and MCB5:

- 3x-5x reduction in active-context pressure on house-22-like runs;
- normal verification payloads around 100-250 KB when inline;
- full-scene image calls become overview/QA events, not routine loops;
- repeated full-state reads reduced by at least 50%;
- no regression in scene-plan terminality or QA scores.

Aggressive target after image handles and handoff discipline:

- 5x-8x effective token efficiency improvement;
- zero full-house restarts caused by context exhaustion on house-22;
- parent context carries summaries, not scene image history.

---

## 9. Open Questions

1. Which runtime will execute production labeling, and does it support MCP
   resources or file handles without reinjecting image bytes into context?
2. Should image handle delivery be opt-in first, or default for full-scene
   views with inline crop mode for decision-critical visuals?
3. What retention policy is acceptable for generated render artifacts?
4. Should summaries live only in scene-plan state, or should there also be a
   run-level structured handoff file?
5. Which current full-state reads are caused by agent behavior versus API/UI
   orchestration?
6. How should the harness normalize token accounting across Claude, Codex,
   and other transcript formats?
7. What minimum crop resolution is required for reliable coordinate reading
   on faint scans?

---

## 10. Completion Definition

This tracker is complete only when:

- the measurement harness exists and has before/after reports;
- crop-first verification is the default documented workflow;
- routing can use compact summaries without full-state fan-out;
- scene/phase handoff summaries are durable and bounded;
- image handle or equivalent compact delivery is available where supported;
- large result tools have budgets and truncation metadata;
- schema/toolset bloat is reduced or phase-filtered;
- house-22 can be labeled with materially lower context pressure and no
  observed quality regression.

---

## 11. Implementation Evidence

Current implementation artifacts on `main`:

- **MCB0 Measurement harness:** `scripts/mcp_context_report.py`
  reports tool catalog size, profile schema estimates, transcript result
  bytes, inline image counts, quality signals, repeated full-state reads, and
  dataset payload samples.
- **MCB1 Crop-first verification:** `verify_label_placement` is documented
  as the routine verify-after-write path and defaults to compact auto-crops.
  The full labeled view remains available for global/topology/final QA.
- **MCB2 Compact summaries:** `get_house_context_summary`,
  `get_scene_context_summary`, and bounded `list_scene_labels` provide
  geometry-free routing summaries with truncation metadata.
- **MCB3 Image handles:** image-producing MCP tools support
  `image_delivery="inline" | "handle" | "auto"`. Handle mode writes local
  files under `tmp/mcp-image-handles` and omits inline base64.
- **MCB4 Bounded results:** wall score, measurement score, topology QA,
  continuity check, ambiguous-line context, label summaries, and handoff
  summaries return bounded lists with explicit truncation/omitted counts.
- **MCB5 Handoff summaries:** `write_handoff_summary` writes structured JSON
  and Markdown handoffs under `tmp/agent-runs/<run-id>/handoffs/`.
- **MCB6 Fan-out cleanup:** compact summary tools centralize routing reads
  and avoid fetching full labels/plan JSON for routine decisions.
- **MCB7 Toolset reduction:** `BIM_MCP_TOOL_PROFILE` supports `inventory`,
  `floorplan`, `elevation`, `review`, or `all`. Measurement harness reports
  per-profile schema estimates.
- **MCB8 Visual policy:** the canonical `label-house` MCP prompt now states
  the visual payload policy, crop-first rule, handle-mode escape hatch, and
  handoff requirement.

Measured current catalog estimates with:

```bash
.venv/bin/python scripts/mcp_context_report.py \
  --catalog mcp_server.py \
  --profile-source mcp_server.py \
  --format json
```

Current rough schema estimates before JSON Schema overhead:

| Profile | Tools | Doc chars | Rough tokens |
|---|---:|---:|---:|
| all | 53 | 41,561 | 11,436 |
| inventory | 16 | 13,376 | 3,592 |
| floorplan | 27 | 21,930 | 6,194 |
| elevation | 18 | 20,925 | 5,682 |
| review | 13 | 11,907 | 3,303 |

Current verification evidence:

- `tests/test_mcp_context_report.py`
- `tests/test_mcp_context_summary.py`
- `tests/test_mcp_context_summary_helpers.py`
- `tests/test_mcp_image_delivery.py`
- `tests/test_mcp_bounded_results.py`
- `tests/test_mcp_handoff_summary.py`
- `tests/test_mcp_tool_profiles.py`
- broader smoke/plan/render tests covering existing behavior.
