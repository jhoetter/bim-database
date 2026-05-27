#!/usr/bin/env python3
"""Compose a 'fake whole document' from one house's individual synthetic scenes.

For each house under data/synthetic/<key>/, lay the rendered scenes (elevations,
floorplans, sections, details, doc pages) onto a single large sheet to mimic a
scanned multi-drawing architect's plan. This composite is the future training
data for the scene-detection model (S-1) — the per-scene bounding boxes in
composite.json are its ground truth.

Layout is deterministic: seed = house_id * 31337. Run with --seed N to resample.

Output:
    data/synthetic/<key>/<key>-composite.png
    data/synthetic/<key>/composite.json

Run:
    python scripts/compose_house_sheet.py house-1
    python scripts/compose_house_sheet.py --all
    python scripts/compose_house_sheet.py house-1 --seed 42
    python scripts/compose_house_sheet.py house-1 --force   # recompose
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parent.parent
DATASET_DIR = REPO / "data" / "dataset"
SYNTHETIC_DIR = DATASET_DIR  # back-compat alias

# A1-landscape proportions, scaled down so the file is manageable. Roughly
# 1.41:1; final PNG ~4-6 MB depending on content.
SHEET_W = 4000
SHEET_H = 2800
MARGIN = 90
GUTTER = 40

PAPER_COLOR = (245, 240, 225)     # warm off-white, ages well
INK_COLOR = (45, 38, 30)          # not pure black — looks more like pencil
SHADOW_OFFSET = 6
SHADOW_ALPHA = 70

# Scene-kind layout weight: floorplans get a big share, elevations a row,
# sections and details fill remaining cells.
KIND_PRIORITY = ["floorplan", "elevation", "section", "detail", "doc", "other"]


def list_houses() -> list[Path]:
    if not SYNTHETIC_DIR.exists():
        return []
    return sorted(p for p in SYNTHETIC_DIR.iterdir() if p.is_dir() and (p / "manifest.json").exists())


def load_manifest(key: str) -> dict:
    return json.loads((SYNTHETIC_DIR / key / "manifest.json").read_text())


def house_id_from_key(key: str) -> int:
    # 'house-22' -> 22
    return int(key.rsplit("-", 1)[-1])


# ── layout engine ───────────────────────────────────────────────────────────

def plan_layout(drawings: list[dict], seed: int) -> list[dict]:
    """Decide each drawing's bounding box on the sheet.

    Strategy:
    1. Group by kind. Order: floorplan first (biggest), then elevation (medium),
       then section + detail + doc (small).
    2. Allocate vertical bands: top band for elevations, middle for floorplans
       (the most prominent), bottom for everything else.
    3. Within each band, lay out left-to-right with a small jitter to mimic
       hand placement.

    Returns a list of {file, kind, bbox_px:[x,y,w,h], rotation_deg=0}.
    """
    rng = random.Random(seed)
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for d in drawings:
        by_kind[d.get("kind", "other")].append(d)

    placements: list[dict] = []

    usable_w = SHEET_W - 2 * MARGIN
    usable_h = SHEET_H - 2 * MARGIN

    # Bands. Heights tuned so a typical house (2 elevations + 1 floorplan)
    # fills the sheet without overcrowding.
    band_elev_y0 = MARGIN
    band_elev_h = int(usable_h * 0.28)
    band_floor_y0 = band_elev_y0 + band_elev_h + GUTTER
    band_floor_h = int(usable_h * 0.45)
    band_misc_y0 = band_floor_y0 + band_floor_h + GUTTER
    band_misc_h = usable_h - (band_misc_y0 - MARGIN)

    # 1. Elevations along the top
    elevs = by_kind.get("elevation", [])
    if elevs:
        place_into_row(elevs, MARGIN, band_elev_y0, usable_w, band_elev_h,
                       placements, rng, target_aspect=3 / 2)

    # 2. Floorplans in the middle band (centerpiece)
    floors = by_kind.get("floorplan", [])
    if floors:
        place_into_row(floors, MARGIN, band_floor_y0, usable_w, band_floor_h,
                       placements, rng, target_aspect=1.0)

    # 3. Everything else along the bottom
    misc: list[dict] = []
    for kind in ("section", "detail", "doc", "other"):
        misc.extend(by_kind.get(kind, []))
    if misc:
        # Reserve right ~25% for the title block — misc fills the rest.
        title_w = int(usable_w * 0.28)
        misc_w = usable_w - title_w - GUTTER
        place_into_row(misc, MARGIN, band_misc_y0, misc_w, band_misc_h,
                       placements, rng, target_aspect=4 / 3)

    return placements


def place_into_row(items: list[dict], x0: int, y0: int, w: int, h: int,
                   placements: list[dict], rng: random.Random, *,
                   target_aspect: float) -> None:
    """Lay `items` horizontally into the bounding region (x0, y0, w, h) keeping
    each item's natural aspect ratio. Uniform scale fits the row's height,
    with a slight per-item jitter for hand-placed feel."""
    n = len(items)
    if n == 0:
        return

    # Width budget per item, leaving gutters between them.
    raw_w = (w - GUTTER * (n - 1)) // n
    # Try to keep each cell roughly target_aspect; clamp by row height.
    for i, d in enumerate(items):
        img_path = SYNTHETIC_DIR / d["_house_key"] / d["file"]
        if not img_path.exists():
            continue
        with Image.open(img_path) as im:
            iw, ih = im.size
        aspect = iw / ih if ih else 1.0

        # Cell box (before jitter)
        cell_w = raw_w
        cell_h = int(cell_w / aspect)
        if cell_h > h:
            cell_h = h
            cell_w = int(cell_h * aspect)

        cell_x = x0 + i * (raw_w + GUTTER) + (raw_w - cell_w) // 2
        cell_y = y0 + (h - cell_h) // 2

        # Slight jitter, ±2% of the cell dimensions
        jx = rng.randint(-cell_w // 50, cell_w // 50)
        jy = rng.randint(-cell_h // 50, cell_h // 50)

        placements.append({
            "file": d["file"],
            "kind": d.get("kind"),
            "view": d.get("view"),
            "floor": d.get("floor"),
            "title": d.get("title"),
            "bbox_px": [int(cell_x + jx), int(cell_y + jy), int(cell_w), int(cell_h)],
            "rotation_deg": 0,
        })


# ── render ──────────────────────────────────────────────────────────────────

def paper_background(w: int, h: int, rng: random.Random) -> Image.Image:
    """A warm off-white background with a touch of speckle noise and a tiny
    diagonal vignette, to read as paper rather than CAD-flat."""
    bg = Image.new("RGB", (w, h), PAPER_COLOR)
    # Sparse noise specks
    noise = Image.new("L", (w // 4, h // 4))
    n_px = noise.load()
    nw, nh = noise.size
    for _ in range(nw * nh // 80):
        x, y = rng.randint(0, nw - 1), rng.randint(0, nh - 1)
        n_px[x, y] = rng.randint(20, 80)
    noise = noise.resize((w, h), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1))
    # Composite specks as a darken overlay
    overlay = Image.new("RGB", (w, h), (0, 0, 0))
    bg = Image.composite(overlay, bg, noise)
    return bg


def title_block(rng: random.Random, model: str | None, manufacturer: str | None,
                key: str) -> Image.Image:
    """Bottom-right title block with fake architect metadata."""
    w = int((SHEET_W - 2 * MARGIN) * 0.26)
    h = int((SHEET_H - 2 * MARGIN) * 0.22)
    block = Image.new("RGB", (w, h), PAPER_COLOR)
    d = ImageDraw.Draw(block)
    d.rectangle([(0, 0), (w - 1, h - 1)], outline=INK_COLOR, width=3)
    # Internal grid lines
    d.line([(0, h // 3), (w, h // 3)], fill=INK_COLOR, width=1)
    d.line([(0, 2 * h // 3), (w, 2 * h // 3)], fill=INK_COLOR, width=1)
    d.line([(w // 2, h // 3), (w // 2, h)], fill=INK_COLOR, width=1)

    font_large = _try_font(48)
    font_mid = _try_font(28)
    font_small = _try_font(22)

    plan_no = f"412.{rng.randint(10, 99)} / {rng.randint(10000, 99999)}"
    date = f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.randint(7, 23):02d}"
    arch_initials = rng.choice(["A.B.", "H.M.", "K.S.", "U.B.", "F.W."])

    d.text((24, 18), "BAUVORHABEN:", fill=INK_COLOR, font=font_small)
    d.text((24, 50), (model or "—")[:36], fill=INK_COLOR, font=font_mid)
    if manufacturer:
        d.text((24, 90), manufacturer[:36], fill=INK_COLOR, font=font_small)

    d.text((24, h // 3 + 20), "PLAN NR.:", fill=INK_COLOR, font=font_small)
    d.text((24, h // 3 + 50), plan_no, fill=INK_COLOR, font=font_mid)
    d.text((w // 2 + 20, h // 3 + 20), "MASSSTAB", fill=INK_COLOR, font=font_small)
    d.text((w // 2 + 20, h // 3 + 50), "1 : 100", fill=INK_COLOR, font=font_mid)

    d.text((24, 2 * h // 3 + 20), "GEZ.", fill=INK_COLOR, font=font_small)
    d.text((24, 2 * h // 3 + 50), arch_initials, fill=INK_COLOR, font=font_mid)
    d.text((w // 2 + 20, 2 * h // 3 + 20), "DATUM", fill=INK_COLOR, font=font_small)
    d.text((w // 2 + 20, 2 * h // 3 + 50), date, fill=INK_COLOR, font=font_mid)

    # Subtle key footnote at the very bottom
    d.text((24, h - 32), f"BIM-DB {key}", fill=INK_COLOR, font=font_small)
    return block


def _try_font(size: int) -> ImageFont.ImageFont:
    """Hand-lettered look is unrealistic without a real handwriting font.
    Use DejaVuSans if available (ships with Pillow on most systems);
    fall back to default."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except Exception:  # noqa: BLE001
                pass
    return ImageFont.load_default()


