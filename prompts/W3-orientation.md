# W3 · Orientation — pick the north edge for `$key`

$spec_notice

Goal: `facts.orientation.north_edge_label_id` set (or
`north_angle_deg` as fallback).

## HONESTY RULE (per §G3-4)

The `assumed` flag MUST reflect reality. Only set `assumed: false` when
there's an EXPLICIT on-drawing compass — a "N" arrow, a "Norden" label,
a compass rose. Everything else is a guess, and a guess MUST carry
`assumed: true`. A human reviewer scans for `assumed: true` rows to
prioritize what to spot-check.

## Steps

1. EG-Grundriss again. `get_scene_view(tiers="broad")`.
2. Look for a compass mark or "Norden" label. Look carefully — small
   compass arrows often hide in corners or near the title block.
3. **If you see an explicit compass mark:**
   - Identify the wall that aligns with north (the wall the compass
     arrow points along, or the wall labeled with "N"). Take its
     label_id from `list_scene_labels`.
   - ```
     set_house_facts(patch={"orientation": {
       "north_edge_label_id": <wall_id>,
       "assumed": false
     }})
     ```
4. **If NO compass mark visible:**
   - Default to north_angle_deg=0 (most catalog houses face the street,
     which is often south — so the back wall points roughly north).
   - You MUST mark this as a guess:
     ```
     set_house_facts(patch={"orientation": {
       "north_angle_deg": 0,
       "assumed": true
     }})
     ```
   - The server-side guard (§G4-3) will auto-correct `assumed: false`
     to `assumed: true` if you forget — but don't rely on that.

## Exit

`get_workflow_state[...]["W3"]["status"] == "done"`
