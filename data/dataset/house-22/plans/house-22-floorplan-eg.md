# Scene plan: house-22 / house-22-floorplan-eg.jpg

Status: draft
Template: scene-plan-v2
Schema: scene-plan-state-v1
Scene tag: grundriss
Level/orientation: eg
Created by: codex
Created at: 2026-06-02T05:36:09+00:00
Last updated: 2026-06-02T05:36:09+00:00

## 1. Current State

- Summary: Scene plan created; analysis not yet complete.
- Label counts: none
- Score walls: not recorded
- Score measurements: not recorded
- Topology: not recorded
- Open blockers: none

## 2. Open Defects

- No open defects.

## 3. Next Actions

- **CLASSIFY_SCENE** (task): Work only on CLASSIFY_SCENE: Set scene tag and floor level. Produce analysis evidence, at most one edit, then verification evidence.
- **ANALYZE_SILHOUETTE** (task): Work only on ANALYZE_SILHOUETTE: Describe outer masses, excluded non-walls, and endpoint rules. Produce analysis evidence, at most one edit, then verification evidence.
- **TRACE_OUTER_WALLS** (task): Work only on TRACE_OUTER_WALLS: Place outer structural walls before openings. Produce analysis evidence, at most one edit, then verification evidence.
- **VERIFY_OUTER_TOPOLOGY** (task): Work only on VERIFY_OUTER_TOPOLOGY: Verify outer wall topology and wall score. Produce analysis evidence, at most one edit, then verification evidence.
- **TRACE_INTERIOR_WALLS** (task): Work only on TRACE_INTERIOR_WALLS: Place interior structural walls. Produce analysis evidence, at most one edit, then verification evidence.

## 4. Task Board

- [ ] **CLASSIFY_SCENE** Set scene tag and floor level — `todo`; gates: SCENE_CLASSIFIED=pending
- [ ] **ANALYZE_SILHOUETTE** Describe outer masses, excluded non-walls, and endpoint rules — `todo`; gates: HAS_SILHOUETTE_HYPOTHESIS=pending
- [ ] **TRACE_OUTER_WALLS** Place outer structural walls before openings — `todo`; gates: WALLS_EXIST=pending
- [ ] **VERIFY_OUTER_TOPOLOGY** Verify outer wall topology and wall score — `todo`; gates: TOPOLOGY_REVIEWED=pending, WALL_SCORE_REVIEWED=pending
- [ ] **TRACE_INTERIOR_WALLS** Place interior structural walls — `todo`; gates: WALLS_EXIST=pending
- [ ] **VERIFY_INTERIOR_TOPOLOGY** Verify interior wall topology and score — `todo`; gates: TOPOLOGY_REVIEWED=pending, WALL_SCORE_REVIEWED=pending
- [ ] **PLACE_OPENINGS** Place doors, windows, passages, and garage doors on parent walls — `todo`; gates: OPENINGS_HAVE_PARENT_WALL=pending
- [ ] **VERIFY_OPENINGS** Verify opening relations and on-wall placement — `todo`; gates: OPENINGS_HAVE_PARENT_WALL=pending, OPENINGS_ON_WALL=pending
- [ ] **READ_DIMENSIONS** Inspect and label readable dimension chains after walls/openings — `todo`; gates: DIMENSIONS_REVIEWED=pending
- [ ] **VERIFY_MEASUREMENTS** Verify dimension chains, reference dims, and wall/opening tick alignment — `todo`; gates: MEASUREMENTS_REVIEWED=pending
- [ ] **FINAL_QA** Run final scene QA and update remaining blockers — `todo`; gates: VISUAL_VERIFY_EXISTS=pending, NO_BLOCKER_DEFECTS=pending

## 5. Evidence

- No evidence recorded.

## 6. Decision Log

- No decisions logged.

## 7. Final Verification

- Final QA not verified; see open defects and next actions.
