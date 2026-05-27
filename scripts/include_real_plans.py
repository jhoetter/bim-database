#!/usr/bin/env python3
"""Materialize real architectural drawings into the supervised-learning dataset.

A house is starred for inclusion by setting "dataset_starred": true in its
data/houses/<key>/<key>.json. This script scans starred houses, copies their
real elevation / floorplan / section / detail JPGs into the dataset folder,
and writes a manifest.json marking each entry with source="real".

The dataset folder then holds two kinds of drawings per house:
    - synthetic (produced by generate_synthetic_drawings.py, source="synthetic")
    - real (produced here, source="real")

Both share the same manifest schema, so downstream training code can read
either without branching.

Filename convention (already consistent across starred houses):
    <key>-elevation-<view>.jpg            → kind=elevation
    <key>-floorplan-<floor>.jpg           → kind=floorplan
    <key>-floorplan-<floor>-detail.jpg    → kind=floorplan
    <key>-floorplan-<floor>-overview.jpg  → kind=floorplan
    <key>-section-<name>.jpg              → kind=section
    <key>-detail-<name>.jpg               → kind=detail

The <key>-doc-*.jpg files (Baubeschreibung, etc.) are administrative
documents, not drawings, so they're explicitly excluded.

Resumable: skips files already in the dataset folder unless --force is
passed. A manifest is rewritten on every run.

Run:
    python scripts/include_real_plans.py                 # all starred houses
    python scripts/include_real_plans.py house-23        # one house
    python scripts/include_real_plans.py --dry-run       # plan only
    python scripts/include_real_plans.py --force         # re-copy even if exists
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOUSES_DIR = REPO / "data" / "houses"

DATASET_DIR = REPO / "data" / "dataset"

# Filename → (kind, key→field). Order matters: floorplan must come before
# the bare key check so "floorplan-eg-detail" maps to kind=floorplan.
KIND_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("elevation", re.compile(r"-elevation-(?P<view>[a-zäöü0-9-]+)\.(?:jpg|jpeg|png)$", re.I), "view"),
    ("floorplan", re.compile(r"-floorplan-(?P<floor>[a-zäöü0-9-]+?)(?:-detail|-overview)?\.(?:jpg|jpeg|png)$", re.I), "floor"),
    ("section",   re.compile(r"-section-(?P<name>[a-zäöü0-9-]+)\.(?:jpg|jpeg|png)$", re.I), "name"),
    ("detail",    re.compile(r"-detail-(?P<name>[a-zäöü0-9-]+)\.(?:jpg|jpeg|png)$", re.I), "name"),
]

# Explicit exclude: administrative documents, not drawings.
EXCLUDE_PREFIXES = ("-doc-",)


def list_starred_houses() -> list[Path]:
    out: list[Path] = []
    for house_dir in sorted(HOUSES_DIR.glob("house-*/")):
        meta_path = house_dir / f"{house_dir.name}.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue
        if meta.get("dataset_starred"):
            out.append(house_dir)
    return out


def classify(filename: str) -> tuple[str, dict] | None:
    """Return (kind, extra_fields) for a drawing filename, or None to skip."""
    for excl in EXCLUDE_PREFIXES:
        if excl in filename:
            return None
    for kind, pat, field_name in KIND_PATTERNS:
        m = pat.search(filename)
        if m:
            return kind, {field_name: m.group(field_name).lower()}
    return None


def collect_real_drawings(house_dir: Path) -> list[tuple[Path, str, dict]]:
    """Find all real drawings in a house folder. Returns list of
    (src_path, kind, extra_fields)."""
    out: list[tuple[Path, str, dict]] = []
    for src in sorted(house_dir.iterdir()):
        if not src.is_file():
            continue
        if src.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        result = classify(src.name)
        if result is None:
            continue
        kind, fields = result
        out.append((src, kind, fields))
    return out


def process_house(house_dir: Path, *, force: bool, dry_run: bool) -> dict:
    key = house_dir.name
    drawings = collect_real_drawings(house_dir)
    if not drawings:
        return {"key": key, "skipped": True, "reason": "no real drawings matched"}

    dest_dir = DATASET_DIR / key
    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)

    # Load existing manifest if it exists — we want to preserve synthetic
    # entries so the dataset folder mixes both. Real entries are rewritten
    # on every run.
    manifest_path = dest_dir / "manifest.json"
    if manifest_path.exists() and not dry_run:
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {"key": key, "linked_house": key, "drawings": []}
    manifest["linked_house"] = key

    # Drop any prior real entries — we'll rewrite them from scratch.
    manifest["drawings"] = [d for d in manifest.get("drawings", []) if d.get("source") != "real"]

    copied = 0
    skipped = 0
    new_entries: list[dict] = []
    for src, kind, fields in drawings:
        dest = dest_dir / src.name
        if dest.exists() and not force:
            skipped += 1
        elif not dry_run:
            shutil.copy2(src, dest)
            copied += 1
        else:
            copied += 1

        entry = {
            "file": src.name,
            "kind": kind,
            "view": fields.get("view"),
            "floor": fields.get("floor"),
            "title": src.stem.replace("-", " ").upper(),
            "source": "real",
            "source_path": str(src.relative_to(REPO)),
            "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "label_status": "unlabeled",
        }
        new_entries.append(entry)

    manifest["drawings"].extend(new_entries)
    # Keep a stable sort: synthetic first, then real, both alphabetical by file.
    manifest["drawings"].sort(key=lambda d: (d.get("source") != "synthetic", d["file"]))

    if not dry_run:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    return {
        "key": key,
        "matched": len(drawings),
        "copied": copied,
        "skipped_existing": skipped,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("house", nargs="?", help="one starred house key (e.g. 'house-23'); omit for all")
    ap.add_argument("--dry-run", action="store_true", help="plan only, no copies")
    ap.add_argument("--force", action="store_true", help="re-copy even if destination exists")
    args = ap.parse_args()

    starred = list_starred_houses()
    if args.house:
        starred = [p for p in starred if p.name == args.house]
        if not starred:
            sys.exit(f"{args.house} is not starred for the dataset")

    if not starred:
        print("No starred houses found. Set \"dataset_starred\": true in a house JSON.")
        return 0

    print(f"Found {len(starred)} starred house(s) → dataset folder: {DATASET_DIR.relative_to(REPO)}")
    for house_dir in starred:
        result = process_house(house_dir, force=args.force, dry_run=args.dry_run)
        if result.get("skipped"):
            print(f"  - {result['key']}: skipped ({result['reason']})")
        else:
            tag = " [dry-run]" if args.dry_run else ""
            print(f"  - {result['key']}: matched={result['matched']} "
                  f"copied={result['copied']} skipped-existing={result['skipped_existing']}{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
