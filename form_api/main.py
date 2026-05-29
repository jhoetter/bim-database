"""Customer submission API.

Public surface — runs separately from api/main.py with its own auth and
rate limiting. Default port: 2600.

    uvicorn form_api.main:app --reload --host 127.0.0.1 --port 2600

Endpoints
---------
- POST  /submit                 — receive uploads + ingest + quarantine
- GET   /submission/{id}        — submitter polls for quality feedback
- GET   /health                 — liveness

The submission ID is a URL-safe random token returned in the POST
response. Submitters can recover their submission status with that
token; they CANNOT list other submissions.

Security
--------
- API_KEY env var required on POST /submit. Configure on the front-door
  reverse proxy; the submission SPA receives a short-lived key from the
  same origin.
- Per-IP token-bucket rate limit (configurable; default 4 submissions /
  hour / IP, 60-second cooldown between submissions).
- Magic-byte verification on every upload — rejects non-image / non-PDF
  bytes before they touch disk.
- 50 MB total size cap per submission; 12 files cap.
- Client IP recorded only as SHA-256(IP + salt) so we keep the abuse
  signal without storing PII.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import secrets
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from ingestion.bundle import IngestProvenance, ingest_to_bundle
from ingestion.config import load_profile
from ingestion.normalize import sniff_kind

BASE = Path(__file__).parent.parent
SUBMISSIONS_DIR = BASE / "data" / "pdfs" / "submissions"

MAX_TOTAL_BYTES = int(os.environ.get("FORM_MAX_TOTAL_BYTES", str(50 * 1024 * 1024)))
MAX_FILES = int(os.environ.get("FORM_MAX_FILES", "12"))
PER_IP_HOURLY_CAP = int(os.environ.get("FORM_HOURLY_CAP", "4"))
PER_IP_COOLDOWN_S = int(os.environ.get("FORM_COOLDOWN_S", "60"))
API_KEY = os.environ.get("FORM_API_KEY")
IP_SALT = os.environ.get("FORM_IP_SALT", "")
PROFILE_NAME = os.environ.get("FORM_PROFILE", "strict-form")

ACCEPTED_KINDS = {"pdf", "jpeg", "png", "tiff", "heif"}

app = FastAPI(
    title="BIM Database — Customer Submissions",
    description="Public ingestion endpoint for customer-submitted architectural drawings.",
    version="1.0.0",
)

# CORS — front-end origin should be set explicitly in production. We
# default to the local dev origins so `make form-ui-dev` works.
_default_origins = os.environ.get("FORM_CORS_ORIGINS", "http://localhost:5174,http://127.0.0.1:5174")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _default_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# ── rate limiter ─────────────────────────────────────────────────────────
# In-memory; fine for a single-process deployment. Swap for Redis if you
# horizontally scale this app.

_BUCKETS: dict[str, deque[float]] = {}


def _hash_ip(ip: str) -> str:
    return hashlib.sha256(f"{IP_SALT}:{ip}".encode()).hexdigest()


def _rate_limit(ip_hash: str) -> None:
    now = time.time()
    bucket = _BUCKETS.setdefault(ip_hash, deque())
    # Drop entries older than an hour.
    while bucket and bucket[0] < now - 3600:
        bucket.popleft()
    if bucket and bucket[-1] > now - PER_IP_COOLDOWN_S:
        raise HTTPException(
            status_code=429,
            detail=f"slow down — wait {PER_IP_COOLDOWN_S}s between submissions",
        )
    if len(bucket) >= PER_IP_HOURLY_CAP:
        raise HTTPException(
            status_code=429,
            detail=f"hourly submission cap reached ({PER_IP_HOURLY_CAP}); try again later",
        )
    bucket.append(now)


def _check_auth(request: Request) -> None:
    if not API_KEY:
        # Refuse to start handling submissions without an API key — the
        # whole point of this surface is that it's NOT the un-auth'd
        # localhost SPA.
        raise HTTPException(
            status_code=503,
            detail="form API not configured (set FORM_API_KEY env var)",
        )
    key = request.headers.get("X-API-Key", "")
    if not secrets.compare_digest(key, API_KEY):
        raise HTTPException(status_code=401, detail="bad or missing X-API-Key")


# ── endpoints ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True, "profile": PROFILE_NAME, "quarantine": str(SUBMISSIONS_DIR)}


@app.post("/submit", status_code=201)
async def submit(
    request: Request,
    files: list[UploadFile] = File(..., description="Architectural drawings to submit"),
    contact_email: str | None = Form(None),
    contact_name: str | None = Form(None),
    license: str = Form(..., description="cc0 | cc-by | cc-by-sa | permission-granted | other"),
    license_notes: str | None = Form(None),
    training_use: bool = Form(..., description="Submitter agrees their files may be used for training"),
    user_notes: str | None = Form(None),
):
    """Customer submission entry point. Runs the same core ingestion as
    the batch path but writes to the quarantine area + records consent
    + an active per-page quality response so the client can re-prompt
    the user on failed pages.

    Returns: {submission_id, page_count, pages:[{decision, reasons, …}],
              promoted: false, message}
    """
    _check_auth(request)
    if not training_use:
        raise HTTPException(status_code=400, detail="training_use consent is required")
    if license not in {"cc0", "cc-by", "cc-by-sa", "permission-granted", "other"}:
        raise HTTPException(status_code=400, detail=f"unknown license {license!r}")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=413, detail=f"too many files (max {MAX_FILES})")

    client_ip = request.client.host if request.client else "0.0.0.0"
    ip_hash = _hash_ip(client_ip)
    _rate_limit(ip_hash)

    submission_id = secrets.token_urlsafe(16)
    SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Stream uploads to a tmp dir under the quarantine, verifying magic
    # bytes + total size as we go. We DO NOT write to the source/ folder
    # directly — the core ingestion pipeline does that, so the on-disk
    # shape stays consistent with the batch path.
    staging = SUBMISSIONS_DIR / submission_id / "_staging"
    staging.mkdir(parents=True, exist_ok=True)

    total_bytes = 0
    staged_paths: list[Path] = []
    for upload in files:
        raw = await upload.read()
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_BYTES:
            _cleanup(staging.parent)
            raise HTTPException(
                status_code=413,
                detail=f"submission exceeds {MAX_TOTAL_BYTES // (1024 * 1024)} MB total",
            )
        kind = sniff_kind(raw)
        if kind not in ACCEPTED_KINDS:
            _cleanup(staging.parent)
            raise HTTPException(
                status_code=400,
                detail=f"{upload.filename!r}: unsupported file type (need PDF/JPEG/PNG/TIFF/HEIC/HEIF)",
            )
        safe_name = Path(upload.filename or f"upload-{len(staged_paths)}").name
        # Disambiguate to prevent overwrite.
        out_path = staging / safe_name
        if out_path.exists():
            out_path = staging / f"{len(staged_paths)}-{safe_name}"
        out_path.write_bytes(raw)
        staged_paths.append(out_path)

    if not staged_paths:
        _cleanup(staging.parent)
        raise HTTPException(status_code=400, detail="no files in submission")

    provenance = IngestProvenance(
        source_type="form",
        submitter={
            "submission_id": submission_id,
            "contact_email": contact_email,
            "contact_name": contact_name,
            "client_ip_hash": ip_hash,
            "user_agent": request.headers.get("user-agent"),
        },
        consent={
            "training_use": training_use,
            "license": license,
            "license_notes": license_notes or "",
            "consented_at": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        user_notes=user_notes or "",
    )

    cfg = load_profile(PROFILE_NAME)
    result = ingest_to_bundle(
        input_files=staged_paths,
        bundle_root=SUBMISSIONS_DIR,
        bundle_key=submission_id,
        provenance=provenance,
        cfg=cfg,
    )

    # Drop the staging dir — the originals already live in source/.
    _cleanup(staging)

    pages_response = [
        {
            "page": p["page"],
            "decision": p["decision"],
            "reasons": p["decision_reasons"],
            "human_qa_required": p["human_qa_required"],
        }
        for p in result.manifest["pages"]
    ]
    has_reject = any(p["decision"] == "reject" for p in result.manifest["pages"])
    has_warn = any(p["decision"] == "warn" for p in result.manifest["pages"])
    message = (
        "Vielen Dank — wir haben deine Unterlagen erhalten."
        if not has_reject and not has_warn
        else (
            "Einige Seiten haben unsere Qualitätsprüfung nicht bestanden. "
            "Schau dir die Hinweise unten an und ersetze die markierten Seiten "
            "(am besten ein scharfes Foto bei Tageslicht ohne Blitz)."
        )
    )
    return {
        "submission_id": submission_id,
        "page_count": result.manifest["page_count"],
        "pages": pages_response,
        "pass": result.pages_pass,
        "warn": result.pages_warn,
        "reject": result.pages_reject,
        "promoted": False,
        "message": message,
    }


@app.get("/submission/{submission_id}")
def get_submission(submission_id: str, request: Request):
    """Lookup-by-token. Returns the per-page quality info (no source files,
    no consent details — minimal projection)."""
    _check_auth(request)
    if "/" in submission_id or ".." in submission_id:
        raise HTTPException(status_code=400, detail="bad submission_id")
    manifest_path = SUBMISSIONS_DIR / submission_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="submission not found")
    m = json.loads(manifest_path.read_text())
    return {
        "submission_id": submission_id,
        "page_count": m.get("page_count"),
        "pages": [
            {
                "page": p["page"],
                "decision": p["decision"],
                "reasons": p["decision_reasons"],
            }
            for p in (m.get("pages") or [])
        ],
        "promoted": bool(m.get("promoted_to")),
    }


def _cleanup(path: Path) -> None:
    if not path.exists():
        return
    import shutil
    shutil.rmtree(path, ignore_errors=True)
