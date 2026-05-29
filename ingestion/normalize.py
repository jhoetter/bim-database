"""Stage 1 — normalize/ingest.

Input: a single file path (any of HEIC/HEIF, JPEG, PNG, TIFF, PDF).
Output: a list of `NormalizedPage` records — one per image-page of the
input, in PIL RGB, EXIF-rotated upright. Originals are NEVER mutated.

Image inputs become a single-page list; PDFs are rasterised via PyMuPDF at
the configured DPI.

HEIC/HEIF support: pillow-heif registers a PIL plugin on import; if it
isn't installed the call raises with an actionable error message rather
than silently failing.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageOps


@dataclass
class NormalizedPage:
    source_file: str
    """Original filename (no path)."""

    source_page: int | None
    """1-indexed page within source. None for single-image inputs."""

    image: Image.Image
    """PIL RGB image, EXIF-rotated upright."""

    is_native_pdf: bool = False
    """True for pages that came from a PDF — used downstream to decide
    whether perspective rectification is worth attempting (native PDFs are
    already flat)."""


# Magic bytes for the formats we accept. Validated before the (potentially
# expensive) PIL/PyMuPDF decode.
_MAGIC = {
    "pdf": (b"%PDF",),
    "jpeg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "tiff": (b"II*\x00", b"MM\x00*"),
    # HEIC/HEIF: 'ftypheic', 'ftypheix', 'ftyphevc', 'ftypmif1', etc. at
    # offset 4. We sniff the box type loosely.
    "heif": (),
}


def sniff_kind(blob: bytes) -> str | None:
    if blob.startswith(_MAGIC["pdf"][0]):
        return "pdf"
    if blob[:3] == _MAGIC["jpeg"][0]:
        return "jpeg"
    if blob[:8] == _MAGIC["png"][0]:
        return "png"
    if blob[:4] in _MAGIC["tiff"]:
        return "tiff"
    # HEIC: bytes 4..12 contain 'ftyp' + brand
    if len(blob) >= 12 and blob[4:8] == b"ftyp":
        brand = blob[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"heim", b"heis"):
            return "heif"
    return None


def normalize_file(path: Path, render_dpi: int = 200) -> list[NormalizedPage]:
    """Decode any supported input into one or more in-memory pages."""
    blob = path.read_bytes()
    kind = sniff_kind(blob)
    if kind is None:
        raise ValueError(f"{path.name}: unrecognised file type (need PDF/JPEG/PNG/TIFF/HEIF)")

    name = path.name
    if kind == "pdf":
        return _rasterise_pdf(blob, name, render_dpi)
    if kind == "heif":
        return _decode_heif(blob, name)
    # Stock PIL-supported single-image formats.
    img = _open_pil(blob, kind)
    img = ImageOps.exif_transpose(img).convert("RGB")
    return [NormalizedPage(source_file=name, source_page=None, image=img)]


def _open_pil(blob: bytes, kind: str) -> Image.Image:
    try:
        return Image.open(io.BytesIO(blob))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"could not decode {kind} image: {e}") from e


def _decode_heif(blob: bytes, name: str) -> list[NormalizedPage]:
    try:
        import pillow_heif  # noqa: F401  (registers the PIL plugin)
    except ImportError as e:
        raise RuntimeError(
            f"{name}: HEIC/HEIF requires `pillow-heif` (install via "
            f"`pip install pillow-heif` or `pip install -r requirements.txt`)"
        ) from e
    img = Image.open(io.BytesIO(blob))
    img = ImageOps.exif_transpose(img).convert("RGB")
    return [NormalizedPage(source_file=name, source_page=None, image=img)]


def _rasterise_pdf(blob: bytes, name: str, dpi: int) -> list[NormalizedPage]:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise RuntimeError("PDF decoding requires PyMuPDF — install via `pip install pymupdf`") from e
    out: list[NormalizedPage] = []
    with fitz.open(stream=blob, filetype="pdf") as doc:
        scale = dpi / 72.0
        mat = fitz.Matrix(scale, scale)
        for i, page in enumerate(doc.pages(), start=1):
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out.append(
                NormalizedPage(
                    source_file=name,
                    source_page=i,
                    image=img,
                    is_native_pdf=True,
                )
            )
    return out


def normalize_files(paths: Sequence[Path], render_dpi: int = 200) -> list[NormalizedPage]:
    """Vectorised — applied per file then concatenated in input order."""
    out: list[NormalizedPage] = []
    for p in paths:
        out.extend(normalize_file(p, render_dpi=render_dpi))
    return out
