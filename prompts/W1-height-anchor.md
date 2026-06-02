# W1 · Height anchor — establish ±0.00 + Firsthöhe for `$key`

$spec_notice

Goal: `facts.heights.bezug_mm == 0` and `facts.heights.first_mm != null`.

## ORDER MATTERS (per §G3-3)

**Drop the height_mark LABELS first, then optionally confirm via
`set_house_facts`.** Server-side derivation (G1) auto-populates
`facts.heights` from `height_mark` labels with `datum` + `value_mm`
set — calling `set_house_facts` afterwards is usually redundant.
SKIPPING the labels and just setting facts is the WRONG shortcut: the
SPA's Höhenkote rendering reads the LABELS, not the facts. A scene
with `facts.heights.first_mm = 8500` but no height_mark label shows
nothing on the canvas. Reviewers can't trust it.

## Steps

0. Call `get_recommended_next_action(key="$key")`. If it returns an EG
   `Wgeo` scene-plan action, stop this W1 pass and finish that EG action
   first. Ground-floor plans have priority over global height anchoring.
1. `get_house(key="$key")` — pick an Ansicht with the most visible
   vertical dimension lines (usually the one labeled "Süd-Ansicht" or
   "Hauptansicht").
2. `get_scene_view(key="$key", file=<ansicht>, tiers="broad,finer")`
   — find the `±0,00` reference line at the ground floor and the
   Firsthöhe (ridge) line at the top.
3. For the bezug (±0.00) line:
   `get_scene_view(file=<ansicht>, region="<tight crop around the ±0 mark>")`
   to identify its exact pixel position. Then:
   ```
   upsert_label(key="$key", file=<ansicht>, label={
     "type": "height_mark",
     "geometry": {"anchor": [x, y]},
     "attributes": {"value_mm": 0, "datum": "ok_ffb"},
     "status": "readable"
   })
   ```
4. For the Firsthöhe: same workflow. Read the value from the drawing
   (e.g. "8,50 m" → 8500 mm). Then:
   ```
   upsert_label(key="$key", file=<ansicht>, label={
     "type": "height_mark",
     "geometry": {"anchor": [x, y]},
     "attributes": {"value_mm": 8500, "datum": "first"},
     "status": "readable"
   })
   ```
5. **VERIFY THE PLACEMENT (per §H5).** Immediately after each
   `upsert_label`, call:
   ```
   get_scene_view_with_labels(key="$key", file=<ansicht>,
                              region=<crop around the just-placed mark>,
                              tiers="finer,detail")
   ```
   The dot + faint Bezugslinie + value chip must visibly sit on the
   `±0,00` / Firsthöhe line you intended. If it floats above/below, the
   anchor is wrong: `update_label_attrs` with corrected `anchor`, then
   re-verify. Budget 3 attempts per height_mark — if the third still
   misses, `set_label_status(..., "uncertain")` and move on.
6. `get_house_facts(key="$key")` — confirm `heights.bezug_mm == 0`
   and `heights.first_mm == <expected>` BOTH appear. If they do, you're
   done; the server-side derivation already filled them in. If not, the
   `datum` on your height_mark labels is probably wrong (`datum: "first"`
   is required for first_mm; `value_mm: 0` is required for bezug_mm).
   Fix the labels and re-check — DO NOT just set facts manually.

## Exit

`get_workflow_state[...]["W1"]["status"] == "done"` AND
`get_house_facts.heights.sources` references at least one `hm:` source
for each populated key (proves labels back the facts).
