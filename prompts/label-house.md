# Label house `$key` end-to-end

$spec_notice

You are driving the bim-database annotation workflow for one house. Your
goal: produce an export-ready labeled house. Open the bim-database SPA
at http://localhost:12500/$key alongside this session — your writes
appear there immediately.

## Tools you'll use (from the bim-database MCP server)

| Phase | Primary tools                                                                |
|-------|------------------------------------------------------------------------------|
| W0    | get_house, get_scene_view, set_scene_tag, set_scene_orientation, set_scene_level |
| W1    | get_scene_view, upsert_label (height_mark), set_house_facts                  |
| W2    | get_scene_view, add_reference_dim, upsert_label (wall), set_house_facts      |
| W3    | get_scene_view, set_house_facts                                              |
| W4    | get_scene_view, add_reference_dim, recompute_homography                      |
| plan  | create_scene_plan_state_from_template, get_scene_plan_status, get_scene_plan_next_action |
| any   | get_workflow_state, get_recommended_next_action, validate_export_readiness, export_house |

## Context-bloat policy

Quality remains more important than saving tokens: request inline images
whenever the current decision needs pixels. But keep context focused:

1. Start routing with `get_house_context_summary` and
   `get_scene_context_summary`, not full house/label/plan dumps.
2. Use one low-detail overview per scene to choose work regions.
3. Use `verify_label_placement` for routine verify-after-write. It auto-crops
   around the edited label and reports numeric offset hints.
4. Use full `get_scene_view_with_labels` only for multi-label/global topology
   QA, final scene review, or when a crop lacks enough context.
5. For large overview/debug renders in runtimes that can inspect file
   handles, pass `image_delivery="handle"` or `"auto"`; pass
   `image_delivery="inline"` when the model must see pixels now.
6. After each scene/phase, call `write_handoff_summary` with open blockers,
   evidence refs, quality metrics, and next action. The parent should keep
   that summary, not the full visual transcript.
7. If a bounded QA result says `truncated=true`, use the returned counts and
   fetch/fix the highest-priority visible blockers first; do not treat
   truncation as a pass.

## Scene-plan gate (REQUIRED before geometry labels)

Once W0 has classified a scene and the crop/bounding box exists, every
geometry-bearing scene (`grundriss`, `ansicht`, `schnitt`) MUST have a
structured scene plan before any subagent places geometry labels.

For each such scene:

1. `get_scene_plan_status(key="$key", file=<scene>)`.
2. If `data.exists == false`, call
   `create_scene_plan_state_from_template(key="$key", file=<scene>,
   scene_tag=<scene_tag>, level_or_orientation=<level-or-orientation>)`.
   This writes both `*.plan.json` and the synced `plan.md`.
3. Scene subagents work only through the plan loop:
   `get_scene_plan_next_action` → `start_scene_plan_action` →
   analyze/edit/verify → `add_scene_plan_evidence` /
   `record_scene_plan_attempt` → `finish_scene_plan_action` →
   `evaluate_scene_plan_gates` → repeat.
4. A scene is NOT complete until every required task is `verified` and
   `get_scene_plan_status(...).data.required_complete == true`. A plan
   that merely exists, a `draft` plan, or a plan whose required tasks are
   `accepted_incomplete` is not an honest pass.
5. If a scene has labels but required tasks are not verified, treat the
   labels as legacy/unverified. Continue through the plan loop until the
   required tasks verify; do not use minimal labels or accepted-incomplete
   waivers to satisfy export.

`get_workflow_state` and `validate_export_readiness` intentionally keep
`Wgeo` pending for geometry-bearing scenes whose plan state is missing or
whose required plan tasks are incomplete, even if labels exist. Labels
without the completed plan loop are not an honest completion.

## Ground-floor-first gate

After W0 has classified scenes, finish every EG/Ground-floor Grundriss
scene plan before touching non-groundfloor geometry, sections, elevations,
or export. In practice:

