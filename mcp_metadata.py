"""MCP resources and prompt registration for bim-database."""
from __future__ import annotations

import json
from pathlib import Path


def register_metadata(mcp, *, server_version: str) -> None:
    SERVER_VERSION = server_version
    # ── §5.10 MCP resources (read-only context) ──────────────────────────────


    @mcp.resource("bim-db://version")
    def resource_version() -> str:
        return json.dumps({
            "server_version": SERVER_VERSION,
            "api_base": API_BASE,
            "tool_count": "phase-A subset (4 tools; Phase B adds 18)",
        }, indent=2)


    @mcp.resource("bim-db://schema/scene_labels")
    def resource_scene_labels_schema() -> str:
        p = Path(__file__).parent / "schema" / "scene_labels.schema.json"
        return p.read_text() if p.exists() else "{}"


    @mcp.resource("bim-db://schema/intake_manifest")
    def resource_intake_manifest_schema() -> str:
        p = Path(__file__).parent / "schema" / "intake_manifest.schema.json"
        return p.read_text() if p.exists() else "{}"


    @mcp.resource("bim-db://docs/grid-coordinates")
    def resource_grid_coordinates() -> str:
        return """# Grid coordinate frame

    Every image returned by `get_scene_view` or `get_pdf_page_view` carries
    a three-tier grid overlay. The coordinate labels in the margins ALWAYS
    reference SOURCE pixels — never the rendered output pixels, never any
    internal cache scale. You can feed any label-frame coordinate you read
    off the grid directly into a tool call like `upsert_label`.

    Tiers (from bold to faint):

    | Tier   | Cell size                            | Use for                                 |
    |--------|--------------------------------------|-----------------------------------------|
    | broad  | image_long_edge / 10 (~200–500 px)   | scoping which quadrant a feature is in  |
    | finer  | image_long_edge / 50 (~40–100 px)    | naming a polygon vertex ±25 px          |
    | detail | image_long_edge / 200 (~10–25 px)    | snap-style precision; no labels (noise) |

    To zoom into a region, call `get_scene_view(file=..., region="x0,y0,x1,y1")`.
    The labels in the zoom still read in source-pixel coords — so a vertex
    you identify in a zoom at (1240, 670) maps to (1240, 670) in the
    un-cropped scene without any translation.

    Don't trace coordinates across the dense grid if you can avoid it
    (issue #10): vision-LLMs are strong at "that feature, there" and weak at
    "row 1797, col 232". Instead:

      - Point in the crop's LOCAL frame. Call
        `resolve_scene_point(point=[lx,ly], region=..., frame="crop")` with the
        point in the zoom's own pixel frame (0..w, 0..h). The server maps it
        back to source pixels for you.
      - Snap to the real mark. `resolve_scene_point(..., snap=true)` snaps the
        point to the nearest drawn feature (tick-triangle, line, dim arrow)
        within `snap_radius_px`. Place approximately; the server lands you on
        the feature. Feed the returned `source_point` into upsert_label /
        add_reference_dim.
      - Correct by a delta. After a write, `verify_label_placement` reports
        `offset_px` — the vector from your anchor to the nearest feature — so
        you nudge by an exact amount instead of eyeballing.
    """


    # ── §5.11 MCP prompts (canonical phase playbooks) ────────────────────────
    # Per tracker §8 decision 7: prompts are the single source of truth for
    # how an agent works the workflow. The house-labeling skill in bim-agent
    # is a thin pointer that says "for phase X, follow this prompt".


    @mcp.prompt(name="label-house")
    def prompt_label_house(key: str) -> str:
        return f"""# Label house `{key}` end-to-end

    You are driving the bim-database annotation workflow for one house. Your
    goal: produce an export-ready labeled house. Open the bim-database SPA
    at http://localhost:12500/{key} alongside this session — your writes
    appear there immediately.

    ## Tools you'll use (from the bim-database MCP server)

    | Stage | Primary tools                                                                |
    |-------|------------------------------------------------------------------------------|
    | inventory | get_house, get_pdf_page_view, extract_scenes, split_scene, set_scene_tag, set_scene_level |
    | floorplans | create_scene_plan_state_from_template, get_scene_plan_next_action, upsert_label, score_walls, score_measurements |
    | sections | create_scene_plan_state_from_template, get_scene_plan_next_action, height_mark/component_line labels, add_reference_dim |
    | elevations | create_scene_plan_state_from_template, get_scene_plan_next_action, view_opening/component_line labels, add_reference_dim |
    | review | list_anomalies, validate_export_readiness, export_house |
    | scene | create_scene_plan_state_from_template, get_scene_plan_next_actions, add_scene_plan_evidence, evaluate_scene_plan_gates |
    | QA    | get_scene_view_with_labels, verify_label_placement, wall_topology_qa, wall_continuity_check, ambiguous_line_context |
    | any   | get_workflow_state, get_recommended_next_action, validate_export_readiness, export_house |

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
    set_house_facts(key="{key}", patch={{
      "workflow": {{
        "driven_by": "bim-agent",
        "driven_by_run_id": "<your-run-id-or-iso-timestamp>",
        "driven_by_started_at": "<iso-timestamp>"
      }}
    }})
    ```

    ## Operating loop

    ```
    state = get_workflow_state(key="{key}")
    while not state.exportable:
        phase = state.next_phase
        follow the prompt named for the stage:
          inventory -> inventory-classify-scenes
          floorplans -> floorplan-scene-pass
          sections -> section-scene-pass
          elevations -> elevation-scene-pass
        state = get_workflow_state(key="{key}")
    validate_export_readiness then export_house
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
    6. **Labels before facts.** Drop geometry-bearing labels before setting
       derived facts. Server-side derivation should populate facts from labels
       where possible. Setting facts without labels makes the SPA's overlay
       rendering go blank — reviewers can't trust it.
    7. **Stamp your run** (Step 0 above).
    8. **VERIFY EVERY GEOMETRY WRITE (per §H5).** After every
       `upsert_label` / `add_reference_dim` / `update_label_attrs`, call
       `get_scene_view_with_labels(key, file, region=<tight crop>)` and
       check the rendered stroke / dot / chip sits on the feature you
       meant. The agent's single biggest historical failure mode was
       placing labels off-feature and never noticing — the verify view is
       the fix. Budget: 3 placement attempts per label; if the third still
       misses, `set_label_status(..., "uncertain")` and move on.
    9. **Scene plan first.** Once scenes are classified, every scene subagent
       starts with `create_scene_plan_state_from_template` / `get_scene_plan_state`
       and keeps tasks, evidence, defects, and rendered Markdown current. Work in
       Analysis View (read/prose/evidence), Editing View (one write), then
       Verification View (labels rendered + scores), followed by
       `evaluate_scene_plan_gates`. Failed verification creates a defect and
       returns to analysis before another edit.
    10. **Walls before openings, with endpoint reasons.** On Grundriss
       scenes, identify the outer silhouette/masses first, place continuous
       walls through door/window symbols, run `wall_topology_qa` and
       `wall_continuity_check`, then place openings. Every wall endpoint
       must be justified as corner, T-junction, real endpoint, separate-mass
       boundary, or uncertainty; an opening alone is not a valid endpoint.
    11. **Scene-subagent final report.** When a scene worker finishes, report
        the plan path, final task states, label counts by type, wall/topology
        scores, measurement score, and unresolved uncertain/blocking items.

    Start now: call `get_workflow_state(key="{key}")` and follow the
    appropriate stage playbook.
    """


    @mcp.prompt(name="inventory-classify-scenes")
    def prompt_inventory_classify_scenes(key: str) -> str:
        return f"""# Inventory · extract and classify every scene of `{key}`

    Goal: every source drawing is represented as one extracted scene, every
    scene has a non-null `scene_tag`, and every Grundriss has `scene_level`.
    Ansicht/Schnitt orientation is optional unless clearly visible.

    ## DEFAULT MAPPING (per §G3-1)

    Each scene's manifest carries an extraction-time `kind` (different
    vocabulary). Start from this default → only override with explicit
    evidence:

    | manifest.kind | default scene_tag | when to override                                    |
    |---------------|-------------------|-----------------------------------------------------|
    | `floorplan`   | `grundriss`       | almost never — confirm by reading the title block   |
    | `elevation`   | `ansicht`         | almost never                                        |
    | `section`     | `schnitt`         | almost never                                        |
    | `detail`      | **`sonstiges`**   | only set `schnitt` if you can point at VISIBLE evidence: floor heights spanning multiple stories, cutaway hatching across the FULL building width, OR a title-block label like "Schnitt A-A". A close-up of a roof corner or eave is NOT a Schnitt — it's `sonstiges`. |

    This default mapping prevents the most common inventory mis-tag (a detail
    crop tagged `schnitt` because the cutaway-ish lines looked sectional
    at a glance).

    ## Steps

    For each scene returned by `get_house(key="{key}").drawings`:

    1. `get_scene_view(key="{key}", file=<file>, tiers="broad")` — overview only.
    2. Look up the default scene_tag from the table above based on
       `drawing.kind`. That's your starting answer.
    3. Confirm by reading the title-block text (usually bottom-right):
       "EG-Grundriss", "Süd-Ansicht", "Schnitt A-A" — best ground truth.
       Override the default only when the title block contradicts it.
    4. `set_scene_tag(key="{key}", file=<file>, tag=<tag>)`.
    5. **scene_orientation: OPTIONAL.** If
       Ansicht/Schnitt with a CLEAR cardinal face (elevation labeled
       "Süd"/"South"; compass mark visible AND the wall it points to is
       the wall this scene shows), call `set_scene_orientation(...)`.
       **If unclear, leave null — DO NOT GUESS.** Per §H3 missing
       orientation does NOT block inventory; it surfaces as a `warning`
       in `list_anomalies` so a human reviewer knows to spot-check.
       Detail crops never have a cardinal orientation; leave null always.
    6. If Grundriss: identify the floor level (kg/ug/eg/og/dg/spitzboden)
       from the title text or by elimination (count the floors). Call
       `set_scene_level(...)`. If genuinely unclear, leave null.

    ## Heuristics for ambiguous cases

    - A drawing with both plan and section (split sheet) → tag as the
      dominant element; flag with `dump_run_summary` for human review.
    - "EG" is the ground floor (Erdgeschoss), "OG" upper, "DG" attic,
      "KG" basement (Kellergeschoss).
    - Cardinal directions in German labels: Nord/Süd/Ost/West.

    ## Exit

    `get_workflow_state(key="{key}")["phases"]["inventory"]["status"] == "done"`

    If inventory still has blockers after one full pass, re-call `get_scene_view`
    on the blocked scene with `region=` zoom to inspect the title block.
    """


    @mcp.prompt(name="floorplan-scene-pass")
    def prompt_floorplan_scene_pass(key: str) -> str:
        return f"""# Floorplans · label Grundriss scenes for `{key}`

    Goal: every `grundriss` scene is terminal or accepted incomplete. Work
    only on Grundriss scenes in this stage; do not start Schnitt/Ansicht
    workers until `get_workflow_state(...).phases.floorplans.status == "done"`.

    ## Order inside each Grundriss

    1. Create or resume the structured plan state.
    2. Analyze the full scene and write the silhouette/mass hypothesis.
    3. Place outer and interior walls first; verify each write.
    4. Run wall topology/continuity and wall score gates.
    5. Place doors/windows/passages only after parent walls exist.
    6. Label dimension chains/reference dimensions third, then run measurement QA.
    7. Finish the scene-plan action and evaluate terminality.

    Use `get_recommended_next_action(key="{key}")` to pick the next incomplete
    Grundriss. For each scene, drive `get_scene_plan_next_action` until
    `get_scene_plan_status` is `verified`, `accepted_incomplete`, or
    `blocked_external`.

    ## Exit

    `get_workflow_state(key="{key}")["phases"]["floorplans"]["status"] == "done"`
    """


    @mcp.prompt(name="section-scene-pass")
    def prompt_section_scene_pass(key: str) -> str:
        return f"""# Sections · label Schnitt scenes for `{key}`

    Goal: every `schnitt` scene is terminal or accepted incomplete after the
    floorplans stage. Sections read heights, datum, storey/roof component
    lines, section openings/components, and their reference dimensions.

    For each Schnitt scene:

    1. Create or resume the structured scene plan.
    2. Read/label height marks and component lines with verify-after-place.
    3. Add section openings/components where visible.
    4. Add reference dimensions and recompute homography.
    5. Evaluate scene-plan gates and terminality.

    ## Exit

    `get_workflow_state(key="{key}")["phases"]["sections"]["status"] == "done"`
    """


    @mcp.prompt(name="elevation-scene-pass")
    def prompt_elevation_scene_pass(key: str) -> str:
        return f"""# Elevations · label Ansicht scenes for `{key}`

    Goal: every `ansicht` scene is terminal or accepted incomplete after
    floorplans and sections. Elevations label facade openings, facade/roof
    component lines, visible height marks, and reference dimensions.

    For each Ansicht scene:

    1. Create or resume the structured scene plan.
    2. Classify facade orientation only when visible; do not guess.
    3. Place facade/roof component lines and view openings with verification.
    4. Add reference dimensions and recompute homography.
    5. Evaluate scene-plan gates and terminality.

    ## Exit

    `get_workflow_state(key="{key}")["phases"]["elevations"]["status"] == "done"`
    """


    @mcp.prompt(name="review-house")
    def prompt_review_house(key: str) -> str:
        return f"""# Review · final optional pass for `{key}`

    Review is opt-in. Use it for `list_anomalies`, accepted-incomplete items,
    uncertain labels, and a final `validate_export_readiness` check. It must
    not be used to start substantive labeling before floorplans, sections, and
    elevations have run in order.

    To mark review complete:

    `set_house_facts(patch={{"workflow": {{"phase_completed_at":
                                           {{"review": "<ISO timestamp>"}}}}}})`
    """


    @mcp.prompt(name="diagnose-failed-export")
    def prompt_diagnose_failed_export(key: str) -> str:
        return f"""# Diagnose why `{key}` won't export

    The agent exited the required scene stages but `export_house` returned 409. Find what's blocking.

    ## Steps

    1. `validate_export_readiness(key="{key}")` — read the blockers list.
    2. Common causes + fixes:
       - **"no annotated scenes"** → no scene has labels. Re-run the current
         scene-class stage or review pass.
       - **"house has zero drawings"** → re-run extract_scenes via the inventory
         bootstrap.
       - **homography degenerate on Scene X** → call `recompute_homography`
         on that scene; the error tells you which ref dims are degenerate.
         Delete + retry.
    3. After fixing, re-call `export_house(key="{key}")` — should return 201
       with the export manifest.

    ## When to give up

    If the same blocker survives 2 fix attempts: `dump_run_summary` with
    notes, exit non-zero so the driver records the failure for human
    review.
    """


    @mcp.prompt(name="diagnose-degenerate-homography")
    def prompt_diagnose_degenerate_homography(key: str, file: str) -> str:
        return f"""# Diagnose degenerate homography on `{key}` / `{file}`

    `recompute_homography` returned `status="degenerate"` or
    `rms_residual_px > 10`. Recover.

    ## Steps

    1. `list_scene_labels(key="{key}", file="{file}")` — find every
       `dimensioned_distance` with `is_reference=true`.
    2. For each, `get_label(label_id=<id>)` and check:
       - Is `target_orientation` set ("horizontal" / "vertical")? If not,
         the rectifier can't tell which axis it anchors. `update_label_attrs`
         to set it.
       - Are the start/end endpoints actually horizontal / vertical in the
         image? Compute angle; if > 10° off-axis, the dim is mis-drawn.
         Delete it.
    3. If only one ref dim survives: add a new one in the missing
       orientation per the current stage playbook.
    4. `recompute_homography(key="{key}", file="{file}")` — confirm
       `status="ok"` with `rms_residual_px ≤ 8`.

    ## When the drawing truly has no orthogonal dim pair

    (e.g. a perspective sketch or a detail with only one dim line)
    `set_label_status(label_id=<best dim>, status="uncertain")` and
    `dump_run_summary` flagging the scene for human review.
    """
