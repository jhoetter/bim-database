"""Stage 1 — normalize."""
from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.normalize import normalize_file, sniff_kind


def test_sniff_jpeg(synth_jpeg: Path):
    assert sniff_kind(synth_jpeg.read_bytes()) == "jpeg"


def test_sniff_pdf(synth_pdf: Path):
    assert sniff_kind(synth_pdf.read_bytes()) == "pdf"


def test_sniff_unknown():
    assert sniff_kind(b"\x00\x00\x00\x00garbage") is None


def test_normalize_jpeg(synth_jpeg: Path):
    pages = normalize_file(synth_jpeg)
    assert len(pages) == 1
    assert pages[0].source_file == "good.jpg"
    assert pages[0].source_page is None
    assert pages[0].is_native_pdf is False
    assert pages[0].image.mode == "RGB"


def test_normalize_pdf_explodes_pages(synth_pdf: Path):
    pages = normalize_file(synth_pdf)
    assert len(pages) == 3
    assert [p.source_page for p in pages] == [1, 2, 3]
    assert all(p.is_native_pdf for p in pages)
    assert all(p.image.mode == "RGB" for p in pages)


def test_normalize_heic(synth_heic: Path):
    pages = normalize_file(synth_heic)
    assert len(pages) == 1
    assert pages[0].source_file == "phone.heic"
    assert pages[0].image.mode == "RGB"


def test_normalize_rejects_unknown(tmp_path: Path):
    bad = tmp_path / "weird.bin"
    bad.write_bytes(b"not an image or pdf")
    with pytest.raises(ValueError, match="unrecognised file type"):
        normalize_file(bad)
