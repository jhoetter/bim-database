# Tool Contract Digest

Coordinates are native source pixels. Use MCP tools or the matching HTTP routes;
never write sidecar labels or use derived export images for coordinates.

Routine scene loop:

1. `get_scene_workbench_state(key, file)`
2. `start_scene_plan_action(key, file, action_id)`
3. Render with the recommended `view_mode`
4. `add_scene_plan_evidence`
5. One coherent write transaction, if allowed
6. `record_scene_plan_attempt`
7. Verify with label/score/topology tools
8. `evaluate_scene_plan_gates`
9. For wall-score missing/off-ink defects, call `classify_plan_defect` before
   closing the defect action
10. `finish_scene_plan_action`

Named view modes:

- `analysis_view`
- `silhouette_view`
- `coordinate_pick_view`
- `edit_verify_view`
- `topology_qa_view`
- `measurement_read_view`
- `opening_candidate_view`
- `final_overlay_view`

Preferred transaction tools:

- Walls: `upsert_rect_mass`, `upsert_stepped_mass`, then
  `upsert_wall_anchored(detail_mode=...)` for local details. Mass transactions
  return `transaction_verification` edge groups for overlay review. Confirmed
  structural masses require strong visible wall evidence; use uncertain or
  projection modes for roof/hatching/context ink.
- Openings: `opening_candidates`, `get_scene_view_with_opening_candidate`,
  `review_opening_candidate`, `review_opening_candidates_batch`,
  `upsert_opening_on_wall`.
- Dimensions: `dimension_station_graph`, `dimension_chain_transaction`,
  `reference_dim_review`.
- Calibration: prefer measured local reference dimensions via
  `add_reference_dim` + `recompute_homography`. If an Ansicht/Schnitt has no
  readable local reference dimensions, do not invent one; use
  `record_transferred_calibration` with source scene/fact provenance and a
  review reason.

Context hygiene:

- Large images default to handle delivery.
- Semantic ink regions are typed: pass `bbox_format='xyxy'` for grid-corner
  bboxes and `bbox_format='xywh'` only for width/height bboxes.
- Use compact summaries for routing: `get_house_context_summary`,
  `get_scene_context_summary`, and `get_scene_workbench_state`.
- Request full plan state only for audit/debug work.
- Repair candidates may include `semantic_context` from classified ink regions.
