# Agent Labeling Quality Follow-up Tracker

**Status:** 2026-06-02. Draft from the post-context-bloat house-22 live run.

**Owner:** jhoetter

**Scope:** Follow-up fixes for the plan-gated labeling workflow after context
bloat reduction: scene-order enforcement, wall/opening visual QA, placement
helpers, and quality metrics.

**Non-goal:** This is not a rollback of context minimization. Compact routing
and image handles are still correct. The goal is to preserve those gains while
making the agent's visible work easier to inspect and harder to prematurely
broaden across scenes.

---

## 1. Situation

The current main-branch fixes have closed the worst loophole:

- required scene-plan tasks cannot honestly complete as
  `accepted_incomplete`;
- readiness now rejects required tasks with waived gates;
- `validate_export_readiness` blocks the current bad/baseline plan state;
- `get_recommended_next_action` points back to EG when EG still has open
  required geometry work.

A fresh Codex run on `house-22` is behaving substantially better than the
previous baseline run. The live logs show actual plan-loop execution:

- repeated `start_scene_plan_action`;
- targeted `get_scene_view` / `get_scene_view_with_labels` crops;
- `score_walls`, `wall_topology_qa`, and `evaluate_scene_plan_gates`;
- `record_scene_plan_attempt` with evidence IDs;
- blocker defects repaired one by one.

However, the run also exposed three quality/ergonomics gaps:

1. The worker can still continue a non-EG scene directly after calling
   `get_scene_plan_next_action(file=...)`, even while the global recommender
   says EG should be next.
2. Wall bands in the UI are rendered from semantic `thickness_mm`, making QA
   overlays visually heavier than the source ink.
3. Floorplan opening overlays are too visually dominant and sometimes obscure
   the walls that the reviewer is trying to judge.

These are workflow and visualization problems, not evidence that context
minimization itself is bad. The agent still needs image access, and the current
run proves that targeted crops plus plan evidence can work. The remaining work
is to make the correct path the only easy path.

---

## 2. Live-Run Evidence

Reference run:

- Codex session log:
  `~/.codex/sessions/2026/06/02/rollout-2026-06-02T10-22-42-019e876d-874c-78d1-b552-9d100d5a3410.jsonl`
- Dataset:
  `data/dataset/house-22`

Observed at the time of this tracker:

- `validate_export_readiness("house-22")` returns `ready=false`.
- `get_recommended_next_action("house-22")` returns Wgeo,
  `scene_priority="groundfloor-first"`, and suggests
  `house-22-floorplan-eg.jpg`.
- Current plan states show EG and UG each with verified work plus remaining
  blocker defects, while DG is still mostly open.
- The active Codex log shows extensive work on `house-22-floorplan-ug.jpg`
  even though the global recommender still points to EG.
- EG labels at that point include 22 wall labels and 4 floorplan openings.
  Openings are plausible as stored geometry, but their rendered polygons and
  hatches cover too much of the scan in visual QA.

The conclusion: plan gates are now strict enough, but scene routing is still
advisory and the QA rendering mode is too visually loud.

---

## 3. Quality Bar

The desired behavior for a house-labeling agent is:

- Work one scene-plan action at a time.
- Do not touch non-groundfloor geometry while any EG required scene-plan task
  or blocker defect remains.
- Do not touch sections/elevations for geometry until required floorplan work
  has honestly completed.
- Use visual crops for every geometry-bearing label.
- Record compact evidence after visual inspection.
- Let readiness/export pass only when required gates are verified.
- Make UI/MCP verification renders expose source ink and saved labels without
  the labels hiding the source feature.

The agent must still be able to inspect full-resolution images whenever needed.
The fix is better routing and better render modes, not less vision.

---

## 4. Workstream A — Enforce Groundfloor-First Routing

### A1. Add Global Routing Guard to Scene-Plan Action Tools

**Problem:** `get_recommended_next_action` is groundfloor-aware, but
`get_scene_plan_next_action(key, file)` can still be called for UG/DG while EG
is open.

**Implementation target:**

- `mcp_server.py`
- API route behind `/datasets/{key}/{file}/plan-state/next-action`, if server
  route-level enforcement is preferable.

