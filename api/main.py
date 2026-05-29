"""bim-database FastAPI server (R0+).

The catalog ("houses") path was removed in R0. The surviving routes:
- SPA: /, /dataset, /dataset/{rest:path}
- Static assets: /static/dataset/* (drawings) + /assets/* (UI bundle)
                 + /static/pdfs/* (incoming PDFs, R1)
- Dataset: GET /datasets, GET /datasets/{key}
- Labels: GET / PUT /labels/dataset/{key}/{file}
- PDF intake (R1): GET /pdfs/incoming, GET /pdfs/incoming/{key},
                  POST /pdfs, POST /pdfs/{key}/consolidate, DELETE …
- PDF extract (R2): POST /pdfs/{key}/extract, GET /pdfs/{key}/page/{n}
- Export (R4/R6): POST /exports/{key}/{file}/preview,
                  POST /exports/{key}, POST /exports
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent.parent
DATASET_DIR = BASE / "data" / "dataset"
PDFS_DIR = BASE / "data" / "pdfs"
INCOMING_DIR = PDFS_DIR / "incoming"
UI_DIST = BASE / "ui" / "dist"

app = FastAPI(
    title="BIM Dataset API",
    description=(
        "REST API for the supervised-learning corpus of architectural drawings. "
        "PDF intake → scene extraction → annotation → export. See "
        "spec/end-to-end-readiness.md for the full pipeline."
    ),
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static mounts. Most-specific prefix first so the generic /static doesn't
# shadow /static/dataset or /static/pdfs.
if DATASET_DIR.exists():
    app.mount("/static/dataset", StaticFiles(directory=str(DATASET_DIR)),
              name="dataset-static")
if PDFS_DIR.exists():
    app.mount("/static/pdfs", StaticFiles(directory=str(PDFS_DIR)),
              name="pdfs-static")

# Built React bundle. Hashed asset files live in ui/dist/assets/.
if (UI_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(UI_DIST / "assets")),
              name="ui-assets")


# ── meta / SPA fallback ────────────────────────────────────────────────────

@app.get("/", tags=["meta"], response_class=FileResponse)
def root():
    """Serve the built React bundle's index.html. If `ui/dist/` is absent,
    return a 503 with the build command — typically run `make web` in a
    second shell during development (Vite on :5173 proxies to here)."""
    index = UI_DIST / "index.html"
    if not index.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "UI not built. Run `cd ui && npm install && npm run build`, "
                "or `make web` for the live dev server on :5173."
            ),
        )
    return FileResponse(str(index))


# Client-side router fallback: any non-API path under /dataset loads
# index.html so react-router can pick up the URL.
@app.get("/dataset", tags=["meta"], response_class=FileResponse)
def _spa_dataset_root():
    return root()


@app.get("/dataset/{rest:path}", tags=["meta"], response_class=FileResponse)
def _spa_dataset(rest: str):
    del rest
    return root()


# ── dataset manifest ───────────────────────────────────────────────────────

def _load_dataset_manifest(key: str) -> dict | None:
    mp = DATASET_DIR / key / "manifest.json"
    if not mp.exists():
        return None
    data = json.loads(mp.read_text())
    data["key"] = key
    labels_dir = DATASET_DIR / key / "labels"
    for d in data.get("drawings") or []:
        d["url"] = f"/static/dataset/{key}/{d['file']}"
        stem = Path(d["file"]).stem
        label_file = labels_dir / f"{stem}.json"
        if label_file.exists():
            try:
                lab = json.loads(label_file.read_text())
                d["labeled"] = True
                d["label_count"] = len(lab.get("labels") or [])
            except Exception:  # noqa: BLE001
                d["labeled"] = False
                d["label_count"] = 0
        else:
            d["labeled"] = False
            d["label_count"] = 0
    # Composite (M0): if scripts/compose_house_sheet.py has produced output
    # for this house, include the bbox metadata + image URL.
    comp_json = DATASET_DIR / key / "composite.json"
    comp_png = DATASET_DIR / key / f"{key}-composite.png"
    if comp_json.exists() and comp_png.exists():
        data["composite"] = {
            **json.loads(comp_json.read_text()),
            "url": f"/static/dataset/{key}/{comp_png.name}",
        }
    return data


@app.get("/datasets", tags=["dataset"])
def list_datasets():
    """Every dataset manifest, with image URLs."""
    if not DATASET_DIR.exists():
        return []
    out = []
    for d in sorted(DATASET_DIR.iterdir()):
        if not d.is_dir():
            continue
        manifest = _load_dataset_manifest(d.name)
        if manifest:
            out.append(manifest)
    return out


@app.get("/datasets/{key}", tags=["dataset"])
def get_dataset(key: str):
    data = _load_dataset_manifest(key)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No dataset manifest for {key!r}")
    return data


# ── annotation labels ─────────────────────────────────────────────────────
# Scope-aware so the URL shape stays compatible with the existing UI; the
# `house` scope is gone — only `dataset` is accepted post-R0.

LABELS_SCHEMA_PATH = BASE / "schema" / "scene_labels.schema.json"
try:
    LABELS_SCHEMA = json.loads(LABELS_SCHEMA_PATH.read_text()) if LABELS_SCHEMA_PATH.exists() else None
except Exception:  # noqa: BLE001
    LABELS_SCHEMA = None

try:
    import jsonschema as _jsonschema  # type: ignore
except ImportError:
    _jsonschema = None


def _scope_root(scope: str) -> Path:
    if scope == "dataset":
        return DATASET_DIR
    raise HTTPException(
        status_code=400,
        detail=f"bad scope {scope!r} — only 'dataset' is supported post-R0",
    )


def _safe_label_path(scope: str, key: str, file: str) -> Path:
    if "/" in key or ".." in key or "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad key or file (traversal blocked)")
    return _scope_root(scope) / key / "labels" / (Path(file).stem + ".json")


def _scene_image_path(scope: str, key: str, file: str) -> Path:
    if "/" in key or ".." in key or "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad key or file")
    return _scope_root(scope) / key / file


@app.get("/labels/{scope}/{key}/{file}", tags=["labels"])
def get_labels(scope: str, key: str, file: str):
    """Return the label set for one scene. If no labels file exists yet,
    return a fresh skeleton with image_size_px pre-filled — so the UI can
    open the editor on a brand-new scene without a separate 'create' step."""
    label_path = _safe_label_path(scope, key, file)
    if label_path.exists():
        return json.loads(label_path.read_text())
    img_path = _scene_image_path(scope, key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {scope}/{key}/{file}")
    from PIL import Image as PILImage
    with PILImage.open(img_path) as im:
        w, h = im.size
    return {
        "schema_version": "1.0",
        "scope": scope,
        "scene_key": key,
        "scene_file": file,
        "scene_tag": "nicht_klassifiziert",
        "image_size_px": [w, h],
        "labels": [],
    }


@app.put("/labels/{scope}/{key}/{file}", tags=["labels"])
def put_labels(scope: str, key: str, file: str, payload: dict[str, Any] = Body(...)):
    """Save the label set for one scene. Validates against the JSON schema
    before writing; rejects on schema error so a buggy client can't corrupt
    the on-disk file. Caller is responsible for round-tripping any unknown
    fields (forward-compat)."""
    label_path = _safe_label_path(scope, key, file)
    if payload.get("scene_key") not in (None, key):
        raise HTTPException(status_code=400, detail=f"payload.scene_key {payload.get('scene_key')!r} != URL key {key!r}")
    if payload.get("scene_file") not in (None, file):
        raise HTTPException(status_code=400, detail="payload.scene_file != URL file")
    payload.setdefault("scope", scope)
    payload.setdefault("scene_key", key)
    payload.setdefault("scene_file", file)
    if _jsonschema and LABELS_SCHEMA:
        try:
            _jsonschema.validate(payload, LABELS_SCHEMA)
        except _jsonschema.ValidationError as e:
            raise HTTPException(status_code=422, detail=f"schema: {e.message} at {list(e.absolute_path)}")
    label_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return {"saved": str(label_path.relative_to(BASE)), "bytes": label_path.stat().st_size}


# ── PDF intake (R1 — landing routes; full impl lands in R1 wave) ──────────

@app.get("/pdfs/incoming", tags=["pdfs"])
def list_incoming_pdfs():
    """List every per-house PDF intake bundle + its manifest. Each entry
    is the on-disk manifest.json content augmented with a `consolidated_url`
    pointing at the static-mounted PDF (when it exists)."""
    if not INCOMING_DIR.exists():
        return []
    out = []
    for d in sorted(INCOMING_DIR.iterdir()):
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


@app.get("/pdfs/incoming/{key}", tags=["pdfs"])
def get_incoming_pdf(key: str):
    _safe_key(key)
    mp = INCOMING_DIR / key / "manifest.json"
    if not mp.exists():
        raise HTTPException(status_code=404, detail=f"No intake bundle for {key!r}")
    m = json.loads(mp.read_text())
    m["key"] = key
    if m.get("consolidated_pdf"):
        m["consolidated_url"] = f"/static/pdfs/incoming/{key}/{m['consolidated_pdf']}"
    return m


def _safe_key(key: str) -> None:
    if not key or "/" in key or ".." in key or "\\" in key:
        raise HTTPException(status_code=400, detail=f"bad key {key!r}")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_free_house_key() -> str:
    """Lowest unused `house-<N>` across both the dataset and the intake
    trees. Lets the user upload a brand-new house without picking a key."""
    used: set[int] = set()
    for d in (DATASET_DIR, INCOMING_DIR):
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


def _write_manifest(key: str, m: dict) -> None:
    bundle = INCOMING_DIR / key
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False))


def _read_manifest(key: str) -> dict | None:
    p = INCOMING_DIR / key / "manifest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _bundle_state(key: str, manifest: dict) -> str:
    """Compute a fresh state from the on-disk facts so it survives
    out-of-band edits."""
    consolidated = manifest.get("consolidated_pdf")
    if not consolidated:
        return "pending"
    if not (INCOMING_DIR / key / consolidated).exists():
        return "pending"
    extracted = manifest.get("extracted_scenes") or []
    if extracted:
        return "extracted"
    return "partial"


@app.post("/pdfs", tags=["pdfs"], status_code=201)
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
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    bundle = INCOMING_DIR / key
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


@app.put("/pdfs/incoming/{key}/manifest", tags=["pdfs"])
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


@app.delete("/pdfs/incoming/{key}", tags=["pdfs"], status_code=204)
def delete_incoming_bundle(key: str):
    """R1 — remove an entire intake bundle (source PDFs, consolidated
    PDF, manifest). Does NOT touch data/dataset/<key>/. The user has to
    delete extracted dataset scenes separately."""
    _safe_key(key)
    bundle = INCOMING_DIR / key
    if not bundle.exists():
        raise HTTPException(status_code=404, detail=f"No intake bundle for {key!r}")
    import shutil
    shutil.rmtree(bundle)
    return None
