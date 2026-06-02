"""Export + export-preview routes (H5).

Extracted from api/main.py: /exports/{key}, POST /exports (batch), and
/exports/{key}/{file}/preview plus their helpers (_export_one_house,
_sanity_check_house, _load_scene_labels, _persist_scene_calibration).
Registered on an APIRouter included by api/main.py before the SPA catch-all.
Shared helpers/config stay in api.main and are imported here. Behavior and
URL shapes are unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from . import main
from .persistence import atomic_write_json, atomic_write_text, locked_path
from .main import (
    BASE,
    DATASET_DIR,
    EXPORTS_DIR,
    EXPORT_CACHE,
    HOUSE_FACTS_DUMP_NOTE,
    SET_A_TYPES,
    _load_dataset_manifest,
    _now_iso,
    _safe_key,
    _safe_label_path,
    _scene_image_path,
)

router = APIRouter()


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


@router.post("/exports/{key}", tags=["exports"], status_code=201)
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


@router.post("/exports", tags=["exports"], status_code=201)
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


@router.post("/exports/{key}/{file}/preview", tags=["exports"])
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