**Behavior:**

- When a caller requests a scene-plan action for a non-EG scene and EG has
  incomplete required plan work, return a structured blocker instead of the
  requested action.
- The blocker should include:
  - `code: "groundfloor_first_blocked"`;
  - `requested_file`;
  - `recommended_file`;
  - `recommended_tool: "get_scene_plan_next_action"`;
  - the first recommended EG action, if compact enough;
  - a short reason.

**Exception:**

- Allow non-EG W0/W1/W2/W3/W4 metadata work where it is required before Wgeo.
  The guard is for geometry scene-plan work, not initial extraction,
  classification, global facts, or calibration.

**Acceptance:**

- A test house with EG and UG incomplete returns EG from
  `get_recommended_next_action`.
- Calling `get_scene_plan_next_action` on UG returns
  `groundfloor_first_blocked`.
- Calling `start_scene_plan_action` for a UG action while EG is open is also
  rejected, so a cached action ID cannot bypass the guard.
- Once EG `required_complete=true`, UG/DG actions are allowed.

### A2. Make Scene Priority Visible in Every Plan-Action Response

**Problem:** The current singular scene action does not remind the worker that
it is operating under a global scene priority.

**Behavior:**

- Include `scene_priority`, `global_recommended_file`, and
  `scene_order_blocked` fields in `get_scene_plan_next_action` responses.
- Keep the default payload compact.

**Acceptance:**

- Agent logs show each plan-action loop carries the current global priority.
- No full house context read is required to know whether the current scene is
  allowed.

### A3. Update Tool Docstrings and Methodology Text

**Problem:** Tool descriptions say to use the singular scene action inside a
scene loop, but do not make the global scene-order guard explicit enough.

**Behavior:**

- Update `get_scene_plan_next_action`, `start_scene_plan_action`, and
  `get_recommended_next_action` docstrings.
- State that orchestrators must always resume from the global recommender, and
  scene workers must accept a redirected/blocker response.

**Acceptance:**

- Tool docs clearly say that EG blockers outrank direct UG/DG scene action
  requests.

---

## 5. Workstream B — Wall QA Render Mode

### B1. Separate Semantic Wall Thickness From QA Stroke Thickness

**Problem:** UI walls render as a full band using `thickness_mm` via
`wallBandPath`. That is semantically meaningful but visually too heavy during
QA, especially on faint scans. The overlay can look off even when the centerline
is on the ink band.

**Implementation target:**

- `ui/src/pages/AnnotatePage.tsx`
- `ui/src/lib/renderGeometry.ts`
- MCP/HTTP render path used by `get_scene_view_with_labels` and
  `verify_label_placement`, if server-side render has equivalent styling.

**Behavior:**

Add a QA wall display style with:

- centerline always visible;
- wall band fill opacity reduced substantially;
- hatch disabled or very faint in Agent View / verification mode;
- optional real-thickness outline as dashed edge lines rather than filled band;
- selected label still clearly highlighted.

**Suggested modes:**

- `semantic`: current full wall-body band, useful for model preview.
- `qa`: thin centerline + faint band, default for annotation and Agent View.
- `ink_compare`: source ink high contrast, labels thin and semi-transparent,
  default for `verify_label_placement` and score-defect crops.

**Acceptance:**

- On house-22 EG, source wall ink remains readable under saved wall labels at
  25-50% zoom.
- Wall labels no longer visually imply a larger footprint than the scan ink
  unless selected.
- Existing `thickness_mm` data is unchanged.

### B2. Add Calibration/Thickness Sanity Indicators

**Problem:** A visually wrong wall band can mean either bad label placement or
bad wall thickness scale. The UI currently does not distinguish those.

**Behavior:**

- Surface per-scene `px_per_mm` and effective wall-band pixel width in a compact
  debug/QA tooltip.
- Warn when fallback `FALLBACK_WALL_PX_PER_MM` is used on a scene with
  dimension references available.
- Warn when most saved walls have the same semantic thickness but the visual
  band is much wider/narrower than detected ink thickness.

**Acceptance:**

