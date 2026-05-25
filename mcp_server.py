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

All records share one schema (see schema/house.schema.json). Enum vocabulary
for filters comes from get_ontology(); see AGENTS.md for how to add a new
house.
"""
import json
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

BASE = Path(__file__).parent
HOUSES_DIR = BASE / "data" / "houses"
ONTOLOGY_FILE = BASE / "data" / "ontology.json"
ISSUE_STATE_FILE = BASE / "data" / ".issue_state.json"
API_BASE = "http://localhost:2500"


def _issue_state() -> dict:
    if not ISSUE_STATE_FILE.exists():
        return {}
    try:
        return json.loads(ISSUE_STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _issue_url(ref: str) -> str:
    repo, _, num = ref.partition("#")
    return f"https://github.com/{repo}/issues/{num}"


def _modelable(rec: dict, state: dict) -> dict:
    """Tri-state — see api/main.py:_modelable docstring."""
    if "bim_ai_blocking_issues" not in rec or rec["bim_ai_blocking_issues"] is None:
        return {"modelable_in_bim_ai": None, "blocking_open": [], "blocking_unknown": [], "assessed": False}
    refs = rec["bim_ai_blocking_issues"]
    if not refs:
        return {"modelable_in_bim_ai": True, "blocking_open": [], "blocking_unknown": [], "assessed": True}
    open_, unknown = [], []
    for r in refs:
        s = state.get(r)
        if s == "open":   open_.append({"ref": r, "url": _issue_url(r)})
        elif s != "closed": unknown.append({"ref": r, "url": _issue_url(r)})
    if unknown:
        return {"modelable_in_bim_ai": None, "blocking_open": open_, "blocking_unknown": unknown, "assessed": True}
    return {"modelable_in_bim_ai": not open_, "blocking_open": open_, "blocking_unknown": [], "assessed": True}


def _house_dir(hid: int) -> Path:
    return HOUSES_DIR / f"house-{hid}"


def _enrich(rec: dict, state: dict) -> dict:
    hid = rec["id"]
    folder = _house_dir(hid)
    out = dict(rec)
    out["key"] = f"house-{hid}"
    out["images"] = [
        {**img, "url": f"{API_BASE}/static/house-{hid}/{img['file']}"}
        for img in rec.get("images") or []
    ]
    pdf = folder / f"house-{hid}.pdf"
    out["pdf_url"] = f"{API_BASE}/static/house-{hid}/house-{hid}.pdf" if pdf.exists() else None
    out["source_pdfs"] = (
        sorted(f"{API_BASE}/static/house-{hid}/{p.name}"
               for p in folder.glob("*.pdf") if p.name != f"house-{hid}.pdf")
        if folder.exists() else []
    )
    out.update(_modelable(rec, state))
    return out


def _load_all() -> list[dict]:
    if not HOUSES_DIR.exists():
        return []
    state = _issue_state()
    recs = []
    for p in sorted(HOUSES_DIR.glob("house-*/house-*.json"),
                    key=lambda q: int(q.stem.split("-")[1])):
        try:
            recs.append(_enrich(json.loads(p.read_text()), state))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return recs


def _by_key(key: str) -> Optional[dict]:
    if key.isdigit():
        key = f"house-{key}"
    return next((r for r in _load_all() if r["key"] == key), None)


mcp = FastMCP("BIM House Database")


@mcp.tool()
def get_ontology() -> dict:
    """Return the enum vocabulary used by all records and image metadata —
    building types, construction methods, roof types, styles, sources,
    levels, image categories/mediums/views. Call this to know what filter
    values are valid."""
    return json.loads(ONTOLOGY_FILE.read_text())


@mcp.tool()
def get_database_summary() -> dict:
    """Return a high-level overview: total count, distribution across sources
    and building types, price/area ranges. Call this first to understand the
    shape of the data."""
    recs = _load_all()
    by_source: dict[str, int] = {}
    by_building_type: dict[str, int] = {}
    by_construction: dict[str, int] = {}
    by_roof: dict[str, int] = {}
    for r in recs:
        for field, bucket in (("source", by_source), ("building_type", by_building_type),
                              ("construction", by_construction), ("roof_type", by_roof)):
            v = r.get(field)
            if v: bucket[v] = bucket.get(v, 0) + 1
    prices = [r["price_eur"] for r in recs if r.get("price_eur") is not None]
    areas = [r["area_m2"] for r in recs if r.get("area_m2") is not None]
    years = [r["year_built"] for r in recs if r.get("year_built") is not None]
    return {
        "total_records": len(recs),
        "by_source": by_source,
        "by_building_type": by_building_type,
        "by_construction": by_construction,
        "by_roof_type": by_roof,
        "price_range_eur": {"min": min(prices), "max": max(prices)} if prices else None,
        "area_range_m2":   {"min": min(areas),  "max": max(areas)}  if areas  else None,
        "year_built_range": {"min": min(years), "max": max(years)} if years else None,
        "records_with_price": len(prices),
        "records_price_on_request": sum(1 for r in recs if r.get("price_on_request")),
    }


@mcp.tool()
def list_houses(
    source: Optional[str] = None,
    building_type: Optional[str] = None,
    construction: Optional[str] = None,
    roof_type: Optional[str] = None,
    style: Optional[str] = None,
    has_basement: Optional[bool] = None,
    min_area: Optional[float] = None,
    max_area: Optional[float] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    energy_standard: Optional[str] = None,
    modelable_in_bim_ai: Optional[bool] = None,
) -> list[dict]:
    """List house records with optional filters. Records missing a filtered
    field are excluded — i.e. roof_type='Satteldach' excludes records where
    roof_type hasn't been filled in. Use get_ontology() for valid enum values.

    Args:
        source: 'catalog' (prefab listings) | 'documentation' (Baupläne) | …
        building_type: e.g. EFH, Doppelhaushälfte, Bungalow
        construction: e.g. Fertighaus, Massivhaus, Blockhaus
        roof_type: e.g. Satteldach, Walmdach, Flachdach, Zwerchdach
        style: e.g. modern, historisch, nachkriegsbau
        has_basement: True/False
        min_area / max_area: Wohnfläche range in m²
        max_price: € (excludes price-on-request)
        min_year / max_year: Baujahr range
        energy_standard: substring match, e.g. '40', '55'
    """
    recs = _load_all()
    def eq(field, val):
        return [r for r in recs if (r.get(field) or "").lower() == val.lower()] if val else recs
    if source:          recs = eq("source",        source)
    if building_type:   recs = eq("building_type", building_type)
    if construction:    recs = eq("construction",  construction)
    if roof_type:       recs = eq("roof_type",     roof_type)
    if style:           recs = eq("style",         style)
    if energy_standard: recs = eq("energy_standard", energy_standard)
    if has_basement is not None: recs = [r for r in recs if r.get("has_basement") is has_basement]
    if min_area is not None: recs = [r for r in recs if r.get("area_m2") is not None and r["area_m2"] >= min_area]
    if max_area is not None: recs = [r for r in recs if r.get("area_m2") is not None and r["area_m2"] <= max_area]
    if min_price is not None: recs = [r for r in recs if r.get("price_eur") is not None and r["price_eur"] >= min_price]
    if max_price is not None: recs = [r for r in recs if r.get("price_eur") is not None and r["price_eur"] <= max_price]
    if min_year is not None: recs = [r for r in recs if r.get("year_built") is not None and r["year_built"] >= min_year]
    if max_year is not None: recs = [r for r in recs if r.get("year_built") is not None and r["year_built"] <= max_year]
    if modelable_in_bim_ai is not None:
        recs = [r for r in recs if r.get("modelable_in_bim_ai") is modelable_in_bim_ai]
    return recs


@mcp.tool()
def get_house(key: str) -> dict:
    """Get one record by key (e.g. 'house-3' or '3'). Returns full record
    with image URLs and PDF URL."""
    rec = _by_key(str(key))
    if not rec:
        raise ValueError(f"Record {key!r} not found")
    return rec


# ── write tools (opt-in via env var) ───────────────────────────────────────
# All writes go through _write_house which: (1) loads the on-disk record,
# (2) applies the mutation, (3) writes back, (4) runs `make validate`. If
# validation fails the change is rolled back from a backup.

import os
import shutil
import subprocess

WRITE_ENABLED = os.environ.get("BIM_DATABASE_MCP_WRITE") == "1"


def _require_write():
    if not WRITE_ENABLED:
        raise PermissionError(
            "MCP write tools disabled. Set BIM_DATABASE_MCP_WRITE=1 to enable."
        )


def _record_path(key: str) -> Path:
    if key.isdigit():
        key = f"house-{key}"
    p = HOUSES_DIR / key / f"{key}.json"
    if not p.exists():
        raise ValueError(f"Record {key!r} not found at {p}")
    return p


def _write_house(key: str, mutate) -> dict:
    """Atomic write + validate. mutate(rec) modifies rec in-place."""
    _require_write()
    path = _record_path(key)
    backup = path.read_bytes()
    rec = json.loads(backup)
    mutate(rec)
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
    r = subprocess.run(["make", "validate"], cwd=BASE, capture_output=True, text=True)
    if r.returncode != 0:
        path.write_bytes(backup)
        raise ValueError(f"validate failed; rolled back. stderr:\n{r.stdout}\n{r.stderr}")
    return rec


@mcp.tool()
def add_image(key: str, image: dict) -> dict:
    """Append an image entry to a house's images[] array. The `image` dict
    must follow schema/house.schema.json#/$defs/Image (required: file,
    category, medium; optional: view, floor, caption, source_ref, facts,
    anomaly_flags). Validation runs after the write — failure rolls back.

    Use this to add a derived scene (cropped from a source PDF, with
    source_ref pointing back to the original).
    """
    def mutate(rec):
        rec.setdefault("images", []).append(image)
    return _write_house(key, mutate)


@mcp.tool()
def update_image(key: str, file: str, patch: dict) -> dict:
    """Patch a single image entry identified by its `file` value. The
    `patch` dict's keys overwrite the existing fields; pass None to clear.
    Useful for adding facts to a scene authored in an earlier turn.
    """
    def mutate(rec):
        imgs = rec.get("images") or []
        for img in imgs:
            if img.get("file") == file:
                for k, v in patch.items():
                    if v is None:
                        img.pop(k, None)
                    else:
                        img[k] = v
                return
        raise ValueError(f"No image with file={file!r} on {key}")
    return _write_house(key, mutate)


@mcp.tool()
def set_derived_facts(key: str, facts: dict, merge: bool = True) -> dict:
    """Set or merge house-level derived_facts. Each entry is
    {value, sources, expected?, ok?}. With merge=True (default), individual
    keys are merged into the existing object; with merge=False, the entire
    derived_facts object is replaced.
    """
    def mutate(rec):
        if merge:
            cur = rec.get("derived_facts") or {}
            cur.update(facts)
            rec["derived_facts"] = cur
        else:
            rec["derived_facts"] = facts
    return _write_house(key, mutate)


@mcp.tool()
def set_anomaly_flags(key: str, flags: list[str], append: bool = True) -> dict:
    """Set or append house-level anomaly_flags. With append=True (default),
    new flags are appended (de-duplicated); with append=False, the list
    is replaced.
    """
    def mutate(rec):
        if append:
            cur = list(rec.get("anomaly_flags") or [])
            for f in flags:
                if f not in cur:
                    cur.append(f)
            rec["anomaly_flags"] = cur
        else:
            rec["anomaly_flags"] = list(flags)
    return _write_house(key, mutate)


@mcp.tool()
def validate() -> str:
    """Run `make validate` server-side and return the output. Use this as
    a final gate after a sequence of writes."""
    r = subprocess.run(["make", "validate"], cwd=BASE, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


if __name__ == "__main__":
    mcp.run()
