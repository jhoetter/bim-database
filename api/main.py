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
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .geometry_util import as_point as _as_point, wall_segment as _wall_segment
from .persistence import atomic_write_json, atomic_write_text, locked_path

BASE = Path(__file__).parent.parent
DATASET_DIR = BASE / "data" / "dataset"
PDFS_DIR = BASE / "data" / "pdfs"
INCOMING_DIR = PDFS_DIR / "incoming"
SUBMISSIONS_DIR = PDFS_DIR / "submissions"
UI_DIST = BASE / "ui" / "dist"

log = logging.getLogger("bim-db-api")

app = FastAPI(
    title="BIM Dataset API",
    description=(
        "REST API for the supervised-learning corpus of architectural drawings. "
        "PDF intake → scene extraction → annotation → export."
    ),
    version="4.0.0",
)

# CORS: default to localhost only (the SPA is served same-origin; dev tools
# run on localhost). Set BIM_DB_CORS_ORIGINS=https://a.com,https://b.com to
# allow specific external origins. Avoid the wildcard so a server reachable
# beyond 127.0.0.1 can't be driven by any page.
_cors_env = os.environ.get("BIM_DB_CORS_ORIGINS", "").strip()
if _cors_env:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_env.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
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
EXPORT_CACHE_STATIC = BASE / "tmp" / "exports-cache"
EXPORT_CACHE_STATIC.mkdir(parents=True, exist_ok=True)
app.mount("/static/exports-cache", StaticFiles(directory=str(EXPORT_CACHE_STATIC)),
          name="exports-cache-static")

# Built React bundle. Hashed asset files live in ui/dist/assets/. Mount
# unconditionally so `vite build --watch` (which empties+rebuilds the dir
# at startup) can't race the uvicorn boot — if the dir doesn't exist when
# a request lands StaticFiles just returns 404, which is what we want.
(UI_DIST / "assets").mkdir(parents=True, exist_ok=True)
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


# Client-side router fallback. The SPA now lives at the root — any path
# that isn't claimed by a JSON API route or a static mount serves
# index.html so react-router can resolve it. Specific routes register
# above this catchall so /datasets, /labels, /pdfs, /exports etc. still
# hit their handlers.
@app.get("/dataset", tags=["meta"], response_class=FileResponse, include_in_schema=False)
def _spa_legacy_dataset_root():
    return root()


@app.get("/dataset/{rest:path}", tags=["meta"], response_class=FileResponse, include_in_schema=False)
def _spa_legacy_dataset(rest: str):
    del rest
    return root()


# ── dataset manifest ───────────────────────────────────────────────────────

def _intake_stub_manifest(key: str) -> dict | None:
    """Return a minimal dataset-manifest shape for a house that has only
    an intake bundle (no extracted scenes yet). Lets the UI list +
    open such houses so the user can navigate straight to /extract."""
    intake_mp = INCOMING_DIR / key / "manifest.json"
    if not intake_mp.exists():
        return None
    try:
        m = json.loads(intake_mp.read_text())
    except json.JSONDecodeError:
        return None
    return {
        "schema_version": "1.0",
        "key": key,
        "linked_house": key,
        # P1.2 — title is just the key; notes ride along separately so a
        # 200-char upload comment doesn't become the card headline.
        "model": key,
        "manufacturer": None,
        "building_type": None,
        "drawings": [],
        "intake_only": True,
        "intake_page_count": m.get("page_count"),
        "intake_notes": m.get("user_notes") or None,
    }


def _load_dataset_manifest(key: str) -> dict | None:
    mp = DATASET_DIR / key / "manifest.json"
    if not mp.exists():
        # Fall back to an intake stub so houses with an upload but no
        # extracted scenes still surface in the UI.
        return _intake_stub_manifest(key)
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
            except (json.JSONDecodeError, OSError) as e:
                # A corrupt/unreadable label file is NOT the same as "no
                # labels": flag it so the UI/agent can spot data damage
                # (M1) instead of silently treating the scene as unlabeled.
                log.warning("label file %s unreadable: %s", label_file, e)
                d["labeled"] = False
                d["label_count"] = 0
                d["corrupt"] = True
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
    # Agentic-labeling F2: surface house_facts.workflow.driven_by so the
    # SPA card shows a chip when the labeling was done by bim-agent.
    facts_path = DATASET_DIR / key / "house_facts.json"
    if facts_path.exists():
        try:
            facts = json.loads(facts_path.read_text())
            wf = facts.get("workflow") or {}
            if wf.get("driven_by"):
                data["driven_by"] = wf.get("driven_by")
                data["driven_by_run_id"] = wf.get("driven_by_run_id")
        except (json.JSONDecodeError, OSError) as e:
            log.warning("house_facts %s unreadable: %s", facts_path, e)
    return data


