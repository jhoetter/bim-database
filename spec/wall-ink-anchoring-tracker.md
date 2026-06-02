# Wall Ink Anchoring Tracker

**Status:** 2026-06-02. Implemented first hardening pass on `main` after the
live Codex `house-22` EG run where the agent produced plausible topology with
walls visibly shifted off the source ink.

**Owner:** jhoetter

**Scope:** Prevent floorplan wall labels from becoming accepted readable
geometry unless they are anchored to detected wall ink, or explicitly marked
uncertain with evidence. This tracker is about geometric trustworthiness, not
visual polish.

**Non-goal:** This is not a demand for perfect source scans or zero ambiguous
regions. House-22 is faint and noisy. The goal is that the workflow never
mistakes a visually plausible rectangle for a verified wall when the rendered
overlay and `score_walls` show that it misses the ink.

---

## 1. Failure Observed

A fresh Codex run on `house-22` produced an EG state that looked structurally
reasonable at a glance:

- 28 wall labels;
- 5 floorplan openings;
- 2 reference dimensions;
- homography `ok`;
- plan state honestly `needs_repair`.

But the geometry was not trustworthy:

- saved walls were visibly shifted from the actual thick wall bands;
- several labels were room/shape rectangles rather than measured wall
  centerlines;
- openings and dimensions were then placed on top of unverified parent wall
  geometry.

The deterministic QA did catch this:

- `score_walls` final-ish F1 was about `0.512`;
- precision was about `0.406`;
- EG still had 42 open blocker defects;
- 24 of those were `wall_off_ink`;
- many off-ink segments had overlap fractions near or equal to `0.0`.

Examples from the plan state:

- `DEF-017`: wall `[1235,985]-[1235,1508]`, overlap `0.0`;
- `DEF-030`: wall `[1710,1510]-[1710,1998]`, overlap `0.0`;
- `DEF-033`: wall `[2040,995]-[2040,1510]`, overlap `0.0`;
- `DEF-061`: garage bottom `[2670,2460]-[3170,2460]`, overlap `0.0`.

The key diagnosis: detection worked, but the workflow still allowed the agent
to write and reason from speculative wall labels before those walls had been
forced through an ink-anchoring step.

---

## 2. Root Causes

### 2.1 Direct Wall Writes Are Too Easy

`upsert_label(type="wall")` accepts any schema-valid centerline. The schema
checks shape, not whether the wall sits on source ink. This is necessary at the
storage layer, but dangerous for autonomous agents.

Observed behavior: the agent wrote coordinate rectangles from visual estimates,
then relied on later `score_walls` to discover they were bad.

### 2.2 Verification Is Reactive, Not a Placement Contract

`evaluate_scene_plan_gates` creates `wall_off_ink` blockers, but only after a
batch of labels is already in the dataset. The plan becomes `needs_repair`,
which is honest, but the bad labels are still present and downstream tools can
use them as parent geometry.

### 2.3 The Agent Can Proceed Downstream While Walls Are Failed

In the observed run, openings and dimensions were added while wall score and
topology gates already had wall blockers. This is particularly harmful because:

- opening quads depend on parent wall lines;
- dimension tick alignment depends on wall faces/centerlines;
- repair later has to disentangle dependent labels from bad walls.

### 2.4 Repair Candidates Are Optional

The tools now expose repair candidates, overlays, and apply/decision paths.
However, the agent can still ignore them and manually add more rough geometry.
In the bad run, `refine_wall` was not called, and repair candidates were only
consulted after the first bad wall batch.

### 2.5 Plan Actions Are Too Broad

The run recorded geometry edits under `ACT-CLASSIFY_SCENE`. That action should
only classify the scene. A broad umbrella action lets the agent mix
classification, wall tracing, openings, dimensions, and house facts without the
proper stage-specific invariants.

---

## 3. Target Behavior

The desired wall workflow is:

