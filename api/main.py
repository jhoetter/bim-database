import json
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent.parent
HOUSES_FILE = BASE / "houses.json"
TESTHOUSES_FILE = BASE / "testhouses.json"

IMAGE_EXTS = ("*.avif", "*.jpg", "*.jpeg", "*.png")

app = FastAPI(
    title="BIM House Database",
    description="REST API for prefab/solid-construction house data, floor plans and exterior images. "
                "Catalog houses and dev-fixture testhouses share one unified record shape and one /houses endpoint, "
                "discriminated by `category`.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve image folders and PDFs as static files
app.mount("/static", StaticFiles(directory=str(BASE)), name="static")

# ── image discovery ─────────────────────────────────────────────────────────

_TYPE_RE = re.compile(
    r"[_-](?:exterior|floorplan|floor_plans?|floor|innen|grundriss|aussen|fassade|section|schnitt)",
    re.IGNORECASE,
)


def _image_sort_key(p: Path) -> tuple:
    stem = re.sub(r"\.original$", "", p.stem).lower()
    m = _TYPE_RE.search(stem)
    if m:
        kw = m.group().lower()
        is_plan = any(w in kw for w in ("floor", "grundriss", "section", "schnitt"))
        kind = 1 if is_plan else 0
        nums = re.findall(r"\d+", stem[m.end():])
    else:
        kind = 0
        nums = re.findall(r"\d+", stem)
    return (kind, int(nums[0]) if nums else 0, stem)


def _folder_images(folder: Path, url_prefix: str) -> dict:
    if not folder.exists():
        return {"exteriors": [], "floorplans": []}
    files: list[Path] = []
    for pat in IMAGE_EXTS:
        files.extend(folder.glob(pat))
    files.sort(key=_image_sort_key)
    exteriors, floorplans = [], []
    for f in files:
        url = f"{url_prefix}/{f.name}"
        (floorplans if _image_sort_key(f)[0] == 1 else exteriors).append(url)
    return {"exteriors": exteriors, "floorplans": floorplans}


# ── unified record assembly ─────────────────────────────────────────────────

# Fields a "house" has that a testhouse doesn't, and vice versa. Both kinds
# are returned as the same shape — missing fields are set to None — so callers
# can render or filter without branching on category.
_HOUSE_ONLY = (
    "manufacturer", "model", "area_m2", "rooms", "floors",
    "price_eur", "price_on_request", "energy_standard", "source_url",
)
_TESTHOUSE_ONLY = (
    "slug", "name", "character", "year_built", "levels",
    "site", "source_origin", "agent_notes",
)


def _unified_skeleton() -> dict:
    return {
        "id": None, "key": None, "category": None,
        "building_type": None, "construction": None,
        **{f: None for f in _HOUSE_ONLY},
        **{f: None for f in _TESTHOUSE_ONLY},
        "images": {"exteriors": [], "floorplans": []},
        "pdf_url": None,
        "source_pdfs": [],
    }


def _enrich_house(house: dict) -> dict:
    hid = house["id"]
    pdf = BASE / f"house-{hid}.pdf"
    rec = _unified_skeleton()
    rec.update(house)
    rec["key"] = f"house-{hid}"
    rec["category"] = "house"
    rec["images"] = _folder_images(BASE / f"house-{hid}", f"/static/house-{hid}")
    rec["pdf_url"] = f"/static/house-{hid}.pdf" if pdf.exists() else None
    rec["price_on_request"] = bool(house.get("price_on_request"))
    return rec


def _enrich_testhouse(th: dict) -> dict:
    slug = th["slug"]
    folder = BASE / slug
    pdf = BASE / f"{slug}.pdf"
    rec = _unified_skeleton()
    rec.update(th)
    rec["key"] = slug
    rec["category"] = "testhouse"
    # Project testhouse-shaped fields onto house-shaped fields so the same
    # card renderer / table can pick them up without branching.
    rec["manufacturer"] = "Testhouse"
    rec["model"] = th.get("name") or slug
    rec["floors"] = float(len(th["levels"])) if th.get("levels") else None
    rec["images"] = _folder_images(folder, f"/static/{slug}")
    rec["pdf_url"] = f"/static/{slug}.pdf" if pdf.exists() else None
    rec["source_pdfs"] = (
        sorted(f"/static/{slug}/{p.name}" for p in folder.glob("*.pdf"))
        if folder.exists() else []
    )
    rec["price_on_request"] = False
    return rec


def _load_houses() -> list[dict]:
    return [_enrich_house(h) for h in json.loads(HOUSES_FILE.read_text())]


def _load_testhouses() -> list[dict]:
    if not TESTHOUSES_FILE.exists():
        return []
    return [_enrich_testhouse(t) for t in json.loads(TESTHOUSES_FILE.read_text())]


def _load_all() -> list[dict]:
    return _load_houses() + _load_testhouses()


def _by_key(key: str) -> Optional[dict]:
    # Accept new globally-unique key ("house-3", "testhouse-1") or a bare
    # integer (legacy: catalog house).
    if key.isdigit():
        key = f"house-{key}"
    return next((r for r in _load_all() if r["key"] == key), None)


# ── routes ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["meta"], response_class=FileResponse)
def root():
    return FileResponse(str(BASE / "ui" / "index.html"))


@app.get("/houses", tags=["houses"])
def list_houses(
    category: Optional[str] = Query(None, description="house | testhouse (omit for both)"),
    building_type: Optional[str] = Query(None, description="Filter by building type, e.g. EFH, Doppelhaus, Bungalow"),
    construction: Optional[str] = Query(None, description="Filter by construction method, e.g. Massivhaus, Fertighaus"),
    min_area: Optional[float] = Query(None, description="Minimum living area in m² (excludes records without area)"),
    max_area: Optional[float] = Query(None, description="Maximum living area in m² (excludes records without area)"),
    max_price: Optional[float] = Query(None, description="Maximum price in € (excludes price-on-request and testhouses)"),
    energy_standard: Optional[str] = Query(None, description="Substring match on energy standard, e.g. '40', '55'"),
):
    recs = _load_all()
    if category:
        recs = [r for r in recs if r["category"] == category.lower()]
    if building_type:
        recs = [r for r in recs if (r.get("building_type") or "").lower() == building_type.lower()]
    if construction:
        recs = [r for r in recs if (r.get("construction") or "").lower() == construction.lower()]
    if min_area is not None:
        recs = [r for r in recs if r.get("area_m2") is not None and r["area_m2"] >= min_area]
    if max_area is not None:
        recs = [r for r in recs if r.get("area_m2") is not None and r["area_m2"] <= max_area]
    if max_price is not None:
        recs = [r for r in recs if r.get("price_eur") is not None and r["price_eur"] <= max_price]
    if energy_standard:
        recs = [r for r in recs if energy_standard.lower() in (r.get("energy_standard") or "").lower()]
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


# ── testhouses: thin aliases for back-compat ────────────────────────────────

@app.get("/testhouses", tags=["testhouses"])
def list_testhouses():
    return [r for r in _load_all() if r["category"] == "testhouse"]


@app.get("/testhouses/{testhouse_id}", tags=["testhouses"])
def get_testhouse(testhouse_id: int):
    rec = _by_key(f"testhouse-{testhouse_id}")
    if not rec:
        raise HTTPException(status_code=404, detail=f"Testhouse {testhouse_id} not found")
    return rec


@app.get("/testhouses/{testhouse_id}/pdf", tags=["testhouses"])
def get_testhouse_pdf(testhouse_id: int):
    return get_pdf(f"testhouse-{testhouse_id}")