- A reviewer can tell whether "wall looks too thick" is a rendering scale issue
  or a label geometry issue.

### B3. Server Render Parity

**Problem:** The browser and MCP verification renders must agree. If the UI is
lighter but MCP renders still obscure ink, agents will keep fighting the wrong
visual.

**Behavior:**

- Add the same QA/ink-compare render style to server label rendering tools.
- Make `verify_label_placement` default to `ink_compare` for floorplan walls and
  openings.

**Acceptance:**

- `get_scene_view_with_labels(..., style="ink_compare")` or equivalent returns
  a crop where source ink is the primary visual and labels are overlays.

---

## 6. Workstream C — Opening Overlay and Placement Quality

### C1. Render Openings as Cuts, Not Opaque Blocks, in QA Mode

**Problem:** Attached `floorplan_opening` currently renders a white polygon,
colored polygon, hatch, inner graphics, and sometimes a glyph pill. On the
shared screenshot this hides nearby walls and room text.

**Implementation target:**

- `LabelGlyph` floorplan opening branch in `ui/src/pages/AnnotatePage.tsx`
- Server verification render equivalent.

**Behavior in QA/Agent View:**

- No filled white polygon by default.
- Use a thin outline plus short perpendicular end caps.
- Use door swing/window sash lines at low opacity.
- Hide hatch unless selected.
- Hide large label pill unless selected or sufficiently zoomed in.
- Keep "unattached opening" warning visible.

**Acceptance:**

- On house-22 EG/UG, opening quads no longer hide parent wall ink.
- Door swing remains readable when zoomed in.
- Window/door/garage kind remains distinguishable without a large filled block.

### C2. Add Opening Geometry Validators

**Problem:** Opening quads can be valid schema-wise but too deep, off wall, or
too long relative to the parent wall segment.

**Implementation target:**

- API/MCP validation helper, likely in or near `api/wall_topology.py`.
- Plan gate for `VERIFY_OPENINGS`.

**Checks:**

- Opening has exactly one `belongs_to` wall relation.
- Quad long axis is collinear with parent wall axis within tolerance.
- Quad center projects onto the parent wall segment or accepted extension
  tolerance.
- Quad depth is within a tolerance of parent semantic thickness converted to
  pixels, or is explicitly marked uncertain.
- Opening length does not exceed a configurable fraction of parent wall length
  unless kind is `garage_door`.
- Door swing arc does not cross an unrelated wall in obvious cases.

**Acceptance:**

- `VERIFY_OPENINGS` cannot pass solely because openings have parent relations.
- The gate reports specific defects like `opening_off_wall`,
  `opening_depth_mismatch`, `opening_too_long`, or `opening_overlaps_wall`.

### C3. MCP Helper: Normalize Floorplan Opening to Parent Wall

**Problem:** Agents create opening quads manually. Small errors in depth and
projection produce visual clutter and QA ambiguity.

**New helper candidate:**

`normalize_floorplan_opening(key, file, opening_id, mode="snap_to_parent")`

**Behavior:**

- Reads the opening and parent wall.
- Projects the opening's long-axis endpoints onto the wall centerline.
- Rebuilds the quad using the parent wall axis and semantic thickness.
- Returns before/after geometry and a verification crop.
- Applies only when it improves validator checks.

**Acceptance:**

- Agents can repair a slightly off opening without manually recomputing all
  four quad corners.
- The helper refuses unattached openings or ambiguous parent relations.

---

## 7. Workstream D — Wall Placement Helpers and Scoring

### D1. Prefer Move/Replace Over Add for Parallel Missing Regions

**Problem:** The live run sometimes correctly detected that a missing region
was a misaligned existing wall, not a new parallel wall. That decision required
manual comparison.

**Behavior:**

- Extend `propose_wall_edit` or add a companion helper that, for a missing
  region, compares:
  - add candidate wall;
  - move nearest existing wall;
  - replace nearest segment endpoints;
  - reject as non-wall.
- Return score deltas and "duplicate risk" based on nearby parallel walls.

**Acceptance:**

- For house-22 UG lower vertical return, helper recommends moving/replacing
  the existing wall instead of adding a duplicate.