1. **Draft:** agent proposes an approximate wall hypothesis from image context.
2. **Anchor:** system refines/snap-checks the candidate against wall ink.
3. **Persist:** only anchored wall geometry is saved as `readable`.
4. **Verify:** `score_walls` and visual overlay confirm no major off-ink
   blocker remains.
5. **Unlock:** openings and dimensions become available only after wall
   anchoring passes, or after each remaining failure is classified with
   evidence.

The workflow must support three honest outcomes:

- `readable`: wall lies on source ink and passes local/global QA;
- `uncertain`: wall is plausible but source ink is too weak or ambiguous;
- rejected/no label: region is furniture/site/dimension/projection/non-wall.

It must not support:

- persisting off-ink walls as `readable`;
- placing openings on walls that currently have off-ink blockers;
- treating "shape topology looks plausible" as equivalent to "geometry sits on
  the drawing."

---

## 4. Workstream A - Ink-Anchored Wall Write Path

### A1. Add `upsert_wall_anchored`

**Problem:** Agents need a wall-specific write tool whose default behavior is
"snap/refine before persist."

**Implementation targets:**

- `api/main.py`
- `mcp_server.py`
- `api/wall_geometry.py`
- tests around route and MCP behavior

**Behavior:**

New API/MCP helper accepts:

```json
{
  "key": "house-22",
  "file": "house-22-floorplan-eg.png",
  "candidate": {
    "start": [1235, 985],
    "end": [1588, 985],
    "thickness_mm": 300
  },
  "anchor": {
    "mode": "refine_wall",
    "search_px": 40,
    "min_confidence": 0.82,
    "snap_corners": true
  },
  "status_if_unanchored": "uncertain"
}
```

The tool must:

- call the existing wall refinement logic;
- optionally snap endpoints to detected wall corners;
- return original vs anchored endpoints;
- return confidence, ink overlap, and movement delta;
- persist as `readable` only if confidence and overlap pass thresholds;
- persist as `uncertain` only when explicitly requested and evidence is
  attached;
- otherwise return a non-persisted failure with suggested next crop.

**Acceptance:**

- A candidate shifted 40-80 px from a known wall is corrected before saving.
- A candidate over furniture/site ink is rejected or saved uncertain, never
  readable.
- Response includes enough compact evidence for the agent to decide whether to
  inspect a crop.
- Tests verify that schema-valid but off-ink wall coordinates do not become
  readable via this path.

### A2. Make Raw `upsert_label(wall)` Emit an Anchoring Warning

**Problem:** We cannot remove raw `upsert_label`, but the agent needs a strong
signal when using the wrong path for walls.

**Behavior:**

When `upsert_label` receives a floorplan `wall`:

- allow the write for backward compatibility;
- immediately compute a local ink-overlap check for that wall;
- include response fields:
  - `anchoring_status: "unchecked" | "off_ink" | "on_ink"`;
  - `ink_overlap`;
  - `recommended_tool: "upsert_wall_anchored"`;
  - `must_verify_before_downstream: true` when overlap is below threshold.

Optionally, add an opt-in strict mode:

```json
"attributes": {"anchoring_required": true}
```

where the server rejects off-ink readable wall writes.

**Acceptance:**

- Existing tests and manual tools do not break.
- Agent transcript immediately sees that raw wall writes are draft quality.
- No wall with overlap `0.0` can be mistaken as accepted high-quality geometry.

### A3. Add Label-Level Anchoring Metadata

**Problem:** Later stages need to know whether a wall was actually anchored or
just manually estimated.

**Behavior:**

Store optional wall attributes:

```json
{
  "thickness_mm": 300,
  "anchoring": {
    "method": "refine_wall",
    "confidence": 0.91,
    "ink_overlap": 0.84,
    "original_start": [1235, 985],
    "original_end": [1588, 985],
    "delta_px": [12, -31],
    "evidence_id": "EV-123"
  }
}
```

If schema constraints make nested attributes undesirable, use a flat
`label_quality` sidecar keyed by label ID.

**Acceptance:**

- Plan gates can distinguish anchored, unanchored, and uncertain walls.
- UI can surface "not ink-anchored" in Agent View.

