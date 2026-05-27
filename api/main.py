import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent.parent
HOUSES_DIR = BASE / "data" / "houses"
SYNTHETIC_DIR = BASE / "data" / "synthetic"
ONTOLOGY_FILE = BASE / "data" / "ontology.json"
ISSUE_STATE_FILE = BASE / "data" / ".issue_state.json"
UI_DIST = BASE / "ui" / "dist"          # produced by `cd ui && npm run build`


def _house_dir(hid: int) -> Path:
    return HOUSES_DIR / f"house-{hid}"

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

# Order matters: FastAPI matches mounts top-down, so the *more specific*
# /static/synthetic prefix must mount BEFORE the generic /static, otherwise
# requests to /static/synthetic/* get routed into HOUSES_DIR and 404.
if SYNTHETIC_DIR.exists():
    app.mount(
        "/static/synthetic",
        StaticFiles(directory=str(SYNTHETIC_DIR)),
        name="synthetic-static",
    )

# Each house's assets (images + combined PDF) live under data/houses/house-N/.
# Mount that root at /static so URLs stay as /static/house-N/<file>, and we
# don't expose the rest of the repo (api/, scripts/, etc.).
app.mount("/static", StaticFiles(directory=str(HOUSES_DIR)), name="static")

# Built React bundle — hashed asset files live in ui/dist/assets/. The HTML
# entry is served by `root()` below so we can fall back to a helpful message
# when the bundle hasn't been built yet (dev: use `make web` on :5173 instead).
if (UI_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(UI_DIST / "assets")), name="ui-assets")


# ── record loading + enrichment ─────────────────────────────────────────────

_FP_TIER = {"none": 0, "room_labels": 1, "dimensioned": 2,
            "fully_specified": 3, "construction_grade": 4}
_EXT_TIER = {"none": 0, "single_view": 1, "multi_view": 2, "all_facades": 3}
_ELEV_TIER = {"none": 0, "schematic": 1, "dimensioned": 2}
_SEC_TIER = {"none": 0, "schematic": 1, "dimensioned": 2}
_CSPECS_TIER = {"none": 0, "summary": 1, "wall_buildup": 2, "full_baubeschreibung": 3}


def _reconstructability_tier(dq: dict | None) -> str | None:
    """Roll the data-quality axes into a single tier for filtering. Returns
    a tier id from ontology.reconstructability_tiers, or None if dq absent.

    Rules (most permissive matched):
      T4: floorplan ≥ construction_grade OR (fully_specified + full_baubeschreibung)
      T3: floorplan ≥ fully_specified AND section ≥ schematic
      T2: floorplan ≥ dimensioned AND exterior_coverage ≥ single_view
      T1: floorplan ≥ room_labels OR exterior_coverage ≥ single_view
      T0: otherwise
    """
    if not dq:
        return None
    fp = _FP_TIER.get(dq.get("floorplan_grade", "none"), 0)
    ext = _EXT_TIER.get(dq.get("exterior_coverage", "none"), 0)
    sec = _SEC_TIER.get(dq.get("section_drawing", "none"), 0)
    cspecs = _CSPECS_TIER.get(dq.get("construction_specs", "none"), 0)
    if fp >= 4 or (fp >= 3 and cspecs >= 3):
        return "T4_construction_grade"
    if fp >= 3 and sec >= 1:
        return "T3_architectural_set"
    if fp >= 2 and ext >= 1:
        return "T2_dimensioned_plans"
    if fp >= 1 or ext >= 1:
        return "T1_schematic"
    return "T0_visual_only"


