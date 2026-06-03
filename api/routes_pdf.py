"""PDF intake, upload, page-render, and scene-extraction routes (H5).

Extracted from api/main.py: the /pdfs, /submit, submission-promote, upload,
page-render, pdf-info, scene-extract, and extract delete/restore family plus
its PDF-exclusive helpers. Registered on an APIRouter included by api/main.py
before the SPA catch-all. Shared helpers/config (paths, _safe_key,
_read_manifest/_write_manifest/_bundle_state, _now_iso, _parse_region/_tiers,
_load_dataset_manifest) stay in api.main and are imported here. Behavior and
URL shapes are unchanged.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from . import main
from .persistence import atomic_write_json
from .main import (
    CLIP_BORDER_FRAC,
    CLIP_GROW_FRAC,
    CLIP_INK_FRAC,
    CLIP_INK_THRESHOLD,
    CLIP_MARGIN_FRAC,
    CLIP_MAX_ITERS,
    GRID_CACHE,
    PDF_CACHE,
    RECYCLE_DIR,
    RECYCLE_TTL_SEC,
    SUBMISSIONS_DIR,
    _bundle_state,
    _load_dataset_manifest,
    _now_iso,
    _parse_region,
    _parse_tiers,
    _read_manifest,
    _safe_key,
    _write_manifest,
)

router = APIRouter()

# DPI bounds (M3 — code-quality-tracker). Page-proxy renders cap at the native
# scan resolution (higher only upscales); scene extraction allows more so fine
# detail survives the crop. Named here so the limits are documented in one spot.
MAX_RENDER_DPI = 600
MAX_EXTRACT_DPI = 1200


def _scene_has_labels_or_plan(ds_dir: Path, file_name: str) -> dict[str, Any]:
    labels_path = ds_dir / "labels" / f"{file_name}.json"
    if not labels_path.exists():
        labels_path = ds_dir / "labels" / f"{Path(file_name).stem}.json"
    label_count = 0
    if labels_path.exists():
        try:
            labels_doc = json.loads(labels_path.read_text())
            label_count = len(labels_doc.get("labels") or [])
        except Exception:  # noqa: BLE001
            label_count = 0
    plan_path = ds_dir / "plans" / f"{Path(file_name).stem}.plan.json"
    return {
        "label_count": label_count,
        "plan_exists": plan_path.exists(),
        "labels_path": str(labels_path) if labels_path.exists() else None,
        "plan_path": str(plan_path) if plan_path.exists() else None,
    }


def _crop_regression_warnings(
    prior_entry: dict[str, Any] | None,
    new_bbox: list[float],
    *,
    crop_intent: str,
) -> list[dict[str, Any]]:
    if not isinstance(prior_entry, dict):
        return []
    prior_bbox = (((prior_entry.get("crop_from") or {}).get("bbox_pdf_units")) or [])
    if not (isinstance(prior_bbox, list) and len(prior_bbox) == 4):
        return []
    try:
        px0, py0, px1, py1 = [float(v) for v in prior_bbox]
        nx0, ny0, nx1, ny1 = [float(v) for v in new_bbox]
    except Exception:  # noqa: BLE001
        return []
    prior_area = max(0.0, px1 - px0) * max(0.0, py1 - py0)
    new_area = max(0.0, nx1 - nx0) * max(0.0, ny1 - ny0)
    if prior_area <= 0:
        return []
    warnings: list[dict[str, Any]] = []
    area_ratio = new_area / prior_area
    if area_ratio < 0.75 and crop_intent == "scene_full_context":
        warnings.append({
            "kind": "crop_regression",
            "severity": "warning",
            "message": "new scene_full_context crop is substantially tighter than prior crop",
            "prior_bbox_pdf_units": prior_bbox,
            "new_bbox_pdf_units": new_bbox,
            "area_ratio": round(area_ratio, 3),
        })
    prior_contains_new = nx0 >= px0 and ny0 >= py0 and nx1 <= px1 and ny1 <= py1
    if prior_contains_new and area_ratio < 0.9 and crop_intent == "scene_full_context":
        warnings.append({
            "kind": "possible_context_loss",
            "severity": "warning",
            "message": "new crop is nested inside prior crop; check dimension chains and context marks before labeling",
            "prior_bbox_pdf_units": prior_bbox,
            "new_bbox_pdf_units": new_bbox,
        })
    return warnings


@router.get("/pdfs/incoming", tags=["pdfs"])
def list_incoming_pdfs():
    """List every per-house PDF intake bundle + its manifest. Each entry
    is the on-disk manifest.json content augmented with a `consolidated_url`
    pointing at the static-mounted PDF (when it exists)."""
    if not main.INCOMING_DIR.exists():
        return []
    out = []
    for d in sorted(main.INCOMING_DIR.iterdir()):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            m = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            continue
        m["key"] = d.name
        if m.get("consolidated_pdf"):
            m["consolidated_url"] = f"/static/pdfs/incoming/{d.name}/{m['consolidated_pdf']}"
        out.append(m)
    return out


@router.get("/pdfs/incoming/{key}", tags=["pdfs"])
def get_incoming_pdf(key: str):
    _safe_key(key)
    mp = main.INCOMING_DIR / key / "manifest.json"
    if not mp.exists():
        raise HTTPException(status_code=404, detail=f"No intake bundle for {key!r}")
    m = json.loads(mp.read_text())
    m["key"] = key
    if m.get("consolidated_pdf"):
        m["consolidated_url"] = f"/static/pdfs/incoming/{key}/{m['consolidated_pdf']}"
    return m


@router.post("/submit", tags=["pdfs"], status_code=201)
async def submit_localhost(
    files: list[UploadFile] = File(..., description="Drawings to submit"),
    contact_email: str | None = None,
    contact_name: str | None = None,
    license: str = "permission-granted",
    license_notes: str | None = None,
    training_use: bool = True,
    user_notes: str | None = None,
) -> dict:
    """Local-only customer-form endpoint. Mirrors form_api/main.py's
    /submit but without the API-key + rate limit since this whole API
    is single-user-localhost. Production deployments should use the
    standalone form_api process behind real auth — this route is
    purely a developer convenience so the form lives on :2500 too.

    Same output shape as the standalone endpoint: per-page decision +
    reasons so the SPA can re-prompt on a borderline submission.
    """
    import datetime as _dt2
    import secrets

    from ingestion.bundle import IngestProvenance, ingest_to_bundle
    from ingestion.config import load_profile
    from ingestion.normalize import sniff_kind

    if not files:
        raise HTTPException(status_code=400, detail="no files provided")
    if not training_use:
        raise HTTPException(status_code=400, detail="training_use consent is required")
    if license not in {"cc0", "cc-by", "cc-by-sa", "permission-granted", "other"}:
        raise HTTPException(status_code=400, detail=f"unknown license {license!r}")

    accepted = {"pdf", "jpeg", "png", "tiff", "heif"}
    submission_id = secrets.token_urlsafe(16)
    SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    staging = SUBMISSIONS_DIR / submission_id / "_staging"
    staging.mkdir(parents=True, exist_ok=True)

    staged: list[Path] = []
    for upload in files:
        raw = await upload.read()
        kind = sniff_kind(raw)
        if kind not in accepted:
            import shutil as _sh
            _sh.rmtree(staging.parent, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"{upload.filename!r}: unsupported file type",
            )
        safe_name = Path(upload.filename or f"upload-{len(staged)}").name
        out_path = staging / safe_name
        if out_path.exists():
            out_path = staging / f"{len(staged)}-{safe_name}"
        out_path.write_bytes(raw)
        staged.append(out_path)

    provenance = IngestProvenance(
        source_type="form",
        submitter={
            "submission_id": submission_id,
            "contact_email": contact_email,
            "contact_name": contact_name,
            "client_ip_hash": None,
            "user_agent": None,
        },
        consent={
            "training_use": training_use,
            "license": license,
            "license_notes": license_notes or "",
            "consented_at": _dt2.datetime.now(_dt2.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        user_notes=user_notes or "",
    )
    result = ingest_to_bundle(
        input_files=staged,
        bundle_root=SUBMISSIONS_DIR,
        bundle_key=submission_id,
        provenance=provenance,
        cfg=load_profile("strict-form"),
    )
    import shutil as _sh
    _sh.rmtree(staging, ignore_errors=True)

    return {
        "submission_id": submission_id,
        "page_count": result.manifest["page_count"],
        "pages": [
            {
                "page": p["page"],
                "decision": p["decision"],
                "reasons": p["decision_reasons"],
                "human_qa_required": p["human_qa_required"],
            }
            for p in result.manifest["pages"]
        ],
        "pass": result.pages_pass,
        "warn": result.pages_warn,
        "reject": result.pages_reject,
        "promoted": False,
    }


@router.get("/pdfs/submissions", tags=["pdfs"])
def list_submissions():
    """Every quarantined customer submission, newest-first. Each entry
    is the on-disk manifest augmented with consolidated_url + a derived
    `summary` describing pass/warn/reject counts so the SPA can render
    a queue at a glance."""
    if not SUBMISSIONS_DIR.exists():
        return []
    out = []
    for d in sorted(SUBMISSIONS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            m = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            continue
        m["submission_id"] = d.name
        if m.get("consolidated_pdf"):
            m["consolidated_url"] = f"/static/pdfs/submissions/{d.name}/{m['consolidated_pdf']}"
        pages = m.get("pages") or []
        m["summary"] = {
            "pass": sum(1 for p in pages if p.get("decision") == "pass"),
            "warn": sum(1 for p in pages if p.get("decision") == "warn"),
            "reject": sum(1 for p in pages if p.get("decision") == "reject"),
            "title_blocks_suspected": sum(
                1 for p in pages if (p.get("pii_flag") or {}).get("title_block_suspected")
            ),
        }
        out.append(m)
    return out


@router.get("/pdfs/submissions/{submission_id}", tags=["pdfs"])
def get_submission(submission_id: str):
    _safe_submission_id(submission_id)
    mp = SUBMISSIONS_DIR / submission_id / "manifest.json"
    if not mp.exists():
        raise HTTPException(status_code=404, detail=f"No submission {submission_id!r}")
    m = json.loads(mp.read_text())
    m["submission_id"] = submission_id
    if m.get("consolidated_pdf"):
        m["consolidated_url"] = (
            f"/static/pdfs/submissions/{submission_id}/{m['consolidated_pdf']}"
        )
    return m


@router.post("/pdfs/submissions/{submission_id}/promote", tags=["pdfs"], status_code=201)
def promote_submission(
    submission_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict:
    """Promote a quarantined submission into the corpus.

    Body (all optional):
      house_key:        target key — defaults to the next free house-NN
      redact_title_block: if true, re-runs ingestion with the redaction
                          hook applied (only meaningful when the
                          submission was flagged)
      user_notes:       supersedes the submission's notes on the new bundle

    The submission directory is NOT deleted; we copy the rectified PDF +
    originals into the new incoming bundle and stamp the submission
    manifest with `promoted_to: <house_key>`. Round-trip stays auditable.
    """
    _safe_submission_id(submission_id)
    src = SUBMISSIONS_DIR / submission_id
    src_manifest_path = src / "manifest.json"
    if not src_manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"No submission {submission_id!r}")
    submission_manifest = json.loads(src_manifest_path.read_text())

    if submission_manifest.get("promoted_to"):
        raise HTTPException(
            status_code=409,
            detail=f"already promoted to {submission_manifest['promoted_to']!r}",
        )

    house_key = payload.get("house_key") or _next_free_house_key()
    _safe_key(house_key)

    target = main.INCOMING_DIR / house_key
    if target.exists():
        raise HTTPException(
            status_code=409,
            detail=f"target bundle {house_key!r} already exists",
        )

    redact = bool(payload.get("redact_title_block"))
    if redact:
        # Re-run ingestion on the originals so the redaction is applied
        # to the rectified PDF — never edit the quarantined artifact in
        # place; we want a clean provenance trail.
        from ingestion.bundle import IngestProvenance, ingest_to_bundle
        from ingestion.config import load_profile

        source_dir = src / "source"
        originals = sorted(source_dir.iterdir()) if source_dir.exists() else []
        if not originals:
            raise HTTPException(
                status_code=409,
                detail="cannot redact: submission has no preserved source originals",
            )
        provenance = IngestProvenance(
            source_type="form",
            submitter=submission_manifest.get("submitter"),
            consent=submission_manifest.get("consent"),
            user_notes=payload.get("user_notes")
                       or submission_manifest.get("user_notes", ""),
        )
        cfg = load_profile()  # re-render uses the dev profile by default
        result = ingest_to_bundle(
            input_files=originals,
            bundle_root=main.INCOMING_DIR,
            bundle_key=house_key,
            provenance=provenance,
            cfg=cfg,
            redact_title_blocks=True,
        )
        new_manifest = result.manifest
    else:
        # Cheap path: copy the rectified PDF + source/ verbatim, rewrite
        # the manifest's house_key + state.
        import shutil
        target.mkdir(parents=True, exist_ok=True)
        consolidated_name = submission_manifest.get("consolidated_pdf")
        if consolidated_name:
            shutil.copyfile(src / consolidated_name, target / consolidated_name)
        if (src / "source").exists():
            shutil.copytree(src / "source", target / "source")
        new_manifest = dict(submission_manifest)
        new_manifest["house_key"] = house_key
        new_manifest["state"] = "partial"
        new_manifest["extracted_scenes"] = []
        if payload.get("user_notes"):
            new_manifest["user_notes"] = payload["user_notes"]
        atomic_write_json(target / "manifest.json", new_manifest)

    # Stamp the source submission so the audit trail is durable.
    submission_manifest["promoted_to"] = house_key
    submission_manifest["promoted_at"] = _now_iso()
    atomic_write_json(src_manifest_path, submission_manifest)

    return {
        "promoted_to": house_key,
        "consolidated_url": (
            f"/static/pdfs/incoming/{house_key}/{new_manifest.get('consolidated_pdf')}"
            if new_manifest.get("consolidated_pdf") else None
        ),
        "redacted": redact,
    }


@router.delete("/pdfs/submissions/{submission_id}", tags=["pdfs"], status_code=204)
def delete_submission(submission_id: str) -> None:
    """Drop a quarantined submission outright. Use for clear-spam / GDPR
    erasure. Refuses if the submission has already been promoted —
    delete the resulting incoming bundle separately if needed."""
    _safe_submission_id(submission_id)
    src = SUBMISSIONS_DIR / submission_id
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"No submission {submission_id!r}")
    manifest_path = src / "manifest.json"
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text())
            if m.get("promoted_to"):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "submission has already been promoted to "
                        f"{m['promoted_to']!r}; delete that bundle separately"
                    ),
                )
        except json.JSONDecodeError:
            pass
    import shutil
    shutil.rmtree(src)
    return None


def _safe_submission_id(submission_id: str) -> None:
    if not submission_id or "/" in submission_id or ".." in submission_id or "\\" in submission_id:
        raise HTTPException(status_code=400, detail=f"bad submission_id {submission_id!r}")


def _next_free_house_key() -> str:
    """Lowest unused `house-<N>` across both the dataset and the intake
    trees. Lets the user upload a brand-new house without picking a key."""
    used: set[int] = set()
    for d in (main.DATASET_DIR, main.INCOMING_DIR):
        if d.exists():
            for p in d.iterdir():
                m = re.match(r"house-(\d+)$", p.name)
                if m:
                    used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"house-{n}"


def _pdf_page_count(path: Path) -> int | None:
    try:
        import fitz  # PyMuPDF
        with fitz.open(path) as doc:
            return doc.page_count
    except Exception:  # noqa: BLE001
        return None


@router.post("/pdfs", tags=["pdfs"], status_code=201)
async def upload_pdfs(
    files: list[UploadFile] = File(..., description="One or more PDF files"),
    house_key: str | None = None,
    notes: str | None = None,
):
    """R1.2 — accept one or more PDFs and stage them under
    `data/pdfs/incoming/<house_key>/source/`. When house_key is omitted
    the next free key is auto-allocated. When multiple files share the
    same house_key they're consolidated into one PDF (R1.3); a single
    file becomes the consolidated PDF directly.

    Returns the resulting bundle manifest.
    """
    if not files:
        raise HTTPException(status_code=400, detail="no files provided")
    key = house_key or _next_free_house_key()
    _safe_key(key)
    main.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    bundle = main.INCOMING_DIR / key
    source = bundle / "source"
    source.mkdir(parents=True, exist_ok=True)

    # Read existing manifest so re-uploads merge cleanly. Source filenames
    # accumulate, consolidated PDF gets re-merged.
    manifest = _read_manifest(key) or {
        "schema_version": "1.0",
        "house_key": key,
        "consolidated_pdf": None,
        "source_filenames": [],
        "uploaded_at": _now_iso(),
        "page_count": None,
        "state": "pending",
        "user_notes": notes or "",
        "extracted_scenes": [],
    }
    if notes:
        manifest["user_notes"] = notes

    # R1.7 — dedup by byte hash within this bundle so the same PDF can't
    # land twice in source/.
    existing_hashes: dict[str, str] = {}
    for p in source.glob("*.pdf"):
        existing_hashes[hashlib.sha256(p.read_bytes()).hexdigest()] = p.name

    saved_names: list[str] = []
    for upload in files:
        raw = await upload.read()
        if not raw.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail=f"{upload.filename!r} is not a PDF")
        h = hashlib.sha256(raw).hexdigest()
        if h in existing_hashes:
            saved_names.append(existing_hashes[h])
            continue
        # Strip path components from the upload name.
        safe_name = Path(upload.filename or f"upload-{h[:8]}.pdf").name
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
        # If the name collides with an existing different file, prefix the
        # hash.
        if (source / safe_name).exists():
            safe_name = f"{h[:8]}-{safe_name}"
        (source / safe_name).write_bytes(raw)
        saved_names.append(safe_name)
        existing_hashes[h] = safe_name

    # Accumulate source filenames (dedupe).
    src_names_set = {*manifest.get("source_filenames", []), *saved_names}
    manifest["source_filenames"] = sorted(src_names_set)

    # R1.3 — consolidate into one PDF. Single source files become the
    # consolidated PDF directly; multiple are merged. Always overwrites
    # so the consolidated artifact always reflects the latest source set.
    consolidated_name = f"{key}.pdf"
    consolidated_path = bundle / consolidated_name
    src_paths = sorted(source.glob("*.pdf"))
    if len(src_paths) == 1:
        consolidated_path.write_bytes(src_paths[0].read_bytes())
    else:
        try:
            from pypdf import PdfReader, PdfWriter
            writer = PdfWriter()
            for sp in src_paths:
                reader = PdfReader(str(sp))
                for page in reader.pages:
                    writer.add_page(page)
            with consolidated_path.open("wb") as f:
                writer.write(f)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"PDF merge failed: {e}")

    manifest["consolidated_pdf"] = consolidated_name
    manifest["page_count"] = _pdf_page_count(consolidated_path)
    manifest["state"] = _bundle_state(key, manifest)
    _write_manifest(key, manifest)
    manifest["key"] = key
    manifest["consolidated_url"] = f"/static/pdfs/incoming/{key}/{consolidated_name}"
    return manifest


@router.put("/pdfs/incoming/{key}/manifest", tags=["pdfs"])
def update_incoming_manifest(key: str, payload: dict[str, Any] = Body(...)):
    """R1 — edit user_notes / state on an existing bundle. Other fields
    are server-managed and rejected to avoid the UI corrupting state."""
    _safe_key(key)
    manifest = _read_manifest(key)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"No intake bundle for {key!r}")
    EDITABLE = {"user_notes", "state"}
    bad = set(payload) - EDITABLE
    if bad:
        raise HTTPException(status_code=400, detail=f"non-editable keys: {sorted(bad)}")
    for k in EDITABLE & payload.keys():
        manifest[k] = payload[k]
    _write_manifest(key, manifest)
    manifest["key"] = key
    if manifest.get("consolidated_pdf"):
        manifest["consolidated_url"] = f"/static/pdfs/incoming/{key}/{manifest['consolidated_pdf']}"
    return manifest


@router.delete("/pdfs/incoming/{key}", tags=["pdfs"], status_code=204)
def delete_incoming_bundle(key: str) -> None:
    """R1 — remove an entire intake bundle (source PDFs, consolidated
    PDF, manifest). Does NOT touch data/dataset/<key>/. The user has to
    delete extracted dataset scenes separately."""
    _safe_key(key)
    bundle = main.INCOMING_DIR / key
    if not bundle.exists():
        raise HTTPException(status_code=404, detail=f"No intake bundle for {key!r}")
    import shutil
    shutil.rmtree(bundle)
    return None


def _consolidated_path(key: str) -> Path:
    m = _read_manifest(key)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No intake bundle for {key!r}")
    name = m.get("consolidated_pdf")
    if not name:
        raise HTTPException(status_code=409, detail=f"{key} has no consolidated PDF yet")
    p = main.INCOMING_DIR / key / name
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Consolidated PDF missing for {key!r}")
    return p


@router.get("/pdfs/{key}/page/{n}", tags=["pdfs"])
def render_pdf_page(key: str, n: int, dpi: int = 300) -> Response:
    """R2 — render PDF page `n` (1-indexed) at the given DPI as a JPEG.
    Cached on disk under tmp/pdf-cache/<key>/page-<n>-<dpi>.jpg keyed on
    the source PDF's mtime so edits invalidate stale crops.

    Quality-first: default 300 dpi (was 96) so the pre-extraction page view
    + grid coordinates are read off near-native resolution, not a coarse
    proxy. Cap stays 600 (>= the ~429 dpi native scan; higher only upscales)."""
    _safe_key(key)
    if dpi <= 0 or dpi > MAX_RENDER_DPI:
        raise HTTPException(status_code=400, detail=f"dpi must be in (0, {MAX_RENDER_DPI}]")
    pdf = _consolidated_path(key)
    pdf_mtime = pdf.stat().st_mtime_ns
    cache_root = PDF_CACHE / key
    cache_root.mkdir(parents=True, exist_ok=True)
    out = cache_root / f"page-{n}-{dpi}.jpg"
    sentinel = out.with_suffix(".mtime")
    if not out.exists() or not sentinel.exists() or sentinel.read_text() != str(pdf_mtime):
        import fitz
        with fitz.open(pdf) as doc:
            if n < 1 or n > doc.page_count:
                raise HTTPException(status_code=404, detail=f"page {n} out of range (1..{doc.page_count})")
            page = doc.load_page(n - 1)
            scale = dpi / 72.0
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            # Issue #24: PyMuPDF yields an all-white pixmap on AcroForm-
            # corrupt scans; recover the page via poppler before saving.
            if _pixmap_is_blank(pix):
                fb = _render_page_poppler(pdf, n, dpi)
                if fb is not None and not _image_is_blank(fb):
                    fb.save(str(out), format="JPEG", quality=95)
                    sentinel.write_text(str(pdf_mtime))
                    return FileResponse(str(out), media_type="image/jpeg")
            pix.pil_save(str(out), format="JPEG", quality=95)
        sentinel.write_text(str(pdf_mtime))
    return FileResponse(str(out), media_type="image/jpeg")


@router.get("/pdfs/{key}/page/{n}/grid", tags=["pdfs"])
def render_pdf_page_grid(
    key: str,
    n: int,
    dpi: int = 300,
    tiers: str = "broad,finer,detail",
    region: str | None = None,
    max_dim: int = 1600,
) -> Response:
    """Same as /datasets/.../grid but for a PDF page (used for scene
    identification at inventory / extract). The grid coordinate labels are in
    pixels at the rendered DPI; downstream `extract_scenes` MCP tool
    converts to PDF units using the same DPI."""
    _safe_key(key)
    if dpi <= 0 or dpi > MAX_RENDER_DPI:
        raise HTTPException(status_code=400, detail=f"dpi must be in (0, {MAX_RENDER_DPI}]")
    if not 100 <= max_dim <= 8000:
        raise HTTPException(status_code=400, detail="max_dim must be in [100, 8000]")
    parsed_tiers = _parse_tiers(tiers)
    parsed_region = _parse_region(region)
    pdf = _consolidated_path(key)
    pdf_mtime = pdf.stat().st_mtime_ns
    cache_root = GRID_CACHE / "pdf" / key
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_name = (
        f"page-{n}-dpi{dpi}"
        f"-r{region or 'full'}"
        f"-t{'_'.join(parsed_tiers)}"
        f"-m{max_dim}.png"
    )
    out = cache_root / cache_name
    sentinel = out.with_suffix(".mtime")
    if not out.exists() or not sentinel.exists() or sentinel.read_text() != str(pdf_mtime):
        import fitz
        from PIL import Image as PILImage
        from .grid_render import render_grid_overlay
        with fitz.open(pdf) as doc:
            if n < 1 or n > doc.page_count:
                raise HTTPException(status_code=404, detail=f"page {n} out of range (1..{doc.page_count})")
            page = doc.load_page(n - 1)
            scale = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            page_img = PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)
            # Issue #24: recover an AcroForm-corrupt blank page via poppler
            # so the grid overlay sits on real content, not white.
            if _pixmap_is_blank(pix):
                fb = _render_page_poppler(pdf, n, dpi)
                if fb is not None and not _image_is_blank(fb):
                    page_img = fb
        overlay = render_grid_overlay(
            page_img,
            tiers=parsed_tiers,
            region=parsed_region,
            max_dim=max_dim,
            source_dpi=dpi,
        )
        overlay.save(out, format="PNG", optimize=True)
        sentinel.write_text(str(pdf_mtime))
    return FileResponse(str(out), media_type="image/png")


@router.get("/pdfs/{key}/info", tags=["pdfs"])
def pdf_info(key: str) -> dict:
    """R2 — quick metadata: page count, per-page width/height in PDF
    units (1 unit = 1/72 inch). The extractor needs page geometry to
    convert client bboxes (image pixels) back to PDF coordinates."""
    _safe_key(key)
    pdf = _consolidated_path(key)
    import fitz
    pages = []
    with fitz.open(pdf) as doc:
        for i, page in enumerate(doc.pages(), start=1):
            r = page.rect
            pages.append({"page": i, "width_pt": r.width, "height_pt": r.height})
    return {"key": key, "page_count": len(pages), "pages": pages}


def _slug_token(s: str | None, fallback: str) -> str:
    s = (s or fallback).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or fallback


def _pixmap_is_blank(pix) -> bool:
    """True when a rendered pixmap is a uniform bright canvas — i.e. the
    render produced no drawing content (issue #12).

    PyMuPDF can fail to rasterize a page (e.g. a corrupt content stream in
    a merged PDF logs 'object is not a stream' to stderr) and silently
    return an all-white pixmap. Saving that yields a blank 'labeled' scene
    the vision-LLM can't read. We treat a near-uniform bright region as a
    failed render: even a faint pencil scan leaves non-uniform pixels, so
    this won't false-positive on real (if sparse) content.
    """
    import numpy as np
    a = np.frombuffer(pix.samples, dtype=np.uint8)
    if a.size == 0:
        return True
    return bool(int(a.min()) >= 250 and int(a.max()) - int(a.min()) <= 2)


def _image_is_blank(img) -> bool:
    """`_pixmap_is_blank` for a PIL.Image — used to check the poppler
    fallback raster (issue #24)."""
    import numpy as np
    a = np.asarray(img.convert("RGB"), dtype=np.uint8).reshape(-1)
    if a.size == 0:
        return True
    return bool(int(a.min()) >= 250 and int(a.max()) - int(a.min()) <= 2)


def _clipped_borders(img, ink_threshold: int = CLIP_INK_THRESHOLD,
                     ink_frac: float = CLIP_INK_FRAC) -> dict[str, bool]:
    """Which borders of a rendered crop have a drawing stroke *crossing* them.

    Returns {'left','right','top','bottom': bool}. A True means a stroke
    runs through that edge — the drawing is almost certainly cut there.

    The signal distinguishes a clipped stroke from a drawing's own frame
    line: a clip is a stroke that hits the very edge AND continues inward,
    so we count the fraction of border positions (columns for top/bottom,
    rows for left/right) where the outermost edge pixels are ink *and* that
    ink continues into an inner band just past the edge. A frame line runs
    parallel to and only at the edge — its ink does not penetrate inward, so
    it doesn't trip the detector and the crop won't over-expand. Works on a
    grayscale PIL.Image.
    """
    import numpy as np
    a = np.asarray(img.convert("L"), dtype=np.uint8)
    h, w = a.shape
    if h == 0 or w == 0:
        return {"left": False, "right": False, "top": False, "bottom": False}
    strip = max(2, int(round(min(h, w) * CLIP_BORDER_FRAC)))
    edge = max(1, strip // 2)            # outermost band that must be inked
    sh = min(strip, max(1, h - 1))
    sw = min(strip, max(1, w - 1))
    eh = min(edge, sh)
    ew = min(edge, sw)
    ink = a < ink_threshold

    def crossing_frac(edge_band, inner_band, axis: int) -> float:
        # positions inked at the very edge that also continue inward
        if edge_band.size == 0 or inner_band.size == 0:
            return 0.0
        crossing = edge_band.any(axis=axis) & inner_band.any(axis=axis)
        return float(crossing.mean())

    return {
        "top": crossing_frac(ink[:eh, :], ink[eh:sh, :], 0) >= ink_frac,
        "bottom": crossing_frac(ink[h - eh:, :], ink[h - sh:h - eh, :], 0) >= ink_frac,
        "left": crossing_frac(ink[:, :ew], ink[:, ew:sw], 1) >= ink_frac,
        "right": crossing_frac(ink[:, w - ew:], ink[:, w - sw:w - ew], 1) >= ink_frac,
    }


def _grow_bbox(bbox, borders, page_w: float, page_h: float,
               grow_frac: float = CLIP_GROW_FRAC) -> list[float]:
    """Grow `bbox` (PDF units, top-left origin) toward each clipped border,
    clamped to the page [0,0,page_w,page_h]. Returns the new bbox."""
    x0, y0, x1, y1 = (float(v) for v in bbox)
    dw = (x1 - x0) * grow_frac
    dh = (y1 - y0) * grow_frac
    if borders.get("left"):
        x0 = max(0.0, x0 - dw)
    if borders.get("right"):
        x1 = min(page_w, x1 + dw)
    if borders.get("top"):
        y0 = max(0.0, y0 - dh)
    if borders.get("bottom"):
        y1 = min(page_h, y1 + dh)
    return [x0, y0, x1, y1]


def _render_crop(page, bbox, dpi: int):
    """Render `bbox` (PDF units) of a fitz page at `dpi` to a PIL.Image."""
    import fitz
    scale = dpi / 72.0
    x0, y0, x1, y1 = (float(v) for v in bbox)
    pix = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        clip=fitz.Rect(x0, y0, x1, y1), alpha=False,
    )
    return pix.pil_image() if hasattr(pix, "pil_image") else _pix_to_pil(pix)


def _pix_to_pil(pix):
    from PIL import Image as PILImage
    mode = "RGB" if pix.n >= 3 else "L"
    return PILImage.frombytes(mode, (pix.width, pix.height), pix.samples)


def _expand_bbox_for_clip(page, bbox, dpi: int, page_w: float, page_h: float):
    """If the crop at `bbox` cuts the drawing at a border, grow the bbox
    toward the clipped border(s) and re-crop, iterating until no border is
    clipped (or we hit the page edge / iteration cap). Adds a final small
    margin so the unclipped drawing has a little breathing room.

    Returns (final_bbox, expanded: bool, history: list[dict]) — `history`
    records the clipped borders seen at each iteration for diagnostics.
    """
    cur = [float(v) for v in bbox]
    history: list[dict] = []
    expanded = False
    for _ in range(CLIP_MAX_ITERS):
        img = _render_crop(page, cur, dpi)
        borders = _clipped_borders(img)
        # Only borders that aren't already pinned to the page edge can grow.
        x0, y0, x1, y1 = cur
        growable = {
            "left": borders["left"] and x0 > 0,
            "right": borders["right"] and x1 < page_w,
            "top": borders["top"] and y0 > 0,
            "bottom": borders["bottom"] and y1 < page_h,
        }
        history.append({k: v for k, v in borders.items()})
        if not any(growable.values()):
            break
        cur = _grow_bbox(cur, growable, page_w, page_h)
        expanded = True
    if expanded:
        # Breathing margin once the drawing no longer touches a border, so
        # the previously-cut feature (e.g. the ridge) isn't flush to the edge.
        mx = (cur[2] - cur[0]) * CLIP_MARGIN_FRAC
        my = (cur[3] - cur[1]) * CLIP_MARGIN_FRAC
        cur = [
            max(0.0, cur[0] - mx), max(0.0, cur[1] - my),
            min(page_w, cur[2] + mx), min(page_h, cur[3] + my),
        ]
    return cur, expanded, history


def _render_page_poppler(pdf, n: int, dpi: int, clip_pdf_units=None):
    """Render PDF page `n` (1-indexed) at `dpi` via poppler's `pdftoppm`
    and return a PIL.Image (issue #24).

    Recovery path for PDFs that PyMuPDF cannot rasterize — e.g. scanned
    municipal Bauakten wrapped in a malformed AcroForm/Fields layer, where
    `fitz.Page.get_pixmap` silently yields an all-white pixmap and
    `get_images` reports zero embedded images, but the page content (the
    embedded JPEG scans) is intact and poppler reads it fine.

    `pdftoppm -r <dpi>` rasterizes the page on the same `dpi/72` pixel
    grid PyMuPDF uses, so cropping `clip_pdf_units` (a 4-tuple in PDF
    units, top-left origin) with `dpi/72` scaling yields pixel-identical
    geometry to the PyMuPDF `clip=` path — `crop_from`/coordinate
    semantics stay consistent across both renderers.

    Returns None if poppler is unavailable or fails (caller falls back to
    the original PyMuPDF behaviour / 422).
    """
    import glob
    import os
    import shutil
    import subprocess
    import tempfile
    from PIL import Image as PILImage

    if shutil.which("pdftoppm") is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        prefix = os.path.join(td, "pg")
        try:
            subprocess.run(
                ["pdftoppm", "-f", str(n), "-l", str(n),
                 "-r", str(int(dpi)), "-png", str(pdf), prefix],
                check=True, capture_output=True, timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return None
        files = sorted(glob.glob(prefix + "*"))
        if not files:
            return None
        img = PILImage.open(files[0]).convert("RGB")
        img.load()
    if clip_pdf_units is not None:
        x0, y0, x1, y1 = (float(v) for v in clip_pdf_units)
        s = dpi / 72.0
        box = (
            max(0, int(round(x0 * s))),
            max(0, int(round(y0 * s))),
            min(img.width, int(round(x1 * s))),
            min(img.height, int(round(y1 * s))),
        )
        if box[2] > box[0] and box[3] > box[1]:
            img = img.crop(box)
    return img


@router.post("/pdfs/{key}/extract", tags=["pdfs"], status_code=201)
def extract_scenes(key: str, payload: dict[str, Any] = Body(...)) -> dict:
    """R2 — crop scenes out of the consolidated PDF.

    Body: {"items": [{
      "page": 1,                     # 1-indexed
      "bbox_pdf_units": [x0, y0, x1, y1],
      "kind": "floorplan"|"elevation"|"section"|"detail",
      "view": "north"|...,           # optional
      "floor": "kg"|"ug"|...,        # optional
      "title": str,                  # optional
      "slug_override": str,          # optional, used as the slug if set
      "dpi": 600,                    # optional output raster DPI, default 600
      "format": "jpg"|"png",         # optional image format, default jpg
      "allow_blank": false,          # optional; bypass the blank-render guard
      "no_clip_expand": false,       # optional; bypass clip-detection bbox
                                     #   auto-expansion (issue #25)
      "bbox_is_authoritative": false # optional (V1.1); the caller's bbox is
                                     #   final — never auto-expand it. Alias
                                     #   of no_clip_expand, intent-named for
                                     #   the vision-LLM-chooses-extent flow.
    }]}

    Per issue #25: the segmentation bbox can under-shoot a tall drawing
    (cutting the roof apex so the Firsthöhe/ridge never lands in any
    raster). Before rendering, if significant ink touches a crop border the
    bbox is grown toward that border and re-cropped until the drawing no
    longer touches an edge (clamped to the page). Expansion only grows the
    rect within the page — an explicit bbox re-extract is still honoured, it
    just can't be clipped. The recorded crop_from bbox is the final rect, so
    a single clipped scene can be re-captured by re-extracting with the same
    slug_override. Set `no_clip_expand: true` to disable.

    Per issue #12: a crop that renders to a blank/uniform canvas (a failed
    rasterization — e.g. a corrupt content stream in the merged PDF) is
    rejected with 422 rather than silently written, so a blank scene never
    masquerades as a labeled one. Set `allow_blank: true` to force.

    For each item, crops the PDF page at the bbox, writes the scene image into
    data/dataset/<key>/, and appends a DatasetDrawing entry to the
    dataset manifest. Idempotent on (page, slug): re-extracting overwrites
    the image and updates the manifest entry while leaving any sibling
    labels.json intact.

    Returns the updated dataset manifest entries + the intake bundle's
    new state."""
    _safe_key(key)
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="items must be a non-empty list")
    pdf = _consolidated_path(key)
    ds_dir = main.DATASET_DIR / key
    ds_dir.mkdir(parents=True, exist_ok=True)
    ds_manifest_path = ds_dir / "manifest.json"
    if ds_manifest_path.exists():
        ds_manifest = json.loads(ds_manifest_path.read_text())
    else:
        ds_manifest = {"key": key, "drawings": []}
    drawings: list[dict] = ds_manifest.setdefault("drawings", [])

    import fitz
    out_entries: list[dict] = []
    used_slugs: set[str] = set()
    # Seed used_slugs from existing manifest so we can dedup correctly.
    for d in drawings:
        st = Path(d.get("file", "")).stem
        used_slugs.add(st)
    with fitz.open(pdf) as doc:
        for raw in items:
            page_n = int(raw.get("page", 0))
            if page_n < 1 or page_n > doc.page_count:
                raise HTTPException(status_code=400, detail=f"page {page_n} out of range")
            bbox = raw.get("bbox_pdf_units")
            if not (isinstance(bbox, list) and len(bbox) == 4):
                raise HTTPException(status_code=400, detail="bbox_pdf_units must be [x0,y0,x1,y1]")
            x0, y0, x1, y1 = (float(v) for v in bbox)
            if not (x1 > x0 and y1 > y0):
                raise HTTPException(status_code=400, detail="bbox must have positive area")
            kind = (raw.get("kind") or "detail").strip().lower()
            crop_intent = str(raw.get("crop_intent") or ("scene_full_context" if kind == "floorplan" else "drawing_only")).strip()
            if crop_intent not in {"scene_full_context", "drawing_only", "detail_crop", "authoritative_manual"}:
                raise HTTPException(
                    status_code=400,
                    detail="crop_intent must be scene_full_context, drawing_only, detail_crop, or authoritative_manual",
                )
            view = raw.get("view")
            floor = raw.get("floor")
            dpi = int(raw.get("dpi", 600))
            if dpi <= 0 or dpi > MAX_EXTRACT_DPI:
                raise HTTPException(status_code=400, detail="dpi out of range")
            fmt = str(raw.get("format") or "jpg").strip().lower()
            if fmt == "jpeg":
                fmt = "jpg"
            if fmt not in {"jpg", "png"}:
                raise HTTPException(status_code=400, detail="format must be 'jpg' or 'png'")

            # Slug derivation. The user may override with an explicit slug
            # for re-extraction; otherwise we synthesize one from
            # kind/view/floor + a sequence suffix.
            override = raw.get("slug_override")
            base_slug = override or f"{kind}-{_slug_token(view or floor, kind)}"
            base_slug = re.sub(r"[^a-z0-9-]+", "-", base_slug.lower()).strip("-")
            # A scene's filename stem is `{key}-{base_slug}`. When the caller
            # passes an explicit slug_override to RE-EXTRACT an existing scene,
            # they pass the full stem (e.g. "house-22-floorplan-eg"), which
            # already starts with "{key}-". Re-prepending the key here produced
            # a phantom double-prefixed scene ("house-22-house-22-floorplan-eg")
            # and left the real scene's crop untouched — so re-cropping silently
            # did nothing. Strip a leading "{key}-" from the override so the
            # full stem resolves to the SAME file and the re-extract overwrites
            # the intended scene.
            key_prefix = f"{key}-"
            if override and base_slug.startswith(key_prefix):
                base_slug = base_slug[len(key_prefix):]
            full = f"{key}-{base_slug}"
            if not override:
                # Append -2, -3, ... if collision.
                slug = full
                n = 2
                while slug in used_slugs:
                    slug = f"{full}-{n}"
                    n += 1
                used_slugs.add(slug)
            else:
                slug = full
                used_slugs.add(slug)

            _ext = "png" if fmt == "png" else "jpg"
            file_name = f"{slug}.{_ext}"
            out_path = ds_dir / file_name
            existing_idx = next((i for i, d in enumerate(drawings) if d.get("file") == file_name), None)
            existing_entry = drawings[existing_idx] if existing_idx is not None else None
            crop_warnings = _crop_regression_warnings(
                existing_entry,
                [x0, y0, x1, y1],
                crop_intent=crop_intent,
            )
            if existing_idx is not None:
                overwrite_state = _scene_has_labels_or_plan(ds_dir, file_name)
                if (
                    (overwrite_state["label_count"] > 0 or overwrite_state["plan_exists"])
                    and not bool(raw.get("confirm_reextract_existing_scene"))
                    and not bool(raw.get("allow_destructive_reextract"))
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "re-extracting this scene would overwrite a crop that already has labels or a scene plan",
                            "scene_file": file_name,
                            "label_count": overwrite_state["label_count"],
                            "plan_exists": overwrite_state["plan_exists"],
                            "required_confirmation": "confirm_reextract_existing_scene=true",
                            "crop_warnings": crop_warnings,
                        },
                    )

            page = doc.load_page(page_n - 1)

            # Issue #25: clip detection + bbox auto-expansion. The
            # segmentation bbox can under-shoot a tall drawing (cutting the
            # roof apex → ridge/Firsthöhe never captured). If significant
            # ink touches a crop border, grow the bbox toward that border
            # and re-crop until the drawing no longer touches an edge (or we
            # hit the page extent). `no_clip_expand: true` opts out, and an
            # explicit re-extract with the desired bbox is still honoured —
            # expansion only ever *grows* the rect within the page, never
            # shrinks it. The recorded crop_from bbox is the final rect.
            page_rect = page.rect
            page_w, page_h = float(page_rect.width), float(page_rect.height)
            clip_diag: dict | None = None
            # V1.1: when the caller (the vision-LLM) has chosen the bbox
            # deliberately, that extent is authoritative — do NOT let #25
            # auto-expand grow it (which would override the chosen crop,
            # e.g. to the whole page). `bbox_is_authoritative` is the
            # intent-named alias of `no_clip_expand`; either disables the
            # auto-expansion.
            _bbox_authoritative = bool(raw.get("no_clip_expand")) or bool(
                raw.get("bbox_is_authoritative")
            )
            if not _bbox_authoritative:
                grown, expanded, history = _expand_bbox_for_clip(
                    page, [x0, y0, x1, y1], dpi, page_w, page_h
                )
                if expanded:
                    x0, y0, x1, y1 = grown
                    clip_diag = {"expanded": True, "iters": len(history),
                                 "history": history}

            # The PDF rect uses top-left origin. fitz.Matrix scales; clip
            # restricts the rendered region to the bbox.
            scale = dpi / 72.0
            clip = fitz.Rect(x0, y0, x1, y1)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
            # Issue #12: refuse to write a blank crop. A failed render
            # (e.g. corrupt content stream in the merged PDF) yields an
            # all-white pixmap; saving it produces a blank scene the agent
            # can't label but that still reports as `labeled`. Fail loudly
            # so the corruption is visible at extraction time. `allow_blank`
            # opts out for the rare intentionally-empty region.
            #
            # Issue #24 (recovery): when PyMuPDF returns blank — the common
            # AcroForm-corrupt scanned-archive case where get_pixmap yields
            # an all-white raster but the embedded scans are intact — first
            # try a poppler render of the page, cropped to the same bbox at
            # the same DPI (pixel-identical geometry to the PyMuPDF clip).
            # Only fall through to the 422 if BOTH renderers come back
            # blank (a genuinely empty region vs. a PyMuPDF-only failure).
            if not bool(raw.get("allow_blank")) and _pixmap_is_blank(pix):
                fb = _render_page_poppler(
                    pdf, page_n, dpi, clip_pdf_units=(x0, y0, x1, y1)
                )
                if fb is not None and not _image_is_blank(fb):
                    if str(out_path).lower().endswith(".png"):
                        fb.save(str(out_path), format="PNG", optimize=True)
                    else:
                        fb.save(str(out_path), format="JPEG", quality=100)
                else:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"page {page_n} rendered blank for {file_name} — no "
                            "content (the page's content stream may be corrupt in "
                            "the merged PDF, or the bbox is empty). Crop not "
                            "written. Re-merge the source PDF or fix the bbox; "
                            "pass allow_blank=true to force."
                        ),
                    )
            else:
                if str(out_path).lower().endswith(".png"):
                    pix.pil_save(str(out_path), format="PNG")
                else:
                    pix.pil_save(str(out_path), format="JPEG", quality=100)

            entry = {
                "file": file_name,
                "kind": kind,
                "source": "pdf",
                "view": view,
                "floor": floor,
                "title": raw.get("title"),
                "imported_at": _now_iso(),
                "crop_intent": crop_intent,
                **({"crop_warnings": crop_warnings} if crop_warnings else {}),
                "crop_from": {
                    "pdf_file": pdf.name,
                    "page": page_n,
                    "bbox_pdf_units": [x0, y0, x1, y1],
                    "dpi": dpi,
                    **({"clip_expand": clip_diag} if clip_diag else {}),
                },
            }
            # Replace existing entry with same file name (re-extract) else append.
            if existing_idx is not None:
                drawings[existing_idx] = entry
            else:
                drawings.append(entry)
            out_entries.append(entry)

    atomic_write_json(ds_manifest_path, ds_manifest)

    # Update intake state.
    intake = _read_manifest(key) or {}
    intake.setdefault("extracted_scenes", [])
    # Replace any same-(page,scene_file) records.
    existing_scene_files = {e["file"] for e in out_entries}
    intake["extracted_scenes"] = [
        s for s in intake["extracted_scenes"]
        if s.get("scene_file") not in existing_scene_files
    ]
    for e in out_entries:
        intake["extracted_scenes"].append({
            "page": e["crop_from"]["page"],
            "bbox_pdf_units": e["crop_from"]["bbox_pdf_units"],
            "scene_file": e["file"],
        })
    intake["state"] = _bundle_state(key, intake)
    _write_manifest(key, intake)

    return {"extracted": out_entries, "intake_state": intake["state"]}


