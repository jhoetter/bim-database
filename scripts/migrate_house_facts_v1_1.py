#!/usr/bin/env python3
"""One-shot migration: rewrite every data/dataset/*/house_facts.json
to v1.1 (rename scene_metadata.kind → scene_metadata.scene_tag).

Per agentic-labeling-followups-tracker §G6-4. Pre-launch, single
pass — no users, no migration window. Idempotent: a v1.1 file passes
through unchanged.

Usage:

    .venv/bin/python scripts/migrate_house_facts_v1_1.py
    .venv/bin/python scripts/migrate_house_facts_v1_1.py --dry-run

Run AFTER deploying the G6 server + UI changes. The server's
fact_derivation also runs this migration on every read, so this
script is a one-time cleanup, not a hard requirement.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.fact_derivation import _migrate_v1_0_facts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dataset-root",
        default=str(REPO_ROOT / "data" / "dataset"),
        help="path to data/dataset/",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="print what would change, don't write")
    args = p.parse_args(argv)

    root = Path(args.dataset_root)
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 2

    migrated = 0
    already_v1_1 = 0
    unchanged_no_kind = 0
    for facts_path in sorted(root.glob("*/house_facts.json")):
        try:
            facts = json.loads(facts_path.read_text())
        except json.JSONDecodeError as e:
            print(f"  ✗ {facts_path.relative_to(REPO_ROOT)}: invalid JSON ({e})",
                  file=sys.stderr)
            continue
        v_before = facts.get("schema_version")
        if v_before == "1.1":
            already_v1_1 += 1
            continue
        # Check whether any scene_metadata entry has the old `kind`.
        sm = facts.get("scene_metadata") or {}
        had_kind = any(
            isinstance(e, dict) and "kind" in e for e in sm.values()
        )
        new_facts = _migrate_v1_0_facts(facts)
        if not had_kind:
            unchanged_no_kind += 1
        if args.dry_run:
            print(f"  → {facts_path.relative_to(REPO_ROOT)}: v{v_before} → v1.1"
                  + (f" ({sum(1 for e in sm.values() if isinstance(e, dict))} scene_metadata entries)"
                     if had_kind else " (no kind field present)"))
        else:
            facts_path.write_text(
                json.dumps(new_facts, indent=2, ensure_ascii=False)
            )
            print(f"  ✓ {facts_path.relative_to(REPO_ROOT)}: v{v_before} → v1.1")
        migrated += 1

    print()
    print(f"migrated:        {migrated}")
    print(f"already at v1.1: {already_v1_1}")
    print(f"of migrated, no .kind to rewrite: {unchanged_no_kind}")
    if args.dry_run:
        print("(dry-run — no files written)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