@app.get("/datasets", tags=["dataset"])
def list_datasets():
    """Every dataset house — both fully-extracted houses (with a manifest
    under data/dataset/<key>/) AND intake-only houses (an upload landed in
    data/pdfs/incoming/<key>/ but no scenes have been cut yet). The
    second set surfaces as cards with drawings:[] + intake_only:true so
    the UI can list them and route the click straight to /extract."""
    keys: set[str] = set()
    if DATASET_DIR.exists():
        for d in DATASET_DIR.iterdir():
            if d.is_dir(): keys.add(d.name)
    if INCOMING_DIR.exists():
        for d in INCOMING_DIR.iterdir():
            if d.is_dir(): keys.add(d.name)
    out = []
    for k in sorted(keys):
        manifest = _load_dataset_manifest(k)
        if manifest:
            out.append(manifest)
    return out


@app.get("/datasets/{key}", tags=["dataset"])
def get_dataset(key: str):
    data = _load_dataset_manifest(key)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No dataset manifest for {key!r}")
    return data


# ── house_facts (U13) ──────────────────────────────────────────────────────
# Per-house structural memory — extent, heights, wall_thickness, orientation,
# workflow phase pointer + per-scene metadata. Lives at
# data/dataset/<key>/house_facts.json. Schema kept light: the UI is the
# producer + sole consumer, and the shape is documented in
# ui/src/lib/house_facts.ts (HouseFacts). Server only validates that the
# payload is a JSON object with schema_version present.

@app.get("/datasets/{key}/house_facts", tags=["dataset"])
def get_house_facts(key: str):
    p = DATASET_DIR / key / "house_facts.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"No house_facts for {key!r}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"house_facts.json corrupt: {e}") from e


@app.put("/datasets/{key}/house_facts", tags=["dataset"])
def put_house_facts(key: str, body: dict = Body(...)):
    if not isinstance(body, dict) or "schema_version" not in body:
        raise HTTPException(status_code=400, detail="payload must be a JSON object with schema_version")
    p = DATASET_DIR / key / "house_facts.json"
    # C2: serialize against recompute (which reads facts to preserve
    # human-set fields) so this full replace isn't lost mid-recompute.
    with locked_path(p):
        atomic_write_json(p, body)
    return {"ok": True, "bytes": p.stat().st_size}


@app.post("/datasets/{key}/recompute-facts", tags=["dataset"])
def recompute_facts(key: str):
    """G1-6 (agentic-labeling-followups-tracker): rebuild
    house_facts.json from every scene's labels. Idempotent; safe to
    call repeatedly. Returns the freshly-derived facts dict.

    USE when:
      - A developer notices stale `scene_metadata` entries (a scene
        was deleted but its facts row stuck around).
      - You want to verify the derivation pipeline is producing what
        the SPA's `promoteToFacts` would.
      - You just touched labels out-of-band (e.g. edited a JSON file
        by hand) and want the facts to catch up without a per-scene
        label PUT.
    """
    _safe_key(key)
    house_dir = DATASET_DIR / key
    if not house_dir.exists():
        raise HTTPException(status_code=404, detail=f"no dataset for {key!r}")
    from .fact_derivation import recompute_facts_after_label_write
    facts = recompute_facts_after_label_write(key, dataset_root=DATASET_DIR)
    return facts


# ── per-scene attribute patch (U9) ─────────────────────────────────────────
# In-place edit of a single scene's classification — kind / floor / view /
# title — used by the U9 popover and the U10 AnnotatePage header. The
# dataset manifest (data/dataset/<key>/manifest.json) is the source of
# truth; the response returns the freshly-loaded manifest so the UI can
# refresh in one round-trip.

_SCENE_PATCH_KEYS = {"kind", "floor", "view", "title"}


@app.patch("/datasets/{key}/drawings/{file}", tags=["dataset"])
def patch_scene_attrs(key: str, file: str, body: dict = Body(...)):
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=400, detail="patch body must be a non-empty object")
    unknown = set(body) - _SCENE_PATCH_KEYS
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown patch keys: {sorted(unknown)}")
    mp = DATASET_DIR / key / "manifest.json"
    if not mp.exists():
        raise HTTPException(status_code=404, detail=f"No dataset manifest for {key!r}")
    # C2: serialize read-modify-write of the manifest so two concurrent
    # scene-attribute patches can't lose each other's edits.
    with locked_path(mp):
        data = json.loads(mp.read_text())
        drawings = data.get("drawings") or []
        target = next((d for d in drawings if d.get("file") == file), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"No drawing {file!r} in {key!r}")
        for k, v in body.items():
            # null clears; otherwise overwrite.
            if v is None:
                target.pop(k, None)
            else:
                target[k] = v
        atomic_write_json(mp, data)
    return _load_dataset_manifest(key)


# ── per-scene agent plan Markdown ─────────────────────────────────────────

def _ensure_dataset_scene(key: str, file: str) -> None:
    _safe_key(key)
    if "/" in file or "\\" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    if not _scene_image_path("dataset", key, file).exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")


