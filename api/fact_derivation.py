"""Server-side derivation of `house_facts.json` from scene labels.

Per agentic-labeling-followups-tracker §G1 + §2 architectural decision
D1: labels JSON is canonical input; HouseFacts is derived. Anyone who
writes labels (SPA OR MCP) triggers a fact recompute via
`recompute_facts_after_label_write(key)` — single source of truth.

This is a Python port of the SPA's
`ui/src/lib/house_facts.ts:promoteToFacts()` +
`computeSceneCalibration()` +
`ui/src/lib/building_dims.ts:dimOrientation()`. Behaviour must match
the TS exactly so the SPA and MCP paths produce identical facts.

Pure functions where practical; the I/O entrypoint
`recompute_facts_after_label_write` reads from disk + writes to disk.

Strict mode (env var `HOUSE_FACTS_STRICT=1`, defaults off): when set,
`recompute_facts_after_label_write` refuses to populate
`facts.heights.bezug_mm`/`first_mm` unless matching `height_mark`
labels exist. Off by default so the SPA flow is unchanged.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any


# ── Pure helpers ──────────────────────────────────────────────────────────


def dim_orientation(start: list[float], end: list[float]) -> str | None:
    """Same H/V buckets the SPA uses (within ±15° of horizontal or
    ±15° of vertical). Returns "horizontal" / "vertical" / None.
    """
    if len(start) < 2 or len(end) < 2:
        return None
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return None
    a = abs(math.degrees(math.atan2(dy, dx)))
    if a < 15 or a > 165:
        return "horizontal"
    if 75 < a < 105:
        return "vertical"
    return None


def compute_scene_calibration(labels: list[dict]) -> dict | None:
    """px_per_mm from M1 reference dimensioned_distances.

    Returns `{px_per_mm, computed_from}` or None.
      - "M1-both"      — both H + V references present (average)
      - "M1-H-Bezug"   — horizontal only
      - "M1-V-Bezug"   — vertical only
    """
    h_calib: float | None = None
    v_calib: float | None = None
    for lab in labels:
        if lab.get("type") != "dimensioned_distance":
            continue
        attrs = lab.get("attributes") or {}
        if not attrs.get("is_reference"):
            continue
        value_mm = attrs.get("value_mm")
        if value_mm is None or value_mm <= 0:
            continue
        geom = lab.get("geometry") or {}
        start = geom.get("start") or [0, 0]
        end = geom.get("end") or [0, 0]
        orient = dim_orientation(start, end)
        if orient is None:
            continue
        len_px = math.hypot(end[0] - start[0], end[1] - start[1])
        if len_px < 1:
            continue
        px_per_mm = len_px / value_mm
        if orient == "horizontal":
            h_calib = px_per_mm
        else:
            v_calib = px_per_mm
    if h_calib is not None and v_calib is not None:
        return {"px_per_mm": (h_calib + v_calib) / 2.0, "computed_from": "M1-both"}
    if h_calib is not None:
        return {"px_per_mm": h_calib, "computed_from": "M1-H-Bezug"}
    if v_calib is not None:
        return {"px_per_mm": v_calib, "computed_from": "M1-V-Bezug"}
    return None


def derive_scene_metadata_entry(
    labels_json: dict,
    *,
    image_size_px: tuple[int, int] | None = None,
) -> dict:
    """Project a scene's labels JSON into a single SceneMetadataEntry.

    Mirrors the SPA's `promoteToFacts` for the scene_metadata section.
    `image_size_px` falls back to the labels JSON's stored value.

    Per agentic-labeling-followups-tracker §G6: writes ONLY `scene_tag`
    (the renamed canonical field). The legacy `kind` is migrated away
    by `_migrate_v1_0_facts` on read. Pre-launch single-pass rename.
    """
    size = image_size_px or tuple(labels_json.get("image_size_px") or (0, 0))
    scene_tag = labels_json.get("scene_tag") or "nicht_klassifiziert"
    return {
        "scene_tag": scene_tag,
        "orientation": labels_json.get("scene_orientation"),
        "level": labels_json.get("scene_level"),
        "image_size_px": list(size),
    }


def _migrate_v1_0_facts(facts: dict) -> dict:
    """v1.0 → v1.1 migration. Renames `scene_metadata[file].kind` →
    `scene_metadata[file].scene_tag`. Idempotent: a v1.1 dict passes
    through unchanged."""
    if facts.get("schema_version") == "1.1":
        return facts
    sm = facts.get("scene_metadata") or {}
    for f, entry in sm.items():
        if not isinstance(entry, dict):
            continue
        if "scene_tag" not in entry and "kind" in entry:
            entry["scene_tag"] = entry.pop("kind")
        else:
            entry.pop("kind", None)
    facts["schema_version"] = "1.1"
    return facts


def _add_source(sources: dict[str, list[str]], fact: str, src: str) -> None:
    bucket = sources.setdefault(fact, [])
    if src not in bucket:
        bucket.append(src)


# Datum → heights-key mapping. Levels matter only for ok_ffb.
def _height_key_for_datum(datum: str, scene_level: str | None) -> str | None:
    if datum == "first":
        return "first_mm"
    if datum == "traufe":
        return "traufe_mm"
    if datum == "gelaende":
        return "gelaende_mm"
    if datum == "sockel":
        return "sockel_mm"
    if datum == "kniestock":
        return "kniestock_mm"
    if datum == "geschoss":
        return "geschoss_mm"
    if datum == "ok_ffb":
        if scene_level == "og":
            return "ok_ffb_og_mm"
        if scene_level == "dg":
            return "ok_ffb_dg_mm"
        return "ok_ffb_eg_mm"
    return None


def promote_scene_to_facts(
    facts: dict,
    *,
    scene_file: str,
    labels_json: dict,
) -> dict:
    """Apply a single scene's labels to the facts dict. Mutates + returns.

    Mirrors SPA `promoteToFacts` in `ui/src/lib/house_facts.ts:266`.
    Idempotent for any given (scene_file, labels) pair.
    """
    facts.setdefault("schema_version", "1.0")
    facts.setdefault("extent", {"sources": {}})
    facts["extent"].setdefault("sources", {})
    facts.setdefault("heights", {"sources": {}})
    facts["heights"].setdefault("sources", {})
    facts.setdefault("wall_thickness", {})
    facts.setdefault("openings_catalog", [])
    facts.setdefault("calibration_per_scene", {})
    facts.setdefault("scene_metadata", {})

    scene_tag = labels_json.get("scene_tag") or "nicht_klassifiziert"
    scene_level = labels_json.get("scene_level")
    labels = labels_json.get("labels") or []
    src_prefix = f"{scene_file}#"

    # 1. Scene metadata (cheap, always promoted).
    facts["scene_metadata"][scene_file] = derive_scene_metadata_entry(
        labels_json,
        image_size_px=tuple(labels_json.get("image_size_px") or (0, 0)),
    )

    # 2. Calibration from M1 reference dims.
    calib = compute_scene_calibration(labels)
    if calib:
        facts["calibration_per_scene"][scene_file] = calib

    # 3. Extent from is_reference dims.
    for lab in labels:
        if lab.get("type") != "dimensioned_distance":
            continue
        attrs = lab.get("attributes") or {}
        if not attrs.get("is_reference"):
            continue
        value_mm = attrs.get("value_mm")
        if value_mm is None:
            continue
        geom = lab.get("geometry") or {}
        start = geom.get("start") or [0, 0]
        end = geom.get("end") or [0, 0]
        orient = dim_orientation(start, end)
        if orient is None:
            continue
        src = f"{src_prefix}dim:{lab.get('id', '')}"
        if orient == "horizontal":
            if scene_tag == "schnitt":
                current = facts["extent"].get("depth_mm")
                if current is None or value_mm > current:
                    facts["extent"]["depth_mm"] = value_mm
                _add_source(facts["extent"]["sources"], "depth_mm", src)
            else:
                current = facts["extent"].get("width_mm")
                if current is None or value_mm > current:
                    facts["extent"]["width_mm"] = value_mm
                _add_source(facts["extent"]["sources"], "width_mm", src)
        else:
            current = facts["extent"].get("height_mm")
            if current is None or value_mm > current:
                facts["extent"]["height_mm"] = value_mm
            _add_source(facts["extent"]["sources"], "height_mm", src)

    # 4. Heights from height_mark labels.
    bezug_y: float | None = None
    for lab in labels:
        if lab.get("type") != "height_mark":
            continue
        attrs = lab.get("attributes") or {}
        v = attrs.get("value_mm")
        if v is None:
            continue
        if v == 0:
            geom = lab.get("geometry") or {}
            anchor = geom.get("anchor") or [0, 0]
            if len(anchor) >= 2:
                bezug_y = anchor[1]
        datum = attrs.get("datum")
        if not datum or datum == "other":
            if v == 0:
                facts["heights"]["bezug_mm"] = 0
                _add_source(
                    facts["heights"]["sources"],
                    "bezug_mm",
                    f"{src_prefix}hm:{lab.get('id', '')}",
                )
            continue
        key = _height_key_for_datum(datum, scene_level)
        if not key:
            continue
        facts["heights"][key] = v
        _add_source(
            facts["heights"]["sources"], key, f"{src_prefix}hm:{lab.get('id', '')}"
        )
    if bezug_y is not None:
        meta = facts["scene_metadata"].setdefault(scene_file, {})
        meta["bezug_y_px"] = bezug_y

    # 5. Openings catalog — bucket by (kind, 50mm-rounded width).
    catalog_index: dict[str, dict] = {
        f"{o.get('kind')}-{o.get('width_mm')}": o
        for o in facts["openings_catalog"]
    }
    for lab in labels:
        t = lab.get("type")
        if t not in ("floorplan_opening", "view_opening"):
            continue
        attrs = lab.get("attributes") or {}
        kind = attrs.get("opening_kind") or "window"
        w = attrs.get("width_mm")
        if not isinstance(w, (int, float)) or w <= 0:
            continue
        bucket = round(w / 50) * 50
        ck = f"{kind}-{bucket}"
        entry = catalog_index.get(ck)
        if entry is None:
            entry = {"kind": kind, "width_mm": bucket, "instances": 0}
            facts["openings_catalog"].append(entry)
            catalog_index[ck] = entry
        entry["instances"] += 1

    return facts


# ── I/O entrypoint ────────────────────────────────────────────────────────


def recompute_facts_after_label_write(
    key: str,
    *,
    dataset_root: Path,
    strict: bool | None = None,
) -> dict:
    """Re-derive `house_facts.json` for one house from every scene's
    labels. Idempotent. Returns the new facts dict.

    Reads:  data/dataset/<key>/manifest.json + labels/*.json
    Writes: data/dataset/<key>/house_facts.json

    Honors existing house_facts: starts from the on-disk facts so any
    human-set fields (e.g. orientation set via the SPA) are preserved.
    Derived fields are recomputed from scratch and overwrite.

    If `strict` is None (default), reads env `HOUSE_FACTS_STRICT`.
    Strict mode refuses to keep `heights.bezug_mm` / `first_mm` that
    weren't produced by a matching height_mark label this pass; warns
    inline in the returned dict under `_derivation_warnings`.
    """
    if strict is None:
        strict = os.environ.get("HOUSE_FACTS_STRICT", "0").strip() not in ("", "0", "false")

    house_dir = dataset_root / key
    facts_path = house_dir / "house_facts.json"
    labels_dir = house_dir / "labels"
    manifest_path = house_dir / "manifest.json"

    # Load existing facts (preserve human-set fields like orientation).
    if facts_path.exists():
        try:
            facts = json.loads(facts_path.read_text())
        except json.JSONDecodeError:
            facts = {}
    else:
        facts = {}
    # G6: migrate v1.0 → v1.1 if needed (rename scene_metadata.kind → scene_tag).
    facts = _migrate_v1_0_facts(facts)
    # Defensive: ensure workflow has the bookkeeping sub-fields even if
    # the patch that created it (e.g. an agent's Step 0 driven_by stamp)
    # only set a few keys. Readers in ui/src/lib/workflow.ts crash on
    # undefined `phase_completed_at` / `user_skipped`.
    facts.setdefault("workflow", {})
    wf = facts["workflow"]
    if isinstance(wf, dict):
        wf.setdefault("schema_version", "1.1")
        wf.setdefault("phase", "inventory")
        wf.setdefault("phase_completed_at", {
            "inventory": None, "height_anchor": None, "footprint": None,
            "orientation": None, "bezugsmasse": None, "detail": None,
        })
        wf.setdefault("source_scene", {
            "inventory": None, "height_anchor": None, "footprint": None,
            "orientation": None, "bezugsmasse": None, "detail": None,
        })
        wf.setdefault("user_skipped", {})

    # Iterate scenes via the manifest (so deleted scenes are pruned).
    valid_files: set[str] = set()
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            for d in manifest.get("drawings") or []:
                if d.get("file"):
                    valid_files.add(d["file"])
        except json.JSONDecodeError:
            pass

    # ── Reset semantics — match the SPA's promoteToFacts behavior ────────
    # Scene-keyed maps: prune stale (no-longer-in-manifest) entries; the
    # remaining ones are re-derived from labels below.
    sm = facts.get("scene_metadata") or {}
    facts["scene_metadata"] = {f: v for f, v in sm.items() if f in valid_files}
    cps = facts.get("calibration_per_scene") or {}
    facts["calibration_per_scene"] = {f: v for f, v in cps.items() if f in valid_files}
    # Openings catalog is accumulative — wipe + rebuild from current labels.
    facts["openings_catalog"] = []
    # Extent + heights: DO NOT wipe. The SPA's promoteToFacts only upserts
    # (overwrites if the new value is larger, for extent; overwrites
    # by-datum-key for heights). Wiping would clobber any human-set value
    # the user typed into the SPA's form without dropping labels first.
    # We DO wipe the source-chains so they reflect only this pass.
    facts.setdefault("extent", {})
    facts["extent"]["sources"] = {}
    facts.setdefault("heights", {})
    facts["heights"]["sources"] = {}
    facts.setdefault("wall_thickness", {})

    if labels_dir.exists():
        for label_file in sorted(labels_dir.glob("*.json")):
            # Match labels to scene by stem.
            scene_file = next(
                (f for f in valid_files if Path(f).stem == label_file.stem),
                None,
            )
            if scene_file is None:
                # Stale label file (scene was deleted). Skip; don't promote.
                continue
            try:
                labels_json = json.loads(label_file.read_text())
            except json.JSONDecodeError:
                continue
            promote_scene_to_facts(
                facts,
                scene_file=scene_file,
                labels_json=labels_json,
            )

    # Strict mode: drop bare-facts heights that have no source-chain.
    warnings: list[str] = []
    if strict:
        sources = (facts.get("heights") or {}).get("sources") or {}
        for k in ("bezug_mm", "first_mm"):
            if k in facts["heights"] and not sources.get(k):
                warnings.append(
                    f"heights.{k} dropped (HOUSE_FACTS_STRICT=1; no matching height_mark label)"
                )
                facts["heights"].pop(k, None)
    if warnings:
        facts["_derivation_warnings"] = warnings
    else:
        facts.pop("_derivation_warnings", None)

    # Persist.
    house_dir.mkdir(parents=True, exist_ok=True)
    facts_path.write_text(json.dumps(facts, indent=2, ensure_ascii=False))
    return facts


def prune_scene_from_facts(
    key: str,
    scene_file: str,
    *,
    dataset_root: Path,
) -> None:
    """Drop the scene_metadata + calibration_per_scene entries for a
    deleted scene, then recompute extent/heights/openings from the
    remaining scenes (since the deleted scene may have contributed)."""
    house_dir = dataset_root / key
    facts_path = house_dir / "house_facts.json"
    if not facts_path.exists():
        return
    try:
        facts = json.loads(facts_path.read_text())
    except json.JSONDecodeError:
        return
    facts.get("scene_metadata", {}).pop(scene_file, None)
    facts.get("calibration_per_scene", {}).pop(scene_file, None)
    facts_path.write_text(json.dumps(facts, indent=2, ensure_ascii=False))
    # Full recompute picks up the cascade — extent could have been
    # derived from this scene's ref dims; let it re-derive from what's left.
    recompute_facts_after_label_write(key, dataset_root=dataset_root)
