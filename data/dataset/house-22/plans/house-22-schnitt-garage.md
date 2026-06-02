# Scene plan: house-22 / house-22-schnitt-garage.jpg

Status: draft
Template: scene-plan-v2
Schema: scene-plan-state-v1
Scene tag: schnitt
Level/orientation: garage
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

- **CLASSIFY_SCENE** (task): Work only on CLASSIFY_SCENE: Set scene tag and orientation if visible. Produce analysis evidence, at most one edit, then verification evidence.
- **READ_HEIGHTS** (task): Work only on READ_HEIGHTS: Read height marks, datum, and roof facts. Produce analysis evidence, at most one edit, then verification evidence.
- **TRACE_COMPONENTS** (task): Work only on TRACE_COMPONENTS: Trace section component lines. Produce analysis evidence, at most one edit, then verification evidence.
- **PLACE_VIEW_OPENINGS** (task): Work only on PLACE_VIEW_OPENINGS: Place visible section openings/components. Produce analysis evidence, at most one edit, then verification evidence.
- **CALIBRATE_SCENE** (task): Work only on CALIBRATE_SCENE: Add reference dimensions and recompute homography. Produce analysis evidence, at most one edit, then verification evidence.

## 4. Task Board

- [ ] **CLASSIFY_SCENE** Set scene tag and orientation if visible — `todo`; gates: SCENE_CLASSIFIED=pending
- [ ] **READ_HEIGHTS** Read height marks, datum, and roof facts — `todo`; gates: HEIGHTS_REVIEWED=pending
- [ ] **TRACE_COMPONENTS** Trace section component lines — `todo`; gates: STRUCTURE_EXISTS=pending
- [ ] **PLACE_VIEW_OPENINGS** Place visible section openings/components — `todo`; gates: VIEW_OPENINGS_REVIEWED=pending
- [ ] **CALIBRATE_SCENE** Add reference dimensions and recompute homography — `todo`; gates: CALIBRATION_REVIEWED=pending
- [ ] **FINAL_QA** Run final section QA — `todo`; gates: VISUAL_VERIFY_EXISTS=pending, NO_BLOCKER_DEFECTS=pending

## 5. Evidence

- No evidence recorded.

## 6. Decision Log

- No decisions logged.

## 7. Final Verification

- Final QA not verified; see open defects and next actions.
