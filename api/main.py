import json
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent.parent
DATA_FILE = BASE / "houses.json"

app = FastAPI(
    title="BIM House Database",
    description="REST API for prefab/solid-construction house data, floor plans and exterior images.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve house image folders and PDFs as static files
app.mount("/static", StaticFiles(directory=str(BASE)), name="static")

# ── helpers ──────────────────────────────────────────────────────────────────

_TYPE_RE = re.compile(
    r"[_-](?:exterior|floorplan|floor_plans?|floor|innen|grundriss|aussen|fassade)",
    re.IGNORECASE,
)


def _image_sort_key(p: Path) -> tuple:
    stem = re.sub(r"\.original$", "", p.stem).lower()
    m = _TYPE_RE.search(stem)
    if m:
        kind = 1 if any(w in m.group().lower() for w in ("floor", "grundriss")) else 0
        nums = re.findall(r"\d+", stem[m.end():])
    else:
        kind = 0
        nums = re.findall(r"\d+", stem)
    return (kind, int(nums[0]) if nums else 0)


def _house_images(house_id: int) -> dict:
    folder = BASE / f"house-{house_id}"
    avifs = sorted(folder.glob("*.avif"), key=_image_sort_key) if folder.exists() else []
    exteriors, floorplans = [], []
    for f in avifs:
        url = f"/static/house-{house_id}/{f.name}"
        (floorplans if _image_sort_key(f)[0] == 1 else exteriors).append(url)
    return {"exteriors": exteriors, "floorplans": floorplans}


def _enrich(house: dict) -> dict:
    hid = house["id"]
    pdf = BASE / f"house-{hid}.pdf"
    return {
        **house,
        "images": _house_images(hid),
        "pdf_url": f"/static/house-{hid}.pdf" if pdf.exists() else None,
    }


def _load() -> list[dict]:
    return json.loads(DATA_FILE.read_text())


# ── routes ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["meta"])
def root():
    return {
        "name": "BIM House Database",
        "version": "1.0.0",
        "houses": len(_load()),
        "docs": "/docs",
    }


@app.get("/houses", tags=["houses"])
def list_houses(
    building_type: Optional[str] = Query(None, description="Filter by building type, e.g. EFH, Doppelhaus, Bungalow"),
    construction: Optional[str] = Query(None, description="Filter by construction method, e.g. Massivhaus, Fertighaus"),
    min_area: Optional[float] = Query(None, description="Minimum living area in m²"),
    max_area: Optional[float] = Query(None, description="Maximum living area in m²"),
    max_price: Optional[float] = Query(None, description="Maximum price in €"),
    energy_standard: Optional[str] = Query(None, description="Filter by energy standard substring, e.g. '40', '55'"),
):
    houses = _load()

    if building_type:
        houses = [h for h in houses if h["building_type"].lower() == building_type.lower()]
    if construction:
        houses = [h for h in houses if h["construction"].lower() == construction.lower()]
    if min_area is not None:
        houses = [h for h in houses if h["area_m2"] is not None and h["area_m2"] >= min_area]
    if max_area is not None:
        houses = [h for h in houses if h["area_m2"] is not None and h["area_m2"] <= max_area]
    if max_price is not None:
        houses = [h for h in houses if h["price_eur"] is not None and h["price_eur"] <= max_price]
    if energy_standard:
        houses = [h for h in houses if energy_standard.lower() in (h["energy_standard"] or "").lower()]

    return [_enrich(h) for h in houses]


@app.get("/houses/{house_id}", tags=["houses"])
def get_house(house_id: int):
    house = next((h for h in _load() if h["id"] == house_id), None)
    if not house:
        raise HTTPException(status_code=404, detail=f"House {house_id} not found")
    return _enrich(house)


@app.get("/houses/{house_id}/pdf", tags=["houses"])
def get_pdf(house_id: int):
    pdf = BASE / f"house-{house_id}.pdf"
    if not pdf.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(str(pdf), media_type="application/pdf", filename=f"house-{house_id}.pdf")


@app.get("/houses/{house_id}/images", tags=["houses"])
def get_images(house_id: int):
    if not any(h["id"] == house_id for h in _load()):
        raise HTTPException(status_code=404, detail=f"House {house_id} not found")
    return _house_images(house_id)
