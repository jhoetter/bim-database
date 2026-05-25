#!/usr/bin/env python3
"""Create house folders, move loose AVIF files into them, and generate PDFs.

Prefix detection is fully dynamic — no hardcoded mappings. The script:
  1. Scans existing house-N folders to learn which prefixes already live there.
  2. Groups any loose AVIF files in the repo root by their detected prefix.
  3. Creates new house-N folders (continuing from the highest existing number)
     for prefixes not yet assigned.
  4. Moves files and builds a PDF for every house that lacks one.
"""

import re
import shutil
import tempfile
from pathlib import Path

# pillow-avif-plugin (optional) registers AVIF decoders into Pillow. Falls
# back gracefully when not installed (e.g. on macOS where `sips` is used).
try:
    import pillow_avif  # noqa: F401
except ImportError:
    pass

from PIL import Image

BASE = Path(__file__).parent

# Matches the image-type keyword that separates the house prefix from the
# image variant (exterior/floorplan/…).  Split point is just before the match.
_TYPE_RE = re.compile(
    r"[_-](?:exterior|floorplan|floor_plans?|floor|innen|grundriss|aussen|fassade)",
    re.IGNORECASE,
)


def extract_prefix(filename: str) -> str:
    """Return the house-identifying prefix from an AVIF filename stem."""
    stem = re.sub(r"\.original$", "", Path(filename).stem)
    m = _TYPE_RE.search(stem)
    return stem[: m.start()] if m else stem


def scan_existing_prefix_map() -> dict[str, str]:
    """Return {prefix: house_name} for all existing house-N folders."""
    mapping: dict[str, str] = {}
    for house_dir in BASE.glob("house-*/"):
        avifs = list(house_dir.glob("*.avif"))
        if avifs:
            prefix = extract_prefix(avifs[0].name)
            mapping[prefix] = house_dir.name
    return mapping


def group_loose_files() -> dict[str, list[Path]]:
    """Group AVIF files sitting in the repo root by their prefix."""
    groups: dict[str, list[Path]] = {}
    for f in BASE.glob("*.avif"):
        prefix = extract_prefix(f.name)
        groups.setdefault(prefix, []).append(f)
    return groups


def next_house_number() -> int:
    existing = [
        int(p.name.split("-")[1])
        for p in BASE.glob("house-*/")
        if p.name.split("-")[1].isdigit()
    ]
    return max(existing, default=0) + 1


def move_loose_files(prefix_map: dict[str, str]) -> dict[str, str]:
    """Move loose files into house folders; returns updated prefix_map."""
    groups = group_loose_files()
    if not groups:
        print("  No loose AVIF files found.")
        return prefix_map

    next_n = next_house_number()
    for prefix, files in sorted(groups.items()):
        if prefix not in prefix_map:
            house_name = f"house-{next_n}"
            prefix_map[prefix] = house_name
            next_n += 1

        house_dir = BASE / prefix_map[prefix]
        house_dir.mkdir(exist_ok=True)
        for f in files:
            dest = house_dir / f.name
            if not dest.exists():
                shutil.move(str(f), str(dest))
                print(f"  Moved {f.name} → {house_dir.name}/")

    return prefix_map


def avif_to_png(avif_path: Path, out_path: Path) -> None:
    """Decode AVIF and write PNG. Uses Pillow + pillow-avif-plugin when
    available (Linux), falls back to sips (macOS)."""
    try:
        Image.open(avif_path).convert("RGB").save(out_path, "PNG")
    except Exception:
        import subprocess
        subprocess.run(
            ["sips", "-s", "format", "png", str(avif_path), "--out", str(out_path)],
            check=True, capture_output=True,
        )


def sort_key(path: Path) -> tuple:
    stem = re.sub(r"\.original$", "", path.stem).lower()
    m = _TYPE_RE.search(stem)
    if m:
        kind = 1 if any(w in m.group().lower() for w in ("floor", "grundriss")) else 0
        # Only use numbers that appear AFTER the type keyword so that numbers
        # embedded in the prefix (e.g. "1876" in "mh_il-6-143_1876") don't
        # affect ordering within a house.
        nums = re.findall(r"\d+", stem[m.end():])
    else:
        kind = 0
        nums = re.findall(r"\d+", stem)
    return (kind, int(nums[0]) if nums else 0)


def build_pdf(house_dir: Path, pdf_path: Path) -> None:
    avifs = sorted(house_dir.glob("*.avif"), key=sort_key)
    if not avifs:
        print(f"  No AVIF files in {house_dir.name}, skipping.")
        return

    print(f"  Building {pdf_path.name} from {len(avifs)} images...")
    images: list[Image.Image] = []
    with tempfile.TemporaryDirectory() as tmp:
        for avif in avifs:
            png = Path(tmp) / (avif.stem + ".png")
            avif_to_png(avif, png)
            images.append(Image.open(png).convert("RGB"))

    PAGE_W, PAGE_H = 1754, 1240  # A4 landscape @ 150 dpi

    pages: list[Image.Image] = []
    for img in images:
        iw, ih = img.size
        scale = min(PAGE_W / iw, PAGE_H / ih)
        img = img.resize((int(iw * scale), int(ih * scale)), Image.LANCZOS)
        page = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
        page.paste(img, ((PAGE_W - img.width) // 2, (PAGE_H - img.height) // 2))
        pages.append(page)

    pages[0].save(
        str(pdf_path),
        save_all=True,
        append_images=pages[1:],
        format="PDF",
        resolution=150,
    )
    print(f"  Saved {pdf_path.name} ({pdf_path.stat().st_size // 1024} KB)")


def main() -> None:
    print("=== Moving loose files into house folders ===")
    prefix_map = scan_existing_prefix_map()
    move_loose_files(prefix_map)

    print("\n=== Generating PDFs for all house folders ===")
    house_dirs = sorted(
        BASE.glob("house-*/"),
        key=lambda p: int(p.name.split("-")[1]),
    )
    for house_dir in house_dirs:
        pdf_path = BASE / f"{house_dir.name}.pdf"
        if pdf_path.exists():
            print(f"  {house_dir.name}.pdf already exists, skipping.")
            continue
        build_pdf(house_dir, pdf_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