---

## 5. Workstream B - Strict Wall Gate Before Downstream Work

### B1. Introduce `WALL_INK_ANCHORED` Gate

**Problem:** Current wall gates detect off-ink labels, but downstream tasks can
still start before wall anchoring is resolved.

**Implementation target:**

- `api/scene_plan_state.py`

**Behavior:**

Add a required wall gate for floorplans:

- passed when no open `wall_off_ink` blocker exists and wall score precision
  meets a threshold;
- failed when `score_walls.off_ink_segments` is non-empty above severity
  threshold;
- pending when score evidence is absent after wall writes.

Suggested default thresholds for floorplans:

- blocker if any readable wall has `on_frac < 0.60`;
- blocker if scene precision `< 0.70` after wall batch;
- warning if precision `< 0.82` but off-ink blockers are classified/uncertain;
- allow house-22 faint-scan profile to use `thin_aware=true`, but not to accept
  `0.0` overlap walls.

**Acceptance:**

- `TRACE_OUTER_WALLS` and `TRACE_INTERIOR_WALLS` cannot become `verified`
  while `WALL_INK_ANCHORED` fails.
- `PLACE_OPENINGS`, `READ_DIMENSIONS`, and `VERIFY_MEASUREMENTS` remain
  `blocked` while wall off-ink blockers exist.
- `evaluate_scene_plan_gates` summary calls this out directly:
  `"Wall ink anchoring failed: 24 readable wall segments are off ink."`

### B2. Stage Lock in `start_scene_plan_action`

**Problem:** Even if tasks are marked blocked, a cached or direct action can
still be started unless start is guarded.

**Behavior:**

Reject or redirect starts for:

- `PLACE_OPENINGS`;
- `VERIFY_OPENINGS`;
- `READ_DIMENSIONS`;
- `VERIFY_MEASUREMENTS`;
- `FINAL_QA`;

when current wall anchoring blockers exist.

The response should include:

- `code: "wall_ink_anchor_blocked"`;
- first blocker defect ID;
- recommended action ID;
- recommended tools: `get_scene_repair_candidates`,
  `get_scene_view_with_repair_candidate`, `apply_repair_candidate`,
  `upsert_wall_anchored`, `score_walls`.

**Acceptance:**

- The observed bad sequence cannot recur: openings cannot be written while
  walls have open `wall_off_ink` blockers.
- Tests cover direct action start and cached action IDs.

### B3. Auto-Downgrade Off-Ink Walls

**Problem:** A wall can be known off-ink but still shown as `readable`.

**Behavior:**

When gate evaluation creates a `wall_off_ink` blocker for a label:

- link the defect to the wall label ID where possible;
- set label status to `uncertain` automatically, or set a separate
  `quality_status: "off_ink"` if changing `status` is too disruptive;
- restore `readable` only after repair and a passing score.

**Acceptance:**

- UI label list no longer shows off-ink walls as normal readable labels.
- Openings cannot attach to a parent wall with `quality_status="off_ink"`.

---

## 6. Workstream C - Better Repair Candidate Coverage for Off-Ink Walls

### C1. Generate Move Candidates for `wall_off_ink`

**Problem:** Current repair candidates are strongest for topology findings and
some missing regions. For shifted walls, the best fix is often "move this wall
onto the nearest parallel wall-band ink."

**Implementation target:**

- `api/topology_repair.py`
- `api/wall_geometry.py`

**Behavior:**

For each `wall_off_ink` segment:

- search a perpendicular band around the saved centerline;
- find nearest parallel dark wall-band centerline;
- propose a `move` candidate with:
  - original wall ID;
  - candidate wall endpoints;
  - estimated shift vector;
  - before/after score delta;
  - confidence;
  - crop region.

Use `propose_wall_edit(..., apply=false)` or a similar simulation before
offering it.

**Acceptance:**

- A shifted vertical wall in a synthetic fixture produces a move candidate.
- Candidate overlay shows original wall and proposed wall in distinct colors.
- Applying the candidate improves precision/F1 and clears/reduces the off-ink
  defect.

