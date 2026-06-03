# Labeling Methodology Digest

Use this digest when you need the short agent loop, not the full methodology.

1. Start every scene iteration with `get_scene_workbench_state`.
2. Use the recommended `view_mode` before raw grid/opacity knobs.
3. Claim exactly one action with `start_scene_plan_action`.
4. Analyze before writing. Record evidence before and after edits.
5. For floorplans, use mass transactions first: `upsert_rect_mass` or
   `upsert_stepped_mass` for the shell, then detail wall tools for exceptions.
   Detail wall passes use `upsert_wall_anchored(detail_mode=...)` with evidence
   and endpoint reasons.
   Use `mass_mode='structural_confirmed'` only for strong visible wall evidence;
   roof/hatching/projection ink belongs in uncertain/projection modes, not
   forced confirmed walls.
6. Place openings only after parent walls are stable. Prefer
   `review_opening_candidate` or `upsert_opening_on_wall` over raw opening quads.
7. Persist reviewed dimension chains with `dimension_chain_transaction`; review
   calibration anchors with `reference_dim_review`.
8. Verify every write with the recommended verification view and gate evaluation.
9. Treat score regions as prompts to re-look, not as automatic truth.
   Read semantic context on repair candidates before deciding wall vs non-wall.
   Semantic regions require typed coordinates: `bbox_format='xyxy'` for grid
   corners, `bbox_format='xywh'` only for width/height boxes.
10. If a plan has an actionable next action, keep working it; escalate only for
    `blocked_external` or unavailable/corrupt tooling/source.
