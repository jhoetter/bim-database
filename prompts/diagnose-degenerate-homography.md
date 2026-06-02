# Diagnose degenerate homography on `$key` / `$file`

$spec_notice

`recompute_homography` returned `status="degenerate"` or
`rms_residual_px > 10`. Recover.

## Steps

1. `list_scene_labels(key="$key", file="$file")` — find every
   `dimensioned_distance` with `is_reference=true`.
2. For each, `get_label(label_id=<id>)` and check:
   - Is `target_orientation` set ("horizontal" / "vertical")? If not,
     the rectifier can't tell which axis it anchors. `update_label_attrs`
     to set it.
   - Are the start/end endpoints actually horizontal / vertical in the
     image? Compute angle; if > 10° off-axis, the dim is mis-drawn.
     Delete it.
3. If only one ref dim survives: add a new one in the missing
   orientation per the W4 playbook.
4. `recompute_homography(key="$key", file="$file")` — confirm
   `status="ok"` with `rms_residual_px ≤ 8`.

## When the drawing truly has no orthogonal dim pair

(e.g. a perspective sketch or a detail with only one dim line)
`set_label_status(label_id=<best dim>, status="uncertain")` and
`dump_run_summary` flagging the scene for human review.
