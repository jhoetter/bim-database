# W2 · Footprint — width + depth + outer wall thickness for `$key`

$spec_notice

Goal: `facts.extent.width_mm`, `facts.extent.depth_mm`, and
`facts.wall_thickness.outer_mm` all set.

## Axis convention (per §H2)

On a Grundriss (plan view), the building's dimensions are:
- **horizontal dim → `extent.width_mm`** (Gebäudebreite)
- **vertical dim → `extent.depth_mm`** (Gebäudetiefe)

So adding ONE horizontal + ONE vertical reference dim on an EG-
Grundriss populates BOTH `width_mm` AND `depth_mm` via server-side
derivation. No need for a follow-up `set_house_facts` patch on
extent — just label the dims and confirm via `get_house_facts`.

## Steps

1. Pick EG-Grundriss (the one with `scene_level == "eg"`).
2. `get_scene_plan_status(key="$key", file=<eg-grundriss>)`.
   If missing, call `create_scene_plan_state_from_template` with
   `scene_tag="grundriss"` and `level_or_orientation="eg"`.
   Then call `get_scene_plan_next_action` and `start_scene_plan_action`
   before placing any geometry/reference labels.
3. `get_scene_view(key="$key", file=<eg-grundriss>, tiers="broad,finer")`
   — find a horizontal dim along the outer edge (full façade length;
   typically the longest dim on the sheet) and a vertical one along
   the depth.
4. Read both dim values from the drawing (e.g. "12,40 m" → 12400 mm).
5. For each, call:
   ```
   add_reference_dim(key="$key", file=<eg>, orientation="horizontal",
                     start=[x1, y1], end=[x2, y2],
                     value_mm=12400, dimension_text="12,40 m")
   ```
   The tool returns `homography.rms_residual_px`. **Reject if > 8 px**
   — delete the dim and try a more-clearly-outer edge. (Use
   `delete_label(label_id=data.distance_id)` and the partner dim_number.)

   **VERIFY (per §H5).** Then immediately:
   ```
   get_scene_view_with_labels(key="$key", file=<eg>,
                              region=<crop around the dim line>,
                              tiers="finer,detail")
   ```
   The green/red dim stroke must sit ON the building's outer edge, not
   on an interior wall or an unrelated line of text. Endpoint caps must
   line up with the corners. If it's wrong: `delete_label` and re-place;
   3-attempt budget then `status="uncertain"`.
6. Once both pass: identify an outer wall on the drawing — typically
   30-40 cm thick (drawn as a thick double line). Read its thickness:
   ```
   upsert_label(key="$key", file=<eg>, label={
     "type": "wall",
     "geometry": {"start": [x1,y1], "end": [x2,y2]},
     "attributes": {"thickness_mm": 365},
     "status": "readable"
   })
   ```
   **VERIFY (per §H5)** — call `verify_label_placement` for the new wall.
   The orange wall stroke must lie along the drawn wall, not floating in
   empty space or crossing through openings. Escalate to a wider
   `get_scene_view_with_labels(region=...)` only if the crop lacks context.
7. Add compact scene-plan evidence for the dimensions/wall and call
   `evaluate_scene_plan_gates(key="$key", file=<eg-grundriss>)`. Finish
   the claimed action with `finish_scene_plan_action` only after the
   verification evidence exists.
8. Confirm via `get_house_facts(key="$key")`:
   - `extent.width_mm` = horizontal dim value
   - `extent.depth_mm` = vertical dim value
   - `wall_thickness.outer_mm` set
   You should NOT need to call `set_house_facts` for extent — derivation
   handles it. The only manual `set_house_facts` is for
   `wall_thickness.outer_mm` (since walls don't derive that
   automatically yet).

## Exit

`get_workflow_state[...]["W2"]["status"] == "done"` AND the auto-derived
`facts.extent` matches the dim values within 2 %.
