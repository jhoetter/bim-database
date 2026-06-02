# W0 · Inventory — categorise every scene of `$key`

$spec_notice

Goal: every scene has a non-null `scene_tag`, Ansicht/Schnitt have
`scene_orientation`, Grundriss have `scene_level`.

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

This default mapping prevents the most common W0 mis-tag (a detail
crop tagged `schnitt` because the cutaway-ish lines looked sectional
at a glance).

## Steps

For each scene returned by `get_house(key="$key").drawings`:

1. `get_scene_view(key="$key", file=<file>, tiers="broad")` — overview only.
2. Look up the default scene_tag from the table above based on
   `drawing.kind`. That's your starting answer.
3. Confirm by reading the title-block text (usually bottom-right):
   "EG-Grundriss", "Süd-Ansicht", "Schnitt A-A" — best ground truth.
   Override the default only when the title block contradicts it.
4. `set_scene_tag(key="$key", file=<file>, tag=<tag>)`.
5. **scene_orientation (per §G3-2 + §H3): OPTIONAL.** If
   Ansicht/Schnitt with a CLEAR cardinal face (elevation labeled
   "Süd"/"South"; compass mark visible AND the wall it points to is
   the wall this scene shows), call `set_scene_orientation(...)`.
   **If unclear, leave null — DO NOT GUESS.** Per §H3 missing
   orientation does NOT block W0 anymore; it surfaces as a `warning`
   in `list_anomalies` so a human reviewer knows to spot-check.
   Detail crops never have a cardinal orientation; leave null always.
6. If Grundriss: identify the floor level (kg/ug/eg/og/dg/spitzboden)
   from the title text or by elimination (count the floors). Call
   `set_scene_level(...)`. If genuinely unclear, leave null.
7. For every scene now tagged as `grundriss`, `ansicht`, or `schnitt`,
   call `get_scene_plan_status`. If it is missing, call
   `create_scene_plan_state_from_template` with the confirmed tag and
   level/orientation. This is the handoff contract for later subagents.
8. Before leaving W0, call `get_recommended_next_action`. If it points to
   an EG `Wgeo` scene-plan action, that is the next required work. Do not
   start UG/DG, section, elevation, or export work while EG plans remain
   incomplete.

## Heuristics for ambiguous cases

- A drawing with both plan and section (split sheet) → tag as the
  dominant element; flag with `dump_run_summary` for human review.
- "EG" is the ground floor (Erdgeschoss), "OG" upper, "DG" attic,
  "KG" basement (Kellergeschoss).
- Cardinal directions in German labels: Nord/Süd/Ost/West.

## Exit

`get_workflow_state(key="$key")["phases"]["W0"]["status"] == "done"`

If W0 still has blockers after one full pass, re-call `get_scene_view`
on the blocked scene with `region=` zoom to inspect the title block.
