"""Pure export-readiness + completeness helpers (V5.2 / V5.3).

These build on the geometry contract that the V5.1 Wgeo gate already enforces
(`_REQUIRED_GEOMETRY` / `_missing_geometry`, defined in `mcp_server.py`). They
are PURE — no I/O, no rendering — so they unit-test deterministically on
synthetic scene dicts and are cheap to call as cross-checks during a run. The
vision-LLM still decides what to do with a failed check.

A `scene` dict here is the same shape the gate consumes, with optional extra
evidence fields the caller fills in from the live state:
    {scene_tag, file, labels: [{type, ...}], calibrated: bool,
     orientation: str|None, observation_count: int}

`bezug_mm` (the house datum, heights.bezug_mm) is a building-global fact, so it
is passed in rather than read per scene.
"""
from __future__ import annotations

# Scene types that sit on a datum and carry a cardinal orientation. Grundriss
# is included for the datum (OK-FFB) even though its compass is the plan north.
_ORIENTED_TAGS = {"grundriss", "schnitt", "ansicht"}


def _required_geometry():
    """Lazy import so this module stays import-cheap and avoids a load-time
    dependency cycle with mcp_server."""
    from mcp_server import _REQUIRED_GEOMETRY, _missing_geometry
    return _REQUIRED_GEOMETRY, _missing_geometry


def _label_types(labels) -> list[str]:
    out = []
    for lb in labels or []:
        t = lb.get("type") if isinstance(lb, dict) else None
        if t:
            out.append(t)
    return out


# ── V5.2 per-scene completeness scorecard ────────────────────────────────

def score_scene_completeness(scene: dict, *, bezug_mm: float | None = None) -> dict:
    """Per-scene scorecard the agent must cite before claiming a scene "done".

    Returns {file, scene_tag, checks, score, max, missing, complete}. `checks`
    is {criterion: True|False|None}; None = "not applicable to this scene
    type" and does NOT count toward score/max. Only applicable criteria are
    scored. Pure.

    Criteria:
      - geometry: required geometry kinds all present (V3.1 / V5.1 contract).
      - dims:     scene is calibrated (a real reference dim was read, V2).
      - bezug:    the house datum is known (V2.3) — for datum-bearing scenes.
      - compass:  orientation set (V4) — for _ORIENTED_TAGS only.
    crop_qa is intentionally NOT scored here: it is a bim-agent observation
    (cross-service), folded into the V7 run, as is the grade-observation log.
    """
    required_geometry, missing_geometry = _required_geometry()
    tag = scene.get("scene_tag") or "nicht_klassifiziert"
    labels = scene.get("labels", [])
    calibrated = bool(scene.get("calibrated", False))
    orientation = scene.get("orientation")

    checks: dict[str, bool | None] = {}

    if tag in required_geometry:
        checks["geometry"] = not missing_geometry(tag, _label_types(labels))
    else:
        checks["geometry"] = None

    checks["dims"] = calibrated if tag != "nicht_klassifiziert" else None
    checks["bezug"] = (bezug_mm is not None) if tag in _ORIENTED_TAGS else None
    checks["compass"] = (orientation is not None) if tag in _ORIENTED_TAGS else None

    applicable = {k: v for k, v in checks.items() if v is not None}
    score = sum(1 for v in applicable.values() if v)
    maximum = len(applicable)
    missing = sorted(k for k, v in applicable.items() if not v)
    return {
        "file": scene.get("file"),
        "scene_tag": tag,
        "checks": checks,
        "score": score,
        "max": maximum,
        "missing": missing,
        "complete": maximum > 0 and score == maximum,
    }


def score_house_completeness(scenes: list[dict], *, bezug_mm: float | None = None) -> dict:
    """House-level V5.2 scorecard: per-scene cards + a rolled-up honest score.

    Returns {per_scene, house_score, house_max, complete_scenes, scene_count}.
    house_score/house_max sum the per-scene APPLICABLE criteria so the agent
    can cite one honest number.
    """
    cards = [score_scene_completeness(sc, bezug_mm=bezug_mm) for sc in scenes]
    return {
        "per_scene": cards,
        "house_score": sum(c["score"] for c in cards),
        "house_max": sum(c["max"] for c in cards),
        "complete_scenes": sum(1 for c in cards if c["complete"]),
        "scene_count": len(scenes),
    }


# ── V5.3 zero-observation defect ─────────────────────────────────────────

def unobserved_labeled_scenes(scenes: list[dict]) -> list[dict]:
    """Scenes that carry labels but logged ZERO observations.

    Silent work is a defect: labels were placed but there is no evidence of
    how those values were derived/verified. `observation_count` is provided by
    the caller (fetched from the bim-agent log API) to keep this pure. A scene
    with no labels is not a defect (nothing was claimed). Returns
    [{file, scene_tag, label_count}]. Pure.
    """
    bad = []
    for sc in scenes:
        labels = sc.get("labels", [])
        obs = int(sc.get("observation_count", 0) or 0)
        if labels and obs == 0:
            bad.append({
                "file": sc.get("file"),
                "scene_tag": sc.get("scene_tag") or "nicht_klassifiziert",
                "label_count": len(labels),
            })
    return bad


def export_readiness(scenes: list[dict]) -> dict:
    """Pure house-level gate combining the V5.1 geometry contract with the
    V5.3 zero-observation defect.

    Ready iff every geometry-bearing scene is geometry-complete AND no labeled
    scene is silent. A house with no geometry-bearing scenes is NOT ready
    (nothing to export).

    `observation_count` is optional: when NO scene carries it, the
    zero-observation check is a no-op (callers that don't wire the bim-agent
    log API see geometry-only behaviour); when present, a labeled-but-
    unobserved scene blocks export with a `["observations"]` blocker.

    Returns {ready, blockers, scene_count, geometry_bearing_scenes,
             silent_scenes}.
    """
    required_geometry, missing_geometry = _required_geometry()
    blockers = []
    geometry_bearing = 0
    obs_wired = any("observation_count" in sc for sc in scenes)
    for sc in scenes:
        tag = sc.get("scene_tag") or "nicht_klassifiziert"
        if tag in required_geometry:
            geometry_bearing += 1
            miss = missing_geometry(tag, _label_types(sc.get("labels", [])))
            if miss:
                blockers.append({"file": sc.get("file"), "scene_tag": tag,
                                 "missing": miss})
    silent = unobserved_labeled_scenes(scenes) if obs_wired else []
    for s in silent:
        blockers.append({"file": s["file"], "scene_tag": s["scene_tag"],
                         "missing": ["observations"]})
    return {
        "ready": geometry_bearing > 0 and not blockers,
        "blockers": blockers,
        "scene_count": len(scenes),
        "geometry_bearing_scenes": geometry_bearing,
        "silent_scenes": silent,
    }
