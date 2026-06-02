# W5 · Detail (OPT-IN) — labels for `$key`

$spec_notice

W5 is off by default; the driver invokes this playbook only when
`--with-detail` is set. The export gate passes without it.

Goal: per scene, label what's visible:
- Grundriss: walls + openings (doors, windows, garage_doors).
- Ansicht: view_openings (windows, doors), height_marks at floor
  divisions, component_lines at roof edges (first/traufe/dachschraege).
- Schnitt: component_lines at floor slabs + roof edges, height_marks.

## Per-scene budget

20 tool calls. The agent stops on budget exhaustion and moves on; the
SKILL never blocks on perfect W5.

## Steps per scene

For each scene:

1. `get_scene_view(key="$key", file=<scene>, tiers="broad,finer")`.
2. Enumerate visible features the scene_tag supports (see
   `bim-db://ontology/scene_tags` for the tool palette).
3. For each: zoom (`region=`), draw with `upsert_label`, mark
   `status="uncertain"` if you can't read the type / dimension
   confidently.
4. Run `recompute_homography` periodically (every ~5 labels) so the
   per-scene calibration stays valid.

## Exit

`set_house_facts(patch={"workflow": {"phase_completed_at":
                                       {"detail": "<ISO timestamp>"}}})`
to mark W5 manually complete.
