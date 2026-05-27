#!/usr/bin/env python3
"""Generate synthetic architectural drawings for every house in bim-database.

Why: bootstrap a labeled training corpus for a supervised vision model that
should recognize architectural elements in technical-paper drawings. Each
synthetic drawing is loosely tied to a real house (so the AI has a sane
content prior) but is not a faithful reproduction — the model imagines
occluded sides, and the artifacts are explicitly meant to be reviewed +
manually labeled afterwards.

Style references: real scanned drawings from h21, h22, h23.
Content references: each target house's existing images (AVIFs / catalog
                    photos / floorplan scans).

Output: data/synthetic/<key>/<file>.png + manifest.json per house.

Resumable: skips targets that already exist on disk; on rate limits, sleeps
and continues; on API errors, logs and continues with the next target.

Run:
    python scripts/generate_synthetic_drawings.py              # all houses
    python scripts/generate_synthetic_drawings.py house-1      # one house
    python scripts/generate_synthetic_drawings.py --dry-run    # plan only
    python scripts/generate_synthetic_drawings.py --kind elevation  # only elevations
    python scripts/generate_synthetic_drawings.py --kind floorplan  # only floorplans

Requires OPENAI_API_KEY in .env at the repo root.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

# The api package is loaded as a sibling — sys.path tweak lets us import
# the renderer for style-ref preparation.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from api.scene_render import HOUSES_DIR, render_scene  # noqa: E402

SYNTHETIC_DIR = REPO / "data" / "synthetic"
PREPARED_DIR = REPO / "tmp" / "synthetic-prepared"

# Houses that DON'T get synthetic drawings — they're the style references.
SKIP_KEYS = {"house-21", "house-22", "house-23"}

# Style-ref pools (kind → list of (house_key, scene_file)). Two are randomly
# sampled per call so multiple synthetic drawings of the same house don't end
# up looking identical.
STYLE_REFS_ELEVATION: list[tuple[str, str]] = [
    ("house-21", "house-21-elevation-tal.jpg"),
    ("house-21", "house-21-elevation-berg.jpg"),
    ("house-21", "house-21-elevation-linke-giebel.jpg"),
    ("house-22", "house-22-elevation-sued.jpg"),
    ("house-22", "house-22-elevation-nord.jpg"),
    ("house-23", "house-23-elevation-strasse.jpg"),
]
STYLE_REFS_FLOORPLAN: list[tuple[str, str]] = [
    ("house-21", "house-21-floorplan-eg-detail.jpg"),
    ("house-21", "house-21-floorplan-dg-detail.jpg"),
    ("house-22", "house-22-floorplan-eg.jpg"),
    ("house-23", "house-23-floorplan-eg.jpg"),
]

MODEL = os.getenv("BIM_SYNTHETIC_MODEL", "gpt-image-2")
MAX_STYLE = 2
MAX_CONTENT = 8
MAX_TOTAL_IMAGES = 16  # OpenAI per-request cap

PROMPT_ELEVATION = """\
You are creating a fake but highly convincing architectural technical paper drawing.

Task:
Create a single orthographic architectural elevation drawing of the house shown
in the house reference images. The drawing must be a straight-on elevation view
— not isometric, not perspective.

View: {view_label}

Use the house images as the building reference:
- Preserve the architectural character, roof geometry, massing, window proportions,
  facade materials, chimney, dormers, balconies, terraces, and visible details.
- If the requested elevation is not fully visible in the references, infer a
  plausible elevation that is architecturally consistent with the visible images.
- Do not invent a completely different house.

Use the technical drawing reference images only as STYLE reference (not content):
- monochrome pencil / graphite / ink linework on paper
- hand-drawn but technically precise
- old-school architectural elevation drawing
- slightly wrinkled paper texture, subtle shadows + folds + paper grain
- clean centered composition, simple ground line
- light hatching / cross-hatching
- realistic photographed-paper look

Output requirements:
- one single sheet of paper, centered building elevation
- no isometric angle, no dramatic perspective
- no photorealistic colored render
- no blue sky, no trees, no garden photo background
- no people unless tiny scale figures are part of the drawing style
- include a handwritten title near the bottom: "{title_text}"
- looks like it came from an architect's physical paper drawing archive

The result should look like a real paper drawing scanned from an old plan set,
NOT a modern CAD export.
"""

PROMPT_FLOORPLAN = """\
You are creating a fake but highly convincing architectural floorplan drawing.

Task:
Create a single top-down orthographic floor plan of the house shown in the
house reference images, for the floor labeled "{floor_label}".

Use the house images as the building reference:
- Match the building's overall massing / footprint as best you can infer.
- Reasonable room layout given visible windows and doors.
- Walls, doors, dimension chains, room labels, stair runs.