def _plan_http_error(e: Exception):
    from .scene_plans import PlanConflictError
    from .scene_plan_state import PlanStateConflictError
    if isinstance(e, (PlanConflictError, PlanStateConflictError)):
        raise HTTPException(status_code=409, detail=str(e)) from e
    if isinstance(e, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(e)) from e
    if isinstance(e, (KeyError, ValueError)):
        raise HTTPException(status_code=400, detail=str(e)) from e
    raise e


# ── annotation labels ─────────────────────────────────────────────────────
# Scope-aware so the URL shape stays compatible with the existing UI; the
# `house` scope is gone — only `dataset` is accepted post-R0.

LABELS_SCHEMA_PATH = BASE / "schema" / "scene_labels.schema.json"
try:
    LABELS_SCHEMA = json.loads(LABELS_SCHEMA_PATH.read_text()) if LABELS_SCHEMA_PATH.exists() else None
except (json.JSONDecodeError, OSError):
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


def _label_skeleton(scope: str, key: str, file: str) -> dict[str, Any]:
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
        "scene_orientation": None,
        "scene_level": None,
        "image_size_px": [w, h],
        "labels": [],
    }


def _recompute_facts_from_scratch(key: str, *, lock_held: bool = False) -> dict:
    """Rebuild facts after a destructive label reset.

    Normal label writes preserve human-entered facts. Reset semantics are
    stronger: remove stale derived/manual labeling state first so the next
    run starts from the current labels only.

    H2: the unlink + rebuild are held under the house_facts lock so a
    concurrent label-write recompute can't read a half-reset house. When the
    caller already holds that lock (e.g. reset_house_labeling, which also
    rmtree's labels/ under the same lock), pass ``lock_held=True`` to call the
    unlocked impl and avoid re-entering the (non-reentrant) lock.
    """
    from .fact_derivation import _recompute_facts_impl
    facts_path = DATASET_DIR / key / "house_facts.json"

    def _do() -> dict:
        if facts_path.exists():
            facts_path.unlink()
        return _recompute_facts_impl(key, dataset_root=DATASET_DIR)

    if lock_held:
        return _do()
    with locked_path(facts_path):
        return _do()


_LABEL_TYPES_BY_SCENE_TAG = {
    "grundriss": {
        "wall",
        "floorplan_opening",
        "dimensioned_distance",
        "dimension_number",
    },
    "ansicht": {
        "view_opening",
        "component_line",
        "height_mark",
        "dimensioned_distance",
        "dimension_number",
    },
    "schnitt": {
        "view_opening",
        "component_line",
        "height_mark",
        "dimensioned_distance",
        "dimension_number",
    },
    "sonstiges": {
        "wall",
        "floorplan_opening",
        "view_opening",
        "component_line",
        "height_mark",
        "dimensioned_distance",
        "dimension_number",
    },
    "nicht_klassifiziert": set(),
}


def _validate_scene_tag_label_palette(payload: dict[str, Any]) -> None:
    """Reject labels that the scene category cannot semantically support.

    The SPA tool palette is only a UI affordance; MCP and direct HTTP writes
    must get the same guard here so invalid labels cannot be persisted.
    """
    scene_tag = payload.get("scene_tag") or "nicht_klassifiziert"
    allowed = _LABEL_TYPES_BY_SCENE_TAG.get(scene_tag)
    if allowed is None:
        raise HTTPException(status_code=400, detail=f"unknown scene_tag {scene_tag!r}")
    labels = payload.get("labels") or []
    bad = sorted({
        str(lab.get("type"))
        for lab in labels
        if isinstance(lab, dict) and lab.get("type") not in allowed
    })
    if bad:
        raise HTTPException(
            status_code=422,
            detail=(
                f"label type(s) {bad} not allowed on scene_tag={scene_tag!r}; "
                f"allowed={sorted(allowed)}"
            ),
        )