def _purge_old_recycle() -> int:
    """Sweep recycle/* older than RECYCLE_TTL_SEC. Called opportunistically
    on every recycle write/read. Returns the count of pruned bundles."""
    if not RECYCLE_DIR.exists():
        return 0
    import time
    cutoff = time.time() - RECYCLE_TTL_SEC
    pruned = 0
    for d in list(RECYCLE_DIR.rglob("*")):
        if not d.is_dir() or d == RECYCLE_DIR:
            continue
        try:
            if d.stat().st_mtime < cutoff:
                for f in d.iterdir():
                    f.unlink(missing_ok=True)
                d.rmdir()
                pruned += 1
        except OSError:
            pass
    return pruned


def _safe_recycle_path(key: str, file: str) -> Path:
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    return RECYCLE_DIR / key / file


@router.delete("/pdfs/{key}/extract/{file}", tags=["pdfs"], status_code=204)
def delete_extracted_scene(key: str, file: str) -> None:
    """R2 — drop one extracted scene (image + dataset manifest entry +
    intake record). The deleted scene goes into a 1-hour recycle bin
    at tmp/recycle/<key>/<file>/ so A3 undo can restore it. The labels
    JSON moves with it so the restore is round-trip clean."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    ds_dir = main.DATASET_DIR / key
    ds_manifest_path = ds_dir / "manifest.json"
    if not ds_manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"no dataset manifest for {key!r}")
    ds_manifest = json.loads(ds_manifest_path.read_text())
    drawings = ds_manifest.get("drawings", [])
    target_entry = next((d for d in drawings if d.get("file") == file), None)
    if target_entry is None:
        raise HTTPException(status_code=404, detail=f"scene {file!r} not in dataset manifest")
    drawings = [d for d in drawings if d.get("file") != file]
    ds_manifest["drawings"] = drawings
    atomic_write_json(ds_manifest_path, ds_manifest)

    # A3 recycle bin
    _purge_old_recycle()
    recycle_dir = _safe_recycle_path(key, file)
    recycle_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(recycle_dir / "manifest_entry.json", target_entry)
    import shutil
    img = ds_dir / file
    if img.exists():
        shutil.move(str(img), str(recycle_dir / file))
    labels_file = ds_dir / "labels" / f"{Path(file).stem}.json"
    if labels_file.exists():
        shutil.move(str(labels_file), str(recycle_dir / "labels.json"))

    # Intake record
    intake = _read_manifest(key)
    intake_record = None
    if intake is not None:
        intake_record = next(
            (s for s in intake.get("extracted_scenes", []) if s.get("scene_file") == file),
            None,
        )
        intake["extracted_scenes"] = [
            s for s in intake.get("extracted_scenes", [])
            if s.get("scene_file") != file
        ]
        intake["state"] = _bundle_state(key, intake)
        _write_manifest(key, intake)
    if intake_record is not None:
        atomic_write_json(recycle_dir / "intake_record.json", intake_record)
    # G1-7 (agentic-labeling-followups-tracker): prune the scene's
    # facts row + re-derive the rest. The deleted scene may have
    # contributed to extent / openings_catalog; recompute picks up
    # the cascade. Non-fatal — the scene is already gone from disk.
    try:
        from .fact_derivation import prune_scene_from_facts
        prune_scene_from_facts(key, file, dataset_root=main.DATASET_DIR)
    except Exception:  # noqa: BLE001
        pass
    return None


@router.post("/pdfs/{key}/extract/{file}/restore", tags=["pdfs"])
def restore_extracted_scene(key: str, file: str) -> dict:
    """A3 — restore a soft-deleted scene from the recycle bin. Looks for
    tmp/recycle/<key>/<file>/ and moves the contents back into the
    dataset + intake. 410 Gone if the bundle has been pruned."""
    _purge_old_recycle()
    recycle_dir = _safe_recycle_path(key, file)
    entry_path = recycle_dir / "manifest_entry.json"
    if not entry_path.exists():
        raise HTTPException(status_code=410, detail=f"recycle window expired for {file!r}")
    entry = json.loads(entry_path.read_text())
    ds_dir = main.DATASET_DIR / key
    ds_manifest_path = ds_dir / "manifest.json"
    if not ds_manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"no dataset manifest for {key!r}")
    ds_manifest = json.loads(ds_manifest_path.read_text())
    drawings = ds_manifest.get("drawings", [])
    # Avoid duplicates if the user managed to extract a same-named scene
    # in between delete and restore.
    if any(d.get("file") == file for d in drawings):
        raise HTTPException(status_code=409, detail=f"scene {file!r} already exists")
    drawings.append(entry)
    ds_manifest["drawings"] = drawings
    atomic_write_json(ds_manifest_path, ds_manifest)
    import shutil
    bundled_img = recycle_dir / file
    if bundled_img.exists():
        shutil.move(str(bundled_img), str(ds_dir / file))
    bundled_labels = recycle_dir / "labels.json"
    if bundled_labels.exists():
        (ds_dir / "labels").mkdir(parents=True, exist_ok=True)
        shutil.move(str(bundled_labels), str(ds_dir / "labels" / f"{Path(file).stem}.json"))
    bundled_intake = recycle_dir / "intake_record.json"
    if bundled_intake.exists():
        intake = _read_manifest(key)
        if intake is not None:
            intake.setdefault("extracted_scenes", []).append(json.loads(bundled_intake.read_text()))
            intake["state"] = _bundle_state(key, intake)
            _write_manifest(key, intake)
        bundled_intake.unlink()
    entry_path.unlink()
    try:
        recycle_dir.rmdir()
    except OSError:
        pass
    return _load_dataset_manifest(key)
