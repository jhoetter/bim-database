# Scene plan: house-22 / house-22-floorplan-eg.png

Status: draft
Template: scene-plan-v2
Schema: scene-plan-state-v1
Scene tag: grundriss
Level/orientation: eg
Created by: claude-opus-eg-labeler
Created at: 2026-06-02T15:07:20+00:00
Last updated: 2026-06-02T15:15:15+00:00

## 1. Current State

- Summary: Scene plan created; analysis not yet complete.
- Label counts: none
- Score walls: not recorded
- Score measurements: not recorded
- Topology: not recorded
- Current findings: count=0, blockers=0, warnings=0, clusters=0
- Open blockers: none

## 2. Open Defects / Current Finding Clusters

- No current finding clusters.

## 3. Open Defects / Action History

- No open defects.

## 4. Next Actions

- **TRACE_OUTER_WALLS** (task): Work only on TRACE_OUTER_WALLS: Place outer structural walls before openings. Produce analysis evidence, at most one edit, then verification evidence.
- **VERIFY_OUTER_TOPOLOGY** (task): Work only on VERIFY_OUTER_TOPOLOGY: Verify outer wall topology and wall score. Produce analysis evidence, at most one edit, then verification evidence.
- **TRACE_INTERIOR_WALLS** (task): Work only on TRACE_INTERIOR_WALLS: Place interior structural walls. Produce analysis evidence, at most one edit, then verification evidence.
- **VERIFY_INTERIOR_TOPOLOGY** (task): Work only on VERIFY_INTERIOR_TOPOLOGY: Verify interior wall topology and score. Produce analysis evidence, at most one edit, then verification evidence.
- **PLACE_OPENINGS** (task): Work only on PLACE_OPENINGS: Place doors, windows, passages, and garage doors on parent walls. Produce analysis evidence, at most one edit, then verification evidence.

## 5. Task Board

- [x] **CLASSIFY_SCENE** Set scene tag and floor level — `verified`; gates: SCENE_CLASSIFIED=passed
- [x] **ANALYZE_SILHOUETTE** Describe outer masses, excluded non-walls, and endpoint rules — `verified`; gates: HAS_SILHOUETTE_HYPOTHESIS=passed
- [ ] **TRACE_OUTER_WALLS** Place outer structural walls before openings — `todo`; gates: WALLS_EXIST=pending
- [ ] **VERIFY_OUTER_TOPOLOGY** Verify outer wall topology and wall score — `todo`; gates: TOPOLOGY_REVIEWED=pending, WALL_SCORE_REVIEWED=pending
- [ ] **TRACE_INTERIOR_WALLS** Place interior structural walls — `todo`; gates: WALLS_EXIST=pending
- [ ] **VERIFY_INTERIOR_TOPOLOGY** Verify interior wall topology and score — `todo`; gates: TOPOLOGY_REVIEWED=pending, WALL_SCORE_REVIEWED=pending
- [ ] **PLACE_OPENINGS** Place doors, windows, passages, and garage doors on parent walls — `todo`; gates: OPENINGS_HAVE_PARENT_WALL=pending
- [ ] **VERIFY_OPENINGS** Verify opening relations and on-wall placement — `todo`; gates: OPENINGS_HAVE_PARENT_WALL=pending, OPENINGS_ON_WALL=pending
- [ ] **READ_DIMENSIONS** Inspect and label readable dimension chains after walls/openings — `todo`; gates: DIMENSIONS_REVIEWED=pending
- [ ] **VERIFY_MEASUREMENTS** Verify dimension chains, reference dims, and wall/opening tick alignment — `todo`; gates: MEASUREMENTS_REVIEWED=pending
- [ ] **FINAL_QA** Run final scene QA and update remaining blockers — `todo`; gates: VISUAL_VERIFY_EXISTS=pending, NO_BLOCKER_DEFECTS=pending

## 6. Evidence

| ID | Mode | Kind | Tool | Summary |
|---|---|---|---|---|
| EV-001 | analysis | scene_view | `get_scene_view+refine_wall+building_silhouette` | EG floorplan analysis. Scene tag=grundriss, level=eg already set; image 5100x3445. North arrow top-right; "EFH = +-0.00 / 843.80" datum text near center (informational only, no height_mark on grundriss). MASS DECOMPOSITION: (1) Main house = stepped/L-shaped block. Confirmed outer edges via refine_wall: LEFT outer wall vertical x~1462 (conf 1.0), TOP outer wall horizontal y~1150 (conf 0.92), RIGHT outer wall of main block vertical x~2153 (conf 0.84) spanning y~1150-1620. Bottom boundary ~y1655 with a thick horizontal wall from x1462 rightward, interrupted by door/passage openings; lower-left the Wohnen/Essen room and Balkon (Balkon NOT a wall) project below. Bottom-right has a NOTCH (the footprint steps in). Interior rooms: DU/WC 103, Kuche 104, Diele 115, zum DG (stair), Gast 113, Wohnen/Essen 105. (2) Detached GARAGE far right (GH=843.20), drawn largely with thin/dashed lines around src x~2300-2950 — a SEPARATE closed polygon with a real gap to the house; do NOT join. CLOCKWISE exterior corner seq for main block (approx): TL(1462,1150) -> TR(2153,1150) -> down right wall -> step at notch near y~1620 -> bottom wall ~y1655 -> back to left wall -> up to TL. Walls are faint single-line freehand (thickness 3-8px). Endpoint reasons: corners=intersection of fitted outer lines; openings (doors to Diele/Wohnen, garage door) are children of walls. SITE noise (Emattweg street, Grenze boundaries, trees, Stellplatz, Zufahrt, title block) IGNORED. |
| EV-002 | analysis | human_note | `` | grundriss + eg already set on scene; confirmed via get_scene_meta. |
| EV-003 | analysis | human_note | `` | Mass decomposition written: L-shaped main house with notched bottom-right + separate detached garage; Balkon excluded; clockwise corner sequence + endpoint reasons recorded. |
| EV-004 | verification | label_view | `connect_corners+upsert_label+grid-with-labels` | Placed 4 outer walls of main house as a closed rectangle via connect_corners (intersection corners). Corners: TL(1474,1139) TR(2613,1156) BR(2612,1664) BL(1459,1649). All edges refine_wall conf 0.88-1.0. Labels: TOP lab-49a9351b25, EAST lab-d86850efed (x2613, right of Gast), BOTTOM lab-83c95f2e41, LEFT lab-9d453f815e. QA overlay confirms orange shell sits on thick outer ink and encloses all rooms (DU/WC, Kuche, Diele, zum DG, Gast, Wohnen). Corrected an initial error where x2283 (Diele/Gast partition) was mistaken for east wall; true east wall is x2613. |

## 7. Decision Log

- No decisions logged.

## 8. Final Verification

- Final QA not verified; see open defects and next actions.
