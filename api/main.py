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
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .persistence import atomic_write_json, atomic_write_text, locked_path

BASE = Path(__file__).parent.parent
DATASET_DIR = BASE / "data" / "dataset"
PDFS_DIR = BASE / "data" / "pdfs"
INCOMING_DIR = PDFS_DIR / "incoming"
SUBMISSIONS_DIR = PDFS_DIR / "submissions"
UI_DIST = BASE / "ui" / "dist"

app = FastAPI(
    title="BIM Dataset API",
    description=(
        "REST API for the supervised-learning corpus of architectural drawings. "
        "PDF intake → scene extraction → annotation → export."
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
        except Exception:  # noqa: BLE001
            pass
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


@app.get("/datasets/{key}/{file}/plan", tags=["dataset"])
def get_scene_plan(key: str, file: str):
    """Read the per-scene Markdown plan used by the labeling agent."""
    _ensure_dataset_scene(key, file)
    from .scene_plans import read_plan
    return {"ok": True, "data": read_plan(DATASET_DIR, key, file)}


@app.post("/datasets/{key}/{file}/plan/template", tags=["dataset"])
def create_scene_plan_from_template_route(key: str, file: str, body: dict[str, Any] = Body(default={})):
    """Create a scene plan from the standard template. Rejects overwrite unless
    `overwrite:true` is passed."""
    _ensure_dataset_scene(key, file)
    from .scene_plans import create_plan_from_template
    try:
        data = create_plan_from_template(
            DATASET_DIR,
            key,
            file,
            scene_tag=str(body.get("scene_tag") or "nicht_klassifiziert"),
            level_or_orientation=body.get("level_or_orientation"),
            created_by=body.get("created_by"),
            overwrite=bool(body.get("overwrite", False)),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.put("/datasets/{key}/{file}/plan", tags=["dataset"])
def put_scene_plan(key: str, file: str, body: dict[str, Any] = Body(...)):
    """Create/update a scene plan. `expected_version` enables optimistic
    concurrency; `create_only:true` rejects overwrite."""
    _ensure_dataset_scene(key, file)
    markdown = body.get("markdown")
    if not isinstance(markdown, str):
        raise HTTPException(status_code=400, detail="markdown must be a string")
    from .scene_plans import write_plan
    try:
        data = write_plan(
            DATASET_DIR,
            key,
            file,
            markdown,
            expected_version=body.get("expected_version"),
            create_only=bool(body.get("create_only", False)),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/plan/log", tags=["dataset"])
def append_scene_plan_log_route(key: str, file: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    from .scene_plans import append_log
    try:
        data = append_log(
            DATASET_DIR,
            key,
            file,
            mode=str(body.get("mode") or ""),
            evidence=str(body.get("evidence") or ""),
            decision=str(body.get("decision") or ""),
            result=str(body.get("result") or ""),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.patch("/datasets/{key}/{file}/plan/tasks/{task_id}", tags=["dataset"])
def patch_scene_plan_task_route(key: str, file: str, task_id: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    from .scene_plans import set_task_status
    try:
        data = set_task_status(
            DATASET_DIR,
            key,
            file,
            task_id=task_id,
            status=str(body.get("status") or ""),
            note=body.get("note"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


# ── per-scene structured plan state ──────────────────────────────────────

@app.get("/datasets/{key}/{file}/plan-state", tags=["dataset"])
def get_scene_plan_state_route(key: str, file: str):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import read_plan_state
    return {"ok": True, "data": read_plan_state(DATASET_DIR, key, file)}


@app.get("/datasets/{key}/{file}/plan-state/status", tags=["dataset"])
def get_scene_plan_status_route(key: str, file: str):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import plan_status
    return {"ok": True, "data": plan_status(DATASET_DIR, key, file)}


@app.post("/datasets/{key}/{file}/plan-state/template", tags=["dataset"])
def create_scene_plan_state_from_template_route(key: str, file: str, body: dict[str, Any] = Body(default={})):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import create_plan_state_from_template
    try:
        data = create_plan_state_from_template(
            DATASET_DIR,
            key,
            file,
            scene_tag=str(body.get("scene_tag") or "nicht_klassifiziert"),
            level_or_orientation=body.get("level_or_orientation"),
            created_by=body.get("created_by"),
            overwrite=bool(body.get("overwrite", False)),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.put("/datasets/{key}/{file}/plan-state", tags=["dataset"])
def put_scene_plan_state_route(key: str, file: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    state = body.get("state")
    if not isinstance(state, dict):
        raise HTTPException(status_code=400, detail="state must be an object")
    state["key"] = key
    state["file"] = file
    from .scene_plan_state import write_plan_state
    try:
        data = write_plan_state(
            DATASET_DIR,
            state,
            expected_version=body.get("expected_version"),
            sync_markdown=bool(body.get("sync_markdown", True)),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/plan-state/evidence", tags=["dataset"])
def add_scene_plan_evidence_route(key: str, file: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import add_evidence
    try:
        data = add_evidence(
            DATASET_DIR,
            key,
            file,
            body,
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/plan-state/defects", tags=["dataset"])
def upsert_scene_plan_defect_route(key: str, file: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import upsert_defect
    try:
        data = upsert_defect(
            DATASET_DIR,
            key,
            file,
            body,
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.patch("/datasets/{key}/{file}/plan-state/defects/{defect_id}", tags=["dataset"])
def update_scene_plan_defect_route(key: str, file: str, defect_id: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import update_defect
    try:
        data = update_defect(
            DATASET_DIR,
            key,
            file,
            defect_id,
            body,
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.patch("/datasets/{key}/{file}/plan-state/tasks/{task_id}", tags=["dataset"])
def set_scene_plan_task_state_route(key: str, file: str, task_id: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import set_task_state
    try:
        data = set_task_state(
            DATASET_DIR,
            key,
            file,
            task_id,
            str(body.get("status") or ""),
            evidence_ids=body.get("evidence_ids"),
            blocked_by=body.get("blocked_by"),
            gate_updates=body.get("gate_updates"),
            note=body.get("note"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


def _compute_plan_state_gate_inputs(key: str, file: str, body: dict[str, Any]) -> dict[str, Any]:
    labels_doc = get_labels("dataset", key, file)
    score_walls_result = body.get("score_walls")
    if score_walls_result is None and bool(body.get("run_score_walls", True)):
        img_path = _scene_image_path("dataset", key, file)
        walls = []
        for lab in (labels_doc.get("labels") or []):
            if lab.get("type") != "wall":
                continue
            g = lab.get("geometry") or {}
            s, e = g.get("start"), g.get("end")
            if s and e:
                walls.append(((float(s[0]), float(s[1])), (float(e[0]), float(e[1]))))
        from PIL import Image as PILImage
        from .wall_score import score_walls
        min_wall_px = int(body.get("min_wall_px", 16))
        tol_px = int(body.get("tol_px", 18))
        close_px = int(body.get("close_px", 82))
        thin_aware = bool(body.get("thin_aware", False))
        with PILImage.open(img_path) as src:
            score_walls_result = score_walls(
                src.convert("RGB"),
                walls,
                min_wall_px=min_wall_px,
                tol_px=tol_px,
                close_px=close_px,
                thin_aware=thin_aware,
            )
        score_walls_result["n_walls"] = len(walls)
        score_walls_result["profile"] = body.get("score_profile") or (
            "faint_scan_thin_aware" if thin_aware
            else "final_scene" if (min_wall_px, tol_px, close_px) == (16, 18, 82)
            else "local_defect_tight"
        )
        score_walls_result["profile_params"] = {
            "min_wall_px": min_wall_px,
            "tol_px": tol_px,
            "close_px": close_px,
            "thin_aware": thin_aware,
        }
    score_measurements_result = body.get("score_measurements")
    if score_measurements_result is None and bool(body.get("run_score_measurements", True)):
        walls, dims = [], []
        for lab in (labels_doc.get("labels") or []):
            g = lab.get("geometry") or {}
            s, e = g.get("start"), g.get("end")
            if not s or not e:
                continue
            if lab.get("type") == "wall":
                walls.append({"start": s, "end": e})
            elif lab.get("type") == "dimensioned_distance":
                attrs = lab.get("attributes") or {}
                dims.append({"start": s, "end": e, "value_mm": attrs.get("value_mm")})
        from .measure_check import score_measurements_from_labels
        score_measurements_result = score_measurements_from_labels(
            walls,
            dims,
            tol_px=float(body.get("measurement_tol_px", 8)),
            axis_tol_px=float(body.get("axis_tol_px", 14)),
        )
    topology_result = body.get("topology_qa")
    if topology_result is None and bool(body.get("run_topology_qa", True)):
        from .wall_topology import wall_topology_qa
        topology_result = wall_topology_qa(labels_doc.get("labels") or [])
    continuity_result = body.get("continuity_check")
    if continuity_result is None and bool(body.get("run_continuity_check", True)):
        from .wall_topology import wall_continuity_check
        continuity_result = wall_continuity_check(labels_doc.get("labels") or [])
    return {
        "labels_doc": labels_doc,
        "score_walls_result": score_walls_result,
        "score_measurements_result": score_measurements_result,
        "topology_result": topology_result,
        "continuity_result": continuity_result,
    }


@app.post("/datasets/{key}/{file}/plan-state/evaluate-gates", tags=["dataset"])
def evaluate_scene_plan_gates_route(key: str, file: str, body: dict[str, Any] = Body(default={})):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import evaluate_gates
    try:
        inputs = _compute_plan_state_gate_inputs(key, file, body)
        data = evaluate_gates(
            DATASET_DIR,
            key,
            file,
            labels_doc=inputs["labels_doc"],
            score_walls_result=inputs["score_walls_result"],
            score_measurements_result=inputs["score_measurements_result"],
            topology_result=inputs["topology_result"],
            continuity_result=inputs["continuity_result"],
            visual_evidence=bool(body.get("visual_evidence", False)),
            quality_profile=body.get("quality_profile"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.get("/datasets/{key}/{file}/plan-state/next-actions", tags=["dataset"])
def get_scene_plan_next_actions_route(key: str, file: str, limit: int = 3):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import next_actions_from_state, read_plan_state
    data = read_plan_state(DATASET_DIR, key, file)
    state = data.get("state")
    return {"ok": True, "data": {"exists": data["exists"], "actions": next_actions_from_state(state, limit=limit) if state else []}}


@app.get("/datasets/{key}/{file}/plan-state/next-action", tags=["dataset"])
def get_scene_plan_next_action_route(key: str, file: str):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import next_action
    return {"ok": True, "data": next_action(DATASET_DIR, key, file)}


@app.post("/datasets/{key}/{file}/plan-state/actions/{action_id}/start", tags=["dataset"])
def start_scene_plan_action_route(key: str, file: str, action_id: str, body: dict[str, Any] = Body(default={})):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import start_action
    try:
        data = start_action(
            DATASET_DIR,
            key,
            file,
            action_id,
            agent_id=body.get("agent_id"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/plan-state/actions/{action_id}/attempts", tags=["dataset"])
def record_scene_plan_attempt_route(key: str, file: str, action_id: str, body: dict[str, Any] = Body(default={})):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import record_attempt
    try:
        data = record_attempt(
            DATASET_DIR,
            key,
            file,
            action_id,
            body,
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/plan-state/actions/{action_id}/finish", tags=["dataset"])
def finish_scene_plan_action_route(key: str, file: str, action_id: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import finish_action
    try:
        data = finish_action(
            DATASET_DIR,
            key,
            file,
            action_id,
            outcome=str(body.get("outcome") or ""),
            attempt_id=body.get("attempt_id"),
            evidence_ids=body.get("evidence_ids"),
            reason=body.get("reason"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/plan-state/tasks/{task_id}/reopen", tags=["dataset"])
def reopen_scene_plan_task_route(key: str, file: str, task_id: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import reopen_task
    try:
        data = reopen_task(
            DATASET_DIR,
            key,
            file,
            task_id,
            reason=str(body.get("reason") or ""),
            evidence_ids=body.get("evidence_ids"),
            invalidate_dependents=bool(body.get("invalidate_dependents", True)),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/plan-state/defects/{defect_id}/classify", tags=["dataset"])
def classify_scene_plan_defect_route(key: str, file: str, defect_id: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import classify_defect
    try:
        data = classify_defect(
            DATASET_DIR,
            key,
            file,
            defect_id,
            str(body.get("classification") or ""),
            evidence_ids=body.get("evidence_ids"),
            note=body.get("note"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/plan-state/evaluate-terminality", tags=["dataset"])
def evaluate_scene_plan_terminality_route(key: str, file: str):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import evaluate_terminality
    return {"ok": True, "data": evaluate_terminality(DATASET_DIR, key, file)}


@app.get("/datasets/{key}/{file}/plan-state/repair-candidates", tags=["dataset"])
def get_scene_repair_candidates_route(key: str, file: str, limit: int = 20):
    _ensure_dataset_scene(key, file)
    labels_doc = get_labels("dataset", key, file)
    from .topology_repair import repair_candidate_report
    from .scene_plan_state import read_plan_state
    try:
        plan = read_plan_state(DATASET_DIR, key, file)
        data = repair_candidate_report(labels_doc, limit=limit, plan_state=plan.get("state"))
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


def _find_repair_candidate(labels_doc: dict[str, Any], candidate_id: str, key: str | None = None, file: str | None = None) -> dict[str, Any]:
    from .topology_repair import repair_candidate_report
    plan_state = None
    if key and file:
        from .scene_plan_state import read_plan_state
        plan_state = (read_plan_state(DATASET_DIR, key, file).get("state") or None)
    report = repair_candidate_report(labels_doc, limit=200, plan_state=plan_state)
    for cluster in report.get("clusters") or []:
        for cand in cluster.get("candidates") or []:
            if cand.get("candidate_id") == candidate_id:
                return cand
    raise KeyError(f"repair candidate {candidate_id!r} not found")


@app.post("/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/apply", tags=["dataset"])
def apply_repair_candidate_route(key: str, file: str, candidate_id: str, body: dict[str, Any] = Body(default={})):
    _ensure_dataset_scene(key, file)
    labels_doc = get_labels("dataset", key, file)
    try:
        candidate = _find_repair_candidate(labels_doc, candidate_id, key, file)
        if body.get("expected_candidate_op") and body.get("expected_candidate_op") != candidate.get("op"):
            raise ValueError("candidate op changed; refresh repair candidates")
        from .topology_repair import apply_candidate_to_labels, simulate_candidate
        from .scene_plan_state import PlanStateConflictError, read_plan_state, record_repair_candidate_decision
        if body.get("expected_version"):
            current_plan = read_plan_state(DATASET_DIR, key, file)
            if current_plan.get("exists") and current_plan.get("version") != body.get("expected_version"):
                raise PlanStateConflictError("plan state version conflict")
        simulation = simulate_candidate(labels_doc, candidate)
        new_doc = apply_candidate_to_labels(labels_doc, candidate)
        persisted = False
        if candidate.get("op") != "no_edit_classification":
            put_labels("dataset", key, file, new_doc)
            persisted = True
        decision = record_repair_candidate_decision(
            DATASET_DIR,
            key,
            file,
            candidate,
            "accepted_applied" if persisted else str(body.get("outcome") or "accepted_uncertain"),
            evidence_ids=body.get("evidence_ids"),
            note=body.get("note"),
            simulation=simulation,
            expected_version=body.get("expected_version"),
        )
        data = {
            "candidate_id": candidate_id,
            "candidate": candidate,
            "simulation": simulation,
            "persisted": persisted,
            "labels_changed": persisted,
            "decision": ((decision.get("state") or {}).get("current_state") or {}).get("repair_candidate_decisions", {}),
        }
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/decision", tags=["dataset"])
def decide_repair_candidate_route(key: str, file: str, candidate_id: str, body: dict[str, Any] = Body(...)):
    _ensure_dataset_scene(key, file)
    labels_doc = get_labels("dataset", key, file)
    try:
        candidate = _find_repair_candidate(labels_doc, candidate_id, key, file)
        if body.get("expected_candidate_op") and body.get("expected_candidate_op") != candidate.get("op"):
            raise ValueError("candidate op changed; refresh repair candidates")
        outcome = str(body.get("outcome") or "")
        from .topology_repair import simulate_candidate
        from .scene_plan_state import record_repair_candidate_decision
        simulation = simulate_candidate(labels_doc, candidate)
        data = record_repair_candidate_decision(
            DATASET_DIR,
            key,
            file,
            candidate,
            outcome,
            evidence_ids=body.get("evidence_ids"),
            note=body.get("note"),
            simulation=simulation,
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.get("/datasets/{key}/{file}/plan-state/quality-report", tags=["dataset"])
def get_scene_plan_quality_report_route(key: str, file: str):
    _ensure_dataset_scene(key, file)
    labels_doc = get_labels("dataset", key, file)
    try:
        from .scene_plan_state import read_plan_state
        from .topology_repair import quality_report, repair_candidate_report
        plan = read_plan_state(DATASET_DIR, key, file)
        state = plan.get("state") or {}
        candidates = repair_candidate_report(labels_doc, limit=200, plan_state=state)
        data = quality_report(state, candidates)
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.get("/datasets/{key}/{file}/plan-state/topology-snapshot", tags=["dataset"])
def get_scene_plan_topology_snapshot_route(key: str, file: str):
    _ensure_dataset_scene(key, file)
    labels_doc = get_labels("dataset", key, file)
    try:
        from .scene_plan_state import read_plan_state
        from .topology_repair import repair_candidate_report, topology_regression_snapshot
        plan = read_plan_state(DATASET_DIR, key, file)
        state = plan.get("state") or {}
        candidates = repair_candidate_report(labels_doc, limit=200, plan_state=state)
        data = topology_regression_snapshot(state, candidates)
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.get("/datasets/{key}/{file}/plan-state/repair-candidates/{candidate_id}/overlay", tags=["dataset"])
def render_repair_candidate_overlay_route(
    key: str,
    file: str,
    candidate_id: str,
    max_dim: int = 1600,
    clean: bool = True,
    style: str | None = "ink_compare",
):
    _ensure_dataset_scene(key, file)
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    labels_doc = get_labels("dataset", key, file)
    try:
        candidate = _find_repair_candidate(labels_doc, candidate_id, key, file)
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    region = candidate.get("region")
    parsed_region = None
    if isinstance(region, list) and len(region) >= 4:
        x0, y0, x1, y1 = [int(round(float(v))) for v in region[:4]]
        pad = 40
        parsed_region = (max(0, x0 - pad), max(0, y0 - pad), max(x1 + pad, x0 + pad), max(y1 + pad, y0 + pad))
    from PIL import Image as PILImage, ImageDraw
    from .label_render import render_grid_with_labels
    with PILImage.open(img_path) as src:
        overlay = render_grid_with_labels(
            src.convert("RGB"),
            labels_doc.get("labels") or [],
            tiers=("finer",),
            region=parsed_region,
            max_dim=max_dim,
            clean=bool(clean),
            style=_parse_label_render_style(style),
            background_opacity=0.2,
            background_opacity_explicit=True,
            contrast="high",
            px_per_mm=_scene_px_per_mm(key, file),
            show_relations="required",
        )
    draw = ImageDraw.Draw(overlay, "RGBA")
    if parsed_region is not None:
        rx0, ry0, rx1, ry1 = parsed_region
    else:
        rx0, ry0 = 0, 0
        with PILImage.open(img_path) as src:
            rx1, ry1 = src.size
    scale = min(max_dim / max(1, rx1 - rx0), max_dim / max(1, ry1 - ry0), 1.0)

    def to_out(pt: Any) -> tuple[float, float] | None:
        if not (isinstance(pt, list) and len(pt) == 2):
            return None
        return ((float(pt[0]) - rx0) * scale, (float(pt[1]) - ry0) * scale)

    for edit in candidate.get("edits") or []:
        if edit.get("to"):
            pt = to_out(edit.get("to"))
            if pt:
                r = 7
                draw.ellipse((pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r), outline=(236, 72, 153, 255), width=3)
        if edit.get("wall"):
            a = to_out(edit["wall"][0])
            b = to_out(edit["wall"][1])
            if a and b:
                draw.line((a[0], a[1], b[0], b[1]), fill=(236, 72, 153, 255), width=5)
    labels_by_id = {str(l.get("id")): l for l in labels_doc.get("labels") or [] if isinstance(l, dict)}
    for edit in candidate.get("edits") or []:
        lab = labels_by_id.get(str(edit.get("label_id") or ""))
        if lab and edit.get("to"):
            g = lab.get("geometry") or {}
            other_key = "start" if edit.get("endpoint") == "end" else "end"
            a = to_out(g.get(other_key))
            b = to_out(edit.get("to"))
            if a and b:
                draw.line((a[0], a[1], b[0], b[1]), fill=(6, 182, 212, 255), width=4)
    import io
    buf = io.BytesIO()
    overlay.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/datasets/{key}/{file}/plan-state/render-markdown", tags=["dataset"])
def render_scene_plan_markdown_route(key: str, file: str, body: dict[str, Any] = Body(default={})):
    _ensure_dataset_scene(key, file)
    from .scene_plan_state import read_plan_state, render_markdown, write_plan_state
    try:
        data = read_plan_state(DATASET_DIR, key, file)
        if not data["exists"]:
            raise FileNotFoundError("plan state does not exist")
        markdown = render_markdown(data["state"])
        if bool(body.get("sync", True)):
            data = write_plan_state(DATASET_DIR, data["state"], expected_version=body.get("expected_version"), sync_markdown=True)
            markdown = data["markdown"]
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": {"markdown": markdown, "path": data.get("markdown_path"), "version": data.get("version")}}


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


def _as_point(pt: Any) -> tuple[float, float] | None:
    if (
        isinstance(pt, list)
        and len(pt) == 2
        and isinstance(pt[0], (int, float))
        and isinstance(pt[1], (int, float))
    ):
        return (float(pt[0]), float(pt[1]))
    return None


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


def _wall_segment(label: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    geom = label.get("geometry") or {}
    start = _as_point(geom.get("start"))
    end = _as_point(geom.get("end"))
    if start is None or end is None:
        return None
    return (start, end)


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


# ── Customer submission review + promote (developer surface) ──────────────
# Submissions land in data/pdfs/submissions/<id>/ via either:
#   * the hardened standalone form_api/ process (production), or
#   * POST /submit on THIS dev API (single-user-localhost convenience).
# The developer reviews them here and promotes the clean ones into
# data/pdfs/incoming/house-NN/ for the existing R2 scene extractor.


@app.post("/submit", tags=["pdfs"], status_code=201)
async def submit_localhost(
    files: list[UploadFile] = File(..., description="Drawings to submit"),
    contact_email: str | None = None,
    contact_name: str | None = None,
    license: str = "permission-granted",
    license_notes: str | None = None,
    training_use: bool = True,
    user_notes: str | None = None,
):
    """Local-only customer-form endpoint. Mirrors form_api/main.py's
    /submit but without the API-key + rate limit since this whole API
    is single-user-localhost. Production deployments should use the
    standalone form_api process behind real auth — this route is
    purely a developer convenience so the form lives on :2500 too.

    Same output shape as the standalone endpoint: per-page decision +
    reasons so the SPA can re-prompt on a borderline submission.
    """
    import datetime as _dt2
    import secrets

    from ingestion.bundle import IngestProvenance, ingest_to_bundle
    from ingestion.config import load_profile
    from ingestion.normalize import sniff_kind

    if not files:
        raise HTTPException(status_code=400, detail="no files provided")
    if not training_use:
        raise HTTPException(status_code=400, detail="training_use consent is required")
    if license not in {"cc0", "cc-by", "cc-by-sa", "permission-granted", "other"}:
        raise HTTPException(status_code=400, detail=f"unknown license {license!r}")

    accepted = {"pdf", "jpeg", "png", "tiff", "heif"}
    submission_id = secrets.token_urlsafe(16)
    SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    staging = SUBMISSIONS_DIR / submission_id / "_staging"
    staging.mkdir(parents=True, exist_ok=True)

    staged: list[Path] = []
    for upload in files:
        raw = await upload.read()
        kind = sniff_kind(raw)
        if kind not in accepted:
            import shutil as _sh
            _sh.rmtree(staging.parent, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"{upload.filename!r}: unsupported file type",
            )
        safe_name = Path(upload.filename or f"upload-{len(staged)}").name
        out_path = staging / safe_name
        if out_path.exists():
            out_path = staging / f"{len(staged)}-{safe_name}"
        out_path.write_bytes(raw)
        staged.append(out_path)

    provenance = IngestProvenance(
        source_type="form",
        submitter={
            "submission_id": submission_id,
            "contact_email": contact_email,
            "contact_name": contact_name,
            "client_ip_hash": None,
            "user_agent": None,
        },
        consent={
            "training_use": training_use,
            "license": license,
            "license_notes": license_notes or "",
            "consented_at": _dt2.datetime.now(_dt2.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        user_notes=user_notes or "",
    )
    result = ingest_to_bundle(
        input_files=staged,
        bundle_root=SUBMISSIONS_DIR,
        bundle_key=submission_id,
        provenance=provenance,
        cfg=load_profile("strict-form"),
    )
    import shutil as _sh
    _sh.rmtree(staging, ignore_errors=True)

    return {
        "submission_id": submission_id,
        "page_count": result.manifest["page_count"],
        "pages": [
            {
                "page": p["page"],
                "decision": p["decision"],
                "reasons": p["decision_reasons"],
                "human_qa_required": p["human_qa_required"],
            }
            for p in result.manifest["pages"]
        ],
        "pass": result.pages_pass,
        "warn": result.pages_warn,
        "reject": result.pages_reject,
        "promoted": False,
    }



@app.get("/pdfs/submissions", tags=["pdfs"])
def list_submissions():
    """Every quarantined customer submission, newest-first. Each entry
    is the on-disk manifest augmented with consolidated_url + a derived
    `summary` describing pass/warn/reject counts so the SPA can render
    a queue at a glance."""
    if not SUBMISSIONS_DIR.exists():
        return []
    out = []
    for d in sorted(SUBMISSIONS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            m = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            continue
        m["submission_id"] = d.name
        if m.get("consolidated_pdf"):
            m["consolidated_url"] = f"/static/pdfs/submissions/{d.name}/{m['consolidated_pdf']}"
        pages = m.get("pages") or []
        m["summary"] = {
            "pass": sum(1 for p in pages if p.get("decision") == "pass"),
            "warn": sum(1 for p in pages if p.get("decision") == "warn"),
            "reject": sum(1 for p in pages if p.get("decision") == "reject"),
            "title_blocks_suspected": sum(
                1 for p in pages if (p.get("pii_flag") or {}).get("title_block_suspected")
            ),
        }
        out.append(m)
    return out


@app.get("/pdfs/submissions/{submission_id}", tags=["pdfs"])
def get_submission(submission_id: str):
    _safe_submission_id(submission_id)
    mp = SUBMISSIONS_DIR / submission_id / "manifest.json"
    if not mp.exists():
        raise HTTPException(status_code=404, detail=f"No submission {submission_id!r}")
    m = json.loads(mp.read_text())
    m["submission_id"] = submission_id
    if m.get("consolidated_pdf"):
        m["consolidated_url"] = (
            f"/static/pdfs/submissions/{submission_id}/{m['consolidated_pdf']}"
        )
    return m


@app.post("/pdfs/submissions/{submission_id}/promote", tags=["pdfs"], status_code=201)
def promote_submission(
    submission_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
):
    """Promote a quarantined submission into the corpus.

    Body (all optional):
      house_key:        target key — defaults to the next free house-NN
      redact_title_block: if true, re-runs ingestion with the redaction
                          hook applied (only meaningful when the
                          submission was flagged)
      user_notes:       supersedes the submission's notes on the new bundle

    The submission directory is NOT deleted; we copy the rectified PDF +
    originals into the new incoming bundle and stamp the submission
    manifest with `promoted_to: <house_key>`. Round-trip stays auditable.
    """
    _safe_submission_id(submission_id)
    src = SUBMISSIONS_DIR / submission_id
    src_manifest_path = src / "manifest.json"
    if not src_manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"No submission {submission_id!r}")
    submission_manifest = json.loads(src_manifest_path.read_text())

    if submission_manifest.get("promoted_to"):
        raise HTTPException(
            status_code=409,
            detail=f"already promoted to {submission_manifest['promoted_to']!r}",
        )

    house_key = payload.get("house_key") or _next_free_house_key()
    _safe_key(house_key)

    target = INCOMING_DIR / house_key
    if target.exists():
        raise HTTPException(
            status_code=409,
            detail=f"target bundle {house_key!r} already exists",
        )

    redact = bool(payload.get("redact_title_block"))
    if redact:
        # Re-run ingestion on the originals so the redaction is applied
        # to the rectified PDF — never edit the quarantined artifact in
        # place; we want a clean provenance trail.
        from ingestion.bundle import IngestProvenance, ingest_to_bundle
        from ingestion.config import load_profile

        source_dir = src / "source"
        originals = sorted(source_dir.iterdir()) if source_dir.exists() else []
        if not originals:
            raise HTTPException(
                status_code=409,
                detail="cannot redact: submission has no preserved source originals",
            )
        provenance = IngestProvenance(
            source_type="form",
            submitter=submission_manifest.get("submitter"),
            consent=submission_manifest.get("consent"),
            user_notes=payload.get("user_notes")
                       or submission_manifest.get("user_notes", ""),
        )
        cfg = load_profile()  # re-render uses the dev profile by default
        result = ingest_to_bundle(
            input_files=originals,
            bundle_root=INCOMING_DIR,
            bundle_key=house_key,
            provenance=provenance,
            cfg=cfg,
            redact_title_blocks=True,
        )
        new_manifest = result.manifest
    else:
        # Cheap path: copy the rectified PDF + source/ verbatim, rewrite
        # the manifest's house_key + state.
        import shutil
        target.mkdir(parents=True, exist_ok=True)
        consolidated_name = submission_manifest.get("consolidated_pdf")
        if consolidated_name:
            shutil.copyfile(src / consolidated_name, target / consolidated_name)
        if (src / "source").exists():
            shutil.copytree(src / "source", target / "source")
        new_manifest = dict(submission_manifest)
        new_manifest["house_key"] = house_key
        new_manifest["state"] = "partial"
        new_manifest["extracted_scenes"] = []
        if payload.get("user_notes"):
            new_manifest["user_notes"] = payload["user_notes"]
        atomic_write_json(target / "manifest.json", new_manifest)

    # Stamp the source submission so the audit trail is durable.
    submission_manifest["promoted_to"] = house_key
    submission_manifest["promoted_at"] = _now_iso()
    atomic_write_json(src_manifest_path, submission_manifest)

    return {
        "promoted_to": house_key,
        "consolidated_url": (
            f"/static/pdfs/incoming/{house_key}/{new_manifest.get('consolidated_pdf')}"
            if new_manifest.get("consolidated_pdf") else None
        ),
        "redacted": redact,
    }


@app.delete("/pdfs/submissions/{submission_id}", tags=["pdfs"], status_code=204)
def delete_submission(submission_id: str):
    """Drop a quarantined submission outright. Use for clear-spam / GDPR
    erasure. Refuses if the submission has already been promoted —
    delete the resulting incoming bundle separately if needed."""
    _safe_submission_id(submission_id)
    src = SUBMISSIONS_DIR / submission_id
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"No submission {submission_id!r}")
    manifest_path = src / "manifest.json"
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text())
            if m.get("promoted_to"):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "submission has already been promoted to "
                        f"{m['promoted_to']!r}; delete that bundle separately"
                    ),
                )
        except json.JSONDecodeError:
            pass
    import shutil
    shutil.rmtree(src)
    return None


def _safe_submission_id(submission_id: str) -> None:
    if not submission_id or "/" in submission_id or ".." in submission_id or "\\" in submission_id:
        raise HTTPException(status_code=400, detail=f"bad submission_id {submission_id!r}")


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


# ── R2 — PDF page render + scene extraction ───────────────────────────────

PDF_CACHE = BASE / "tmp" / "pdf-cache"


def _consolidated_path(key: str) -> Path:
    m = _read_manifest(key)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No intake bundle for {key!r}")
    name = m.get("consolidated_pdf")
    if not name:
        raise HTTPException(status_code=409, detail=f"{key} has no consolidated PDF yet")
    p = INCOMING_DIR / key / name
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Consolidated PDF missing for {key!r}")
    return p


@app.get("/pdfs/{key}/page/{n}", tags=["pdfs"])
def render_pdf_page(key: str, n: int, dpi: int = 300):
    """R2 — render PDF page `n` (1-indexed) at the given DPI as a JPEG.
    Cached on disk under tmp/pdf-cache/<key>/page-<n>-<dpi>.jpg keyed on
    the source PDF's mtime so edits invalidate stale crops.

    Quality-first: default 300 dpi (was 96) so the pre-extraction page view
    + grid coordinates are read off near-native resolution, not a coarse
    proxy. Cap stays 600 (>= the ~429 dpi native scan; higher only upscales)."""
    _safe_key(key)
    if dpi <= 0 or dpi > 600:
        raise HTTPException(status_code=400, detail="dpi must be in (0, 600]")
    pdf = _consolidated_path(key)
    pdf_mtime = pdf.stat().st_mtime_ns
    cache_root = PDF_CACHE / key
    cache_root.mkdir(parents=True, exist_ok=True)
    out = cache_root / f"page-{n}-{dpi}.jpg"
    sentinel = out.with_suffix(".mtime")
    if not out.exists() or not sentinel.exists() or sentinel.read_text() != str(pdf_mtime):
        import fitz
        with fitz.open(pdf) as doc:
            if n < 1 or n > doc.page_count:
                raise HTTPException(status_code=404, detail=f"page {n} out of range (1..{doc.page_count})")
            page = doc.load_page(n - 1)
            scale = dpi / 72.0
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            # Issue #24: PyMuPDF yields an all-white pixmap on AcroForm-
            # corrupt scans; recover the page via poppler before saving.
            if _pixmap_is_blank(pix):
                fb = _render_page_poppler(pdf, n, dpi)
                if fb is not None and not _image_is_blank(fb):
                    fb.save(str(out), format="JPEG", quality=95)
                    sentinel.write_text(str(pdf_mtime))
                    return FileResponse(str(out), media_type="image/jpeg")
            pix.pil_save(str(out), format="JPEG", quality=95)
        sentinel.write_text(str(pdf_mtime))
    return FileResponse(str(out), media_type="image/jpeg")


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


@app.get("/datasets/{key}/{file}/grid", tags=["pdfs"])
def render_scene_grid(
    key: str,
    file: str,
    region: str | None = None,
    tiers: str = "broad,finer",
    max_dim: int = 1600,
    enhance: str | None = None,
    format: str | None = None,
    style: str | None = None,
    target: str | None = None,
    target_line: str | None = None,
    background_opacity: float | None = None,
):
    """Agent vision aid: scene image + coordinate-anchored grid overlay.

    Query args:
      region   optional 'x0,y0,x1,y1' (source-pixel coords) — agent zoom
      tiers    comma list of {broad, finer, detail}; default broad+finer
      max_dim  cap on the longer side of the output PNG; default 1600
      enhance  contrast lift for faint scans (issue #2): none|auto|clahe|
               threshold. Default none. Changes pixel intensity only, so
               coordinates stay in the SOURCE-pixel frame.
      format   png|png8 (issue #3). Default png8: a 256-colour palette
               PNG, typically 2-4x smaller than RGBA at near-identical
               legibility, to cut the token cost of each read. Pass
               format=png for full-fidelity RGBA.
      background_opacity  optional fade of the source drawing against white
               in (0,1]. Defaults to 0.5; enhanced images raise to 0.85
               only when this parameter is omitted.

    Returns image/png; cached on disk under tmp/grid-cache/. The coordinate
    labels in the output reference SOURCE pixels, so the agent can take a
    reading from a zoomed crop and feed it back into upsert_label against
    the un-cropped scene without further translation.
    """
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    if not 100 <= max_dim <= 8000:
        raise HTTPException(status_code=400, detail="max_dim must be in [100, 8000]")
    parsed_tiers = _parse_tiers(tiers)
    parsed_region = _parse_region(region)
    parsed_enhance = _parse_enhance(enhance)
    parsed_format = _parse_format(format)
    parsed_style = _parse_grid_style(style)
    parsed_target = _parse_target(target)
    parsed_target_line = _parse_target_line(target_line)
    parsed_opacity, opacity_explicit = _parse_background_opacity(background_opacity)

    img_mtime = img_path.stat().st_mtime_ns
    cache_root = GRID_CACHE / "scene" / key
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_name = (
        f"{Path(file).stem}"
        f"-r{region or 'full'}"
        f"-t{'_'.join(parsed_tiers)}"
        f"-m{max_dim}"
        f"-e{parsed_enhance}"
        f"-s{parsed_style}"
        f"-g{target or 'none'}"
        f"-gl{parsed_target_line}"
        f"-o{parsed_opacity:g}x{int(opacity_explicit)}"
        f"-f{parsed_format}.png"
    )
    out = cache_root / cache_name
    sentinel = out.with_suffix(".mtime")
    if not out.exists() or not sentinel.exists() or sentinel.read_text() != str(img_mtime):
        from PIL import Image as PILImage
        from .grid_render import render_grid_overlay
        with PILImage.open(img_path) as src:
            _m = _load_dataset_manifest(key)
            _scene_dpi = next(
                (d.get("crop_from", {}).get("dpi")
                 for d in ((_m or {}).get("drawings") or [])
                 if d.get("file") == file),
                None,
            )
            overlay = render_grid_overlay(
                src,
                tiers=parsed_tiers,
                region=parsed_region,
                max_dim=max_dim,
                enhance=parsed_enhance,
                background_opacity=parsed_opacity,
                background_opacity_explicit=opacity_explicit,
                source_dpi=_scene_dpi,
                style=parsed_style,
                target=parsed_target,
                target_line=parsed_target_line,  # type: ignore[arg-type]
            )
        _save_grid_png(overlay, out, parsed_format)
        sentinel.write_text(str(img_mtime))
    return FileResponse(str(out), media_type="image/png")


# ── wall-corner detection (classic-CV positional prior) ────────────────────
# Hand-drawn floorplans force the vision-LLM agent to guess exact source
# pixels off a faint, downscaled scan, which misplaces wall endpoints. These
# routes run a deterministic morphological-open + contour-vertex pass over the
# THICK wall ink (thin annotation lines are erased by the open) and hand the
# agent candidate corner coordinates to SNAP endpoints to. Per project rules
# this is a positional prior / cross-check only — the agent stays the judge.

@app.get("/datasets/{key}/{file}/wall-corners", tags=["pdfs"])
def wall_corners(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 8,
    thresh: int | None = None,
    max_dim: int = 1600,
    format: str = "json",
):
    """Candidate wall-corner coords (full-image source px). JSON by default;
    `format=png` returns the grid overlay with each corner ringed + indexed."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")

    from PIL import Image as PILImage, ImageDraw
    from .corner_detect import detect_wall_corners

    parsed = _parse_region(region)
    with PILImage.open(img_path) as src:
        src = src.convert("RGB")
        corners = detect_wall_corners(
            src, region=parsed, min_wall_px=min_wall_px, thresh=thresh
        )
        params = {
            "region": list(parsed) if parsed else None,
            "min_wall_px": min_wall_px,
            "thresh": thresh,
        }

        if format == "png":
            from .grid_render import render_grid_overlay
            overlay = render_grid_overlay(
                src, region=parsed, tiers=("finer",), max_dim=max_dim
            ).convert("RGB")
            if parsed:
                ox, oy, x1, y1 = parsed
                base_w = x1 - ox
            else:
                ox, oy = 0, 0
                base_w = src.size[0]
            scale = overlay.size[0] / base_w if base_w else 1.0
            draw = ImageDraw.Draw(overlay)
            r = 7
            for i, (cx, cy) in enumerate(corners):
                sx = (cx - ox) * scale
                sy = (cy - oy) * scale
                draw.ellipse([sx - r, sy - r, sx + r, sy + r],
                             outline=(0, 255, 0), width=2)
                draw.ellipse([sx - 1, sy - 1, sx + 1, sy + 1], fill=(255, 0, 255))
                draw.text((sx + r + 1, sy - r - 1), str(i), fill=(0, 255, 0))
            import io as _io
            buf = _io.BytesIO()
            overlay.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")

    return {
        "ok": True,
        "data": {
            "corners": [[x, y] for (x, y) in corners],
            "count": len(corners),
            "params": params,
        },
    }


@app.get("/datasets/{key}/{file}/check-corner", tags=["pdfs"])
def check_corner_route(
    key: str,
    file: str,
    x: int,
    y: int,
    search_px: int = 40,
    min_wall_px: int = 8,
):
    """Nearest detected wall corner to (x,y) with a snap/move hint.
    dx>0 => true corner is RIGHT of (x,y); dy>0 => BELOW (y grows down)."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    from PIL import Image as PILImage
    from .corner_detect import check_corner
    with PILImage.open(img_path) as src:
        src = src.convert("RGB")
        result = check_corner(
            src, x, y, search_px=search_px, min_wall_px=min_wall_px
        )
    return {"ok": True, "data": result}


@app.get("/datasets/{key}/{file}/wall-outline", tags=["pdfs"])
def wall_outline(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 8,
    thresh: int | None = None,
    n_outlines: int = 2,
    epsilon_px: float = 8.0,
):
    """Ordered outer-boundary polygon(s) of the thick-wall ink (full-image
    source px). Each consecutive vertex pair is one wall segment; disjoint
    structures (main block vs. garage) return as separate polygons. Use a
    small min_wall_px (6-10) so faint outer walls survive the morphology."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    from PIL import Image as PILImage
    from .corner_detect import detect_wall_outline
    parsed = _parse_region(region)
    with PILImage.open(img_path) as src:
        src = src.convert("RGB")
        outlines = detect_wall_outline(
            src, region=parsed, min_wall_px=min_wall_px, thresh=thresh,
            n_outlines=n_outlines, epsilon_px=epsilon_px,
        )
    return {
        "ok": True,
        "data": {
            "outlines": outlines,
            "count": len(outlines),
            "params": {
                "region": list(parsed) if parsed else None,
                "min_wall_px": min_wall_px,
                "thresh": thresh,
                "n_outlines": n_outlines,
                "epsilon_px": epsilon_px,
            },
        },
    }


@app.get("/datasets/{key}/{file}/refine-wall", tags=["pdfs"])
def refine_wall(
    key: str,
    file: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    search_px: int = 22,
    n_samples: int = 25,
    thresh: int | None = None,
):
    """Sub-pixel refine a candidate wall segment to the measured ink BAND.

    Samples perpendicular profiles along (x0,y0)->(x1,y1), finds the dark
    band's centre in each slice, and TLS/PCA-fits a line through those centre
    points so the result follows the wall's TRUE tilt (handles non-axis-aligned
    scans + non-90 corners). Returns corrected endpoints, measured
    thickness_px, angle_deg, fit_line, and confidence (frac of slices that
    found a band). Pair with /check-corner for endpoints; use line_intersection
    of adjacent refined walls to make exact shared corners."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    from PIL import Image as PILImage
    from .wall_refine import refine_segment
    with PILImage.open(img_path) as src:
        src = src.convert("RGB")
        res = refine_segment(
            src, (x0, y0), (x1, y1),
            search_px=search_px, n_samples=n_samples, thresh=thresh,
        )
    return {"ok": True, "data": res}


@app.post("/datasets/{key}/{file}/wall-labels/anchored", tags=["pdfs"])
def upsert_wall_anchored_route(
    key: str,
    file: str,
    body: dict[str, Any] = Body(...),
):
    """Create/replace a floorplan wall only after measuring it against ink.

    The route treats the input as a draft centerline, refines it with the same
    CV primitive exposed by /refine-wall, scores local ink overlap, and only
    persists a readable wall when both confidence and overlap pass.
    """
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    candidate = body.get("candidate") or {}
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=400, detail="body.candidate object required")
    start = _as_point(candidate.get("start"))
    end = _as_point(candidate.get("end"))
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="candidate.start and candidate.end must be [x,y]")
    anchor = body.get("anchor") or {}
    if not isinstance(anchor, dict):
        anchor = {}
    search_px = int(anchor.get("search_px", 40))
    n_samples = int(anchor.get("n_samples", 31))
    min_confidence = float(anchor.get("min_confidence", 0.82))
    min_overlap = float(anchor.get("min_overlap", 0.6))
    tol_px = int(anchor.get("tol_px", 18))
    min_wall_px = int(anchor.get("min_wall_px", 8))
    close_px = int(anchor.get("close_px", 82))
    snap_corners = bool(anchor.get("snap_corners", False))
    status_if_unanchored = str(body.get("status_if_unanchored") or "reject")
    evidence_id = body.get("evidence_id")

    from PIL import Image as PILImage
    from .wall_refine import refine_segment
    from .corner_detect import check_corner

    with PILImage.open(img_path) as src_img:
        src = src_img.convert("RGB")
        refined = refine_segment(src, start, end, search_px=search_px, n_samples=n_samples)
        refined_start = _as_point(refined.get("start")) or start
        refined_end = _as_point(refined.get("end")) or end
        if snap_corners:
            snapped: list[tuple[float, float]] = []
            for pt in (refined_start, refined_end):
                corner = check_corner(src, int(round(pt[0])), int(round(pt[1])), search_px=max(18, search_px), min_wall_px=min_wall_px)
                if corner.get("found") and isinstance(corner.get("nearest"), list):
                    snapped.append((float(corner["nearest"][0]), float(corner["nearest"][1])))
                else:
                    snapped.append(pt)
            refined_start, refined_end = snapped[0], snapped[1]
        overlap = _wall_ink_overlap(
            src,
            refined_start,
            refined_end,
            min_wall_px=min_wall_px,
            tol_px=tol_px,
            thin_aware=True,
            close_px=close_px,
        )

    confidence = float(refined.get("confidence") or 0.0)
    ink_overlap = float(overlap["ink_overlap"])
    passes = confidence >= min_confidence and ink_overlap >= min_overlap
    status = "readable" if passes else "uncertain"
    persisted = passes or status_if_unanchored == "uncertain"
    if not passes and status_if_unanchored == "uncertain" and not evidence_id:
        raise HTTPException(
            status_code=400,
            detail="status_if_unanchored='uncertain' requires evidence_id; otherwise leave it non-persisted",
        )
    label_id = str(candidate.get("id") or body.get("label_id") or _wall_label_id())
    dx = ((refined_start[0] - start[0]) + (refined_end[0] - end[0])) / 2.0
    dy = ((refined_start[1] - start[1]) + (refined_end[1] - end[1])) / 2.0
    data: dict[str, Any] = {
        "label_id": label_id if persisted else None,
        "persisted": persisted,
        "anchoring_status": "ink_anchored" if passes else "failed",
        "original": {"start": [start[0], start[1]], "end": [end[0], end[1]]},
        "anchored": {"start": [refined_start[0], refined_start[1]], "end": [refined_end[0], refined_end[1]]},
        "confidence": round(confidence, 3),
        "ink_overlap": ink_overlap,
        "delta_px": [round(dx, 2), round(dy, 2)],
        "suggested_next_crop": overlap["region"],
        "score": overlap["score"],
    }
    if not persisted:
        data["reason"] = (
            f"confidence {confidence:.2f} / overlap {ink_overlap:.2f} below "
            f"thresholds {min_confidence:.2f} / {min_overlap:.2f}"
        )
        return {"ok": True, "data": data}

    doc = get_labels("dataset", key, file)
    attrs = {
        **(candidate.get("attributes") or {}),
        "quality_status": "ink_anchored" if passes else "uncertain",
        "anchoring": {
            "method": "refine_wall",
            "confidence": round(confidence, 3),
            "ink_overlap": ink_overlap,
            "original_start": [start[0], start[1]],
            "original_end": [end[0], end[1]],
            "delta_px": [round(dx, 2), round(dy, 2)],
            "evidence_id": evidence_id,
        },
    }
    thickness_mm = candidate.get("thickness_mm", (candidate.get("attributes") or {}).get("thickness_mm"))
    if thickness_mm is not None:
        attrs["thickness_mm"] = thickness_mm
    label = {
        "id": label_id,
        "type": "wall",
        "status": status,
        "geometry": {"start": [refined_start[0], refined_start[1]], "end": [refined_end[0], refined_end[1]]},
        "attributes": attrs,
    }
    labels = doc.setdefault("labels", [])
    idx = next((i for i, lab in enumerate(labels) if lab.get("id") == label_id), None)
    if idx is None:
        labels.append(label)
        action = "created"
    else:
        labels[idx] = label
        action = "replaced"
    put_labels("dataset", key, file, doc)
    data["label_id"] = label_id
    data["action"] = action
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/wall-labels/anchoring-check", tags=["pdfs"])
def wall_label_anchoring_check_route(
    key: str,
    file: str,
    body: dict[str, Any] = Body(...),
):
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    label = body.get("label") if isinstance(body.get("label"), dict) else body
    if not isinstance(label, dict):
        raise HTTPException(status_code=400, detail="label object required")
    seg = _wall_segment(label)
    if seg is None:
        raise HTTPException(status_code=400, detail="wall label geometry.start/end required")
    from PIL import Image as PILImage
    with PILImage.open(img_path) as src_img:
        src = src_img.convert("RGB")
        data = _wall_ink_overlap(
            src,
            seg[0],
            seg[1],
            min_wall_px=int(body.get("min_wall_px", 8)),
            tol_px=int(body.get("tol_px", 18)),
            thin_aware=bool(body.get("thin_aware", True)),
            close_px=int(body.get("close_px", 82)),
        )
    data.update({
        "anchoring_status": data["status"],
        "recommended_tool": "upsert_wall_anchored",
        "must_verify_before_downstream": data["status"] == "off_ink",
    })
    return {"ok": True, "data": data}


@app.get("/datasets/{key}/{file}/score-walls", tags=["pdfs"])
def score_walls_route(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 8,
    tol_px: int = 9,
    thresh: int | None = None,
    thin_aware: bool = False,
    close_px: int = 0,
):
    """Objective QA of the CURRENTLY SAVED wall labels vs the ink.

    Returns precision (labels on ink), recall (ink covered by labels), f1,
    plus MISSING_REGIONS (bboxes of ink walls no label covers — "add a wall
    here") and OFF_INK_SEGMENTS (labels that don't sit on ink — "this one's
    wrong"). This is the agent's self-QA signal for human-free convergence:
    recall < 1 => walls missing; precision < 1 => labels misplaced."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    doc = get_labels("dataset", key, file)
    walls = []
    for lab in (doc.get("labels") or []):
        if lab.get("type") != "wall":
            continue
        g = lab.get("geometry") or {}
        s, e = g.get("start"), g.get("end")
        if s and e:
            walls.append(((float(s[0]), float(s[1])), (float(e[0]), float(e[1]))))
    from PIL import Image as PILImage
    from .wall_score import score_walls
    parsed = _parse_region(region)
    with PILImage.open(img_path) as src:
        src = src.convert("RGB")
        res = score_walls(src, walls, region=parsed,
                          min_wall_px=min_wall_px, tol_px=tol_px, thresh=thresh,
                          thin_aware=thin_aware, close_px=close_px)
    res["n_walls"] = len(walls)
    return {"ok": True, "data": res}


@app.get("/datasets/{key}/{file}/score-measurements", tags=["pdfs"])
def score_measurements_route(
    key: str,
    file: str,
    tol_px: int = 8,
    axis_tol_px: int = 14,
):
    """Metric-correctness QA of the saved geometry against the saved
    dimension chains — the oracle layer over score-walls.

    score-walls checks only INK coverage, so it accepts a wall on the wrong
    line if that line has ink. This checks PLACEMENT: each dimension segment's
    endpoints are ticks that must be the projection of a wall face; a tick
    with no wall face within tol is a misplaced/missing wall (returned in
    `unmatched_ticks` with the nearest wall + delta, so 'move wall to the
    tick' is mechanical). Also reports per-chain collinearity + part-sum so
    the agent can compare to the printed overall. Pure core in
    `measure_check.score_measurements_from_labels`."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    doc = get_labels("dataset", key, file)
    walls, dims = [], []
    for lab in (doc.get("labels") or []):
        g = lab.get("geometry") or {}
        s, e = g.get("start"), g.get("end")
        if not s or not e:
            continue
        t = lab.get("type")
        if t == "wall":
            walls.append({"start": s, "end": e})
        elif t == "dimensioned_distance":
            attrs = lab.get("attributes") or {}
            dims.append({"start": s, "end": e, "value_mm": attrs.get("value_mm")})
    from .measure_check import score_measurements_from_labels
    res = score_measurements_from_labels(
        walls, dims, tol_px=tol_px, axis_tol_px=axis_tol_px)
    return {"ok": True, "data": res}


@app.get("/datasets/{key}/{file}/dimension-chain-candidates", tags=["pdfs"])
def dimension_chain_candidates_route(
    key: str,
    file: str,
    region: str | None = None,
    orientation: str | None = None,
    thresh: int = 205,
    min_line_frac: float = 0.25,
    min_tick_px: int = 12,
    tick_search_px: int = 45,
    pad_px: int = 80,
):
    """Dimension-chain context-gatherer for measurement-first labeling.

    Given an optional scene region, returns a deterministic positional prior:
    the strongest likely dimension line, its running orientation, tick
    positions, and a tight crop region. It does not read text or values; the
    harness vision model reads the returned crop and writes
    dimensioned_distance + dimension_number labels.
    """
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    if orientation not in (None, "horizontal", "vertical"):
        raise HTTPException(status_code=400, detail="orientation must be horizontal, vertical, or omitted")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    from PIL import Image as PILImage
    from .dimension_chain import detect_dimension_chain
    parsed = _parse_region(region)
    with PILImage.open(img_path) as src:
        src = src.convert("RGB")
        res = detect_dimension_chain(
            src,
            region=parsed,
            orientation=orientation,  # type: ignore[arg-type]
            thresh=thresh,
            min_line_frac=min_line_frac,
            min_tick_px=min_tick_px,
            tick_search_px=tick_search_px,
            pad_px=pad_px,
        )
    return {"ok": True, "data": res}


@app.get("/datasets/{key}/{file}/dimension-station-graph", tags=["pdfs"])
def dimension_station_graph_route(
    key: str,
    file: str,
    region: str | None = None,
    orientation: str | None = None,
    thresh: int = 205,
    min_line_frac: float = 0.25,
    min_tick_px: int = 12,
    tick_search_px: int = 45,
    pad_px: int = 80,
    wall_anchor_tol_px: float = 28.0,
):
    """Dimension-chain station graph.

    This keeps the existing no-OCR contract but returns stable station/span
    ids and nearest-wall context so agents can label tick-to-tick distances
    without inventing endpoints.
    """
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    if orientation not in (None, "horizontal", "vertical"):
        raise HTTPException(status_code=400, detail="orientation must be horizontal, vertical, or omitted")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    from PIL import Image as PILImage
    from .dimension_station_graph import dimension_station_graph
    parsed = _parse_region(region)
    labels_doc = get_labels("dataset", key, file)
    with PILImage.open(img_path) as src:
        res = dimension_station_graph(
            src.convert("RGB"),
            labels_doc,
            region=parsed,
            orientation=orientation,
            thresh=thresh,
            min_line_frac=min_line_frac,
            min_tick_px=min_tick_px,
            tick_search_px=tick_search_px,
            pad_px=pad_px,
            wall_anchor_tol_px=wall_anchor_tol_px,
        )
    return {"ok": True, "data": res}


@app.get("/datasets/{key}/{file}/opening-candidates", tags=["pdfs"])
def opening_candidates_route(
    key: str,
    file: str,
    strip_half_width_px: float = 18.0,
    step_px: float = 4.0,
    min_gap_px: float = 28.0,
    max_gap_px: float = 260.0,
    endpoint_margin_px: float = 18.0,
    thresh: int = 180,
    limit: int = 40,
):
    """Return deterministic floorplan opening candidates from wall gaps."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    labels_doc = get_labels("dataset", key, file)
    from PIL import Image as PILImage
    from .opening_candidates import opening_candidate_report
    with PILImage.open(img_path) as src:
        data = opening_candidate_report(
            src.convert("RGB"),
            labels_doc,
            strip_half_width_px=strip_half_width_px,
            step_px=step_px,
            min_gap_px=min_gap_px,
            max_gap_px=max_gap_px,
            endpoint_margin_px=endpoint_margin_px,
            thresh=thresh,
            limit=limit,
        )
    return {"ok": True, "data": data}


@app.get("/datasets/{key}/{file}/view-geometry-candidates", tags=["pdfs"])
def view_geometry_candidates_route(
    key: str,
    file: str,
    region: str | None = None,
    thresh: int = 185,
    min_line_px: int = 80,
    min_rect_px: int = 18,
    max_candidates: int = 40,
):
    """Return component/opening candidates for Ansicht/Schnitt scenes."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    from PIL import Image as PILImage
    from .view_geometry_candidates import view_geometry_candidates
    parsed = _parse_region(region)
    with PILImage.open(img_path) as src:
        data = view_geometry_candidates(
            src.convert("RGB"),
            region=parsed,
            thresh=thresh,
            min_line_px=min_line_px,
            min_rect_px=min_rect_px,
            max_candidates=max_candidates,
        )
    return {"ok": True, "data": data}


def _find_opening_candidate(labels_doc: dict[str, Any], img_path: Path, candidate_id: str) -> dict[str, Any]:
    from PIL import Image as PILImage
    from .opening_candidates import opening_candidate_report
    with PILImage.open(img_path) as src:
        report = opening_candidate_report(src.convert("RGB"), labels_doc, limit=200)
    for candidate in report.get("candidates") or []:
        if candidate.get("candidate_id") == candidate_id:
            return candidate
    raise KeyError(f"opening candidate {candidate_id!r} not found")


@app.get("/datasets/{key}/{file}/opening-candidates/{candidate_id}/overlay", tags=["pdfs"])
def opening_candidate_overlay_route(
    key: str,
    file: str,
    candidate_id: str,
    max_dim: int = 1600,
    clean: bool = True,
):
    """Render current labels plus one opening candidate quad/axis."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    labels_doc = get_labels("dataset", key, file)
    try:
        candidate = _find_opening_candidate(labels_doc, img_path, candidate_id)
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    region = candidate.get("region")
    parsed_region = None
    if isinstance(region, list) and len(region) >= 4:
        x0, y0, x1, y1 = [int(round(float(v))) for v in region[:4]]
        pad = 55
        parsed_region = (max(0, x0 - pad), max(0, y0 - pad), max(x1 + pad, x0 + pad), max(y1 + pad, y0 + pad))
    from PIL import Image as PILImage, ImageDraw
    from .label_render import render_grid_with_labels
    with PILImage.open(img_path) as src:
        overlay = render_grid_with_labels(
            src.convert("RGB"),
            labels_doc.get("labels") or [],
            tiers=("finer",),
            region=parsed_region,
            max_dim=max_dim,
            clean=bool(clean),
            style="ink_compare",
            background_opacity=0.2,
            background_opacity_explicit=True,
            contrast="high",
            px_per_mm=_scene_px_per_mm(key, file),
            show_relations="required",
            show_openings="full",
        )
    if parsed_region is not None:
        rx0, ry0, rx1, ry1 = parsed_region
    else:
        rx0, ry0 = 0, 0
        with PILImage.open(img_path) as src:
            rx1, ry1 = src.size
    scale = min(max_dim / max(1, rx1 - rx0), max_dim / max(1, ry1 - ry0), 1.0)

    def to_out(pt: Any) -> tuple[float, float] | None:
        if not (isinstance(pt, list) and len(pt) == 2):
            return None
        return ((float(pt[0]) - rx0) * scale, (float(pt[1]) - ry0) * scale)

    draw = ImageDraw.Draw(overlay, "RGBA")
    pts = [to_out(p) for p in candidate.get("quad") or []]
    pts = [p for p in pts if p is not None]
    if len(pts) == 4:
        draw.polygon(pts, fill=(20, 184, 166, 50), outline=(20, 184, 166, 255))
        draw.line(pts + [pts[0]], fill=(20, 184, 166, 255), width=4)
    axis = [to_out(p) for p in candidate.get("centerline") or []]
    axis = [p for p in axis if p is not None]
    if len(axis) == 2:
        draw.line(axis, fill=(236, 72, 153, 255), width=4)
    import io
    buf = io.BytesIO()
    overlay.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


def _next_label_id(labels_doc: dict[str, Any], prefix: str) -> str:
    import uuid
    existing = {str(lab.get("id")) for lab in labels_doc.get("labels") or [] if isinstance(lab, dict)}
    for _ in range(20):
        label_id = f"{prefix}-{uuid.uuid4().hex[:8]}"
        if label_id not in existing:
            return label_id
    return f"{prefix}-{uuid.uuid4()}"


def _apply_opening_candidate_to_labels(
    labels_doc: dict[str, Any],
    candidate: dict[str, Any],
    attrs_patch: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    suggested = candidate.get("suggested_label")
    if not isinstance(suggested, dict):
        raise ValueError("candidate has no suggested label; record a decision instead")
    new_doc = json.loads(json.dumps(labels_doc))
    label = json.loads(json.dumps(suggested))
    attrs = label.setdefault("attributes", {})
    for key in ("opening_kind", "width_mm", "swing", "swing_side"):
        if attrs_patch and key in attrs_patch and attrs_patch[key] is not None:
            attrs[key] = attrs_patch[key]
    if attrs.get("opening_kind") == "unknown":
        attrs["opening_kind"] = "window"
    label["id"] = _next_label_id(new_doc, "opening")
    label["status"] = "readable"
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    label["created_at"] = now
    label["updated_at"] = now
    new_doc.setdefault("labels", []).append(label)
    return new_doc, label["id"]


@app.post("/datasets/{key}/{file}/opening-candidates/{candidate_id}/apply", tags=["pdfs"])
def apply_opening_candidate_route(key: str, file: str, candidate_id: str, body: dict[str, Any] = Body(default={})):
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    labels_doc = get_labels("dataset", key, file)
    try:
        candidate = _find_opening_candidate(labels_doc, img_path, candidate_id)
        if body.get("expected_candidate_kind") and body.get("expected_candidate_kind") != candidate.get("kind"):
            raise ValueError("candidate kind changed; refresh opening candidates")
        if body.get("expected_version"):
            from .scene_plan_state import PlanStateConflictError, read_plan_state
            current_plan = read_plan_state(DATASET_DIR, key, file)
            if current_plan.get("exists") and current_plan.get("version") != body.get("expected_version"):
                raise PlanStateConflictError("plan state version conflict")
        new_doc, label_id = _apply_opening_candidate_to_labels(labels_doc, candidate, body.get("attrs_patch") or body)
        put_labels("dataset", key, file, new_doc)
        from .scene_plan_state import record_opening_candidate_decision
        decision = record_opening_candidate_decision(
            DATASET_DIR,
            key,
            file,
            candidate,
            "accepted_applied",
            label_id=label_id,
            evidence_ids=body.get("evidence_ids"),
            note=body.get("note"),
            expected_version=body.get("expected_version"),
        )
        data = {
            "candidate_id": candidate_id,
            "candidate_fingerprint": candidate.get("candidate_fingerprint"),
            "persisted": True,
            "label_id": label_id,
            "candidate": candidate,
            "decision": ((decision.get("state") or {}).get("current_state") or {}).get("opening_candidate_decisions", {}),
        }
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/opening-candidates/{candidate_id}/decision", tags=["pdfs"])
def decide_opening_candidate_route(key: str, file: str, candidate_id: str, body: dict[str, Any] = Body(...)):
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    labels_doc = get_labels("dataset", key, file)
    try:
        candidate = _find_opening_candidate(labels_doc, img_path, candidate_id)
        if body.get("expected_candidate_kind") and body.get("expected_candidate_kind") != candidate.get("kind"):
            raise ValueError("candidate kind changed; refresh opening candidates")
        from .scene_plan_state import record_opening_candidate_decision
        data = record_opening_candidate_decision(
            DATASET_DIR,
            key,
            file,
            candidate,
            str(body.get("outcome") or ""),
            evidence_ids=body.get("evidence_ids"),
            note=body.get("note"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@app.get("/datasets/{key}/{file}/building-silhouette", tags=["pdfs"])
def building_silhouette_route(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 16,
    thresh: int | None = None,
    angle_tol_deg: float = 18.0,
    min_area_frac: float = 0.02,
):
    """Shape-first decomposition (methodology §2): the outer silhouette as
    ORDERED stepped polygon(s), one per connected mass (house vs detached garage
    auto-separate), edges snapped to axis-aligned steps, non-wall specks dropped.
    Wraps wall-outline + rectilinearize so the agent gets the masses in one call.
    Returns {masses:[{polygon,area,bbox}], count}."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    from PIL import Image as PILImage
    from .wall_geometry import building_silhouette
    parsed = _parse_region(region)
    with PILImage.open(img_path) as src:
        src = src.convert("RGB")
        res = building_silhouette(
            src, region=parsed, min_wall_px=min_wall_px, thresh=thresh,
            angle_tol_deg=angle_tol_deg, min_area_frac=min_area_frac,
        )
    res["params"] = {
        "region": list(parsed) if parsed else None,
        "min_wall_px": min_wall_px, "thresh": thresh,
        "angle_tol_deg": angle_tol_deg, "min_area_frac": min_area_frac,
    }
    return {"ok": True, "data": res}


@app.get("/datasets/{key}/{file}/outer-wall-topology-context", tags=["pdfs"])
def outer_wall_topology_context_route(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 12,
    thresh: int | None = None,
):
    """Context package for the required silhouette-first analysis pass.

    Deterministic CV priors may be empty on pencil scans; the returned prompts
    tell the vision agent what to write into the scene plan before wall edits.
    """
    _ensure_dataset_scene(key, file)
    outline = wall_outline(
        key,
        file,
        region=region,
        min_wall_px=max(6, min_wall_px - 4),
        thresh=thresh,
        n_outlines=3,
        epsilon_px=10.0,
    )["data"]
    silhouette = building_silhouette_route(
        key,
        file,
        region=region,
        min_wall_px=min_wall_px,
        thresh=thresh,
        angle_tol_deg=18.0,
        min_area_frac=0.02,
    )["data"]
    return {
        "ok": True,
        "data": {
            "region": list(_parse_region(region)) if _parse_region(region) else None,
            "outline_prior": outline,
            "silhouette_prior": silhouette,
            "questions": [
                "List connected masses before placing walls.",
                "Write the clockwise exterior corner sequence for each mass.",
                "Name excluded non-walls: balcony, terrace, furniture, dimensions, dashed projections, site lines.",
                "Identify places where openings interrupt ink but the structural wall should continue.",
                "Record this in the scene plan's Silhouette And Masses section before edits.",
            ],
            "cv_prior_note": (
                "Empty outline/silhouette priors are normal on faint freehand scans; "
                "the harness vision agent remains the reader."
            ),
        },
    }


@app.get("/datasets/{key}/{file}/wall-topology-qa", tags=["pdfs"])
def wall_topology_qa_route(
    key: str,
    file: str,
    endpoint_tol_px: float = 18.0,
    near_miss_px: float = 60.0,
    collinear_tol_deg: float = 8.0,
    collinear_gap_px: float = 140.0,
    short_stub_px: float = 80.0,
):
    _ensure_dataset_scene(key, file)
    doc = get_labels("dataset", key, file)
    from .wall_topology import wall_topology_qa
    data = wall_topology_qa(
        doc.get("labels") or [],
        endpoint_tol_px=endpoint_tol_px,
        near_miss_px=near_miss_px,
        collinear_tol_deg=collinear_tol_deg,
        collinear_gap_px=collinear_gap_px,
        short_stub_px=short_stub_px,
    )
    return {"ok": True, "data": data}


@app.get("/datasets/{key}/{file}/wall-continuity-check", tags=["pdfs"])
def wall_continuity_check_route(
    key: str,
    file: str,
    collinear_tol_deg: float = 8.0,
    gap_px: float = 180.0,
    line_tol_px: float = 24.0,
    opening_near_px: float = 80.0,
):
    _ensure_dataset_scene(key, file)
    doc = get_labels("dataset", key, file)
    from .wall_topology import wall_continuity_check
    data = wall_continuity_check(
        doc.get("labels") or [],
        collinear_tol_deg=collinear_tol_deg,
        gap_px=gap_px,
        line_tol_px=line_tol_px,
        opening_near_px=opening_near_px,
    )
    return {"ok": True, "data": data}


@app.get("/datasets/{key}/{file}/ambiguous-line-context", tags=["pdfs"])
def ambiguous_line_context_route(
    key: str,
    file: str,
    bbox: str | None = None,
    line: str | None = None,
    pad_px: float = 120.0,
):
    """Return a context checklist for a suspicious stroke/continuation.

    `bbox` is x0,y0,x1,y1. `line` is x0,y0,x1,y1. The route does not classify;
    it provides the crop region + checklist for the vision agent.
    """
    _ensure_dataset_scene(key, file)
    parsed_bbox = [float(v) for v in bbox.split(",")] if bbox else None
    if parsed_bbox is not None and len(parsed_bbox) != 4:
        raise HTTPException(status_code=400, detail="bbox must be x0,y0,x1,y1")
    parsed_line = None
    if line:
        vals = [float(v) for v in line.split(",")]
        if len(vals) != 4:
            raise HTTPException(status_code=400, detail="line must be x0,y0,x1,y1")
        parsed_line = [[vals[0], vals[1]], [vals[2], vals[3]]]
    doc = get_labels("dataset", key, file)
    from .wall_topology import ambiguous_line_context
    data = ambiguous_line_context(
        doc.get("labels") or [],
        bbox=parsed_bbox,
        line=parsed_line,
        pad_px=pad_px,
    )
    return {"ok": True, "data": data}


@app.post("/datasets/{key}/{file}/propose-wall-edit", tags=["pdfs"])
def propose_wall_edit_route(
    key: str,
    file: str,
    body: dict[str, Any] = Body(...),
):
    """Atomic test-and-apply for ONE wall edit (methodology §5). Body:
      {"candidate": {"op":"add|move|delete", ...}, "params": {..score-walls..},
       "region": "x0,y0,x1,y1"|null, "apply": false}
    where candidate.add={"op":"add","wall":[[x0,y0],[x1,y1]]},
    move={"op":"move","index":i,"wall":[...]}, delete={"op":"delete","index":i}.
    Scores the CURRENT saved walls and the candidate-edited walls with the
    canonical params; returns {applied, gain, before, after, walls_after}. If
    apply=true AND f1 improved, persists walls_after (non-wall labels preserved).
    A delete that lowers recall scores worse and is rejected (never delete a real
    wall to chase a metric). Removes the test-vs-apply desync."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    candidate = body.get("candidate")
    if not isinstance(candidate, dict) or "op" not in candidate:
        raise HTTPException(status_code=400, detail="body.candidate {op,...} required")
    params = body.get("params") or {}
    parsed = _parse_region(body.get("region"))
    apply = bool(body.get("apply", False))
    doc = get_labels("dataset", key, file)
    walls = []
    for lab in (doc.get("labels") or []):
        if lab.get("type") != "wall":
            continue
        g = lab.get("geometry") or {}
        s, e = g.get("start"), g.get("end")
        if s and e:
            walls.append(((float(s[0]), float(s[1])), (float(e[0]), float(e[1]))))
    from PIL import Image as PILImage
    from .wall_geometry import propose_wall_edit
    try:
        with PILImage.open(img_path) as src:
            src = src.convert("RGB")
            res = propose_wall_edit(src, walls, candidate, region=parsed, params=params)
    except (ValueError, IndexError, KeyError) as ex:
        raise HTTPException(status_code=400, detail=f"bad candidate: {ex}")
    res["persisted"] = False
    if apply and res.get("applied"):
        non_walls = [l for l in (doc.get("labels") or []) if l.get("type") != "wall"]
        new_walls = [{
            "type": "wall",
            "geometry": {"start": [w[0][0], w[0][1]], "end": [w[1][0], w[1][1]]},
            "attributes": {"thickness_mm": None},
            "status": "readable",
        } for w in res["walls_after"]]
        new_doc = dict(doc)
        new_doc["labels"] = non_walls + new_walls
        put_labels("dataset", key, file, new_doc)
        res["persisted"] = True
    return {"ok": True, "data": res}


@app.post("/geometry/connect-corners", tags=["pdfs"])
def connect_corners_route(body: dict[str, Any] = Body(...)):
    """Pure geometry (methodology §3): given ORDERED fitted edges
    [[[x0,y0],[x1,y1]], ...] (each ~a refine-wall band centerline), return walls
    whose shared corners are the INTERSECTIONS of adjacent edges' lines, so the
    shell is closed by construction (honors tilt; corners are not equal-y).
    Body: {"edges": [...], "closed": true}. Returns {walls, count, closed}."""
    edges = body.get("edges")
    if not isinstance(edges, list) or not edges:
        raise HTTPException(status_code=400, detail="body.edges (list of [start,end]) required")
    closed = bool(body.get("closed", True))
    from .wall_geometry import connect_corners
    try:
        pairs = [((e[0][0], e[0][1]), (e[1][0], e[1][1])) for e in edges]
        walls = connect_corners(pairs, closed=closed)
    except (IndexError, TypeError, ValueError) as ex:
        raise HTTPException(status_code=400, detail=f"bad edges: {ex}")
    return {"ok": True, "data": {
        "walls": [[[w[0][0], w[0][1]], [w[1][0], w[1][1]]] for w in walls],
        "count": len(walls), "closed": closed,
    }}


@app.get("/datasets/{key}/{file}/grid-with-labels", tags=["pdfs"])
def render_scene_grid_with_labels(
    key: str,
    file: str,
    region: str | None = None,
    tiers: str = "broad,finer",
    max_dim: int = 1600,
    enhance: str | None = None,
    format: str | None = None,
    clean: bool = False,
    style: str | None = None,
    target: str | None = None,
    target_line: str | None = None,
    background_opacity: float | None = None,
    contrast: str | None = None,
    show_relations: str | None = None,
    show_height_guides: str | None = None,
    show_openings: str | None = None,
    include_hidden: bool = False,
):
    """H5-1 (followups-2): same as /grid but with the scene's CURRENTLY
    SAVED labels rendered on top. Used by `get_scene_view_with_labels`
    so an agent can verify a label landed on the intended feature.

    The labels JSON drives the overlay; if no labels.json exists the
    output is identical to /grid. Cached on (image mtime, labels mtime).
    `enhance` (issue #2): none|auto|clahe|threshold, contrast lift for
    faint scans; coordinates stay source-pixel.
    `format` (issue #3): png|png8, default png8 — the cheaper palette PNG.
    """
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    if not 100 <= max_dim <= 8000:
        raise HTTPException(status_code=400, detail="max_dim must be in [100, 8000]")
    parsed_tiers = _parse_tiers(tiers)
    parsed_region = _parse_region(region)
    parsed_enhance = _parse_enhance(enhance)
    parsed_format = _parse_format(format)
    parsed_style = _parse_label_render_style(style)
    parsed_target = _parse_target(target)
    parsed_target_line = _parse_target_line(target_line)
    parsed_opacity, opacity_explicit = _parse_background_opacity(background_opacity)
    if clean and background_opacity is None:
        parsed_opacity, opacity_explicit = 0.2, True
    parsed_contrast = _parse_contrast(contrast)
    parsed_show_relations = _parse_show_relations(show_relations)
    parsed_show_height_guides = _parse_show_height_guides(show_height_guides)
    parsed_show_openings = _parse_show_openings(show_openings)

    label_path = _safe_label_path("dataset", key, file)
    img_mtime = img_path.stat().st_mtime_ns
    lbl_mtime = label_path.stat().st_mtime_ns if label_path.exists() else 0

    cache_root = GRID_CACHE / "scene-with-labels" / key
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_name = (
        f"{Path(file).stem}"
        f"-r{region or 'full'}"
        f"-t{'_'.join(parsed_tiers)}"
        f"-m{max_dim}"
        f"-e{parsed_enhance}"
        f"-c{int(bool(clean))}"
        f"-s{parsed_style}"
        f"-g{target or 'none'}"
        f"-gl{parsed_target_line}"
        f"-o{parsed_opacity:g}x{int(opacity_explicit)}"
        f"-k{parsed_contrast}"
        f"-rel{parsed_show_relations}"
        f"-hg{parsed_show_height_guides}"
        f"-op{parsed_show_openings}"
        f"-ih{int(bool(include_hidden))}"
        f"-f{parsed_format}.png"
    )
    out = cache_root / cache_name
    sentinel = out.with_suffix(".mtime")
    cache_key = f"{img_mtime}/{lbl_mtime}"
    if not out.exists() or not sentinel.exists() or sentinel.read_text() != cache_key:
        from PIL import Image as PILImage
        from .label_render import render_grid_with_labels
        labels: list[dict] = []
        if label_path.exists():
            try:
                lbl_doc = json.loads(label_path.read_text())
                labels = lbl_doc.get("labels") or []
                hidden = set(((lbl_doc.get("display") or {}).get("hidden_label_ids") or []))
                if hidden and not include_hidden:
                    labels = [lab for lab in labels if lab.get("id") not in hidden]
            except json.JSONDecodeError:
                labels = []
        with PILImage.open(img_path) as src:
            overlay = render_grid_with_labels(
                src,
                labels,
                tiers=parsed_tiers,
                region=parsed_region,
                max_dim=max_dim,
                enhance=parsed_enhance,
                clean=bool(clean),
                style=parsed_style,
                target=parsed_target,
                target_line=parsed_target_line,
                background_opacity=parsed_opacity,
                background_opacity_explicit=opacity_explicit,
                contrast=parsed_contrast,
                px_per_mm=_scene_px_per_mm(key, file),
                show_relations=parsed_show_relations,
                show_height_guides=parsed_show_height_guides,
                show_openings=parsed_show_openings,
            )
        _save_grid_png(overlay, out, parsed_format)
        sentinel.write_text(cache_key)
    return FileResponse(str(out), media_type="image/png")


@app.get("/datasets/{key}/{file}/resolve-point", tags=["pdfs"])
def resolve_scene_point(
    key: str,
    file: str,
    point: str,
    region: str | None = None,
    max_dim: int = 1600,
    frame: str = "source",
    snap: bool = True,
    snap_radius_px: int = 14,
    ink_threshold: int = 140,
):
    """Issue #10: resolve a point to final SOURCE pixels — optional
    crop-local → source mapping, then optional snap-to-nearest-feature.

    Query args:
      point          'x,y'. In source pixels when frame='source', or in
                     the local frame of the `region` crop when frame='crop'.
      region         'x0,y0,x1,y1' source-pixel crop (required for
                     frame='crop'); the same rect passed to get_scene_view.
      max_dim        the same cap used for the crop, so downscaled crops
                     map back correctly.
      frame          'source' | 'crop'.
      snap           snap the mapped point to the nearest ink feature.
      snap_radius_px search radius for the snap.
      ink_threshold  grayscale cutoff (0..255) below which a pixel is ink.

    Returns JSON: {source_point, mapped_point, snapped, offset_px,
    distance_px, feature_point, frame}.
    """
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    if not 100 <= max_dim <= 8000:
        raise HTTPException(status_code=400, detail="max_dim must be in [100, 8000]")
    if frame not in ("source", "crop"):
        raise HTTPException(status_code=400, detail="frame must be 'source' or 'crop'")
    try:
        pparts = [float(p) for p in point.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="point must be 'x,y'")
    if len(pparts) != 2:
        raise HTTPException(status_code=400, detail="point must be 'x,y' (2 numbers)")
    parsed_region = _parse_region(region)
    if frame == "crop" and parsed_region is None:
        raise HTTPException(status_code=400, detail="frame='crop' requires a region")
    if not 1 <= snap_radius_px <= 200:
        raise HTTPException(status_code=400, detail="snap_radius_px must be in [1, 200]")
    if not 0 <= ink_threshold <= 255:
        raise HTTPException(status_code=400, detail="ink_threshold must be in [0, 255]")

    from PIL import Image as PILImage
    from .snap import resolve_point
    with PILImage.open(img_path) as src:
        src.load()
        result = resolve_point(
            src, (pparts[0], pparts[1]),
            region=parsed_region, max_dim=max_dim, frame=frame,
            snap=snap, snap_radius_px=snap_radius_px, ink_threshold=ink_threshold,
        )
    return result


@app.get("/pdfs/{key}/page/{n}/grid", tags=["pdfs"])
def render_pdf_page_grid(
    key: str,
    n: int,
    dpi: int = 300,
    tiers: str = "broad,finer,detail",
    region: str | None = None,
    max_dim: int = 1600,
):
    """Same as /datasets/.../grid but for a PDF page (used for scene
    identification at inventory / extract). The grid coordinate labels are in
    pixels at the rendered DPI; downstream `extract_scenes` MCP tool
    converts to PDF units using the same DPI."""
    _safe_key(key)
    if dpi <= 0 or dpi > 600:
        raise HTTPException(status_code=400, detail="dpi must be in (0, 600]")
    if not 100 <= max_dim <= 8000:
        raise HTTPException(status_code=400, detail="max_dim must be in [100, 8000]")
    parsed_tiers = _parse_tiers(tiers)
    parsed_region = _parse_region(region)
    pdf = _consolidated_path(key)
    pdf_mtime = pdf.stat().st_mtime_ns
    cache_root = GRID_CACHE / "pdf" / key
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_name = (
        f"page-{n}-dpi{dpi}"
        f"-r{region or 'full'}"
        f"-t{'_'.join(parsed_tiers)}"
        f"-m{max_dim}.png"
    )
    out = cache_root / cache_name
    sentinel = out.with_suffix(".mtime")
    if not out.exists() or not sentinel.exists() or sentinel.read_text() != str(pdf_mtime):
        import fitz
        from PIL import Image as PILImage
        from .grid_render import render_grid_overlay
        with fitz.open(pdf) as doc:
            if n < 1 or n > doc.page_count:
                raise HTTPException(status_code=404, detail=f"page {n} out of range (1..{doc.page_count})")
            page = doc.load_page(n - 1)
            scale = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            page_img = PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)
            # Issue #24: recover an AcroForm-corrupt blank page via poppler
            # so the grid overlay sits on real content, not white.
            if _pixmap_is_blank(pix):
                fb = _render_page_poppler(pdf, n, dpi)
                if fb is not None and not _image_is_blank(fb):
                    page_img = fb
        overlay = render_grid_overlay(
            page_img,
            tiers=parsed_tiers,
            region=parsed_region,
            max_dim=max_dim,
            source_dpi=dpi,
        )
        overlay.save(out, format="PNG", optimize=True)
        sentinel.write_text(str(pdf_mtime))
    return FileResponse(str(out), media_type="image/png")


@app.get("/pdfs/{key}/info", tags=["pdfs"])
def pdf_info(key: str):
    """R2 — quick metadata: page count, per-page width/height in PDF
    units (1 unit = 1/72 inch). The extractor needs page geometry to
    convert client bboxes (image pixels) back to PDF coordinates."""
    _safe_key(key)
    pdf = _consolidated_path(key)
    import fitz
    pages = []
    with fitz.open(pdf) as doc:
        for i, page in enumerate(doc.pages(), start=1):
            r = page.rect
            pages.append({"page": i, "width_pt": r.width, "height_pt": r.height})
    return {"key": key, "page_count": len(pages), "pages": pages}


def _slug_token(s: str | None, fallback: str) -> str:
    s = (s or fallback).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or fallback


def _pixmap_is_blank(pix) -> bool:
    """True when a rendered pixmap is a uniform bright canvas — i.e. the
    render produced no drawing content (issue #12).

    PyMuPDF can fail to rasterize a page (e.g. a corrupt content stream in
    a merged PDF logs 'object is not a stream' to stderr) and silently
    return an all-white pixmap. Saving that yields a blank 'labeled' scene
    the vision-LLM can't read. We treat a near-uniform bright region as a
    failed render: even a faint pencil scan leaves non-uniform pixels, so
    this won't false-positive on real (if sparse) content.
    """
    import numpy as np
    a = np.frombuffer(pix.samples, dtype=np.uint8)
    if a.size == 0:
        return True
    return bool(int(a.min()) >= 250 and int(a.max()) - int(a.min()) <= 2)


def _image_is_blank(img) -> bool:
    """`_pixmap_is_blank` for a PIL.Image — used to check the poppler
    fallback raster (issue #24)."""
    import numpy as np
    a = np.asarray(img.convert("RGB"), dtype=np.uint8).reshape(-1)
    if a.size == 0:
        return True
    return bool(int(a.min()) >= 250 and int(a.max()) - int(a.min()) <= 2)


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


def _clipped_borders(img, ink_threshold: int = CLIP_INK_THRESHOLD,
                     ink_frac: float = CLIP_INK_FRAC) -> dict[str, bool]:
    """Which borders of a rendered crop have a drawing stroke *crossing* them.

    Returns {'left','right','top','bottom': bool}. A True means a stroke
    runs through that edge — the drawing is almost certainly cut there.

    The signal distinguishes a clipped stroke from a drawing's own frame
    line: a clip is a stroke that hits the very edge AND continues inward,
    so we count the fraction of border positions (columns for top/bottom,
    rows for left/right) where the outermost edge pixels are ink *and* that
    ink continues into an inner band just past the edge. A frame line runs
    parallel to and only at the edge — its ink does not penetrate inward, so
    it doesn't trip the detector and the crop won't over-expand. Works on a
    grayscale PIL.Image.
    """
    import numpy as np
    a = np.asarray(img.convert("L"), dtype=np.uint8)
    h, w = a.shape
    if h == 0 or w == 0:
        return {"left": False, "right": False, "top": False, "bottom": False}
    strip = max(2, int(round(min(h, w) * CLIP_BORDER_FRAC)))
    edge = max(1, strip // 2)            # outermost band that must be inked
    sh = min(strip, max(1, h - 1))
    sw = min(strip, max(1, w - 1))
    eh = min(edge, sh)
    ew = min(edge, sw)
    ink = a < ink_threshold

    def crossing_frac(edge_band, inner_band, axis: int) -> float:
        # positions inked at the very edge that also continue inward
        if edge_band.size == 0 or inner_band.size == 0:
            return 0.0
        crossing = edge_band.any(axis=axis) & inner_band.any(axis=axis)
        return float(crossing.mean())

    return {
        "top": crossing_frac(ink[:eh, :], ink[eh:sh, :], 0) >= ink_frac,
        "bottom": crossing_frac(ink[h - eh:, :], ink[h - sh:h - eh, :], 0) >= ink_frac,
        "left": crossing_frac(ink[:, :ew], ink[:, ew:sw], 1) >= ink_frac,
        "right": crossing_frac(ink[:, w - ew:], ink[:, w - sw:w - ew], 1) >= ink_frac,
    }


def _grow_bbox(bbox, borders, page_w: float, page_h: float,
               grow_frac: float = CLIP_GROW_FRAC) -> list[float]:
    """Grow `bbox` (PDF units, top-left origin) toward each clipped border,
    clamped to the page [0,0,page_w,page_h]. Returns the new bbox."""
    x0, y0, x1, y1 = (float(v) for v in bbox)
    dw = (x1 - x0) * grow_frac
    dh = (y1 - y0) * grow_frac
    if borders.get("left"):
        x0 = max(0.0, x0 - dw)
    if borders.get("right"):
        x1 = min(page_w, x1 + dw)
    if borders.get("top"):
        y0 = max(0.0, y0 - dh)
    if borders.get("bottom"):
        y1 = min(page_h, y1 + dh)
    return [x0, y0, x1, y1]


def _render_crop(page, bbox, dpi: int):
    """Render `bbox` (PDF units) of a fitz page at `dpi` to a PIL.Image."""
    import fitz
    scale = dpi / 72.0
    x0, y0, x1, y1 = (float(v) for v in bbox)
    pix = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        clip=fitz.Rect(x0, y0, x1, y1), alpha=False,
    )
    return pix.pil_image() if hasattr(pix, "pil_image") else _pix_to_pil(pix)


def _pix_to_pil(pix):
    from PIL import Image as PILImage
    mode = "RGB" if pix.n >= 3 else "L"
    return PILImage.frombytes(mode, (pix.width, pix.height), pix.samples)


def _expand_bbox_for_clip(page, bbox, dpi: int, page_w: float, page_h: float):
    """If the crop at `bbox` cuts the drawing at a border, grow the bbox
    toward the clipped border(s) and re-crop, iterating until no border is
    clipped (or we hit the page edge / iteration cap). Adds a final small
    margin so the unclipped drawing has a little breathing room.

    Returns (final_bbox, expanded: bool, history: list[dict]) — `history`
    records the clipped borders seen at each iteration for diagnostics.
    """
    cur = [float(v) for v in bbox]
    history: list[dict] = []
    expanded = False
    for _ in range(CLIP_MAX_ITERS):
        img = _render_crop(page, cur, dpi)
        borders = _clipped_borders(img)
        # Only borders that aren't already pinned to the page edge can grow.
        x0, y0, x1, y1 = cur
        growable = {
            "left": borders["left"] and x0 > 0,
            "right": borders["right"] and x1 < page_w,
            "top": borders["top"] and y0 > 0,
            "bottom": borders["bottom"] and y1 < page_h,
        }
        history.append({k: v for k, v in borders.items()})
        if not any(growable.values()):
            break
        cur = _grow_bbox(cur, growable, page_w, page_h)
        expanded = True
    if expanded:
        # Breathing margin once the drawing no longer touches a border, so
        # the previously-cut feature (e.g. the ridge) isn't flush to the edge.
        mx = (cur[2] - cur[0]) * CLIP_MARGIN_FRAC
        my = (cur[3] - cur[1]) * CLIP_MARGIN_FRAC
        cur = [
            max(0.0, cur[0] - mx), max(0.0, cur[1] - my),
            min(page_w, cur[2] + mx), min(page_h, cur[3] + my),
        ]
    return cur, expanded, history


def _render_page_poppler(pdf, n: int, dpi: int, clip_pdf_units=None):
    """Render PDF page `n` (1-indexed) at `dpi` via poppler's `pdftoppm`
    and return a PIL.Image (issue #24).

    Recovery path for PDFs that PyMuPDF cannot rasterize — e.g. scanned
    municipal Bauakten wrapped in a malformed AcroForm/Fields layer, where
    `fitz.Page.get_pixmap` silently yields an all-white pixmap and
    `get_images` reports zero embedded images, but the page content (the
    embedded JPEG scans) is intact and poppler reads it fine.

    `pdftoppm -r <dpi>` rasterizes the page on the same `dpi/72` pixel
    grid PyMuPDF uses, so cropping `clip_pdf_units` (a 4-tuple in PDF
    units, top-left origin) with `dpi/72` scaling yields pixel-identical
    geometry to the PyMuPDF `clip=` path — `crop_from`/coordinate
    semantics stay consistent across both renderers.

    Returns None if poppler is unavailable or fails (caller falls back to
    the original PyMuPDF behaviour / 422).
    """
    import glob
    import os
    import shutil
    import subprocess
    import tempfile
    from PIL import Image as PILImage

    if shutil.which("pdftoppm") is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        prefix = os.path.join(td, "pg")
        try:
            subprocess.run(
                ["pdftoppm", "-f", str(n), "-l", str(n),
                 "-r", str(int(dpi)), "-png", str(pdf), prefix],
                check=True, capture_output=True, timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return None
        files = sorted(glob.glob(prefix + "*"))
        if not files:
            return None
        img = PILImage.open(files[0]).convert("RGB")
        img.load()
    if clip_pdf_units is not None:
        x0, y0, x1, y1 = (float(v) for v in clip_pdf_units)
        s = dpi / 72.0
        box = (
            max(0, int(round(x0 * s))),
            max(0, int(round(y0 * s))),
            min(img.width, int(round(x1 * s))),
            min(img.height, int(round(y1 * s))),
        )
        if box[2] > box[0] and box[3] > box[1]:
            img = img.crop(box)
    return img


@app.post("/pdfs/{key}/extract", tags=["pdfs"], status_code=201)
def extract_scenes(key: str, payload: dict[str, Any] = Body(...)):
    """R2 — crop scenes out of the consolidated PDF.

    Body: {"items": [{
      "page": 1,                     # 1-indexed
      "bbox_pdf_units": [x0, y0, x1, y1],
      "kind": "floorplan"|"elevation"|"section"|"detail",
      "view": "north"|...,           # optional
      "floor": "kg"|"ug"|...,        # optional
      "title": str,                  # optional
      "slug_override": str,          # optional, used as the slug if set
      "dpi": 600,                    # optional output raster DPI, default 600
      "format": "jpg"|"png",         # optional image format, default jpg
      "allow_blank": false,          # optional; bypass the blank-render guard
      "no_clip_expand": false,       # optional; bypass clip-detection bbox
                                     #   auto-expansion (issue #25)
      "bbox_is_authoritative": false # optional (V1.1); the caller's bbox is
                                     #   final — never auto-expand it. Alias
                                     #   of no_clip_expand, intent-named for
                                     #   the vision-LLM-chooses-extent flow.
    }]}

    Per issue #25: the segmentation bbox can under-shoot a tall drawing
    (cutting the roof apex so the Firsthöhe/ridge never lands in any
    raster). Before rendering, if significant ink touches a crop border the
    bbox is grown toward that border and re-cropped until the drawing no
    longer touches an edge (clamped to the page). Expansion only grows the
    rect within the page — an explicit bbox re-extract is still honoured, it
    just can't be clipped. The recorded crop_from bbox is the final rect, so
    a single clipped scene can be re-captured by re-extracting with the same
    slug_override. Set `no_clip_expand: true` to disable.

    Per issue #12: a crop that renders to a blank/uniform canvas (a failed
    rasterization — e.g. a corrupt content stream in the merged PDF) is
    rejected with 422 rather than silently written, so a blank scene never
    masquerades as a labeled one. Set `allow_blank: true` to force.

    For each item, crops the PDF page at the bbox, writes the scene image into
    data/dataset/<key>/, and appends a DatasetDrawing entry to the
    dataset manifest. Idempotent on (page, slug): re-extracting overwrites
    the image and updates the manifest entry while leaving any sibling
    labels.json intact.

    Returns the updated dataset manifest entries + the intake bundle's
    new state."""
    _safe_key(key)
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="items must be a non-empty list")
    pdf = _consolidated_path(key)
    ds_dir = DATASET_DIR / key
    ds_dir.mkdir(parents=True, exist_ok=True)
    ds_manifest_path = ds_dir / "manifest.json"
    if ds_manifest_path.exists():
        ds_manifest = json.loads(ds_manifest_path.read_text())
    else:
        ds_manifest = {"key": key, "drawings": []}
    drawings: list[dict] = ds_manifest.setdefault("drawings", [])

    import fitz
    out_entries: list[dict] = []
    used_slugs: set[str] = set()
    # Seed used_slugs from existing manifest so we can dedup correctly.
    for d in drawings:
        st = Path(d.get("file", "")).stem
        used_slugs.add(st)
    with fitz.open(pdf) as doc:
        for raw in items:
            page_n = int(raw.get("page", 0))
            if page_n < 1 or page_n > doc.page_count:
                raise HTTPException(status_code=400, detail=f"page {page_n} out of range")
            bbox = raw.get("bbox_pdf_units")
            if not (isinstance(bbox, list) and len(bbox) == 4):
                raise HTTPException(status_code=400, detail="bbox_pdf_units must be [x0,y0,x1,y1]")
            x0, y0, x1, y1 = (float(v) for v in bbox)
            if not (x1 > x0 and y1 > y0):
                raise HTTPException(status_code=400, detail="bbox must have positive area")
            kind = (raw.get("kind") or "detail").strip().lower()
            view = raw.get("view")
            floor = raw.get("floor")
            dpi = int(raw.get("dpi", 600))
            if dpi <= 0 or dpi > 1200:
                raise HTTPException(status_code=400, detail="dpi out of range")
            fmt = str(raw.get("format") or "jpg").strip().lower()
            if fmt == "jpeg":
                fmt = "jpg"
            if fmt not in {"jpg", "png"}:
                raise HTTPException(status_code=400, detail="format must be 'jpg' or 'png'")

            # Slug derivation. The user may override with an explicit slug
            # for re-extraction; otherwise we synthesize one from
            # kind/view/floor + a sequence suffix.
            override = raw.get("slug_override")
            base_slug = override or f"{kind}-{_slug_token(view or floor, kind)}"
            base_slug = re.sub(r"[^a-z0-9-]+", "-", base_slug.lower()).strip("-")
            # A scene's filename stem is `{key}-{base_slug}`. When the caller
            # passes an explicit slug_override to RE-EXTRACT an existing scene,
            # they pass the full stem (e.g. "house-22-floorplan-eg"), which
            # already starts with "{key}-". Re-prepending the key here produced
            # a phantom double-prefixed scene ("house-22-house-22-floorplan-eg")
            # and left the real scene's crop untouched — so re-cropping silently
            # did nothing. Strip a leading "{key}-" from the override so the
            # full stem resolves to the SAME file and the re-extract overwrites
            # the intended scene.
            key_prefix = f"{key}-"
            if override and base_slug.startswith(key_prefix):
                base_slug = base_slug[len(key_prefix):]
            full = f"{key}-{base_slug}"
            if not override:
                # Append -2, -3, ... if collision.
                slug = full
                n = 2
                while slug in used_slugs:
                    slug = f"{full}-{n}"
                    n += 1
                used_slugs.add(slug)
            else:
                slug = full
                used_slugs.add(slug)

            _ext = "png" if fmt == "png" else "jpg"
            file_name = f"{slug}.{_ext}"
            out_path = ds_dir / file_name

            page = doc.load_page(page_n - 1)

            # Issue #25: clip detection + bbox auto-expansion. The
            # segmentation bbox can under-shoot a tall drawing (cutting the
            # roof apex → ridge/Firsthöhe never captured). If significant
            # ink touches a crop border, grow the bbox toward that border
            # and re-crop until the drawing no longer touches an edge (or we
            # hit the page extent). `no_clip_expand: true` opts out, and an
            # explicit re-extract with the desired bbox is still honoured —
            # expansion only ever *grows* the rect within the page, never
            # shrinks it. The recorded crop_from bbox is the final rect.
            page_rect = page.rect
            page_w, page_h = float(page_rect.width), float(page_rect.height)
            clip_diag: dict | None = None
            # V1.1: when the caller (the vision-LLM) has chosen the bbox
            # deliberately, that extent is authoritative — do NOT let #25
            # auto-expand grow it (which would override the chosen crop,
            # e.g. to the whole page). `bbox_is_authoritative` is the
            # intent-named alias of `no_clip_expand`; either disables the
            # auto-expansion.
            _bbox_authoritative = bool(raw.get("no_clip_expand")) or bool(
                raw.get("bbox_is_authoritative")
            )
            if not _bbox_authoritative:
                grown, expanded, history = _expand_bbox_for_clip(
                    page, [x0, y0, x1, y1], dpi, page_w, page_h
                )
                if expanded:
                    x0, y0, x1, y1 = grown
                    clip_diag = {"expanded": True, "iters": len(history),
                                 "history": history}

            # The PDF rect uses top-left origin. fitz.Matrix scales; clip
            # restricts the rendered region to the bbox.
            scale = dpi / 72.0
            clip = fitz.Rect(x0, y0, x1, y1)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
            # Issue #12: refuse to write a blank crop. A failed render
            # (e.g. corrupt content stream in the merged PDF) yields an
            # all-white pixmap; saving it produces a blank scene the agent
            # can't label but that still reports as `labeled`. Fail loudly
            # so the corruption is visible at extraction time. `allow_blank`
            # opts out for the rare intentionally-empty region.
            #
            # Issue #24 (recovery): when PyMuPDF returns blank — the common
            # AcroForm-corrupt scanned-archive case where get_pixmap yields
            # an all-white raster but the embedded scans are intact — first
            # try a poppler render of the page, cropped to the same bbox at
            # the same DPI (pixel-identical geometry to the PyMuPDF clip).
            # Only fall through to the 422 if BOTH renderers come back
            # blank (a genuinely empty region vs. a PyMuPDF-only failure).
            if not bool(raw.get("allow_blank")) and _pixmap_is_blank(pix):
                fb = _render_page_poppler(
                    pdf, page_n, dpi, clip_pdf_units=(x0, y0, x1, y1)
                )
                if fb is not None and not _image_is_blank(fb):
                    if str(out_path).lower().endswith(".png"):
                        fb.save(str(out_path), format="PNG", optimize=True)
                    else:
                        fb.save(str(out_path), format="JPEG", quality=100)
                else:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"page {page_n} rendered blank for {file_name} — no "
                            "content (the page's content stream may be corrupt in "
                            "the merged PDF, or the bbox is empty). Crop not "
                            "written. Re-merge the source PDF or fix the bbox; "
                            "pass allow_blank=true to force."
                        ),
                    )
            else:
                if str(out_path).lower().endswith(".png"):
                    pix.pil_save(str(out_path), format="PNG")
                else:
                    pix.pil_save(str(out_path), format="JPEG", quality=100)

            entry = {
                "file": file_name,
                "kind": kind,
                "source": "pdf",
                "view": view,
                "floor": floor,
                "title": raw.get("title"),
                "imported_at": _now_iso(),
                "crop_from": {
                    "pdf_file": pdf.name,
                    "page": page_n,
                    "bbox_pdf_units": [x0, y0, x1, y1],
                    "dpi": dpi,
                    **({"clip_expand": clip_diag} if clip_diag else {}),
                },
            }
            # Replace existing entry with same file name (re-extract) else append.
            existing_idx = next((i for i, d in enumerate(drawings) if d.get("file") == file_name), None)
            if existing_idx is not None:
                drawings[existing_idx] = entry
            else:
                drawings.append(entry)
            out_entries.append(entry)

    atomic_write_json(ds_manifest_path, ds_manifest)

    # Update intake state.
    intake = _read_manifest(key) or {}
    intake.setdefault("extracted_scenes", [])
    # Replace any same-(page,scene_file) records.
    existing_scene_files = {e["file"] for e in out_entries}
    intake["extracted_scenes"] = [
        s for s in intake["extracted_scenes"]
        if s.get("scene_file") not in existing_scene_files
    ]
    for e in out_entries:
        intake["extracted_scenes"].append({
            "page": e["crop_from"]["page"],
            "bbox_pdf_units": e["crop_from"]["bbox_pdf_units"],
            "scene_file": e["file"],
        })
    intake["state"] = _bundle_state(key, intake)
    _write_manifest(key, intake)

    return {"extracted": out_entries, "intake_state": intake["state"]}


# ── R6 — bulk export ─────────────────────────────────────────────────────

EXPORTS_DIR = BASE / "data" / "exports"

HOUSE_FACTS_DUMP_NOTE = (
    "house_facts in this app live in the browser's localStorage. Export "
    "captures the per-scene labels + the derived homography; the user is "
    "expected to copy house_facts.json into the export via the UI download."
)


def _sanity_check_house(key: str, dataset: dict) -> list[str]:
    """R6.4 — pre-export sanity checks. Returns a list of human-readable
    reasons. Empty list means the house is clean to export."""
    issues: list[str] = []
    drawings = dataset.get("drawings") or []
    if not drawings:
        issues.append("house has zero drawings")
        return issues
    have_labels = 0
    for d in drawings:
        if d.get("labeled"):
            have_labels += 1
    if have_labels == 0:
        issues.append("no annotated scenes")
    return issues


def _export_one_house(key: str) -> dict:
    """Render the export for one house to data/exports/<key>/. Returns
    a summary {key, scenes_exported, scenes_skipped, anomalies}."""
    _safe_key(key)
    src = DATASET_DIR / key
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"no dataset for {key!r}")
    ds_manifest = _load_dataset_manifest(key) or {}
    issues = _sanity_check_house(key, ds_manifest)

    out_root = EXPORTS_DIR / key
    out_root.mkdir(parents=True, exist_ok=True)
    set_a_dir = out_root / "setA"
    set_b_dir = out_root / "setB"
    diag_dir = out_root / "diagnostics"
    for d in (set_a_dir, set_b_dir, diag_dir):
        d.mkdir(parents=True, exist_ok=True)

    exported: list[str] = []
    skipped: list[tuple[str, str]] = []
    from .homography import compute_rectification, rectify_image, transform_label
    import shutil

    drawings = ds_manifest.get("drawings") or []
    for d in drawings:
        file = d.get("file")
        if not file:
            continue
        stem = Path(file).stem
        labels_path = src / "labels" / f"{stem}.json"
        img_path = src / file
        if not labels_path.exists():
            skipped.append((file, "no labels JSON"))
            continue
        if not img_path.exists():
            skipped.append((file, "image file missing"))
            continue
        scene = json.loads(labels_path.read_text())
        labels = scene.get("labels") or []
        image_size = tuple(scene.get("image_size_px") or [0, 0])
        if not image_size or image_size[0] <= 0 or image_size[1] <= 0:
            from PIL import Image as PILImage
            with PILImage.open(img_path) as im:
                image_size = im.size  # type: ignore[assignment]
        rect = compute_rectification(labels, image_size)

        # Set A: raw image + only dimensioned strokes / numbers.
        shutil.copyfile(img_path, set_a_dir / file)
        set_a_labels = [l for l in labels if l.get("type") in SET_A_TYPES]
        atomic_write_json(set_a_dir / f"{stem}.json", {
            **{k: v for k, v in scene.items() if k != "labels"},
            "labels": set_a_labels,
        })

        # Set B: rectified image + every label transformed. When rectification
        # is degenerate we still write the unrectified image so the export
        # captures *all* scenes; the diagnostics file records which were
        # rectified.
        if rect.status == "ok":
            try:
                rectify_image(img_path, set_b_dir / file, rect.affine,
                              rect.rectified_size_px)
            except Exception as e:  # noqa: BLE001
                rect.status = "degenerate"
                rect.reason = f"PIL transform failed: {e}"
                shutil.copyfile(img_path, set_b_dir / file)
        else:
            shutil.copyfile(img_path, set_b_dir / file)
        set_b_labels = (
            [transform_label(rect.affine, l) for l in labels]
            if rect.status == "ok" else labels
        )
        atomic_write_json(set_b_dir / f"{stem}.json", {
            **{k: v for k, v in scene.items() if k != "labels"},
            "labels": set_b_labels,
        })
        atomic_write_json(set_b_dir / f"{stem}.homography.json", {
            "matrix": rect.matrix,
            "computed_from": rect.computed_from,
            "rectified_size_px": list(rect.rectified_size_px),
            "rms_residual_px": rect.rms_residual_px,
            "status": rect.status,
            "reason": rect.reason,
        })
        exported.append(file)

    # Manifest
    manifest = {
        "schema_version": "1.0",
        "house_key": key,
        "generated_at": _now_iso(),
        "scenes_exported": exported,
        "scenes_skipped": [{"file": f, "reason": r} for (f, r) in skipped],
        "anomalies": issues,
        "house_facts_note": HOUSE_FACTS_DUMP_NOTE,
    }
    atomic_write_json(out_root / "manifest.json", manifest)
    atomic_write_text(
        diag_dir / "coverage.txt",
        f"exported: {len(exported)}/{len(drawings)}\n"
        + "\n".join(f"  ✓ {f}" for f in exported)
        + "\n"
        + "\n".join(f"  ⊘ {f}: {r}" for f, r in skipped),
    )
    if issues:
        atomic_write_text(diag_dir / "anomalies.txt", "\n".join(f"- {i}" for i in issues))
    return {
        "key": key,
        "scenes_exported": len(exported),
        "scenes_skipped": len(skipped),
        "anomalies": issues,
        "path": str(out_root.relative_to(BASE)),
    }


@app.post("/exports/{key}", tags=["exports"], status_code=201)
def export_house(key: str, force: bool = False):
    """R6.2 — produce the per-house export tree at data/exports/<key>/
    with setA/ + setB/ + manifest + diagnostics.

    When `force=false` (default), reject the export if sanity checks fail
    (no annotated scenes, no drawings). Set `force=true` to bypass."""
    _safe_key(key)
    ds = _load_dataset_manifest(key)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"no dataset for {key!r}")
    issues = _sanity_check_house(key, ds)
    if issues and not force:
        raise HTTPException(
            status_code=409,
            detail={"reason": "sanity check failed", "anomalies": issues,
                    "hint": "pass ?force=true to override"},
        )
    return _export_one_house(key)


@app.post("/exports", tags=["exports"], status_code=201)
def export_all(force: bool = False):
    """R6.3 — export every house in the dataset. Returns a per-house
    summary. Skips houses that fail sanity unless force=true."""
    if not DATASET_DIR.exists():
        return {"jobs": []}
    out = []
    for d in sorted(DATASET_DIR.iterdir()):
        if not d.is_dir():
            continue
        try:
            r = export_house(d.name, force=force)
            out.append(r)
        except HTTPException as e:
            out.append({"key": d.name, "skipped": True,
                        "detail": getattr(e, "detail", str(e))})
    return {"jobs": out}


# ── R4 — export preview (per-scene rectified + Set A / Set B labels) ─────

EXPORT_CACHE = BASE / "tmp" / "exports-cache"

# Label types that go into Set A (the "Model 1 must detect" subset —
# dimensioned strokes only, plus their paired dim_numbers when present).
SET_A_TYPES = {"dimensioned_distance", "dimension_number"}


def _load_scene_labels(key: str, file: str) -> dict | None:
    p = _safe_label_path("dataset", key, file)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _persist_scene_calibration(key: str, file: str, calib: dict) -> None:
    """Merge one scene's calibration into
    house_facts.calibration_per_scene (issue #27).

    Idempotent — a later `recompute_facts_after_label_write` re-derives the
    same value via `compute_scene_calibration`, so this only makes the
    derived export-readiness state stable EARLIER (right after the
    homography is computed), in particular for the single-ref isotropic
    path whose preview previously persisted nothing. The entry carries the
    `single_ref_assumed_isotropic` honesty flag.
    """
    p = DATASET_DIR / key / "house_facts.json"
    # C2: hold the facts lock across read-modify-write so a concurrent
    # recompute / another scene's calibration merge can't drop this entry.
    with locked_path(p):
        facts: dict = {}
        if p.exists():
            try:
                loaded = json.loads(p.read_text())
                if isinstance(loaded, dict):
                    facts = loaded
            except json.JSONDecodeError:
                facts = {}
        facts.setdefault("schema_version", "1.1")
        cps = facts.get("calibration_per_scene")
        if not isinstance(cps, dict):
            cps = {}
            facts["calibration_per_scene"] = cps
        cps[file] = calib
        atomic_write_json(p, facts)


@app.post("/exports/{key}/{file}/preview", tags=["exports"])
def export_preview(key: str, file: str, assume_isotropic: bool = False):
    """R4 — return the two ground-truth views for one scene:
       Set A = raw image + dimensioned strokes only
       Set B = rectified image + every label, geometry transformed through H

    Both sets are computed on the fly. The rectified image is cached at
    tmp/exports-cache/<key>/<file>/rectified.jpg keyed on (image mtime,
    labels mtime); the response carries rectified_url pointing at the
    static-mounted cache.

    `assume_isotropic` (issue #26): when true and the scene has exactly
    one reference dim, the calibration derives the second-axis scale from
    the same px-per-mm (square-pixel / isotropic assumption — valid for
    axis-aligned orthographic German Ansicht/Schnitt) instead of returning
    `insufficient_references`. The harness vision-LLM makes the
    axis-aligned judgement and opts in; the engine just honours the flag
    and stamps `single_ref_assumed_isotropic` into the homography snapshot.

    Issue #27: when the rectification is valid (status == "ok") the scene's
    calibration is PERSISTED into house_facts.calibration_per_scene (with
    the single_ref_assumed_isotropic flag), returned as
    `persisted_calibration`. This makes the export-readiness gate
    stable on the single-ref isotropic path without a separate label-write
    recompute. Degenerate/insufficient results persist nothing.
    """
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    src_img = _scene_image_path("dataset", key, file)
    if not src_img.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    scene = _load_scene_labels(key, file)
    if scene is None:
        raise HTTPException(status_code=404, detail=f"no labels for {file}")
    labels = scene.get("labels") or []
    img_size = tuple(scene.get("image_size_px") or [0, 0])
    if not img_size or img_size[0] <= 0 or img_size[1] <= 0:
        # Fall back to PIL.
        from PIL import Image as PILImage
        with PILImage.open(src_img) as im:
            img_size = im.size  # type: ignore[assignment]

    from .homography import compute_rectification, rectify_image, transform_label

    rect = compute_rectification(labels, img_size, assume_isotropic=assume_isotropic)

    # Issue #27: persist the scene's calibration when the rectification is
    # valid, so the derived export-readiness state is stable without a
    # separate label-write recompute. In particular the single-ref
    # isotropic path (assume_isotropic=true) now lands in
    # calibration_per_scene with its single_ref_assumed_isotropic flag, so
    # `recompute_homography(assume_isotropic=true)` (which calls this
    # endpoint) actually updates the gate. Gated on status == "ok", so the
    # genuine-degenerate guard (no refs / near-parallel / single ref
    # without opt-in) persists nothing.
    persisted_calibration = None
    if rect.status == "ok":
        from .fact_derivation import compute_scene_calibration
        calib = compute_scene_calibration(labels)
        if calib:
            _persist_scene_calibration(key, file, calib)
            persisted_calibration = calib

    # Cache key based on (image mtime, labels mtime). Either dimension
    # changing invalidates the rectified output.
    img_mtime = src_img.stat().st_mtime_ns
    lbl_mtime = _safe_label_path("dataset", key, file).stat().st_mtime_ns
    cache_dir = EXPORT_CACHE / key / Path(file).stem
    cache_dir.mkdir(parents=True, exist_ok=True)
    rectified_path = cache_dir / "rectified.jpg"
    sentinel = cache_dir / "rectified.mtime"
    sentinel_value = f"{img_mtime}/{lbl_mtime}/{rect.status}"
    needs_render = (
        rect.status == "ok"
        and (not rectified_path.exists()
             or not sentinel.exists()
             or sentinel.read_text() != sentinel_value)
    )
    if needs_render:
        try:
            rectify_image(src_img, rectified_path, rect.affine, rect.rectified_size_px)
            sentinel.write_text(sentinel_value)
        except Exception as e:  # noqa: BLE001
            return {
                "status": "degenerate",
                "reason": f"rectify failed: {e}",
                "homography": None,
                "raw_url": f"/static/dataset/{key}/{file}",
                "rectified_url": None,
                "set_a": [l for l in labels if l.get("type") in SET_A_TYPES],
                "set_b": labels,
                "computed_from": rect.computed_from,
                "rms_residual_px": rect.rms_residual_px,
            }
    set_a = [l for l in labels if l.get("type") in SET_A_TYPES]
    if rect.status == "ok":
        set_b = [transform_label(rect.affine, l) for l in labels]
    else:
        set_b = labels
    return {
        "status": rect.status,
        "reason": rect.reason,
        "homography": {
            "matrix": rect.matrix,
            "computed_from": rect.computed_from,
            "rectified_size_px": list(rect.rectified_size_px),
            "rms_residual_px": rect.rms_residual_px,
            "single_ref_assumed_isotropic": rect.single_ref_assumed_isotropic,
        },
        "persisted_calibration": persisted_calibration,
        "raw_url": f"/static/dataset/{key}/{file}",
        "rectified_url": (
            f"/static/exports-cache/{key}/{Path(file).stem}/rectified.jpg"
            if rect.status == "ok" else None
        ),
        "set_a": set_a,
        "set_b": set_b,
        "computed_from": rect.computed_from,
        "rms_residual_px": rect.rms_residual_px,
    }


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


def _purge_old_recycle() -> int:
    """Sweep recycle/* older than RECYCLE_TTL_SEC. Called opportunistically
    on every recycle write/read. Returns the count of pruned bundles."""
    if not RECYCLE_DIR.exists():
        return 0
    import time
    cutoff = time.time() - RECYCLE_TTL_SEC
    pruned = 0
    for d in list(RECYCLE_DIR.rglob("*")):
        if not d.is_dir() or d == RECYCLE_DIR:
            continue
        try:
            if d.stat().st_mtime < cutoff:
                for f in d.iterdir():
                    f.unlink(missing_ok=True)
                d.rmdir()
                pruned += 1
        except OSError:
            pass
    return pruned


def _safe_recycle_path(key: str, file: str) -> Path:
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    return RECYCLE_DIR / key / file


@app.delete("/pdfs/{key}/extract/{file}", tags=["pdfs"], status_code=204)
def delete_extracted_scene(key: str, file: str):
    """R2 — drop one extracted scene (image + dataset manifest entry +
    intake record). The deleted scene goes into a 1-hour recycle bin
    at tmp/recycle/<key>/<file>/ so A3 undo can restore it. The labels
    JSON moves with it so the restore is round-trip clean."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    ds_dir = DATASET_DIR / key
    ds_manifest_path = ds_dir / "manifest.json"
    if not ds_manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"no dataset manifest for {key!r}")
    ds_manifest = json.loads(ds_manifest_path.read_text())
    drawings = ds_manifest.get("drawings", [])
    target_entry = next((d for d in drawings if d.get("file") == file), None)
    if target_entry is None:
        raise HTTPException(status_code=404, detail=f"scene {file!r} not in dataset manifest")
    drawings = [d for d in drawings if d.get("file") != file]
    ds_manifest["drawings"] = drawings
    atomic_write_json(ds_manifest_path, ds_manifest)

    # A3 recycle bin
    _purge_old_recycle()
    recycle_dir = _safe_recycle_path(key, file)
    recycle_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(recycle_dir / "manifest_entry.json", target_entry)
    import shutil
    img = ds_dir / file
    if img.exists():
        shutil.move(str(img), str(recycle_dir / file))
    labels_file = ds_dir / "labels" / f"{Path(file).stem}.json"
    if labels_file.exists():
        shutil.move(str(labels_file), str(recycle_dir / "labels.json"))

    # Intake record
    intake = _read_manifest(key)
    intake_record = None
    if intake is not None:
        intake_record = next(
            (s for s in intake.get("extracted_scenes", []) if s.get("scene_file") == file),
            None,
        )
        intake["extracted_scenes"] = [
            s for s in intake.get("extracted_scenes", [])
            if s.get("scene_file") != file
        ]
        intake["state"] = _bundle_state(key, intake)
        _write_manifest(key, intake)
    if intake_record is not None:
        atomic_write_json(recycle_dir / "intake_record.json", intake_record)
    # G1-7 (agentic-labeling-followups-tracker): prune the scene's
    # facts row + re-derive the rest. The deleted scene may have
    # contributed to extent / openings_catalog; recompute picks up
    # the cascade. Non-fatal — the scene is already gone from disk.
    try:
        from .fact_derivation import prune_scene_from_facts
        prune_scene_from_facts(key, file, dataset_root=DATASET_DIR)
    except Exception:  # noqa: BLE001
        pass
    return None


@app.post("/pdfs/{key}/extract/{file}/restore", tags=["pdfs"])
def restore_extracted_scene(key: str, file: str):
    """A3 — restore a soft-deleted scene from the recycle bin. Looks for
    tmp/recycle/<key>/<file>/ and moves the contents back into the
    dataset + intake. 410 Gone if the bundle has been pruned."""
    _purge_old_recycle()
    recycle_dir = _safe_recycle_path(key, file)
    entry_path = recycle_dir / "manifest_entry.json"
    if not entry_path.exists():
        raise HTTPException(status_code=410, detail=f"recycle window expired for {file!r}")
    entry = json.loads(entry_path.read_text())
    ds_dir = DATASET_DIR / key
    ds_manifest_path = ds_dir / "manifest.json"
    if not ds_manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"no dataset manifest for {key!r}")
    ds_manifest = json.loads(ds_manifest_path.read_text())
    drawings = ds_manifest.get("drawings", [])
    # Avoid duplicates if the user managed to extract a same-named scene
    # in between delete and restore.
    if any(d.get("file") == file for d in drawings):
        raise HTTPException(status_code=409, detail=f"scene {file!r} already exists")
    drawings.append(entry)
    ds_manifest["drawings"] = drawings
    atomic_write_json(ds_manifest_path, ds_manifest)
    import shutil
    bundled_img = recycle_dir / file
    if bundled_img.exists():
        shutil.move(str(bundled_img), str(ds_dir / file))
    bundled_labels = recycle_dir / "labels.json"
    if bundled_labels.exists():
        (ds_dir / "labels").mkdir(parents=True, exist_ok=True)
        shutil.move(str(bundled_labels), str(ds_dir / "labels" / f"{Path(file).stem}.json"))
    bundled_intake = recycle_dir / "intake_record.json"
    if bundled_intake.exists():
        intake = _read_manifest(key)
        if intake is not None:
            intake.setdefault("extracted_scenes", []).append(json.loads(bundled_intake.read_text()))
            intake["state"] = _bundle_state(key, intake)
            _write_manifest(key, intake)
        bundled_intake.unlink()
    entry_path.unlink()
    try:
        recycle_dir.rmdir()
    except OSError:
        pass
    return _load_dataset_manifest(key)


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
