# Scene plan: house-22 / house-22-floorplan-ug.png

Status: draft
Template: scene-plan-v2
Schema: scene-plan-state-v1
Scene tag: grundriss
Level/orientation: ug
Created by: bim-agent
Created at: 2026-06-02T15:06:43+00:00
Last updated: 2026-06-02T15:14:59+00:00

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

- **CLASSIFY_SCENE** (task): Work only on CLASSIFY_SCENE: Set scene tag and floor level. Produce analysis evidence, at most one edit, then verification evidence.
- **ANALYZE_SILHOUETTE** (task): Work only on ANALYZE_SILHOUETTE: Describe outer masses, excluded non-walls, and endpoint rules. Produce analysis evidence, at most one edit, then verification evidence.
- **TRACE_OUTER_WALLS** (task): Work only on TRACE_OUTER_WALLS: Place outer structural walls before openings. Produce analysis evidence, at most one edit, then verification evidence.
- **VERIFY_OUTER_TOPOLOGY** (task): Work only on VERIFY_OUTER_TOPOLOGY: Verify outer wall topology and wall score. Produce analysis evidence, at most one edit, then verification evidence.
- **TRACE_INTERIOR_WALLS** (task): Work only on TRACE_INTERIOR_WALLS: Place interior structural walls. Produce analysis evidence, at most one edit, then verification evidence.

## 5. Task Board

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

## 6. Evidence

| ID | Mode | Kind | Tool | Summary |
|---|---|---|---|---|
| EV-001 | analysis | scene_view | `building_silhouette+get_scene_view` | UG mass decomposition (vision read, CV priors noisy). L-shaped house + detached garage. Main house outer shell TL~(1490,1185); top wall y~1185 to x~2600; left wall x~1490 from y~1185 to y~2210; bottom of left/main mass y~2210. Upper-right room (Zuluft/WA-FE-W) top-right corner ~(2600,1185), right edge x~2600 down to step at y~1520. Detached GARAGE Geraete-029 separate rectangle ~x[2620,3080] y[1545,2285] with real gap (do not join). Interior horizontal division ~y=1700/1710 separating top rooms (Vorrat034/Vorraum033/DU-WC003/Technik026) from bottom rooms (Arbeiten014/Gast013). Interior vertical walls ~x=1540, x=2020, x=2230, x=2480. Terrasse (bottom) and trees/Emattweg/Grenze are NOT walls. |
| EV-002 | verification | score_walls | `score_walls+get_scene_view_with_labels` | 15 walls placed and visually verified on full-res scene. score_walls (min16 tol18 close82): precision 0.769 recall 0.678 f1 0.721. Outer shell of main L-house + detached garage rectangle + 7 interior walls all land on ink per qa overlay. Garage-left wall faint -> uncertain. Remaining missing_regions: site Grenze line at crop x1380 (NOT building, ignore) + thick-wall band edges. off_ink: garage-left 0.12 (uncertain, accepted). |
| EV-003 | analysis | score_measurements | `get_scene_view+score_measurements` | DIMENSIONS honest-incomplete. The legible horizontal dim chains at top of sheet (segs 2.30/1.90/3.90/2.65, overall 7.56) measure SITE frontage (Emattweg/Grenze) above the building, not building walls; per task instructions site-plan annotation is ignored. The building's own dimension chains are faint freehand pencil intermixed with site lines; interior '885' labels are room/wall widths but tick-to-value mapping cannot be established tick-precise without fabrication. score_measurements: n_dims=0 (no fabricated dims). Decision: do NOT invent building dims from site chains; mark dimensions incomplete-uncertain rather than place wrong labels. Walls remain the verified deliverable (15 walls, f1 0.72). |

## 7. Decision Log

- No decisions logged.

## 8. Final Verification

- Final QA not verified; see open defects and next actions.
