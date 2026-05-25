#!/usr/bin/env python3
"""CLI for the scene render cache (see api/scene_render.py for the engine).

Usage:
    scripts/render_scene.py house-22 house-22-elevation-nord.jpg
    scripts/render_scene.py --all                # warm cache for every scene
    scripts/render_scene.py --key house-22       # warm cache for one house
    scripts/render_scene.py --clean --all        # wipe + rebuild cache
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.scene_render import CACHE_DIR, render_scene, warm_all  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("key", nargs="?", help="house key, e.g. 'house-22'")
    ap.add_argument("file", nargs="?", help="scene filename, e.g. 'house-22-elevation-nord.jpg'")
    ap.add_argument("--all", action="store_true", help="warm cache for every house")
    ap.add_argument("--key", dest="key_only", help="warm cache for a single house")
    ap.add_argument("--clean", action="store_true", help="wipe the cache before rendering")
    ap.add_argument("--force", action="store_true", help="re-render even if cache is fresh")
    args = ap.parse_args()

    if args.clean and CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)

    if args.all:
        r, s, e = warm_all()
        print(f"warm-cache: rendered {r}, skipped {s}, errors {e}")
        return 1 if e else 0

    if args.key_only:
        r, s, e = warm_all(args.key_only)
        print(f"warm-cache {args.key_only}: rendered {r}, skipped {s}, errors {e}")
        return 1 if e else 0

    if not (args.key and args.file):
        ap.error("provide KEY FILE, or --all, or --key <name>")
    print(render_scene(args.key, args.file, force=args.force))
    return 0


if __name__ == "__main__":
    sys.exit(main())
