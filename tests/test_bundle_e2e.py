"""End-to-end: identical R1-shaped bundle from PDF and HEIC inputs.

This is the cross-adapter contract test the spec demanded — both the
batch CLI and the form endpoint route through `ingest_to_bundle`, so we
prove the bundle shape is identical regardless of input modality.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion.bundle import IngestProvenance, ingest_to_bundle
from ingestion.config import load_profile
from ingestion.manifest import assert_v2_shape


REQUIRED_FILES = ("manifest.json",)
REQUIRED_DIRS = ("source",)


def _assert_canonical_bundle(bundle: Path, key: str):
    assert (bundle / "manifest.json").exists(), "manifest.json missing"
    assert (bundle / "source").is_dir(), "source/ dir missing"
    consolidated = bundle / f"{key}.pdf"
    assert consolidated.exists(), f"consolidated PDF {consolidated.name} missing"
    m = json.loads((bundle / "manifest.json").read_text())
    assert_v2_shape(m)
    assert m["house_key"] == key
    assert m["consolidated_pdf"] == f"{key}.pdf"
    assert m["page_count"] == len(m["pages"]) > 0
    assert m["state"] == "partial"


def test_e2e_messy_pdf(synth_pdf: Path, tmp_path: Path):
    bundle_root = tmp_path / "incoming"
    result = ingest_to_bundle(
        input_files=[synth_pdf],
        bundle_root=bundle_root,
        bundle_key="house-100",
        provenance=IngestProvenance(source_type="batch", user_notes="from PDF"),
        cfg=load_profile("lenient-scrape"),
    )
    _assert_canonical_bundle(result.bundle_dir, "house-100")
    m = result.manifest
    assert m["source_type"] == "batch"
    assert m["pipeline"]["version"]
    assert m["pipeline"]["enhancer"]["backend"] == "noop"
    # 3 pages came in; 3 pages must come out — order preserved.
    assert [p["page"] for p in m["pages"]] == [1, 2, 3]
    # Native-PDF pages skip rectification.
    assert all(p["rectify_method"] == "passthrough" for p in m["pages"])


def test_e2e_heic(synth_heic: Path, tmp_path: Path):
    bundle_root = tmp_path / "incoming"
    result = ingest_to_bundle(
        input_files=[synth_heic],
        bundle_root=bundle_root,
        bundle_key="house-101",
        provenance=IngestProvenance(source_type="form", user_notes="from phone"),
        cfg=load_profile("lenient-scrape"),
    )
    _assert_canonical_bundle(result.bundle_dir, "house-101")
    m = result.manifest
    assert m["source_type"] == "form"
    # Single-page input → single-page bundle.
    assert m["page_count"] == 1
    assert len(m["pages"]) == 1


def test_e2e_pdf_and_heic_yield_identical_shape(synth_pdf, synth_heic, tmp_path):
    """The cross-adapter contract test. Bundles from different input
    types must conform to the same top-level keys + sub-shapes — only
    values differ. Asserting key-set equality catches accidental
    one-sided field additions."""
    bundle_root = tmp_path / "incoming"
    cfg = load_profile("lenient-scrape")

    a = ingest_to_bundle(
        input_files=[synth_pdf],
        bundle_root=bundle_root,
        bundle_key="house-200",
        provenance=IngestProvenance(source_type="batch"),
        cfg=cfg,
    )
    b = ingest_to_bundle(
        input_files=[synth_heic],
        bundle_root=bundle_root,
        bundle_key="house-201",
        provenance=IngestProvenance(source_type="form"),
        cfg=cfg,
    )

    assert set(a.manifest.keys()) == set(b.manifest.keys())
    # Per-page record shape is the same.
    assert set(a.manifest["pages"][0].keys()) == set(b.manifest["pages"][0].keys())
    assert set(a.manifest["pipeline"].keys()) == set(b.manifest["pipeline"].keys())
    # Both bundles place files in the same canonical layout.
    a_files = {p.relative_to(a.bundle_dir).as_posix() for p in a.bundle_dir.rglob("*") if p.is_file()}
    b_files = {p.relative_to(b.bundle_dir).as_posix() for p in b.bundle_dir.rglob("*") if p.is_file()}
    # Both should have manifest.json + the consolidated PDF + at least
    # one source file.
    assert "manifest.json" in a_files
    assert "manifest.json" in b_files
    assert any(f.startswith("source/") for f in a_files)
    assert any(f.startswith("source/") for f in b_files)
    assert "house-200.pdf" in a_files
    assert "house-201.pdf" in b_files


def test_source_preserves_originals_with_sha_dedup(synth_jpeg, tmp_path):
    """Re-uploading the same file under a different name must NOT
    duplicate it in source/."""
    bundle_root = tmp_path / "incoming"
    p2 = tmp_path / "duplicate.jpg"
    p2.write_bytes(synth_jpeg.read_bytes())
    result = ingest_to_bundle(
        input_files=[synth_jpeg, p2],
        bundle_root=bundle_root,
        bundle_key="house-300",
        provenance=IngestProvenance(source_type="batch"),
        cfg=load_profile("lenient-scrape"),
    )
    source = result.bundle_dir / "source"
    files = list(source.iterdir())
    # Both inputs are byte-identical → one entry in source/.
    assert len(files) == 1


def test_cli_dry_run_does_not_write(synth_jpeg, tmp_path, capsys):
    from ingestion.cli import main

    rc = main([
        str(synth_jpeg),
        "--bundle-root", str(tmp_path / "incoming"),
        "--dry-run",
    ])
    assert rc == 0
    assert not (tmp_path / "incoming").exists()
    captured = capsys.readouterr()
    plan = json.loads(captured.out)["plan"]
    assert plan["source_type"] == "batch"
    assert plan["bundle_key"].startswith("house-")


def test_cli_writes_bundle(synth_jpeg, tmp_path):
    from ingestion.cli import main

    bundle_root = tmp_path / "incoming"
    rc = main([
        str(synth_jpeg),
        "--house-key", "house-501",
        "--bundle-root", str(bundle_root),
        "--profile", "lenient-scrape",
    ])
    assert rc == 0
    _assert_canonical_bundle(bundle_root / "house-501", "house-501")
