# Scene plan: house-22 / house-22-floorplan-dg.png

Status: draft
Template: scene-plan-v2
Schema: scene-plan-state-v1
Scene tag: grundriss
Level/orientation: dg
Created by: claude-dg-worker
Created at: 2026-06-02T15:07:30+00:00
Last updated: 2026-06-02T15:15:14+00:00

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
| EV-001 | analysis | scene_view | `` | Probe: DG analysis evidence to test whether evidence writes are gated by groundfloor-first. |
| EV-002 | analysis | human_note | `` | SILHOUETTE/MASS DECOMPOSITION (DG, refine_wall confidence 1.0 unless noted). DG is a single stepped (L/notched) mass; Flachdach Garage to the right is a SEPARATE structure (not labeled as part of house, separate roof). Outer shell clockwise from top-left: TL(1548,889) -> top wall y~889-909 (slight tilt, Schlafen seg y889, Bad seg y909) -> TR(2688,909) -> right/east wall x~2688 down to y~1430 (Bad east) -> step inward -> Kind2 east wall x~2625 from y1622 to BR(2612,1887) -> bottom/south wall y~1887 left to BL(1648,1887) -> lower-west wall x~1648 up to step ledge (1648,1424) -> step ledge left to (1548,1424) -> upper-west wall x~1548 up to TL. NON-WALLS (exclude): 2m-Linie + Dachschraege dashed lines & roof-slope hatching around footprint = dashed_projection; site GRENZE boundary lines (top-left diagonal); trees (asterisks bottom-right); title block bottom-right; furniture (beds/tub/sink/toilet); DFF 94/1.40 = roof windows (Dachflaechenfenster) -> openings on roof not walls, treat as uncertain/skip. ENDPOINT RULES: every outer corner = intersection of adjacent refined edges; interior walls (Schlafen/Bad/Flur/Kind dividers) stop at T-junctions with outer shell. Right side has a notch step between Bad(east x2688) and Kind2(east x2625); left side has inward step at y~1424 (upper x1548 -> lower x1648). |
| EV-003 | verification | score_walls | `score_walls` | Outer shell (8 walls) score_walls: precision=0.92, recall=0.689, f1=0.788, off_ink_segments=0. All outer walls on ink; low recall is uncovered INTERIOR walls (expected before interior pass). Visual QA: shell traces stepped footprint cleanly with both steps. |

## 7. Decision Log

- No decisions logged.

## 8. Final Verification

- Final QA not verified; see open defects and next actions.
