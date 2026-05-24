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
"""
import json
import re
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

BASE = Path(__file__).parent
DATA_FILE = BASE / "houses.json"
API_BASE = "http://localhost:2500"

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
        url = f"{API_BASE}/static/house-{house_id}/{f.name}"
        (floorplans if _image_sort_key(f)[0] == 1 else exteriors).append(url)
    return {"exteriors": exteriors, "floorplans": floorplans}


def _load() -> list[dict]:
    return json.loads(DATA_FILE.read_text())


def _enrich(house: dict) -> dict:
    hid = house["id"]
    pdf = BASE / f"house-{hid}.pdf"
    return {
        **house,
        "images": _house_images(hid),
        "pdf_url": f"{API_BASE}/static/house-{hid}.pdf" if pdf.exists() else None,
    }


mcp = FastMCP("BIM House Database")


@mcp.tool()
def get_database_summary() -> dict:
    """Return an overview of the database: total count, available building types,
    construction methods, manufacturers, price range, and area range.
    Call this first to understand what filters make sense."""
    houses = _load()
    prices = [h["price_eur"] for h in houses if h["price_eur"] is not None]
    areas  = [h["area_m2"]   for h in houses if h["area_m2"]   is not None]
    return {
        "total_houses": len(houses),
        "building_types": sorted({h["building_type"] for h in houses if h["building_type"]}),
        "construction_methods": sorted({h["construction"] for h in houses if h["construction"]}),
        "manufacturers": sorted({h["manufacturer"] for h in houses if h["manufacturer"]}),
        "price_range_eur": {"min": min(prices), "max": max(prices)} if prices else None,
        "area_range_m2":   {"min": min(areas),  "max": max(areas)}  if areas  else None,
        "houses_with_price": len(prices),
        "houses_price_on_request": sum(1 for h in houses if h.get("price_on_request")),
    }


@mcp.tool()
def list_houses(
    building_type: Optional[str] = None,
    construction: Optional[str] = None,
    min_area: Optional[float] = None,
    max_area: Optional[float] = None,
    max_price: Optional[float] = None,
    energy_standard: Optional[str] = None,
) -> list[dict]:
    """List houses with optional filters. Returns full records including image URLs.

    Args:
        building_type: e.g. EFH, Bungalow, Doppelhaus, MFH, Zweifamilienhaus, Blockhaus
        construction: e.g. Fertighaus, Massivhaus, Blockhaus
        min_area: minimum living area in m²
        max_area: maximum living area in m²
        max_price: maximum price in € (excludes price-on-request houses)
        energy_standard: substring match, e.g. '40', '55', 'Plus'
    """
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


@mcp.tool()
def get_house(house_id: int) -> dict:
    """Get full details for a single house by ID, including image URLs and PDF URL.

    Args:
        house_id: integer ID (see list_houses results for valid IDs)
    """
    house = next((h for h in _load() if h["id"] == house_id), None)
    if not house:
        raise ValueError(f"House {house_id} not found")
    return _enrich(house)


if __name__ == "__main__":
    mcp.run()
