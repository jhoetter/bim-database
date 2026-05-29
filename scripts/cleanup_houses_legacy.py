#!/usr/bin/env python3
"""R0.6 — irreversibly delete the legacy data/houses/ tree.

The dataset path (data/dataset/) is the only data source going forward.
Source PDFs for houses 21, 22, 23 are preserved at data/pdfs/incoming/;
every other house directory under data/houses/ becomes dead weight.

This script asks for explicit confirmation before deleting and logs what
it removed. Run it manually:

    python3 scripts/cleanup_houses_legacy.py

To skip the prompt (e.g. CI):

    python3 scripts/cleanup_houses_legacy.py --yes

The script is idempotent — running it twice is a no-op.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOUSES = REPO / "data" / "houses"
PRESERVED_KEYS = {"house-21", "house-22", "house-23"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation.")
    args = parser.parse_args()

    if not HOUSES.exists():
        print(f"{HOUSES} does not exist — nothing to clean.")
        return 0

    # House dirs (skip files like ontology.json).
    targets = sorted(
        p for p in HOUSES.iterdir()
        if p.is_dir() and p.name not in PRESERVED_KEYS
    )
    if not targets:
        print("data/houses/ already cleaned. Nothing to do.")
        return 0

    print(f"About to delete {len(targets)} legacy house directories:")
    for t in targets[:10]:
        print(f"  {t.relative_to(REPO)}")
    if len(targets) > 10:
        print(f"  ... and {len(targets) - 10} more")
    print()
    print("PRESERVED (data already migrated to data/pdfs/incoming/):")
    for k in sorted(PRESERVED_KEYS):
        print(f"  {(HOUSES / k).relative_to(REPO)}")
    print()

    if not args.yes:
        answer = input("Type 'delete' to confirm: ").strip().lower()
        if answer != "delete":
            print("Aborted.")
            return 1

    for t in targets:
        shutil.rmtree(t)
        print(f"deleted {t.relative_to(REPO)}")
    # After deletion, also remove preserved house directories — their
    # PDFs are now in data/pdfs/incoming/ and the catalog metadata is
    # not used anymore. Keep the directories one cleanup at a time so
    # the operator can sanity-check the move first.
    for k in sorted(PRESERVED_KEYS):
        p = HOUSES / k
        if p.exists():
            print(f"keeping {p.relative_to(REPO)} (re-run with --finalize to remove)")

    print()
    print(f"Removed {len(targets)} directories.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
