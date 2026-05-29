"""Batch ingestion CLI.

    python -m ingestion.cli <inputs...>
        [--house-key house-24]
        [--source-type batch|scrape]
        [--profile default|strict-form|lenient-scrape]
        [--bundle-root data/pdfs/incoming]
        [--notes "..."]
        [--dry-run]

Inputs are file paths or globs (shell-expanded). The CLI runs every
ingestion stage in-process and writes an R1-shaped bundle.

Gating policy for the batch path: low-quality pages are FLAGGED in the
manifest but NEVER block the bundle from being written. The form
adapter handles the "re-prompt the submitter" path; the batch user is
the developer.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from .bundle import IngestProvenance, ingest_to_bundle, next_free_house_key
from .config import load_profile


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ingestion", description=__doc__)
    p.add_argument("inputs", nargs="+", help="File paths or globs")
    p.add_argument("--house-key", default=None, help="Bundle key (auto-allocated if omitted)")
    p.add_argument("--source-type", default="batch", choices=["batch", "scrape"])
    p.add_argument("--profile", default=None, help="Thresholds profile (default | strict-form | lenient-scrape)")
    p.add_argument(
        "--bundle-root",
        default="data/pdfs/incoming",
        help="Where to write the bundle (default: data/pdfs/incoming)",
    )
    p.add_argument(
        "--dataset-root",
        default="data/dataset",
        help="Where to look for existing house-NN dirs when auto-allocating (default: data/dataset)",
    )
    p.add_argument("--notes", default="", help="user_notes saved into the manifest")
    p.add_argument("--dry-run", action="store_true", help="Resolve inputs + print plan; don't write anything")
    args = p.parse_args(argv)

    paths: list[Path] = []
    for inp in args.inputs:
        expanded = glob.glob(inp)
        if not expanded:
            # Maybe the user passed a literal path that doesn't exist on
            # disk yet — let the resolver fail loudly with that filename.
            expanded = [inp]
        for e in expanded:
            paths.append(Path(e))

    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"error: input(s) not found: {', '.join(str(m) for m in missing)}", file=sys.stderr)
        return 2

    bundle_root = Path(args.bundle_root)
    dataset_root = Path(args.dataset_root)
    key = args.house_key or next_free_house_key([bundle_root, dataset_root])

    cfg = load_profile(args.profile)
    provenance = IngestProvenance(
        source_type=args.source_type,
        user_notes=args.notes,
    )

    if args.dry_run:
        print(json.dumps({
            "plan": {
                "bundle_key": key,
                "bundle_root": str(bundle_root),
                "input_files": [str(p) for p in paths],
                "profile": cfg.profile,
                "rectify_method": cfg.rectify_method,
                "enhancer_backend": cfg.enhancer.backend,
                "source_type": provenance.source_type,
            }
        }, indent=2))
        return 0

    result = ingest_to_bundle(
        input_files=paths,
        bundle_root=bundle_root,
        bundle_key=key,
        provenance=provenance,
        cfg=cfg,
    )
    print(json.dumps({
        "bundle": str(result.bundle_dir),
        "page_count": result.manifest["page_count"],
        "pass": result.pages_pass,
        "warn": result.pages_warn,
        "reject": result.pages_reject,
        "consolidated_pdf": result.manifest["consolidated_pdf"],
    }, indent=2))
    if result.pages_reject:
        print(
            f"note: {result.pages_reject} page(s) marked 'reject' — flagged in the "
            f"manifest but kept in the bundle (batch policy).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