def _issue_state() -> dict:
    if not ISSUE_STATE_FILE.exists():
        return {}
    try:
        return json.loads(ISSUE_STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _modelable(rec: dict, state: dict) -> dict:
    """Tri-state derived from the cached GH issue state.

    - field missing / null → modelable_in_bim_ai = None  (not yet assessed)
    - field = []           → modelable_in_bim_ai = True  (assessed, no blockers)
    - any blocker open     → False
    - any blocker unknown  → None
    - all blockers closed  → True
    """
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


def _issue_url(ref: str) -> str:
    repo, _, num = ref.partition("#")
    return f"https://github.com/{repo}/issues/{num}"


def _image_url(hid: int, img: dict) -> str:
    """PDF-sourced scenes go through the /scene/ render-cache endpoint; everything
    else (catalog AVIFs, original photos, unchanged-from-source files) goes through
    /static/ as before."""
    src = img.get("source_ref") or {}
    src_file = src.get("file") or ""
    if src_file.lower().endswith(".pdf"):
        return f"/scene/house-{hid}/{img['file']}"
    return f"/static/house-{hid}/{img['file']}"


def _enrich(rec: dict, state: dict) -> dict:
    """Resolve `images[].file` to absolute URLs, attach pdf_url + source_pdfs,
    and project the `modelable_in_bim_ai` flag derived from the cached issue
    state. PDF-sourced scenes resolve to `/scene/...` (renderer); everything
    else to `/static/...`."""
    hid = rec["id"]
    folder = _house_dir(hid)
    out = dict(rec)
    out["key"] = f"house-{hid}"
    out["images"] = [
        {**img, "url": _image_url(hid, img)}
        for img in rec.get("images") or []
    ]
    pdf = folder / f"house-{hid}.pdf"
    out["pdf_url"] = f"/static/house-{hid}/house-{hid}.pdf" if pdf.exists() else None
    # Source PDFs are *additional* PDFs in the folder (e.g. testhouse Bauplan
    # pages); skip the combined `house-N.pdf` since it's already pdf_url.
    out["source_pdfs"] = (
        sorted(f"/static/house-{hid}/{p.name}"
               for p in folder.glob("*.pdf") if p.name != f"house-{hid}.pdf")
        if folder.exists() else []
    )
    out.update(_modelable(rec, state))
    out["reconstructability_tier"] = _reconstructability_tier(rec.get("data_quality"))
    return out


def _load_all() -> list[dict]:
    if not HOUSES_DIR.exists():
        return []
    state = _issue_state()
    recs = []
    # Each house lives in data/houses/house-N/ with its metadata at house-N.json.
    for p in sorted(HOUSES_DIR.glob("house-*/house-*.json"),
                    key=lambda q: int(q.stem.split("-")[1])):
        try:
            recs.append(_enrich(json.loads(p.read_text()), state))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            # A malformed record shouldn't take down /houses entirely.
            print(f"warning: skipping {p.relative_to(BASE)}: {e}")
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
    """Serve the built React bundle's HTML entry. If `ui/dist/` is absent,
    return a 503 with the build command — typically you'd run `make web`
    in a second shell during development (Vite on :5173 proxies to here)."""
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


# Client-side router fallback: any non-API path (e.g. /house/house-21,
# /synthetic, /synthetic/house-1) loads the SPA's index.html so
# react-router can pick up the URL.
@app.get("/house/{rest:path}", tags=["meta"], response_class=FileResponse)
def _spa_house(rest: str):
    del rest
    return root()


@app.get("/synthetic", tags=["meta"], response_class=FileResponse)
def _spa_synthetic_root():
    return root()


@app.get("/synthetic/{rest:path}", tags=["meta"], response_class=FileResponse)
def _spa_synthetic(rest: str):
    # SPA fallback for browser navigation/reload to /synthetic, /synthetic/house-1,
    # etc. JSON endpoints live under /synthetics (plural) — same singular/plural
    # split as /house/* (SPA) vs /houses/* (API).
    del rest
    return root()


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
    energy_standard: Optional[str]   = Query(None, description="Exact match (enum from /ontology.energy_standards)"),
    has_basement:    Optional[bool]  = Query(None),
    min_area:        Optional[float] = Query(None),
    max_area:        Optional[float] = Query(None),
    min_price:       Optional[float] = Query(None),
    max_price:       Optional[float] = Query(None),
    min_year:        Optional[int]   = Query(None),
    max_year:        Optional[int]   = Query(None),
    modelable_in_bim_ai: Optional[bool] = Query(None, description="true → only houses bim-ai can model today; false → only blocked houses"),
    min_tier: Optional[str] = Query(None, description="Lowest acceptable reconstructability_tier: T0/T1/T2/T3/T4 — filters out records below it"),
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
    if energy_standard: recs = eq("energy_standard", energy_standard)
    if has_basement is not None:
        recs = [r for r in recs if r.get("has_basement") is has_basement]
    if min_area is not None: recs = [r for r in recs if r.get("area_m2") is not None and r["area_m2"] >= min_area]
    if max_area is not None: recs = [r for r in recs if r.get("area_m2") is not None and r["area_m2"] <= max_area]
    if min_price is not None: recs = [r for r in recs if r.get("price_eur") is not None and r["price_eur"] >= min_price]
    if max_price is not None: recs = [r for r in recs if r.get("price_eur") is not None and r["price_eur"] <= max_price]
    if min_year is not None: recs = [r for r in recs if r.get("year_built") is not None and r["year_built"] >= min_year]
    if max_year is not None: recs = [r for r in recs if r.get("year_built") is not None and r["year_built"] <= max_year]
    if modelable_in_bim_ai is not None:
        recs = [r for r in recs if r.get("modelable_in_bim_ai") is modelable_in_bim_ai]
    if min_tier:
        # Compare by leading "TN" prefix so callers can pass "T2" or full id.
        wanted = int(min_tier.lstrip("T").split("_")[0])
        recs = [r for r in recs
                if r.get("reconstructability_tier")
                and int(r["reconstructability_tier"].lstrip("T").split("_")[0]) >= wanted]
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
    pdf = _house_dir(rec["id"]) / f"{rec['key']}.pdf"
    if not pdf.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(str(pdf), media_type="application/pdf", filename=pdf.name)


@app.get("/houses/{key}/images", tags=["houses"])
def get_images(key: str):
    rec = _by_key(key)
    if not rec:
        raise HTTPException(status_code=404, detail=f"House {key!r} not found")
    return rec["images"]


# ── scene render cache ───────────────────────────────────────────────────────
# PDF-sourced scenes are reconstructed on demand from (PDF, page, crop_box, dpi)
# in the JSON record, written to tmp/scene-cache/<key>/<file>, and served from
# there. A JSON edit (newer mtime) triggers a re-render; otherwise the cache
# is reused. Non-PDF scenes never reach this route — they go through /static/.

@app.get("/scene/{key}/{file}", tags=["houses"])
def get_scene(key: str, file: str):
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad scene filename")
    from .scene_render import render_scene
    try:
        path = render_scene(key, file)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return FileResponse(str(path), media_type="image/avif")


# ── synthetic drawings ──────────────────────────────────────────────────────
# Synthetic technical-paper drawings generated by gpt-image-* for vision-model
# training. Lives under data/synthetic/<key>/ with one manifest.json per house.
# Tracked separately from real records — the UI exposes them as a second
# top-level section.

def _load_synthetic_manifest(key: str) -> Optional[dict]:
    mp = SYNTHETIC_DIR / key / "manifest.json"
    if not mp.exists():
        return None
    data = json.loads(mp.read_text())
    data["key"] = key
    for d in data.get("drawings") or []:
        d["url"] = f"/static/synthetic/{key}/{d['file']}"
    # Best-effort cross-link to the parent house record.
    linked_key = data.get("linked_house") or key
    parent = next((r for r in _load_all() if r["key"] == linked_key), None)
    if parent:
        data["linked_house_meta"] = {
            "key": parent["key"],
            "model": parent.get("model"),
            "manufacturer": parent.get("manufacturer"),
            "building_type": parent.get("building_type"),
        }
    # Composite sheet (M0): if scripts/compose_house_sheet.py has produced
    # output for this house, include the bbox metadata + image URL so the UI
    # can render the "fake whole document" view.
    comp_json = SYNTHETIC_DIR / key / "composite.json"
    comp_png = SYNTHETIC_DIR / key / f"{key}-composite.png"
    if comp_json.exists() and comp_png.exists():
        data["composite"] = {
            **json.loads(comp_json.read_text()),
            "url": f"/static/synthetic/{key}/{comp_png.name}",
        }
    return data


@app.get("/synthetics", tags=["synthetic"])
def list_synthetics():
    """Every synthetic-drawing manifest, with image URLs + parent metadata.

    Includes houses that have no synthetic drawings yet — they're listed with
    `drawings: []` so the UI can show the full coverage matrix and mark
    "not generated yet" cells."""
    if not SYNTHETIC_DIR.exists():
        return []
    out = []
    for d in sorted(SYNTHETIC_DIR.iterdir()):
        if not d.is_dir():
            continue
        manifest = _load_synthetic_manifest(d.name)
        if manifest:
            out.append(manifest)
    return out


@app.get("/synthetics/{key}", tags=["synthetic"])
def get_synthetic(key: str):
    data = _load_synthetic_manifest(key)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No synthetic manifest for {key!r}")
    return data
