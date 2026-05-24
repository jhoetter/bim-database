import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent.parent
HOUSES_DIR = BASE / "data" / "houses"
ONTOLOGY_FILE = BASE / "data" / "ontology.json"

app = FastAPI(
    title="BIM House Database",
    description=(
        "REST API for house records (catalog products and existing-building documentation). "
        "All records share one schema (see schema/house.schema.json); enum vocabulary "
        "for filters comes from /ontology. See AGENTS.md for how to add a new house."
    ),
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve image folders, PDFs, and the data/schema dirs as static files
app.mount("/static", StaticFiles(directory=str(BASE)), name="static")


# ── record loading + enrichment ─────────────────────────────────────────────

def _enrich(rec: dict) -> dict:
    """Resolve `images[].file` to absolute `/static/...` URLs and attach
    pdf_url + source_pdfs. The on-disk record stays storage-clean (filenames
    only); URL resolution happens once per request here."""
    hid = rec["id"]
    folder = BASE / f"house-{hid}"
    out = dict(rec)
    out["key"] = f"house-{hid}"
    out["images"] = [
        {**img, "url": f"/static/house-{hid}/{img['file']}"}
        for img in rec.get("images") or []
    ]
    pdf = BASE / f"house-{hid}.pdf"
    out["pdf_url"] = f"/static/house-{hid}.pdf" if pdf.exists() else None
    out["source_pdfs"] = (
        sorted(f"/static/house-{hid}/{p.name}" for p in folder.glob("*.pdf"))
        if folder.exists() else []
    )
    return out


def _load_all() -> list[dict]:
    if not HOUSES_DIR.exists():
        return []
    recs = []
    for p in sorted(HOUSES_DIR.glob("house-*.json"), key=lambda q: int(q.stem.split("-")[1])):
        try:
            recs.append(_enrich(json.loads(p.read_text())))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            # A malformed record shouldn't take down /houses entirely.
            print(f"warning: skipping {p.name}: {e}")
    return recs


def _by_key(key: str) -> Optional[dict]:
    if key.isdigit():
        key = f"house-{key}"
    return next((r for r in _load_all() if r["key"] == key), None)


def _ontology() -> dict:
    return json.loads(ONTOLOGY_FILE.read_text())


# ── routes ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["meta"], response_class=FileResponse)
def root():
    return FileResponse(str(BASE / "ui" / "index.html"))


@app.get("/ontology", tags=["meta"])
def ontology():
    """Enum vocabulary used by house records and image metadata. UIs should
    populate filter dropdowns from this so adding a new enum value requires
    no code changes."""
    return _ontology()


@app.get("/houses", tags=["houses"])
def list_houses(
    source:          Optional[str]   = Query(None, description="Enum: see ontology.sources (catalog, documentation, …)"),
    building_type:   Optional[str]   = Query(None),
    construction:    Optional[str]   = Query(None),
    roof_type:       Optional[str]   = Query(None),
    style:           Optional[str]   = Query(None),
    energy_standard: Optional[str]   = Query(None, description="Substring match"),
    has_basement:    Optional[bool]  = Query(None),
    min_area:        Optional[float] = Query(None),
    max_area:        Optional[float] = Query(None),
    max_price:       Optional[float] = Query(None),
    min_year:        Optional[int]   = Query(None),
    max_year:        Optional[int]   = Query(None),
):
    """List records with optional filters. Records missing the filtered field
    are excluded (i.e. roof_type=Satteldach excludes records where roof_type
    hasn't been filled in yet)."""
    recs = _load_all()
    def eq(field, val):
        return [r for r in recs if (r.get(field) or "").lower() == val.lower()] if val else recs
    if source:          recs = eq("source",        source)
    if building_type:   recs = eq("building_type", building_type)
    if construction:    recs = eq("construction",  construction)
    if roof_type:       recs = eq("roof_type",     roof_type)
    if style:           recs = eq("style",         style)
    if energy_standard: recs = [r for r in recs if energy_standard.lower() in (r.get("energy_standard") or "").lower()]
    if has_basement is not None:
        recs = [r for r in recs if r.get("has_basement") is has_basement]
    if min_area is not None: recs = [r for r in recs if r.get("area_m2") is not None and r["area_m2"] >= min_area]
    if max_area is not None: recs = [r for r in recs if r.get("area_m2") is not None and r["area_m2"] <= max_area]
    if max_price is not None: recs = [r for r in recs if r.get("price_eur") is not None and r["price_eur"] <= max_price]
    if min_year is not None: recs = [r for r in recs if r.get("year_built") is not None and r["year_built"] >= min_year]
    if max_year is not None: recs = [r for r in recs if r.get("year_built") is not None and r["year_built"] <= max_year]
    return recs


@app.get("/houses/{key}", tags=["houses"])
def get_house(key: str):
    rec = _by_key(key)
    if not rec:
        raise HTTPException(status_code=404, detail=f"House {key!r} not found")
    return rec


@app.get("/houses/{key}/pdf", tags=["houses"])
def get_pdf(key: str):
    rec = _by_key(key)
    if not rec:
        raise HTTPException(status_code=404, detail=f"House {key!r} not found")
    pdf = BASE / f"{rec['key']}.pdf"
    if not pdf.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(str(pdf), media_type="application/pdf", filename=pdf.name)


@app.get("/houses/{key}/images", tags=["houses"])
def get_images(key: str):
    rec = _by_key(key)
    if not rec:
        raise HTTPException(status_code=404, detail=f"House {key!r} not found")
    return rec["images"]
