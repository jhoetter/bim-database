"""Tests for the blank-render extraction guard (issue #12).

A failed PDF rasterization (e.g. a corrupt content stream in a merged PDF)
silently produced an all-white crop that still reported as a `labeled`
scene. The guard rejects a blank render at extraction time.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main  # noqa: E402
from api.main import (  # noqa: E402
    _image_is_blank,
    _pixmap_is_blank,
    _render_page_poppler,
)


# ── unit: blank detector ──────────────────────────────────────────────────


def _blank_page_pixmap():
    doc = fitz.open()
    doc.new_page(width=400, height=300)  # empty -> white
    pix = doc[0].get_pixmap(dpi=150)
    return pix


def _content_page_pixmap():
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.draw_line(fitz.Point(20, 20), fitz.Point(380, 280),
                   color=(0, 0, 0), width=3)
    page.draw_rect(fitz.Rect(50, 50, 200, 150), color=(0, 0, 0), width=2)
    pix = page.get_pixmap(dpi=150)
    return pix


def test_pixmap_is_blank_true_on_empty_page():
    assert _pixmap_is_blank(_blank_page_pixmap()) is True


def test_pixmap_is_blank_false_on_drawn_content():
    assert _pixmap_is_blank(_content_page_pixmap()) is False


# ── integration: extract endpoint rejects a blank crop ────────────────────


@pytest.fixture
def blank_and_content_house():
    """A throwaway house whose consolidated PDF has page 1 blank and page
    2 with content. Cleaned up after the test."""
    key = "house-zzguardtest"
    incoming = api_main.INCOMING_DIR / key
    dataset = api_main.DATASET_DIR / key
    incoming.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    doc.new_page(width=600, height=400)  # page 1 — blank
    page2 = doc.new_page(width=600, height=400)  # page 2 — content
    page2.draw_rect(fitz.Rect(40, 40, 560, 360), color=(0, 0, 0), width=4)
    page2.draw_line(fitz.Point(40, 40), fitz.Point(560, 360), color=(0, 0, 0), width=3)
    pdf_name = f"{key}.pdf"
    doc.save(str(incoming / pdf_name))
    doc.close()
    (incoming / "manifest.json").write_text(json.dumps({
        "key": key, "consolidated_pdf": pdf_name, "state": "ready",
        "extracted_scenes": [],
    }))
    try:
        yield key
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
        shutil.rmtree(dataset, ignore_errors=True)


def test_extract_rejects_blank_page(blank_and_content_house):
    key = blank_and_content_house
    client = TestClient(api_main.app)
    # Page 1 is blank -> 422, and no crop file written.
    r = client.post(f"/pdfs/{key}/extract", json={"items": [{
        "page": 1, "bbox_pdf_units": [0, 0, 600, 400], "kind": "floorplan",
        "dpi": 150,
    }]})
    assert r.status_code == 422, r.text
    assert "blank" in r.text.lower()
    assert not (api_main.DATASET_DIR / key / f"{key}-floorplan.jpg").exists()


def test_extract_accepts_content_page(blank_and_content_house):
    key = blank_and_content_house
    client = TestClient(api_main.app)
    r = client.post(f"/pdfs/{key}/extract", json={"items": [{
        "page": 2, "bbox_pdf_units": [0, 0, 600, 400], "kind": "section",
        "dpi": 150,
    }]})
    assert r.status_code == 201, r.text
    files = [e["file"] for e in r.json()["extracted"]]
    assert files
    assert (api_main.DATASET_DIR / key / files[0]).exists()


def test_extract_allow_blank_forces_write(blank_and_content_house):
    key = blank_and_content_house
    client = TestClient(api_main.app)
    r = client.post(f"/pdfs/{key}/extract", json={"items": [{
        "page": 1, "bbox_pdf_units": [0, 0, 600, 400], "kind": "detail",
        "dpi": 150, "allow_blank": True,
    }]})
    assert r.status_code == 201, r.text
    files = [e["file"] for e in r.json()["extracted"]]
    assert (api_main.DATASET_DIR / key / files[0]).exists()


# ── issue #24: poppler-render fallback recovers PyMuPDF-blank pages ────────

pytestmark_poppler = pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="poppler (pdftoppm) not installed"
)


@pytestmark_poppler
def test_render_page_poppler_full_page(blank_and_content_house):
    """The poppler fallback rasterizes a page to non-blank content and
    crops to the same dpi/72 pixel grid PyMuPDF uses."""
    key = blank_and_content_house
    pdf = api_main.INCOMING_DIR / key / f"{key}.pdf"
    # page 2 has content; full-page poppler render must be non-blank.
    img = _render_page_poppler(pdf, 2, 150)
    assert img is not None
    assert not _image_is_blank(img)
    # dimensions match the PyMuPDF raster at the same dpi (pixel parity).
    doc = fitz.open(str(pdf))
    pix = doc[1].get_pixmap(dpi=150)
    doc.close()
    assert (img.width, img.height) == (pix.width, pix.height)


@pytestmark_poppler
def test_render_page_poppler_bbox_crop(blank_and_content_house):
    """A bbox crop via poppler matches the requested PDF-unit region."""
    key = blank_and_content_house
    pdf = api_main.INCOMING_DIR / key / f"{key}.pdf"
    dpi = 150
    crop = _render_page_poppler(pdf, 2, dpi, clip_pdf_units=(0, 0, 300, 200))
    assert crop is not None
    s = dpi / 72.0
    assert crop.width == round(300 * s)
    assert crop.height == round(200 * s)
    assert not _image_is_blank(crop)


@pytestmark_poppler
def test_extract_recovers_pymupdf_blank_via_poppler(
    blank_and_content_house, monkeypatch
):
    """When PyMuPDF returns a blank pixmap but poppler can render the page
    (the AcroForm-corrupt scanned-archive case, issue #24), extract_scenes
    recovers the content via poppler instead of 422-ing. Simulated by
    forcing the PyMuPDF pixmap to always read as blank; the content page 2
    must still be written from the poppler fallback."""
    key = blank_and_content_house
    monkeypatch.setattr(api_main, "_pixmap_is_blank", lambda pix: True)
    client = TestClient(api_main.app)
    r = client.post(f"/pdfs/{key}/extract", json={"items": [{
        "page": 2, "bbox_pdf_units": [0, 0, 600, 400], "kind": "floorplan",
        "dpi": 150,
    }]})
    assert r.status_code == 201, r.text
    files = [e["file"] for e in r.json()["extracted"]]
    assert files
    out = api_main.DATASET_DIR / key / files[0]
    assert out.exists()
    from PIL import Image as _PILImage
    assert not _image_is_blank(_PILImage.open(out))


@pytestmark_poppler
def test_extract_422_when_both_renderers_blank(
    blank_and_content_house, monkeypatch
):
    """Page 1 is genuinely empty: both PyMuPDF and poppler render blank,
    so the #12 guard still fires even with the #24 fallback in place."""
    key = blank_and_content_house
    monkeypatch.setattr(api_main, "_pixmap_is_blank", lambda pix: True)
    client = TestClient(api_main.app)
    r = client.post(f"/pdfs/{key}/extract", json={"items": [{
        "page": 1, "bbox_pdf_units": [0, 0, 600, 400], "kind": "floorplan",
        "dpi": 150,
    }]})
    assert r.status_code == 422, r.text
    assert "blank" in r.text.lower()