### C2. Prefer Repair Candidates Over Manual Wall Adds

**Problem:** The agent added a second garage rectangle instead of repairing or
removing the wrong earlier "garage" rectangle.

**Behavior:**

When a wall repair action is active:

- `get_scene_plan_next_action` should prioritize candidate review;
- allowed tools should list repair tools before raw `upsert_label`;
- tool response should say "do not add new walls until current off-ink move or
  delete candidates are accepted/rejected."

**Acceptance:**

- Agent logs show repair candidate overlay/apply/decision before raw wall
  writes during defect repair.

### C3. Add Delete Candidates for False Structural Rectangles

**Problem:** If a wall rectangle is drawn over non-wall site/furniture area, the
correct repair is delete, not move.

**Behavior:**

For off-ink walls with low overlap and no nearby parallel wall-band:

- propose `delete` candidate;
- simulate score impact;
- classify likely false-positive context using `ambiguous_line_context`;
- require visual confirmation before applying.

**Acceptance:**

- The mistaken middle "garage" rectangle from the observed run would produce
  delete or move candidates rather than becoming permanent clutter.

---

## 7. Workstream D - Visual Modes That Make Off-Ink Obvious

### D1. Default Wall Defect Crops to `ink_compare`

**Problem:** In broad QA overlays, heavy wall bands can make shifted geometry
look less obviously wrong.

**Behavior:**

For `wall_off_ink` and `wall_missing_region` next actions:

- default crop style should be `ink_compare`;
- source ink should be high contrast;
- saved wall labels should be thin, semi-transparent, and centered;
- optionally draw nearest detected ink centerline in a separate color.

**Acceptance:**

- A crop for `DEF-017` clearly shows label line and source wall band separated.

### D2. Add Off-Ink Heat Overlay

**Behavior:**

`get_scene_view_with_labels` or a new QA route can render:

- green wall segments with acceptable overlap;
- orange/red wall segments below threshold;
- missing-region boxes;
- a compact legend in the metadata, not large in-image text.

**Acceptance:**

- Full-scene EG overview makes off-ink walls visually impossible to miss.

---

## 8. Workstream E - Plan Action Discipline

### E1. Do Not Record Geometry Under Classification Actions

**Problem:** The observed run recorded `labels_added: 37` under
`ACT-CLASSIFY_SCENE`.

**Behavior:**

`record_scene_plan_attempt` and `finish_scene_plan_action` should reject
geometry-bearing edits for classification-only actions, or mark the action
invalid and force a proper wall action.

**Acceptance:**

- `ACT-CLASSIFY_SCENE` can only record scene tag/level/orientation evidence.
- Wall writes must occur under `TRACE_OUTER_WALLS`, `TRACE_INTERIOR_WALLS`, or
  explicit defect repair actions.

### E2. Add Tool-Allowed Write Guard

**Behavior:**

When an action is in progress, geometry writes should be checked against the
action's `allowed_label_types`.

Examples:

- `CLASSIFY_SCENE`: no wall/opening/dimension writes;
- `TRACE_OUTER_WALLS`: walls only;
- `PLACE_OPENINGS`: openings only and only if wall ink gate passed;
- `READ_DIMENSIONS`: dimension labels only and only if wall ink gate passed.

This can be implemented as a soft warning first, then as a hard gate.

**Acceptance:**

- The bad "classification action wrote everything" pattern cannot repeat.

---

## 9. Workstream F - Tests and Regression Harness

### F1. Unit Tests

Add tests for:

- `upsert_wall_anchored` refines shifted walls;
- off-ink candidate cannot become readable when strict anchoring is enabled;
- `WALL_INK_ANCHORED` gate fails on `off_ink_segments`;
- downstream actions are blocked by wall anchoring failures;
- off-ink labels are downgraded or quality-marked;
- geometry writes under the wrong action type are rejected/warned.

### F2. Synthetic Image Fixtures

Create small test images with:

