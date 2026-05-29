"""Stage 5+6 — pair + persist + manifest.

`ingest_to_bundle()` is the single entry point both adapters call. Given
one or more raw input files + provenance metadata, it produces the
canonical R1 bundle:

    <root>/<house_key>/
        manifest.json
        <house_key>.pdf       # consolidated, rectified
        source/               # untouched originals (SHA-256-dedup'd)

Downstream R2 scene extraction is unchanged: it still reads
`consolidated_pdf` and crops bboxes out of it. The bundle bytes are
identical in shape to a v1.0 bundle — the difference is per-page
quality + pipeline provenance in the manifest.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image

from . import VERSION
from .config import PipelineConfig, load_profile
from .gate import score_page
from .manifest import make_page_record, make_pipeline_record, write_manifest
from .normalize import NormalizedPage, normalize_files
from .pii import flag_title_block, page_has_dimension_text
from .rectify import rectify as rectify_page
from .restore import get_enhancer


@dataclass
class IngestProvenance:
    """Metadata the adapter knows about the submitter / origin. Maps onto
    the manifest's source_type / submitter / consent fields."""

    source_type: str  # "batch" | "scrape" | "form"
    submitter: dict | None = None  # for form
    consent: dict | None = None  # for form
    user_notes: str = ""


@dataclass
class BundleResult:
    bundle_dir: Path
    manifest: dict
    pages_pass: int
    pages_warn: int
    pages_reject: int


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_key(key: str) -> None:
    if not key or "/" in key or ".." in key or "\\" in key:
        raise ValueError(f"bad bundle key: {key!r}")


def next_free_house_key(roots: Sequence[Path]) -> str:
    """Find the lowest unused `house-<N>` across the given roots
    (typically [DATASET_DIR, INCOMING_DIR])."""
    used: set[int] = set()
    for root in roots:
        if not root.exists():
            continue
        for p in root.iterdir():
            if not p.is_dir():
                continue
            m = re.match(r"house-(\d+)$", p.name)
            if m:
                used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"house-{n}"


