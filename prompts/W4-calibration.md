# W4 · Calibration — per-scene reference dims for `$key`

$spec_notice

Goal: every Ansicht/Schnitt has `facts.calibration_per_scene[file]`
populated (one horizontal + one vertical reference dim, homography
RMS ≤ 8 px).

## ZOOM-BEFORE-NAMING DISCIPLINE (per §G3-5)

Every `add_reference_dim` call MUST be preceded by a
`get_scene_view(region=…)` call cropping to a tight bbox around the
dim line + its numeric label. Reading endpoints off the BROAD-tier
full-image view is what causes building-scale values to land on
detail-crop scenes (the 9084 mm horizontal ref on a roof-corner
detail bug, §B4). The plan.yaml the driver writes records every
zoom region used — if a plan step adds a ref dim without a paired
zoom call, the reviewer rejects the run.

## Steps per scene

0. Call `get_recommended_next_action(key="$key")`. If it returns an EG
   `Wgeo` scene-plan action, stop W4 immediately. Do not calibrate
   Ansichten/Schnitte while ground-floor plans are incomplete.

For each scene where `scene_tag` ∈ {"ansicht", "schnitt"} AND
`get_house_facts.calibration_per_scene[file]` is absent:

1. `get_scene_view(key="$key", file=<scene>, tiers="broad,finer")`
   — full image.
2. Apply the **is_reference selection ladder**
   (per agentic-labeling-tracker §8 decision 3):
   a. Identify the title-block bbox (usually bottom-right; it's the
      densest-text region). Exclude this half of the image.
   b. Find the **longest clearly-labeled horizontal** dim line in the
      remaining area — typically along the eaves or the foundation. The
      grid overlay's broad tier (~100-200 px cells) tells you the gross
      length.
   c. Find the **longest clearly-labeled vertical** dim — typically
      ground-to-eaves or ground-to-ridge.
3. **ZOOM FIRST — REQUIRED.** Pick a tight rectangle that includes
   BOTH the dim line's endpoints AND the numeric label text. Call:
   ```
   get_scene_view(file=<scene>,
                  region="<x0>,<y0>,<x1>,<y1>",
                  tiers="finer,detail")
   ```
   Read off the endpoint coords from the GRID LABELS IN THE ZOOM (they
   still reference source pixels) and read the numeric value from the
   visible text.
4. Sanity check the value: does the value match the scene's expected
   scale? A 9000+ mm dim on a 600-px-wide detail crop is almost
   certainly a building-scale dim that bled into the crop frame —
   reject and pick a smaller candidate.
5. ```
   add_reference_dim(key="$key", file=<scene>, orientation="horizontal",
                     start=[x1,y1], end=[x2,y2],
                     value_mm=<value>, dimension_text="<as written>")
   ```
   Check `homography.rms_residual_px` in the response:
     - ≤ 8: keep going.
     - > 8: delete this dim + its partner dim_number, try the
       second-best candidate. Repeat up to 3 times.
6. **VERIFY THE PLACEMENT (per §H5).** Immediately call:
   ```
   get_scene_view_with_labels(key="$key", file=<scene>,
                              region="<same zoom region as step 3>",
                              tiers="finer,detail")
   ```
   - The red REF-dim stroke (reference dims render red) must visibly
     span the dim line you read the value off of, with endpoint caps
     on the exact corners.
   - The value chip must show `REF <value>m`.
   - If the stroke floats next to / beside / through the dim instead
     of along it: `delete_label(label_id=data.distance_id)` + delete
     the dim_number partner, then re-place using the corrected
     endpoint reads. 3-attempt budget per scene per orientation.
   - After the third miss: `set_label_status(..., "uncertain")` on
     the closest attempt, log via `dump_run_summary`, move on.
7. Repeat for vertical (including a fresh verify pass).
8. Confirm `get_house_facts.calibration_per_scene` now has the file.

## Hard caps (per scene budget)

- 6 tool calls including all `get_scene_view`s.
- If still failing after 3 ref-dim attempts: `set_label_status(...,
  "uncertain")` on whichever dim came closest, then call
  `dump_run_summary` with `notes="W4 calibration failed on <scene>;
  human review needed"` and move to the next scene.

## Exit

`get_workflow_state[...]["W4"]["status"] == "done"`
