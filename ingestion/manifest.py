"""Versioned manifest read/write.

v1.0 bundles (R1) stay valid forever; v2.0 reads fall back to defaults
for fields v1.0 didn't carry. Anywhere downstream that reads a manifest
goes through `load_manifest` so the rest of the code can assume v2.0
shape.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import VERSION
from .config import PipelineConfig


def load_manifest(bundle_dir: Path) -> dict | None:
    p = bundle_dir / "manifest.json"
    if not p.exists():
        return None
    try:
        m = json.loads(p.read_text())
    except json.JSONDecodeError:
        return None
    return upgrade_to_v2(m)


def upgrade_to_v2(m: dict) -> dict:
    """In-memory v1.0 → v2.0 upgrade. Pure — no disk writes. Adds
    sensible defaults so v2.0 consumers can assume the new fields
    exist."""
    if m.get("schema_version") == "2.0":
        return m
    m = dict(m)  # shallow copy so we don't mutate the caller's dict
    m["schema_version"] = "2.0"
    m.setdefault("source_type", "batch")
    m.setdefault("submitter", None)
    m.setdefault("consent", None)
    m.setdefault("pages", [])
    m.setdefault("pipeline", {
        "version": VERSION,
        "thresholds_profile": "v1-legacy",
        "rectify_method": "passthrough",
        "enhancer": {"backend": "noop", "version": None, "applied_to_text": False},
    })
    return m


def write_manifest(bundle_dir: Path, manifest: dict) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False)
    )


def make_pipeline_record(cfg: PipelineConfig) -> dict[str, Any]:
    return {
        "version": VERSION,
        "thresholds_profile": cfg.profile,
        "rectify_method": cfg.rectify_method,
        "enhancer": {
            "backend": cfg.enhancer.backend,
            "version": None,
            "applied_to_text": False,
        },
    }


def make_page_record(
    page: int,
    *,
    source_file: str,
    source_page: int | None,
    width_px: int,
    height_px: int,
    blur_laplacian_var: float | None,
    exposure_mean: float | None,
    glare_fraction: float | None,
    skew_deg: float | None,
    document_present: bool,
    decision: str,
    decision_reasons: list[str],
    rectified: bool,
    rectify_method: str | None,
    title_block_suspected: bool,
    title_block_bbox_px: tuple[float, float, float, float] | None,
    redacted: bool,
) -> dict:
    return {
        "page": page,
        "source_origin": {
            "source_file": source_file,
            "source_page": source_page,
        },
        "quality": {
            "width_px": width_px,
            "height_px": height_px,
            "dpi_estimate": None,
            "blur_laplacian_var": blur_laplacian_var,
            "exposure_mean": exposure_mean,
            "glare_fraction": glare_fraction,
            "skew_deg": skew_deg,
            "document_present": document_present,
        },
        "decision": decision,
        "decision_reasons": decision_reasons,
        "rectified": rectified,
        "rectify_method": rectify_method,
        "pii_flag": {
            "title_block_suspected": title_block_suspected,
            "title_block_bbox_px": list(title_block_bbox_px) if title_block_bbox_px else None,
            "redacted": redacted,
        },
        "human_qa_required": decision != "pass",
    }


# Lightweight in-process schema check that doesn't require jsonschema —
# enough to catch the obvious shape mistakes inside tests + the bundle
# writer. Full validation lives behind `make validate-manifests` (TODO).
REQUIRED_TOP_FIELDS_V2 = {
    "schema_version",
    "house_key",
    "consolidated_pdf",
    "source_filenames",
    "uploaded_at",
    "state",
    "source_type",
    "pages",
    "pipeline",
}


def assert_v2_shape(m: dict) -> None:
    missing = REQUIRED_TOP_FIELDS_V2 - set(m)
    if missing:
        raise AssertionError(f"manifest missing v2 fields: {sorted(missing)}")
    if m["schema_version"] != "2.0":
        raise AssertionError(f"expected schema_version 2.0, got {m['schema_version']!r}")
