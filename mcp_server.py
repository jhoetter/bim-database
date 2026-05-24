#!/usr/bin/env python3
"""MCP server for the BIM House Database.

Configure in Claude Desktop (~/Library/Application Support/Claude/claude_desktop_config.json):
{
  "mcpServers": {
    "bim-database": {
      "command": "/Users/jhoetter/repos/bim-database/.venv/bin/python",
      "args": ["/Users/jhoetter/repos/bim-database/mcp_server.py"]
    }
  }
}

Image / PDF URLs assume the REST API is running on http://localhost:2500 (make dev).

Catalog houses and dev-fixture testhouses are exposed through the same tools
and share a unified record shape — they are discriminated by the `category`
field ("house" | "testhouse"). The globally-unique `key` field
("house-3", "testhouse-1") identifies a record across both categories.
"""
import json
import re
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

BASE = Path(__file__).parent
HOUSES_FILE = BASE / "houses.json"
TESTHOUSES_FILE = BASE / "testhouses.json"
API_BASE = "http://localhost:2500"

IMAGE_EXTS = ("*.avif", "*.jpg", "*.jpeg", "*.png")

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
    rec["images"] = _folder_images(BASE / f"house-{hid}", f"{API_BASE}/static/house-{hid}")
    rec["pdf_url"] = f"{API_BASE}/static/house-{hid}.pdf" if pdf.exists() else None
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
    rec["manufacturer"] = "Testhouse"
    rec["model"] = th.get("name") or slug
    rec["floors"] = float(len(th["levels"])) if th.get("levels") else None
    rec["images"] = _folder_images(folder, f"{API_BASE}/static/{slug}")
    rec["pdf_url"] = f"{API_BASE}/static/{slug}.pdf" if pdf.exists() else None
    rec["source_pdfs"] = (
        sorted(f"{API_BASE}/static/{slug}/{p.name}" for p in folder.glob("*.pdf"))
        if folder.exists() else []
    )
    rec["price_on_request"] = False
    return rec


def _load_all() -> list[dict]:
    houses = [_enrich_house(h) for h in json.loads(HOUSES_FILE.read_text())]
    testhouses = (
        [_enrich_testhouse(t) for t in json.loads(TESTHOUSES_FILE.read_text())]
        if TESTHOUSES_FILE.exists() else []
    )
    return houses + testhouses


def _by_key(key: str) -> Optional[dict]:
    if key.isdigit():
        key = f"house-{key}"
    return next((r for r in _load_all() if r["key"] == key), None)


mcp = FastMCP("BIM House Database")


@mcp.tool()
def get_database_summary() -> dict:
    """Return an overview of the database: total count by category, available
    building types, construction methods, manufacturers, price range, and area
    range. Call this first to understand what filters make sense.

    Catalog houses (category='house') are real prefab/solid-construction
    products with prices and full specs. Testhouses (category='testhouse') are
    dev fixtures from real building documentation (Baupläne) — they have
    architectural character but no price/area metadata.
    """
    recs = _load_all()
    catalog = [r for r in recs if r["category"] == "house"]
    testhouses = [r for r in recs if r["category"] == "testhouse"]
    prices = [r["price_eur"] for r in catalog if r["price_eur"] is not None]
    areas = [r["area_m2"] for r in catalog if r["area_m2"] is not None]
    return {
        "total_records": len(recs),
        "by_category": {"house": len(catalog), "testhouse": len(testhouses)},
        "building_types": sorted({r["building_type"] for r in recs if r["building_type"]}),
        "construction_methods": sorted({r["construction"] for r in recs if r["construction"]}),
        "manufacturers": sorted({r["manufacturer"] for r in catalog if r["manufacturer"]}),
        "price_range_eur": {"min": min(prices), "max": max(prices)} if prices else None,
        "area_range_m2": {"min": min(areas), "max": max(areas)} if areas else None,
        "houses_with_price": len(prices),
        "houses_price_on_request": sum(1 for r in catalog if r.get("price_on_request")),
    }


@mcp.tool()
def list_houses(
    category: Optional[str] = None,
    building_type: Optional[str] = None,
    construction: Optional[str] = None,
    min_area: Optional[float] = None,
    max_area: Optional[float] = None,
    max_price: Optional[float] = None,
    energy_standard: Optional[str] = None,
) -> list[dict]:
    """List records with optional filters. Returns the unified shape (catalog
    houses and testhouses share fields, missing values are null), including
    image URLs and a `key` field that is globally unique across categories.

    Args:
        category: 'house' for catalog only, 'testhouse' for dev fixtures only,
                  omit for both
        building_type: e.g. EFH, Bungalow, Doppelhaus, MFH, Zweifamilienhaus, Doppelhaushälfte, Blockhaus
        construction: e.g. Fertighaus, Massivhaus, Blockhaus
        min_area: minimum living area in m² (excludes records without area, including testhouses)
        max_area: maximum living area in m² (excludes records without area, including testhouses)
        max_price: maximum price in € (excludes price-on-request and testhouses)
        energy_standard: substring match, e.g. '40', '55', 'Plus'
    """
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


@mcp.tool()
def get_house(key: str) -> dict:
    """Get full details for a single record by its globally-unique key,
    including image URLs and PDF URL.

    Args:
        key: 'house-<N>' or 'testhouse-<N>' (a bare integer is accepted as
             shorthand for 'house-<N>'). See list_houses results for valid keys.
    """
    rec = _by_key(str(key))
    if not rec:
        raise ValueError(f"Record {key!r} not found")
    return rec


if __name__ == "__main__":
    mcp.run()
