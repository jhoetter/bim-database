#!/usr/bin/env python3
"""Crop a region from a source image and save as a new scene.
Coordinates are fractional (0..1) so they're DPI-independent.

Usage:
  scripts/crop_scene.py SRC_IMG OUT_PATH X0 Y0 X1 Y1
  scripts/crop_scene.py /tmp/h21-Ansichten-1.jpg \\
        data/houses/house-21/house-21-elevation-berg.jpg \\
        0.15 0.13 0.50 0.50
"""
import sys
from pathlib import Path
try:
    import pillow_avif  # noqa: F401  (registers AVIF decoder if installed)
except ImportError:
    pass
from PIL import Image


def main():
    if len(sys.argv) != 7:
        sys.exit(__doc__)
    src, out, x0, y0, x1, y1 = sys.argv[1:]
    img = Image.open(src)
    w, h = img.size
    box = (int(float(x0)*w), int(float(y0)*h), int(float(x1)*w), int(float(y1)*h))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").crop(box).save(out, quality=92)
    print(f"wrote {out}  ({box[2]-box[0]}×{box[3]-box[1]}px)")


if __name__ == "__main__":
    main()
