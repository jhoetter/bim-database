"""Shared pytest fixtures for ingestion tests."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

# Run from the worktree root so `import ingestion` resolves whether the
# tests are invoked via `pytest` or `python -m pytest`.
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def tmp_bundle_root(tmp_path: Path) -> Path:
    root = tmp_path / "incoming"
    root.mkdir()
    return root


@pytest.fixture
def synth_jpeg(tmp_path: Path) -> Path:
    """A clean, in-focus, well-lit synthetic 'document' image. Passes the
    gate; useful for smoke-testing the happy path."""
    img = _draw_document(2400, 3200)
    p = tmp_path / "good.jpg"
    img.save(p, format="JPEG", quality=92)
    return p


@pytest.fixture
def blurry_jpeg(tmp_path: Path) -> Path:
    """Same synthetic document, then a coarse Gaussian blur — the gate
    should warn or reject."""
    from PIL import ImageFilter
    img = _draw_document(1800, 2400).filter(ImageFilter.GaussianBlur(radius=8))
    p = tmp_path / "blurry.jpg"
    img.save(p, format="JPEG", quality=92)
    return p


@pytest.fixture
def synth_pdf(tmp_path: Path) -> Path:
    """A 3-page PDF with synthetic 'document' content on each page."""
    pages = [_draw_document(1700, 2200) for _ in range(3)]
    p = tmp_path / "messy.pdf"
    pages[0].convert("RGB").save(
        p,
        format="PDF",
        save_all=True,
        append_images=[pg.convert("RGB") for pg in pages[1:]],
        resolution=200.0,
    )
    return p


@pytest.fixture
def synth_heic(tmp_path: Path) -> Path:
    """A single-page HEIC of the synthetic document. Skipped at import
    time when pillow-heif isn't installed so the rest of the test suite
    still runs."""
    pytest.importorskip("pillow_heif", reason="pillow-heif required for HEIC test")
    import pillow_heif  # noqa: F401  (side-effect: register PIL plugin)
    # Newer pillow-heif requires an explicit register call before save.
    if hasattr(pillow_heif, "register_heif_opener"):
        pillow_heif.register_heif_opener()
    if hasattr(pillow_heif, "register_heif_saver"):
        pillow_heif.register_heif_saver()

    img = _draw_document(2400, 3200)
    p = tmp_path / "phone.heic"
    try:
        img.save(p, format="HEIF", quality=85)
    except (KeyError, ValueError) as e:
        pytest.skip(f"pillow-heif build lacks HEIF write support ({e})")
    return p


def _draw_document(w: int, h: int) -> Image.Image:
    """Synthesise a 'photographed sheet of architectural paper': off-white
    page on a darker grey background, with plenty of dark linework and
    text — realistic-enough that the gate's exposure / glare metrics
    don't flag it as a blown-out scan."""
    import numpy as np
    # Mid-grey background; warm paper tone in the centre.
    img = Image.new("RGB", (w, h), (140, 140, 145))
    draw = ImageDraw.Draw(img)
    margin = int(min(w, h) * 0.08)
    draw.rectangle([margin, margin, w - margin, h - margin], fill=(205, 205, 200))
    # Dense linework — both horizontal + vertical so the page is well
    # below the overexposure threshold.
    for i in range(14):
        y = margin + int((h - 2 * margin) * (0.06 + i * 0.06))
        draw.line([margin + 30, y, w - margin - 30, y], fill=(20, 20, 30), width=3)
    for i in range(10):
        x = margin + int((w - 2 * margin) * (0.08 + i * 0.08))
        draw.line([x, margin + 30, x, h - margin - 30], fill=(20, 20, 30), width=2)
    # Title block (bottom-right) with text.
    tb_x0 = int(w * 0.62)
    tb_y0 = int(h * 0.80)
    draw.rectangle([tb_x0, tb_y0, w - margin, h - margin], outline=(20, 20, 30), width=4)
    draw.rectangle([tb_x0, tb_y0, w - margin, h - margin], fill=(190, 190, 185))
    for i in range(6):
        y = tb_y0 + 16 + i * 28
        draw.text((tb_x0 + 16, y), "Musterstraße 12 · 12345 Berlin", fill=(20, 20, 30))
    # Dimension-y digits scattered around.
    for x, y, t in [
        (int(w * 0.2), int(h * 0.25), "12.45 m"),
        (int(w * 0.55), int(h * 0.40), "3.20"),
        (int(w * 0.30), int(h * 0.60), "4.80"),
        (int(w * 0.70), int(h * 0.20), "8.10"),
        (int(w * 0.15), int(h * 0.45), "2.55"),
    ]:
        draw.text((x, y), t, fill=(20, 20, 30))
    # Add a touch of Gaussian-style noise so blur-variance has signal.
    rng = np.random.default_rng(42)
    arr = np.asarray(img, dtype=np.int16)
    arr = arr + rng.integers(-6, 7, size=arr.shape, dtype=np.int16)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


# Skip OpenCV-dependent rectifier tests when cv2 isn't available so the
# CPU-only no-deps install still gets a green test run.
def pytest_collection_modifyitems(config, items):  # noqa: D401
    try:
        import cv2  # noqa: F401
        has_cv2 = True
    except ImportError:
        has_cv2 = False
    skip_cv2 = pytest.mark.skip(reason="cv2 not installed")
    for item in items:
        if "needs_cv2" in item.keywords and not has_cv2:
            item.add_marker(skip_cv2)