def drop_shadow(im: Image.Image) -> Image.Image:
    """Soft shadow under a scene tile."""
    w, h = im.size
    canvas = Image.new("RGBA", (w + SHADOW_OFFSET * 4, h + SHADOW_OFFSET * 4), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, SHADOW_ALPHA))
    canvas.paste(shadow, (SHADOW_OFFSET * 2 + SHADOW_OFFSET // 2,
                           SHADOW_OFFSET * 2 + SHADOW_OFFSET // 2))
    canvas = canvas.filter(ImageFilter.GaussianBlur(SHADOW_OFFSET))
    canvas.paste(im, (SHADOW_OFFSET * 2, SHADOW_OFFSET * 2))
    return canvas


def render_sheet(key: str, manifest: dict, seed: int) -> tuple[Image.Image, dict]:
    rng = random.Random(seed)
    # Attach the house key so placement can resolve paths.
    drawings = [{**d, "_house_key": key} for d in manifest.get("drawings", [])]
    placements = plan_layout(drawings, seed)

    sheet = paper_background(SHEET_W, SHEET_H, rng)

    for placement in placements:
        img_path = SYNTHETIC_DIR / key / placement["file"]
        if not img_path.exists():
            continue
        with Image.open(img_path) as im:
            im = im.convert("RGB")
            tw = placement["bbox_px"][2]
            th = placement["bbox_px"][3]
            im = im.resize((tw, th), Image.LANCZOS)
            tile = drop_shadow(im)
            # Drop-shadow grows the bbox by 2*SHADOW_OFFSET each side
            sheet.paste(tile,
                        (placement["bbox_px"][0] - SHADOW_OFFSET * 2,
                         placement["bbox_px"][1] - SHADOW_OFFSET * 2),
                        tile if tile.mode == "RGBA" else None)

    # Title block — bottom-right
    block = title_block(rng, manifest.get("model"), manifest.get("manufacturer"), key)
    bw, bh = block.size
    bx = SHEET_W - MARGIN - bw
    by = SHEET_H - MARGIN - bh
    sheet.paste(block, (bx, by))

    composite_meta = {
        "key": key,
        "linked_house": manifest.get("linked_house", key),
        "sheet_size_px": [SHEET_W, SHEET_H],
        "seed": seed,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scenes": placements,
        "title_block_bbox_px": [bx, by, bw, bh],
    }
    return sheet, composite_meta


# ── orchestration ───────────────────────────────────────────────────────────

def compose(key: str, *, seed: int | None = None, force: bool = False) -> tuple[Path, Path]:
    house_dir = SYNTHETIC_DIR / key
    if not house_dir.exists() or not (house_dir / "manifest.json").exists():
        raise FileNotFoundError(f"{key}: no synthetic manifest at {house_dir}")

    if seed is None:
        seed = house_id_from_key(key) * 31337

    composite_png = house_dir / f"{key}-composite.png"
    composite_json = house_dir / "composite.json"
    if composite_png.exists() and composite_json.exists() and not force:
        return composite_png, composite_json

    manifest = load_manifest(key)
    if not manifest.get("drawings"):
        raise RuntimeError(f"{key}: manifest has no drawings — nothing to compose")

    sheet, meta = render_sheet(key, manifest, seed)
    sheet.save(composite_png, "PNG", optimize=True)
    composite_json.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return composite_png, composite_json


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("house", nargs="?", help="one house key, e.g. 'house-1'")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--seed", type=int, help="override the deterministic seed")
    ap.add_argument("--force", action="store_true", help="recompose even if output exists")
    args = ap.parse_args()

    if args.all:
        keys = [p.name for p in list_houses()]
    elif args.house:
        keys = [args.house]
    else:
        ap.error("provide a house key or --all")

    for key in keys:
        try:
            png, meta = compose(key, seed=args.seed, force=args.force)
            print(f"  ✓ {key}: {png.name} ({png.stat().st_size // 1024} KB), {meta.name}")
        except Exception as e:
            print(f"  ✗ {key}: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