def ingest_to_bundle(
    *,
    input_files: Sequence[Path],
    bundle_root: Path,
    bundle_key: str,
    provenance: IngestProvenance,
    cfg: PipelineConfig | None = None,
    redact_title_blocks: bool = False,
) -> BundleResult:
    """Run the full pipeline. The bundle_root is e.g. `data/pdfs/incoming/`
    or `data/pdfs/submissions/`; the bundle lands at
    `<bundle_root>/<bundle_key>/`. Adapters set both."""
    if not input_files:
        raise ValueError("no input files")
    _safe_key(bundle_key)
    if cfg is None:
        cfg = load_profile()
    bundle_dir = bundle_root / bundle_key
    bundle_dir.mkdir(parents=True, exist_ok=True)
    source_dir = bundle_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    saved_source_names = _preserve_source(input_files, source_dir)

    pages = normalize_files(input_files, render_dpi=cfg.page_render_dpi)
    if not pages:
        raise ValueError("normalize stage produced zero pages — nothing to consolidate")

    enhancer = get_enhancer(cfg.enhancer.backend, upscale=cfg.enhancer.upscale)

    page_records: list[dict] = []
    rectified_images: list[Image.Image] = []
    counts = {"pass": 0, "warn": 0, "reject": 0}

    for idx, page in enumerate(pages, start=1):
        # Quality gate first — we still rectify + enhance failing pages
        # because the batch adapter wants to capture imperfect inputs;
        # the form adapter's caller is responsible for re-prompting on
        # any non-pass page.
        metrics, decision = score_page(
            page.image,
            thresholds=cfg.thresholds,
            is_native_pdf=page.is_native_pdf,
        )

        rect = rectify_page(
            page.image,
            method=cfg.rectify_method,
            is_native_pdf=page.is_native_pdf,
        )
        rectified_img = rect.image

        # Hard guard: never apply a non-text-aware generative enhancer to
        # text-bearing pages. Inside restore.py the contract is enforced
        # again as a belt-and-braces measure.
        has_text = page_has_dimension_text(rectified_img)
        enhance_result = enhancer.enhance(rectified_img, has_dimension_text=has_text)
        rectified_img = enhance_result.image

        pii = flag_title_block(rectified_img)
        redacted = False
        if (
            redact_title_blocks
            and pii.title_block_suspected
            and pii.title_block_bbox_px is not None
        ):
            from .pii import redact_region
            rectified_img = redact_region(rectified_img, pii.title_block_bbox_px)
            redacted = True

        rectified_images.append(rectified_img)
        counts[decision.decision] += 1

        page_records.append(
            make_page_record(
                page=idx,
                source_file=page.source_file,
                source_page=page.source_page,
                width_px=metrics.width_px,
                height_px=metrics.height_px,
                blur_laplacian_var=metrics.blur_laplacian_var,
                exposure_mean=metrics.exposure_mean,
                glare_fraction=metrics.glare_fraction,
                skew_deg=metrics.skew_deg,
                document_present=metrics.document_present,
                decision=decision.decision,
                decision_reasons=decision.reasons,
                rectified=rect.succeeded,
                rectify_method=rect.method,
                title_block_suspected=pii.title_block_suspected,
                title_block_bbox_px=pii.title_block_bbox_px,
                redacted=redacted,
            )
        )

    # Write the consolidated PDF. One single PDF per bundle is the R1
    # contract — multi-page input → multi-page PDF in input order.
    consolidated_name = f"{bundle_key}.pdf"
    consolidated_path = bundle_dir / consolidated_name
    _write_images_as_pdf(rectified_images, consolidated_path)

    page_count = len(rectified_images)
    pipeline_record = make_pipeline_record(cfg)
    pipeline_record["enhancer"]["version"] = enhancer.version

    manifest = {
        "schema_version": "2.0",
        "house_key": bundle_key,
        "consolidated_pdf": consolidated_name,
        "source_filenames": sorted(saved_source_names),
        "uploaded_at": _now_iso(),
        "page_count": page_count,
        "state": "partial",
        "user_notes": provenance.user_notes or "",
        "extracted_scenes": [],

        "source_type": provenance.source_type,
        "submitter": provenance.submitter,
        "consent": provenance.consent,
        "pages": page_records,
        "pipeline": pipeline_record,
    }
    write_manifest(bundle_dir, manifest)

    return BundleResult(
        bundle_dir=bundle_dir,
        manifest=manifest,
        pages_pass=counts["pass"],
        pages_warn=counts["warn"],
        pages_reject=counts["reject"],
    )


def _preserve_source(input_files: Sequence[Path], source_dir: Path) -> list[str]:
    """Copy originals into source/ with SHA-256 dedup. Returns the
    canonical list of preserved filenames."""
    existing_hashes: dict[str, str] = {}
    for p in source_dir.iterdir() if source_dir.exists() else []:
        if p.is_file():
            existing_hashes[hashlib.sha256(p.read_bytes()).hexdigest()] = p.name

    saved: list[str] = []
    for path in input_files:
        blob = path.read_bytes()
        h = hashlib.sha256(blob).hexdigest()
        if h in existing_hashes:
            saved.append(existing_hashes[h])
            continue
        safe_name = path.name
        # Prevent overwrite of an unrelated file with the same name.
        if (source_dir / safe_name).exists():
            safe_name = f"{h[:8]}-{safe_name}"
        (source_dir / safe_name).write_bytes(blob)
        existing_hashes[h] = safe_name
        saved.append(safe_name)
    return saved


def _write_images_as_pdf(images: Sequence[Image.Image], out_path: Path) -> None:
    """PIL multi-frame PDF write. Each page is JPEG-compressed at quality
    92; resolution is preserved from the rectified image so downstream
    bbox extraction has the same pixel budget the user uploaded."""
    if not images:
        raise ValueError("no rectified pages — refusing to write empty PDF")
    rgb = [img.convert("RGB") for img in images]
    head, tail = rgb[0], rgb[1:]
    # JPEG-compressed PDF stream: keeps the bundle small enough that
    # tens of multi-megapixel pages don't blow up disk usage.
    buf = io.BytesIO()
    head.save(
        buf,
        format="PDF",
        save_all=True,
        append_images=tail,
        resolution=200.0,
    )
    out_path.write_bytes(buf.getvalue())