Use the technical drawing reference images only as STYLE reference (not content):
- monochrome pencil / graphite / ink linework on paper
- hand-drawn but technically precise
- old-school architectural floor plan style
- thick outer walls, thin partitions
- room name + area annotations in German (Wohnzimmer, Schlafzimmer, Küche,
  Bad, Flur, Diele, Eltern, Kind, …) where layout warrants
- light dimension chains along the outside
- slightly wrinkled paper texture, realistic photographed-paper look

Output requirements:
- one single sheet of paper, top-down view
- no perspective, no isometric angle
- include a handwritten title near the bottom: "{title_text}"
- looks like it came from an architect's physical paper drawing archive

The result should look like a real paper floorplan scanned from an old plan set,
NOT a modern CAD export.
"""


# ── house + target discovery ─────────────────────────────────────────────────

def list_houses() -> list[Path]:
    return sorted(p for p in HOUSES_DIR.iterdir() if p.is_dir())


def load_house(key: str) -> dict | None:
    p = HOUSES_DIR / key / f"{key}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def list_content_refs(house_dir: Path) -> list[Path]:
    """Pick image files (AVIF / JPG / PNG / WEBP) suitable as content references."""
    refs: list[Path] = []
    for ext in (".avif", ".png", ".jpg", ".jpeg", ".webp"):
        refs.extend(house_dir.glob(f"*{ext}"))
    # Drop any synthetic outputs that might have been copied here.
    refs = [p for p in refs if "-syn-" not in p.name]
    return sorted(refs)


def determine_targets(house: dict, kind_filter: str | None = None) -> list[dict]:
    """Return the deterministic list of (elevation + floorplan) targets for a house.

    Elevations are always N/S/E/W. Floorplans follow record.levels, defaulting
    to a single 'EG' if levels is unset. kind_filter ('elevation'|'floorplan')
    restricts the output."""
    key = f"house-{house['id']}"
    targets: list[dict] = []

    if kind_filter in (None, "elevation"):
        for view, title in [
            ("north", "NORDANSICHT"),
            ("south", "SÜDANSICHT"),
            ("east", "OSTANSICHT"),
            ("west", "WESTANSICHT"),
        ]:
            targets.append({
                "kind": "elevation",
                "view": view,
                "title": title,
                "filename": f"{key}-syn-elevation-{view}.png",
                "prompt_args": {"view_label": title, "title_text": title},
            })

    if kind_filter in (None, "floorplan"):
        levels = house.get("levels") or ["EG"]
        for floor in levels:
            slug = floor.lower().replace(" ", "").replace(".", "").replace("ö", "oe")
            targets.append({
                "kind": "floorplan",
                "floor": floor,
                "title": f"GRUNDRISS {floor.upper()}",
                "filename": f"{key}-syn-floorplan-{slug}.png",
                "prompt_args": {"floor_label": floor, "title_text": f"GRUNDRISS {floor.upper()}"},
            })

    return targets


# ── image preparation ────────────────────────────────────────────────────────

def prepare_image(src: Path, out_name: str) -> Path:
    """Convert any image to a JPG suitable for OpenAI (RGB, ≤2048px, ≤2MB).

    Caches under tmp/synthetic-prepared/. Reuses the cached file if it's at
    least as new as the source."""
    PREPARED_DIR.mkdir(parents=True, exist_ok=True)
    out = PREPARED_DIR / f"{out_name}.jpg"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    with Image.open(src) as im:
        im = im.convert("RGB")
        max_side = 2048
        w, h = im.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            im = im.resize((int(w * scale), int(h * scale)))
        im.save(out, "JPEG", quality=92)
    return out


def prepare_style_ref(key: str, file: str) -> Path:
    """Render the cached scene (AVIF) via the scene renderer + convert to JPG."""
    src = render_scene(key, file)
    out_name = f"styleref-{key}-{Path(file).stem}"
    return prepare_image(src, out_name)


# ── manifest I/O ─────────────────────────────────────────────────────────────

def load_manifest(key: str) -> dict:
    p = SYNTHETIC_DIR / key / "manifest.json"
    if not p.exists():
        return {"key": key, "linked_house": key, "drawings": []}
    return json.loads(p.read_text())


def save_manifest(manifest: dict) -> None:
    p = SYNTHETIC_DIR / manifest["key"] / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def update_manifest_entry(manifest: dict, entry: dict) -> None:
    """Replace any existing entry with the same filename, then append the new one."""
    manifest["drawings"] = [d for d in manifest["drawings"] if d["file"] != entry["file"]]
    manifest["drawings"].append(entry)


# ── core generation loop ─────────────────────────────────────────────────────

def generate_one(client, key: str, target: dict, style_paths: list[Path],
                 content_paths: list[Path], *, dry_run: bool) -> Path | None:
    """Generate one synthetic drawing. Returns the output path on success,
    None if skipped (already exists or dry-run)."""
    out_path = SYNTHETIC_DIR / key / target["filename"]
    if out_path.exists():
        return None

    prompt = (PROMPT_ELEVATION if target["kind"] == "elevation"
              else PROMPT_FLOORPLAN).format(**target["prompt_args"])

    images = (style_paths[:MAX_STYLE] + content_paths[:MAX_CONTENT])[:MAX_TOTAL_IMAGES]
    if not images:
        raise RuntimeError(f"{key}: no images available")

    if dry_run:
        return None

    handles = [open(p, "rb") for p in images]
    try:
        result = client.images.edit(
            model=MODEL,
            image=handles,
            prompt=prompt,
            size="1536x1024" if target["kind"] == "elevation" else "1024x1024",
            quality="high",
            n=1,
        )
        b64 = result.data[0].b64_json
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(b64))
        return out_path
    finally:
        for h in handles:
            h.close()


def process_house(client, key: str, house: dict, *, args,
                  style_elev: list[Path], style_fp: list[Path]) -> None:
    manifest = load_manifest(key)
    manifest["linked_house"] = key
    manifest["model"] = house.get("model")
    manifest["manufacturer"] = house.get("manufacturer")
    manifest["building_type"] = house.get("building_type")

    targets = determine_targets(house, kind_filter=args.kind)
    print(f"\n{key}: {(house.get('model') or '?')[:70]}  ({len(targets)} targets)")

    house_dir = HOUSES_DIR / key
    content_sources = list_content_refs(house_dir)[:MAX_CONTENT]
    if not content_sources:
        print("  ⚠ no content references in house folder; skipping")
        return
    content_paths = [prepare_image(p, f"content-{key}-{p.stem}") for p in content_sources]

    for target in targets:
        out_path = SYNTHETIC_DIR / key / target["filename"]
        if out_path.exists():
            print(f"  ✓ {target['filename']} (exists)")
            continue

        style_pool = style_elev if target["kind"] == "elevation" else style_fp
        style_paths = random.sample(style_pool, min(MAX_STYLE, len(style_pool)))

        print(f"  → {target['filename']}", end=" ", flush=True)
        t0 = time.time()
        try:
            res = generate_one(client, key, target, style_paths, content_paths, dry_run=args.dry_run)
            if res:
                print(f"({time.time() - t0:.1f}s, {res.stat().st_size // 1024} KB)")
                update_manifest_entry(manifest, {
                    "file": target["filename"],
                    "kind": target["kind"],
                    "view": target.get("view"),
                    "floor": target.get("floor"),
                    "title": target["title"],
                    "model": MODEL,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "style_refs": [Path(p).name for p in style_paths],
                    "content_refs": [Path(p).name for p in content_paths],
                    "label_status": "unlabeled",
                })
                save_manifest(manifest)
            else:
                print("(dry-run)")
        except KeyboardInterrupt:
            print("\n  ⏹ interrupted")
            raise
        except Exception as e:
            msg = str(e).lower()
            if "rate limit" in msg or "too many requests" in msg or "429" in msg:
                wait = 60
                print(f"\n  ⏸ rate limit — sleeping {wait}s")
                time.sleep(wait)
                continue
            print(f"\n  ✗ {type(e).__name__}: {e}")
            time.sleep(5)
        time.sleep(args.sleep)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("house", nargs="?", help="one house key (e.g. 'house-1'); omit to do all")
    ap.add_argument("--dry-run", action="store_true", help="plan only — no API calls")
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between calls (default 1.0)")
    ap.add_argument("--kind", choices=["elevation", "floorplan"], help="restrict to one kind")
    args = ap.parse_args()

    load_dotenv(REPO / ".env")
    if not args.dry_run and not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY not set in .env — see .env.example")

    client = None
    if not args.dry_run:
        from openai import OpenAI
        client = OpenAI()

    print("Preparing style references…")
    style_elev = [prepare_style_ref(k, f) for k, f in STYLE_REFS_ELEVATION]
    style_fp = [prepare_style_ref(k, f) for k, f in STYLE_REFS_FLOORPLAN]
    print(f"  {len(style_elev)} elevation style refs + {len(style_fp)} floorplan style refs")

    keys = [args.house] if args.house else [p.name for p in list_houses()]

    for key in keys:
        if key in SKIP_KEYS:
            print(f"\nskip {key}: has real drawings (used as style reference)")
            continue
        house = load_house(key)
        if house is None:
            print(f"\nskip {key}: not found")
            continue
        process_house(client, key, house, args=args, style_elev=style_elev, style_fp=style_fp)

    print("\ndone.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped.")
        sys.exit(130)
