"""Render a scene image from (PDF + page + crop_box + dpi) coordinates.

PDF-sourced scenes don't live in git anymore — they're reconstructed on
demand from the JSON record and cached at `tmp/scene-cache/<key>/<file>`.
A JSON edit (newer mtime than the cache) invalidates the cache for that
house's scenes. Non-PDF scenes (catalog AVIFs, original photos) bypass
this module entirely; the API serves those from `/static/`.

Used by:
- the `/scene/{key}/{file}` API route (FastAPI calls `render_scene()`)
- the `scripts/render_scene.py` CLI (warm-cache + manual one-offs)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
HOUSES_DIR = REPO / "data" / "houses"
CACHE_DIR = REPO / "tmp" / "scene-cache"
DEFAULT_DPI = 200
AVIF_QUALITY = 80    # ~50% smaller than JPEG q92 for line drawings; visually equivalent.
CACHE_FORMAT = "AVIF"
CACHE_SUFFIX = ".avif"


def is_pdf_sourced(img: dict) -> bool:
    src = img.get("source_ref") or {}
    return bool(src.get("file") and src["file"].lower().endswith(".pdf"))


def _cache_path(key: str, file: str) -> Path:
    """Cache path always ends in .avif regardless of the logical scene filename.
    The /scene/<key>/<file> URL keeps the JSON's filename (typically .jpg) for
    URL stability; the API maps to this cache path and returns image/avif."""
    return CACHE_DIR / key / (Path(file).stem + CACHE_SUFFIX)


def _needs_render(json_path: Path, cache_path: Path) -> bool:
    if not cache_path.exists():
        return True
    return json_path.stat().st_mtime > cache_path.stat().st_mtime


def render_scene(key: str, file: str, *, force: bool = False) -> Path:
    """Render one scene; return the path to the cached image.

    Raises:
        FileNotFoundError — scene not in JSON, or source PDF missing
        ValueError        — scene isn't PDF-sourced (caller falls back to /static/)
    """
    json_path = HOUSES_DIR / key / f"{key}.json"
    if not json_path.exists():
        raise FileNotFoundError(f"{key}: no such house")
    record = json.loads(json_path.read_text())
    img = next((i for i in record.get("images") or [] if i["file"] == file), None)
    if img is None:
        raise FileNotFoundError(f"{key}: no image {file}")
    if not is_pdf_sourced(img):
        raise ValueError(f"{key}/{file}: source_ref is not a PDF (serve from /static/)")

    src = img["source_ref"]
    pdf = HOUSES_DIR / key / src["file"]
    if not pdf.exists():
        raise FileNotFoundError(f"source PDF not found: {pdf}")
    page = int(src.get("page") or 1)
    dpi = int(src.get("dpi") or DEFAULT_DPI)
    rotation_deg = int(src.get("rotation_deg") or 0)
    crop_box = src.get("crop_box_pct") or [0, 0, 1, 1]

    cache_path = _cache_path(key, file)
    if not force and not _needs_render(json_path, cache_path):
        return cache_path

    cache_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        prefix = Path(td) / "p"
        subprocess.run(
            [
                "pdftoppm",
                "-jpeg",
                "-jpegopt", "quality=92",
                "-r", str(dpi),
                "-f", str(page),
                "-l", str(page),
                str(pdf),
                str(prefix),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        rendered = next(Path(td).glob("p-*.jpg"), None)
        if rendered is None:
            raise RuntimeError(f"pdftoppm produced no output for {pdf} p.{page}")
        with Image.open(rendered) as im:
            # Rotation must happen pre-crop because crop_box_pct is in the
            # readable orientation (post-rotation), not the raw PDF orientation.
            if rotation_deg:
                im = im.rotate(rotation_deg, expand=True)
            w, h = im.size
            x0, y0, x1, y1 = crop_box
            im.crop(
                (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
            ).save(cache_path, format=CACHE_FORMAT, quality=AVIF_QUALITY)

    return cache_path


def warm_all(key: str | None = None) -> tuple[int, int, int]:
    """Render every PDF-sourced scene's cache entry. Returns (rendered, skipped, errors)."""
    targets = [HOUSES_DIR / key] if key else sorted(HOUSES_DIR.glob("house-*"))
    rendered = skipped = errors = 0
    for hd in targets:
        if not hd.is_dir():
            continue
        k = hd.name
        jp = hd / f"{k}.json"
        if not jp.exists():
            continue
        rec = json.loads(jp.read_text())
        for img in rec.get("images") or []:
            if not is_pdf_sourced(img):
                continue
            cache = _cache_path(k, img["file"])
            if not _needs_render(jp, cache):
                skipped += 1
                continue
            try:
                render_scene(k, img["file"])
                rendered += 1
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {k}/{img['file']}: {e}", file=sys.stderr)
                errors += 1
    return rendered, skipped, errors