def _floorplan_opening_axis(label: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return the opening's along-wall centerline from its quad geometry.

    UI-created floorplan openings store quad corners as [a,b,c,d], with a-b
    and d-c running along the wall. The semantic placement check should test
    the centerline, not the quad corners, because the corners are intentionally
    offset by wall thickness.
    """
    quad = ((label.get("geometry") or {}).get("quad") or [])
    if not isinstance(quad, list) or len(quad) != 4:
        return None
    pts = [_as_point(p) for p in quad]
    if any(p is None for p in pts):
        return None
    a, b, c, d = pts  # type: ignore[misc]
    return (
        ((a[0] + d[0]) / 2.0, (a[1] + d[1]) / 2.0),
        ((b[0] + c[0]) / 2.0, (b[1] + c[1]) / 2.0),
    )


def _floorplan_opening_depth_axis(label: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    quad = ((label.get("geometry") or {}).get("quad") or [])
    if not isinstance(quad, list) or len(quad) != 4:
        return None
    pts = [_as_point(p) for p in quad]
    if any(p is None for p in pts):
        return None
    a, b, c, d = pts  # type: ignore[misc]
    return (
        ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0),
        ((d[0] + c[0]) / 2.0, (d[1] + c[1]) / 2.0),
    )


def _wall_label_id() -> str:
    return f"lab-{hashlib.sha256(str(_dt.datetime.now().timestamp()).encode()).hexdigest()[:10]}"


def _wall_bbox_region(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    pad_px: int = 96,
    image_size: tuple[int, int] | None = None,
) -> tuple[int, int, int, int]:
    x0 = int(round(min(start[0], end[0]) - pad_px))
    y0 = int(round(min(start[1], end[1]) - pad_px))
    x1 = int(round(max(start[0], end[0]) + pad_px))
    y1 = int(round(max(start[1], end[1]) + pad_px))
    if image_size:
        w, h = image_size
        x0 = max(0, min(w, x0))
        y0 = max(0, min(h, y0))
        x1 = max(x0 + 1, min(w, x1))
        y1 = max(y0 + 1, min(h, y1))
    return (x0, y0, x1, y1)


def _wall_ink_overlap(
    image: Any,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    min_wall_px: int = 8,
    tol_px: int = 18,
    thin_aware: bool = True,
    close_px: int = 82,
    thresh: int | None = None,
    region: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    from .wall_score import score_walls

    if region is None:
        region = _wall_bbox_region(start, end, image_size=getattr(image, "size", None))
    res = score_walls(
        image,
        [(start, end)],
        region=region,
        min_wall_px=min_wall_px,
        tol_px=tol_px,
        thresh=thresh,
        thin_aware=thin_aware,
        close_px=close_px,
    )
    off = res.get("off_ink_segments") or []
    overlap = 1.0 if not off else float(off[0][4])
    return {
        "ink_overlap": round(overlap, 3),
        "status": "on_ink" if overlap >= 0.6 else "off_ink",
        "score": res,
        "region": list(region),
    }


def _validate_dependent_labels(payload: dict[str, Any]) -> None:
    """Reject label sets the UI cannot honestly create.

    UI affordances are not enough because MCP/direct HTTP can bypass them. Keep
    semantic dependency checks here so every writer follows the same rules.
    """
    labels = [lab for lab in (payload.get("labels") or []) if isinstance(lab, dict)]
    by_id: dict[str, dict[str, Any]] = {}
    for lab in labels:
        label_id = lab.get("id")
        if not isinstance(label_id, str) or not label_id:
            continue
        if label_id in by_id:
            raise HTTPException(status_code=422, detail=f"duplicate label id {label_id!r}")
        by_id[label_id] = lab

    # All declared relations must point at an existing label. This matches the
    # UI delete path, which strips stale incoming relations when a target goes
    # away.
    for lab in labels:
        label_id = lab.get("id", "<unknown>")
        for rel in lab.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            other_id = rel.get("other_id")
            if isinstance(other_id, str) and other_id not in by_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"label {label_id!r} relation target {other_id!r} does not exist",
                )

    for lab in labels:
        label_id = lab.get("id", "<unknown>")
        label_type = lab.get("type")

        if label_type == "floorplan_opening":
            parent_ids = [
                rel.get("other_id")
                for rel in (lab.get("relations") or [])
                if isinstance(rel, dict) and rel.get("kind") == "belongs_to"
            ]
            if not parent_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"floorplan_opening {label_id!r} must belong_to a wall",
                )
            parent = next(
                (
                    by_id.get(pid)
                    for pid in parent_ids
                    if isinstance(pid, str)
                    and by_id.get(pid, {}).get("type") == "wall"
                ),
                None,
            )
            if parent is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"floorplan_opening {label_id!r} belongs_to target must be an existing wall",
                )
            parent_attrs = parent.get("attributes") or {}
            if parent_attrs.get("quality_status") in {"off_ink", "unanchored"}:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"floorplan_opening {label_id!r} cannot belong_to parent wall "
                        f"{parent.get('id')!r} while parent quality_status="
                        f"{parent_attrs.get('quality_status')!r}; repair with upsert_wall_anchored first"
                    ),
                )
            opening_axis = _floorplan_opening_axis(lab)
            opening_depth_axis = _floorplan_opening_depth_axis(lab)
            parent_wall = _wall_segment(parent)
            if opening_axis is None or parent_wall is None:
                continue
            from .geometry_checks import floorplan_opening_quality

            quality = floorplan_opening_quality(
                opening_axis,
                opening_depth_axis or opening_axis,
                parent_wall,
                tol_px=30.0,
                is_garage_door=(lab.get("attributes") or {}).get("opening_kind") == "garage_door",
            )
            if not quality["ok"]:
                first = quality["defects"][0]
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"floorplan_opening {label_id!r} failed {first['category']} "
                        f"against parent wall {parent.get('id')!r}: {first['message']} "
                        "(not placed on parent wall)"
                    ),
                )

        if label_type == "dimension_number":
            targets = [
                by_id.get(rel.get("other_id"))
                for rel in (lab.get("relations") or [])
                if isinstance(rel, dict) and rel.get("kind") == "labels"
            ]
            if not any(t and t.get("type") == "dimensioned_distance" for t in targets):
                raise HTTPException(
                    status_code=422,
                    detail=f"dimension_number {label_id!r} must label an existing dimensioned_distance",
                )

        ref_line_id = (lab.get("attributes") or {}).get("reference_line_id")
        if ref_line_id is not None:
            target = by_id.get(ref_line_id)
            if target is None or target.get("type") != "component_line":
                raise HTTPException(
                    status_code=422,
                    detail=f"label {label_id!r} reference_line_id must point to an existing component_line",
                )


@app.get("/labels/{scope}/{key}/{file}", tags=["labels"])
def get_labels(scope: str, key: str, file: str):
    """Return the label set for one scene. If no labels file exists yet,
    return a fresh skeleton with image_size_px pre-filled — so the UI can
    open the editor on a brand-new scene without a separate 'create' step."""
    label_path = _safe_label_path(scope, key, file)
    if label_path.exists():
        return json.loads(label_path.read_text())
    return _label_skeleton(scope, key, file)


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
    _validate_scene_tag_label_palette(payload)
    if _jsonschema and LABELS_SCHEMA:
        try:
            _jsonschema.validate(payload, LABELS_SCHEMA)
        except _jsonschema.ValidationError as e:
            raise HTTPException(status_code=422, detail=f"schema: {e.message} at {list(e.absolute_path)}")
    _validate_dependent_labels(payload)
    if scope == "dataset":
        strict_walls = [
            lab for lab in (payload.get("labels") or [])
            if isinstance(lab, dict)
            and lab.get("type") == "wall"
            and lab.get("status", "readable") == "readable"
            and (lab.get("attributes") or {}).get("anchoring_required") is True
        ]
        if strict_walls:
            img_path = _scene_image_path(scope, key, file)
            if img_path.exists():
                from PIL import Image as PILImage
                with PILImage.open(img_path) as src_img:
                    src = src_img.convert("RGB")
                    for lab in strict_walls:
                        seg = _wall_segment(lab)
                        if seg is None:
                            continue
                        check = _wall_ink_overlap(src, seg[0], seg[1])
                        if check["status"] == "off_ink":
                            raise HTTPException(
                                status_code=422,
                                detail=(
                                    f"wall {lab.get('id')!r} has anchoring_required=true but "
                                    f"ink_overlap={check['ink_overlap']}; use upsert_wall_anchored"
                                ),
                            )
    label_path.parent.mkdir(parents=True, exist_ok=True)
    # H2: skip the write AND the O(scenes) full-house recompute when the
    # payload is byte-identical to what's already on disk — a common case for
    # the agent loop re-PUTing an unchanged scene. Safe: identical labels
    # derive identical facts, and we only skip when facts already exist (so
    # they were already derived from exactly these labels).
    if scope == "dataset" and label_path.exists():
        try:
            existing = json.loads(label_path.read_text())
        except json.JSONDecodeError:
            existing = None
        if existing == payload and (DATASET_DIR / key / "house_facts.json").exists():
            return {
                "saved": str(label_path.relative_to(BASE)),
                "bytes": label_path.stat().st_size,
                "unchanged": True,
            }
    atomic_write_json(label_path, payload)
    # G1 (agentic-labeling-followups-tracker): server-side fact derivation.
    # Every label write triggers a full recompute of facts.calibration_per_scene
    # + scene_metadata + extent + heights + openings_catalog. Single source of
    # truth so the MCP + SPA paths produce identical facts. Failure here is
    # non-fatal — the label write already succeeded.
    derivation_note: str | None = None
    if scope == "dataset":
        try:
            from .fact_derivation import recompute_facts_after_label_write
            recompute_facts_after_label_write(key, dataset_root=DATASET_DIR)
        except Exception as e:  # noqa: BLE001
            derivation_note = f"label saved; fact derivation failed: {e!s}"
    resp = {
        "saved": str(label_path.relative_to(BASE)),
        "bytes": label_path.stat().st_size,
    }
    if derivation_note:
        resp["derivation_warning"] = derivation_note
    return resp


@app.delete("/labels/{scope}/{key}/{file}", tags=["labels"])
def reset_scene_labels(scope: str, key: str, file: str, reset_plan: bool = False):
    """Reset one scene's labels and workflow metadata, keeping the scene image.

    Mirrors the AnnotatePage "Labels zurücksetzen" action, but also rebuilds
    house_facts from scratch so stale calibration/extent/orientation facts from
    the cleared labels cannot leak into the next run.
    """
    if scope != "dataset":
        raise HTTPException(status_code=400, detail="only dataset labels can be reset")
    payload = _label_skeleton(scope, key, file)
    label_path = _safe_label_path(scope, key, file)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(label_path, payload)
    plan_deleted = False
    if reset_plan:
        from .scene_plan_state import delete_plan_state_files
        plan_deleted = delete_plan_state_files(DATASET_DIR, key, file) > 0
    else:
        from .scene_plan_state import mark_state_stale_after_reset
        mark_state_stale_after_reset(DATASET_DIR, key, file)
    facts = _recompute_facts_from_scratch(key)
    return {
        "ok": True,
        "file": file,
        "labels_reset": 1,
        "label_count": 0,
        "plan_deleted": plan_deleted,
        "house_facts": facts,
    }


@app.delete("/datasets/{key}/labels", tags=["dataset"])
def reset_house_labeling(key: str, reset_plans: bool = False):
    """Reset every scene's labels for a house, keeping extracted scenes.

    This is the MCP/automation counterpart to scene-level UI resets when the
    user wants a fresh labeling run without re-extracting the source PDF.
    """
    _safe_key(key)
    house_dir = DATASET_DIR / key
    manifest_path = house_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"no dataset manifest for {key!r}")
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"manifest.json corrupt: {e}") from e
    drawings = [d for d in (manifest.get("drawings") or []) if d.get("file")]
    labels_dir = house_dir / "labels"
    facts_path = house_dir / "house_facts.json"
    reset_files: list[str] = []
    # H2: hold the house_facts lock across the rmtree + reskeleton + rebuild so
    # a concurrent label-write recompute (which locks house_facts) cannot read
    # the labels dir mid-deletion. Recompute is called with lock_held=True to
    # avoid re-entering this same lock.
    with locked_path(facts_path):
        if labels_dir.exists():
            import shutil
            shutil.rmtree(labels_dir)
        labels_dir.mkdir(parents=True, exist_ok=True)
        for d in drawings:
            file = d["file"]
            payload = _label_skeleton("dataset", key, file)
            atomic_write_json(_safe_label_path("dataset", key, file), payload)
            reset_files.append(file)
        facts = _recompute_facts_from_scratch(key, lock_held=True)
    plans_deleted = 0
    if reset_plans:
        from .scene_plan_state import delete_plan_state_files
        plans_deleted = delete_plan_state_files(DATASET_DIR, key)
    else:
        from .scene_plan_state import mark_state_stale_after_reset
        for file in reset_files:
            mark_state_stale_after_reset(DATASET_DIR, key, file)
    return {
        "ok": True,
        "key": key,
        "mode": "labels_only_keep_scenes",
        "labels_reset": len(reset_files),
        "plans_deleted": plans_deleted,
        "files": reset_files,
        "house_facts": facts,
    }


# ── PDF intake (R1 — landing routes; full impl lands in R1 wave) ──────────


# ── Customer submission review + promote (developer surface) ──────────────
# Submissions land in data/pdfs/submissions/<id>/ via either:
#   * the hardened standalone form_api/ process (production), or
#   * POST /submit on THIS dev API (single-user-localhost convenience).
# The developer reviews them here and promotes the clean ones into
# data/pdfs/incoming/house-NN/ for the existing R2 scene extractor.


def _safe_key(key: str) -> None:
    if not key or "/" in key or ".." in key or "\\" in key:
        raise HTTPException(status_code=400, detail=f"bad key {key!r}")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_manifest(key: str, m: dict) -> None:
    bundle = INCOMING_DIR / key
    bundle.mkdir(parents=True, exist_ok=True)
    atomic_write_json(bundle / "manifest.json", m)


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


# ── R2 — PDF page render + scene extraction ───────────────────────────────

PDF_CACHE = BASE / "tmp" / "pdf-cache"


# ── Agentic-labeling grid overlay (tracker §C2) ───────────────────────────
# Renders the scene image (or PDF page) with the three-tier coordinate
# grid an agent uses to point at pixels precisely. Disk-cached under
# tmp/grid-cache/<key>/ keyed on (image mtime, region, tiers, max_dim).

GRID_CACHE = BASE / "tmp" / "grid-cache"


def _parse_tiers(tiers: str) -> tuple[str, ...]:
    raw = [t.strip() for t in tiers.split(",") if t.strip()]
    valid = {"broad", "finer", "detail"}
    bad = [t for t in raw if t not in valid]
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown tier(s) {bad}; allowed {sorted(valid)}")
    if not raw:
        raise HTTPException(status_code=400, detail="at least one tier required")
    return tuple(raw)


def _parse_enhance(enhance: str | None) -> str:
    """Validate the `enhance` query arg (issue #2). Returns a normalized
    mode; defaults to 'none'."""
    from .grid_render import ENHANCE_MODES
    mode = (enhance or "none").strip().lower()
    if mode not in ENHANCE_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown enhance mode {mode!r}; allowed {list(ENHANCE_MODES)}",
        )
    return mode


GRID_FORMATS = ("png", "png8")


def _parse_format(fmt: str | None) -> str:
    """Validate the grid output `format` query arg (issue #3). Returns a
    normalized value; defaults to the cheaper palette PNG."""
    f = (fmt or "png").strip().lower()
    if f not in GRID_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown format {f!r}; allowed {list(GRID_FORMATS)}",
        )
    return f


def _save_grid_png(overlay, out_path, fmt: str) -> None:
    """Save the rendered grid overlay as PNG (issue #3).

    `png8` quantizes to a 256-color palette/indexed PNG. The grid lines,
    line-art/scan, and the handful of label colors are few-coloured, so
    256 colours is visually near-lossless yet typically 2-4x smaller than
    full RGBA — which directly cuts the base64 token cost of every
    verify-after-place read. Dithering is disabled to keep thin grid
    lines crisp (and to compress better). `png` keeps full RGBA.
    """
    from PIL import Image as PILImage
    if fmt == "png8":
        flat = overlay.convert("RGB") if overlay.mode != "RGB" else overlay
        flat.quantize(colors=256, dither=PILImage.Dither.NONE).save(
            out_path, format="PNG", optimize=True,
        )
    else:
        overlay.save(out_path, format="PNG", optimize=True)


def _parse_region(region: str | None) -> tuple[int, int, int, int] | None:
    if not region:
        return None
    try:
        parts = [int(p) for p in region.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="region must be 'x0,y0,x1,y1' integers")
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="region must be 'x0,y0,x1,y1' (4 ints)")
    return (parts[0], parts[1], parts[2], parts[3])


def _parse_grid_style(style: str | None) -> str:
    s = (style or "standard").strip().lower()
    if s not in ("standard", "coordinate_audit", "coordinate_pair", "coordinate_multicolor"):
        raise HTTPException(
            status_code=400,
            detail="style must be standard, coordinate_audit, coordinate_pair, or coordinate_multicolor",
        )
    return s


def _parse_label_render_style(style: str | None) -> str:
    s = (style or "standard").strip().lower()
    if s not in (
        "standard",
        "coordinate_audit",
        "coordinate_pair",
        "coordinate_multicolor",
        "semantic",
        "qa",
        "ink_compare",
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "style must be standard, coordinate_audit, coordinate_pair, "
                "coordinate_multicolor, semantic, qa, or ink_compare"
            ),
        )
    return s


def _parse_target(target: str | None) -> tuple[int, int] | None:
    if not target:
        return None
    try:
        parts = [int(p) for p in target.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="target must be 'x,y' integers")
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="target must be 'x,y' (2 ints)")
    return (parts[0], parts[1])


def _parse_target_line(target_line: str | None) -> str:
    t = (target_line or "none").strip().lower()
    if t not in ("vertical", "horizontal", "none"):
        raise HTTPException(status_code=400, detail="target_line must be vertical, horizontal, or none")
    return t


def _parse_background_opacity(background_opacity: float | None) -> tuple[float, bool]:
    if background_opacity is None:
        return 0.5, False
    try:
        value = float(background_opacity)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="background_opacity must be a number in (0, 1]")
    if not 0.0 < value <= 1.0:
        raise HTTPException(status_code=400, detail="background_opacity must be in (0, 1]")
    return value, True


def _parse_contrast(contrast: str | None) -> str:
    value = (contrast or "high").strip().lower()
    if value not in ("normal", "high"):
        raise HTTPException(status_code=400, detail="contrast must be normal or high")
    return value


def _parse_show_relations(show_relations: str | None) -> str:
    value = (show_relations or "required").strip().lower()
    if value not in ("required", "all", "none"):
        raise HTTPException(status_code=400, detail="show_relations must be required, all, or none")
    return value


def _parse_show_height_guides(show_height_guides: str | None) -> str:
    value = (show_height_guides or "auto").strip().lower()
    if value not in ("auto", "always", "never"):
        raise HTTPException(status_code=400, detail="show_height_guides must be auto, always, or never")
    return value


def _parse_show_openings(show_openings: str | None) -> str:
    value = (show_openings or "full").strip().lower()
    if value not in ("full", "outline", "hide"):
        raise HTTPException(status_code=400, detail="show_openings must be full, outline, or hide")
    return value


def _scene_px_per_mm(key: str, file: str) -> float | None:
    facts_path = DATASET_DIR / key / "house_facts.json"
    if not facts_path.exists():
        return None
    try:
        facts = json.loads(facts_path.read_text())
    except json.JSONDecodeError:
        return None
    calib = ((facts.get("calibration_per_scene") or {}).get(file) or {})
    value = calib.get("px_per_mm")
    try:
        px_per_mm = float(value)
    except (TypeError, ValueError):
        return None
    return px_per_mm if px_per_mm > 0 else None


# ── issue #25: clip detection + bbox auto-expansion ───────────────────────
#
# The auto-segmentation bbox (from the vision-LLM region detection) can
# under-shoot a drawing's true extent — most often vertically on a tall
# section/elevation, cutting the roof apex so the Firsthöhe (ridge) never
# lands in any extracted raster. We detect that at extract time: if
# significant ink touches a crop border, the drawing was cut there, so we
# grow the bbox toward that border (clamped to the page) and re-crop, then
# add a small breathing margin. General — not house-specific — and it keeps
# the crop_from / (dpi/72) coordinate semantics intact (the bbox we record
# is always the final, expanded PDF-unit rect actually rendered).

CLIP_BORDER_FRAC = 0.012      # border strip thickness as a fraction of the
                              # crop's short side (min 2 px)
CLIP_INK_FRAC = 0.015         # ≥1.5% of border positions carrying ink ⇒ a
                              # stroke crosses the edge → content is cut
CLIP_INK_THRESHOLD = 200      # grayscale < this counts as ink
CLIP_GROW_FRAC = 0.10         # grow a clipped side by 10% of that span/iter
CLIP_MAX_ITERS = 6
CLIP_MARGIN_FRAC = 0.02       # final breathing margin once unclipped


# ── R6 — bulk export ─────────────────────────────────────────────────────

EXPORTS_DIR = BASE / "data" / "exports"

HOUSE_FACTS_DUMP_NOTE = (
    "house_facts in this app live in the browser's localStorage. Export "
    "captures the per-scene labels + the derived homography; the user is "
    "expected to copy house_facts.json into the export via the UI download."
)


# ── R4 — export preview (per-scene rectified + Set A / Set B labels) ─────

EXPORT_CACHE = BASE / "tmp" / "exports-cache"

# Label types that go into Set A (the "Model 1 must detect" subset —
# dimensioned strokes only, plus their paired dim_numbers when present).
SET_A_TYPES = {"dimensioned_distance", "dimension_number"}


@app.delete("/datasets/{key}", tags=["dataset"], status_code=204)
def reset_house(key: str):
    """Wipe every extracted scene + every label for a house, BUT keep the
    intake bundle so the user can re-extract from the same PDF.

    Removes:
      - data/dataset/<key>/ (manifest, drawings, labels)
      - the intake manifest's extracted_scenes list (reset to [])
      - sets intake state back to 'partial'

    Keeps:
      - data/pdfs/incoming/<key>/ (the consolidated PDF + source files)

    This is the "I messed up, let me start over from the PDF" action that
    the Extract page surfaces in its menu. It is destructive and cannot
    be undone — the caller is responsible for confirmation.
    """
    _safe_key(key)
    import shutil
    ds_dir = DATASET_DIR / key
    # H2: exclude a concurrent label-write recompute (which locks house_facts)
    # from reading the dataset dir while it is being removed.
    with locked_path(ds_dir / "house_facts.json"):
        if ds_dir.exists():
            shutil.rmtree(ds_dir)
    # Reset the intake manifest in lockstep so the next list call shows
    # the bundle as "ready to extract" rather than "extracted".
    manifest = _read_manifest(key)
    if manifest is not None:
        manifest["extracted_scenes"] = []
        manifest["state"] = _bundle_state(key, manifest)
        _write_manifest(key, manifest)
    return None


RECYCLE_DIR = BASE / "tmp" / "recycle"
RECYCLE_TTL_SEC = 3600  # A3 Q5 ★ — 1 h


# Plan-state subsystem (H5) lives in api/routes_plan_state.py. Include it
# here — after all shared helpers are defined and before the SPA catch-all
# — so its routes register ahead of /{rest:path}.
from .routes_geometry import router as _geometry_router  # noqa: E402
app.include_router(_geometry_router)
from .routes_pdf import router as _pdf_router  # noqa: E402
app.include_router(_pdf_router)
from .routes_export import router as _export_router  # noqa: E402
app.include_router(_export_router)
from .routes_plan_state import router as _plan_state_router  # noqa: E402
app.include_router(_plan_state_router)


# ── SPA catchall ────────────────────────────────────────────────────────
# MUST be registered last. Any GET path that wasn't claimed by a JSON
# route or a static mount above falls through to index.html so
# react-router's BrowserRouter can resolve it (e.g. /house-21,
# /house-21/extract, /intake, /house-21/3d). Known API prefixes are
# rejected with 404 so genuine wrong calls still surface.
@app.get("/{rest:path}", response_class=FileResponse, include_in_schema=False)
def _spa_root_catchall(rest: str):
    head = rest.split("/", 1)[0]
    if head in {"datasets", "labels", "pdfs", "exports", "static", "assets",
                "docs", "redoc", "openapi.json"}:
        raise HTTPException(status_code=404, detail=f"{rest!r} not found")
    return root()