1. Call `get_recommended_next_action(key="$key")`.
2. If it returns a `Wgeo` action for an EG scene, do exactly that action.
3. Repeat until all EG scene plans report `required_complete=true` through
   verified required tasks, not accepted-incomplete waivers.
4. Only then continue to UG/DG plans, sections, elevations, W4 calibration,
   and export.

## Resources to read first

- `bim-db://schema/scene_labels` — Label types + geometry shapes ([x,y] arrays)
- `bim-db://docs/grid-coordinates` — How to read the grid overlay

## Step 0 — STAMP YOUR RUN (per §G3-6, before any other write)

The bim-database SPA shows a `🤖 Agent` chip on the dataset card
when `house_facts.workflow.driven_by == "bim-agent"`. Reviewers use
the chip to find agent-labeled houses for spot-checking. STAMP THIS
FIRST, before any other tool call — if you crash mid-run, the partial
result is still attributable to you.

```
set_house_facts(key="$key", patch={
  "workflow": {
    "driven_by": "bim-agent",
    "driven_by_run_id": "<your-run-id-or-iso-timestamp>",
    "driven_by_started_at": "<iso-timestamp>"
  }
})
```

## Operating loop

```
while true:
    action = get_recommended_next_action(key="$key")
    if action.done:
        break
    do exactly action.suggested_tool/action.suggested_args
    if action.phase == "Wgeo":
        stay on that one scene-plan action until it has
        start_scene_plan_action -> evidence/attempt -> finish -> evaluate-gates
validate_export_readiness
export_house only if ready=true
```

## Core principles (DO NOT SKIP)

1. **Always look at the grid before naming coordinates.** Call
   `get_scene_view` (with `region=` zoom for precision) before EVERY
   label. The labels in the overlay show source pixels — feed them
   directly into tool calls.
2. **Honest values.** If you can't read a dim number confidently, set
   `status="uncertain"` on the label. Never invent.
3. **One reference dim at a time.** Add → call `recompute_homography`.
   If RMS > 8 px, delete it and try a more-orthogonal candidate.
4. **Never edit existing human work.** Check
   `get_house_facts.workflow.touched_by` before overwriting; if a human
   has touched the house, halt.
5. **Honest reporting.** When you halt or finish, call `dump_run_summary`
   so the developer sees what you did.
6. **Labels before facts.** For W1 + W2 specifically: drop the
   geometry-bearing labels (height_mark, dimensioned_distance with
   is_reference) BEFORE setting facts. Server-side derivation will
   populate facts automatically. Setting facts without labels makes
   the SPA's overlay rendering go blank — reviewers can't trust it.
7. **Stamp your run** (Step 0 above).
8. **VERIFY EVERY GEOMETRY WRITE (per §H5).** After every
   `upsert_label` / `add_reference_dim` / `update_label_attrs`, call
   `get_scene_view_with_labels(key, file, region=<tight crop>)` and
   check the rendered stroke / dot / chip sits on the feature you
   meant. The agent's single biggest historical failure mode was
   placing labels off-feature and never noticing — the verify view is
   the fix. Budget: 3 placement attempts per label; if the third still
   misses, `set_label_status(..., "uncertain")` and move on.
9. **PLAN BEFORE LABELS.** Do not place walls, openings, component lines,
   height marks, or reference dimensions on a geometry-bearing scene until
   `get_scene_plan_status(...).data.exists == true` for that scene and you
   have claimed the relevant plan action. If a prior run left labels but
   no verified plan, create/repair the plan and verify through the plan loop.
10. **EG BEFORE EVERYTHING ELSE.** If any EG Grundriss plan has
    `required_complete=false`, do not label UG/DG, sections, elevations,
    or W4 calibration. Finish EG final QA as verified first.
11. **NO BASELINE WAIVERS.** Do not mark required tasks
    `accepted_incomplete` to reach export. If you cannot verify a required
    task, leave it open or `blocked_external` with evidence; export must
    remain blocked.

Start now: call `get_workflow_state(key="$key")` and follow the
appropriate phase playbook.
