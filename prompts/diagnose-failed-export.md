# Diagnose why `$key` won't export

$spec_notice

The agent exited W4 but `export_house` returned 409. Find what's blocking.

## Steps

1. `validate_export_readiness(key="$key")` — read the blockers list.
2. Common causes + fixes:
   - **"no annotated scenes"** → no scene has labels. Re-run W4 (it
     adds dim labels) or run a W5 pass.
   - **"house has zero drawings"** → re-run extract_scenes via the W0
     bootstrap.
   - **homography degenerate on Scene X** → call `recompute_homography`
     on that scene; the error tells you which ref dims are degenerate.
     Delete + retry.
3. After fixing, re-call `export_house(key="$key")` — should return 201
   with the export manifest.

## When to give up

If the same blocker survives 2 fix attempts: `dump_run_summary` with
notes, exit non-zero so the driver records the failure for human
review.