### D2. Score With QA-Appropriate Parameters and Persist Them

**Problem:** The live run used different `score_walls` parameter sets
(`min_wall_px=16`, then `8`, different `tol_px`). This can be legitimate for
faint scans, but the plan evidence should make the chosen scoring profile
explicit.

**Behavior:**

- Add named scoring profiles:
  - `thick_wall_default`;
  - `faint_scan_thin_aware`;
  - `local_defect_tight`;
  - `final_scene`.
- Store the selected profile in score evidence.
- Gate final QA on the canonical final profile, not only local repair profiles.

**Acceptance:**

- A scene cannot pass final wall QA with only local/tight score evidence.
- Evidence summaries show which score profile was used.

### D3. Defect ID Stability

**Problem:** Logs show defect IDs such as `DEF-009` being reused after gate
reevaluation for a different residual region. This is confusing for agents and
humans.

**Behavior:**

- Once a defect ID is closed, do not reuse it for a new region.
- If a regenerated score region overlaps a fixed defect only partially, create
  a new defect with `related_defect_ids`.
- Store region fingerprint/hash to support matching.

**Acceptance:**

- Plan logs never show `ACT-DEF-009` closed, then reopened for unrelated
  coordinates.
- Human handoffs can track what was actually fixed.

---

## 8. Workstream E — Measurement and Regression Harness

### E1. Transcript Quality Report

Extend the context-bloat transcript analyzer with quality dimensions:

- plan-action count by scene;
- count of direct non-recommended scene actions;
- number of blocker defects opened/fixed/rejected;
- wall-score trend by scene;
- opening validator defects;
- final readiness outcome;
- export attempt count;
- total inline image bytes and image-handle count;
- full-state reads vs compact routing calls.

**Acceptance:**

- Comparing the bad baseline run and the current live run shows:
  - fewer premature export attempts;
  - more plan-action evidence;
  - no `accepted_incomplete` readiness pass;
  - whether non-EG work happened before EG completion.

### E2. Visual Regression Fixtures

Add screenshots/fixtures from house-22:

- EG overview at 25-50% zoom with current wall/opening overlay.
- EG same view after QA render changes.
- One tight opening crop.
- One wall-score defect crop.

**Acceptance:**

- Playwright or image snapshot tests confirm QA mode keeps source ink visible.
- Tests do not require committing large raw dataset images; use existing scene
  reconstruction or small cropped fixtures.

---

## 9. Implementation Order

1. **A1/A2:** enforce global scene priority in action tools.
   This prevents the agent from drifting to UG while EG remains open.
2. **C1/B1:** add QA render modes for walls and openings.
   This immediately improves human and agent visual inspection.
3. **C2:** add opening validators and wire them into `VERIFY_OPENINGS`.
4. **D3:** make defect IDs stable.
5. **D1/D2:** improve wall repair helper recommendations and score profiles.
6. **E1/E2:** add reporting and visual regression coverage.

---

## 10. Open Questions

- Should the hard groundfloor-first guard live only in MCP tools, or also in
  API routes so every client is forced to respect it?
- Should `Agent View` default to `qa` mode while the normal editor remains
  `semantic`, or should both default to `qa` until model-preview/export?
- Should opening quads remain full wall-depth geometry in data, or should data
  eventually store an opening axis plus parent wall relation and derive the
  quad for rendering?
- What final wall-score thresholds are realistic for faint architect scans
  like house-22 without encouraging agents to label furniture/dimension ink as
  walls?

---

## 11. Definition of Done

This follow-up is complete when:

- A fresh `house-22` run cannot request or start UG/DG/section/elevation
  geometry actions while EG required work is open.
- Required scene-plan completion still requires verified tasks and passed
  gates.
- Wall/opening overlays in Agent View and MCP verification crops no longer
  obscure the source ink.
- Opening QA catches off-wall, over-deep, and over-long quads.
- Final readiness remains blocked until wall, opening, measurement, and visual
  QA gates honestly pass.
- The transcript report can explain whether a run stayed in the intended
  scene order and how label quality changed, not only how much context it used.