- thick horizontal and vertical wall bands;
- labels shifted by 20-80 px;
- furniture-like thin lines;
- intentional opening gaps.

---

## 10. Implementation Notes - 2026-06-02

Implemented safeguards:

- Added `upsert_wall_anchored` as HTTP + MCP write path. It refines draft wall
  geometry against wall ink, returns original/anchored geometry, confidence,
  overlap, movement delta, and only persists readable walls above threshold.
- Added `wall-labels/anchoring-check` and MCP raw-wall upsert warnings so direct
  `upsert_label(type="wall")` reports `anchoring_status`, `ink_overlap`, and
  recommends `upsert_wall_anchored`.
- Added schema metadata for wall `quality_status`, `anchoring_required`, and
  nested `anchoring` provenance.
- Added strict raw-write mode: `attributes.anchoring_required=true` rejects a
  readable wall that does not overlap source ink.
- Added `WALL_INK_ANCHORED` gates to floorplan wall tasks and a stage lock that
  blocks openings, dimensions, measurement verification, and final QA while
  wall anchoring blockers exist.
- Added automatic off-ink quality marking: matched off-ink wall labels become
  `status=uncertain` with `quality_status=off_ink`; repaired/passing labels are
  restored to readable/unanchored or ink-anchored based on metadata.
- Added opening-parent guard: openings cannot attach to parent walls marked
  `quality_status=off_ink` or `unanchored`.
- Added score-driven repair candidates: move-to-nearby-missing-region,
  re-anchor-through-`upsert_wall_anchored`, and delete-if-false-positive.
- Kept repair overlays defaulting to `ink_compare`; score-driven candidates are
  now discoverable through plan-state candidate lookup.
- Added action-discipline guards so classification actions cannot record
  geometry edits and action attempts are checked against allowed label types.
- Added MCP write-time action-scope warnings so `upsert_label` and
  `upsert_wall_anchored` report when a geometry write happens under the wrong
  current action or while wall anchoring is failed.

Regression coverage added:

- Anchored route refines a shifted synthetic wall and persists it as readable.
- Anchored route refuses to persist a failed readable wall.
- Strict raw-wall anchoring rejects off-ink walls.
- `WALL_INK_ANCHORED` fails on score off-ink evidence.
- Downstream action start is blocked by wall anchoring failures.
- Off-ink labels are quality-marked and expose repair candidates.
- Openings cannot use off-ink parent walls.
- Geometry edits under `ACT-CLASSIFY_SCENE` are rejected.

Verification:

- `.venv/bin/python -m pytest tests/test_wall_tools_routes.py tests/test_scene_plans.py -q`
- `.venv/bin/python -m py_compile api/main.py api/scene_plan_state.py api/topology_repair.py mcp_server.py`

These should exercise the exact observed failure without relying on the large
house-22 dataset.

### F3. House-22 Regression Check

Add an optional, non-default regression script:

```bash
scripts/check_wall_anchoring.py --key house-22 --file house-22-floorplan-eg.png
```

Report:

- wall count;
- anchored/readable/uncertain counts;
- off-ink blocker count;
- worst 10 overlap fractions;
- whether downstream labels are attached to off-ink parent walls.

**Acceptance:**

- The current bad EG state fails loudly.
- A high-quality state with walls on ink passes.

---

## 10. Success Criteria

This tracker is complete when:

- a fresh agent cannot write a batch of readable off-ink walls without seeing
  immediate anchoring failures;
- plan gates block openings/dimensions until walls are ink-anchored or
  explicitly uncertain/rejected;
- off-ink walls are linked to labels and shown as non-readable/quality-failed
  until repaired;
- repair candidates include move/delete proposals for shifted walls;
- defect crops make label-vs-ink separation visually obvious;
- tests cover the failure mode observed in the 2026-06-02 Codex run.

The expected behavioral change is not that agents never draft bad geometry.
They will. The expected change is that bad geometry cannot masquerade as
accepted geometry, cannot become a parent for later labels, and is immediately
routed into a repair loop with deterministic move/delete candidates.
