"""Grid-render, computer-vision, and wall/dimension-geometry routes (H5).

Extracted verbatim from api/main.py (the grid / wall-corner / wall-outline /
score / dimension / opening-candidate / topology / propose-edit / resolve-
point family) to shrink that god file. Registered on an APIRouter that
api/main.py includes before the SPA catch-all; shared helpers/config stay in
api.main and are imported here. Behavior and URL shapes are unchanged.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, Response

from .wall_score import WALL_SCORE_DEFAULTS

from .main import (
    DATASET_DIR,
    GRID_CACHE,
    _as_point,
    _ensure_dataset_scene,
    _load_dataset_manifest,
    _parse_background_opacity,
    _parse_contrast,
    _parse_enhance,
    _parse_format,
    _parse_grid_style,
    _parse_label_render_style,
    _parse_region,
    _parse_show_height_guides,
    _parse_show_openings,
    _parse_show_relations,
    _parse_target,
    _parse_target_line,
    _parse_tiers,
    _plan_http_error,
    _safe_key,
    _safe_label_path,
    _save_grid_png,
    _scene_image_path,
    _scene_px_per_mm,
    _wall_ink_overlap,
    _wall_label_id,
    _wall_segment,
    get_labels,
    put_labels,
)

router = APIRouter()


@router.get("/datasets/{key}/{file}/grid", tags=["pdfs"])
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
) -> Response:
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

@router.get("/datasets/{key}/{file}/wall-corners", tags=["pdfs"])
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


@router.get("/datasets/{key}/{file}/check-corner", tags=["pdfs"])
def check_corner_route(
    key: str,
    file: str,
    x: int,
    y: int,
    search_px: int = 40,
    min_wall_px: int = 8,
) -> dict:
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


@router.get("/datasets/{key}/{file}/wall-outline", tags=["pdfs"])
def wall_outline(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 8,
    thresh: int | None = None,
    n_outlines: int = 2,
    epsilon_px: float = 8.0,
) -> dict:
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


@router.get("/datasets/{key}/{file}/refine-wall", tags=["pdfs"])
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
) -> dict:
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


@router.post("/datasets/{key}/{file}/wall-labels/anchored", tags=["pdfs"])
def upsert_wall_anchored_route(
    key: str,
    file: str,
    body: dict[str, Any] = Body(...),
) -> dict:
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
    tol_px = int(anchor.get("tol_px", WALL_SCORE_DEFAULTS["tol_px"]))
    min_wall_px = int(anchor.get("min_wall_px", WALL_SCORE_DEFAULTS["min_wall_px"]))
    close_px = int(anchor.get("close_px", WALL_SCORE_DEFAULTS["close_px"]))
    snap_corners = bool(anchor.get("snap_corners", False))
    status_if_unanchored = str(body.get("status_if_unanchored") or "reject")
    evidence_id = body.get("evidence_id")
    try:
        from .scene_plan_state import preflight_label_write
        plan_preflight = preflight_label_write(
            DATASET_DIR,
            key,
            file,
            ["wall"],
            tool="upsert_wall_anchored",
            allow_override=bool(body.get("allow_plan_order_override", False)),
            override_reason=body.get("override_reason"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)

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
    endpoint_reasons = candidate.get("endpoint_reasons")
    if isinstance(endpoint_reasons, dict):
        if endpoint_reasons.get("start"):
            label["attributes"]["endpoint_reason_start"] = endpoint_reasons.get("start")
        if endpoint_reasons.get("end"):
            label["attributes"]["endpoint_reason_end"] = endpoint_reasons.get("end")
    elif not (candidate.get("attributes") or {}).get("mass_id"):
        label["attributes"]["endpoint_reason_start"] = "missing"
        label["attributes"]["endpoint_reason_end"] = "missing"
        data.setdefault("warnings", []).append(
            "manual wall detail write has no endpoint_reasons; classify endpoints before downstream QA"
        )
    labels = doc.setdefault("labels", [])
    before_doc = dict(doc)
    before_doc["labels"] = list(labels)
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
    data["plan_preflight"] = plan_preflight
    if not (label["attributes"].get("mass_id") or (endpoint_reasons or {}).get("separate_mass")):
        try:
            from .wall_topology import wall_topology_qa
            before_topo = wall_topology_qa(before_doc.get("labels") or []) or {}
            after_topo = wall_topology_qa(doc.get("labels") or []) or {}
            before_components = len(before_topo.get("components") or [])
            after_components = len(after_topo.get("components") or [])
            if after_components > before_components and before_components > 0:
                data.setdefault("warnings", []).append(
                    "manual wall write created a new disconnected component; mark endpoint_reasons.separate_mass=true or use a mass transaction"
                )
                data["disconnected_component_warning"] = {
                    "before_components": before_components,
                    "after_components": after_components,
                }
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "data": data}


def _mass_edges_from_rect(body: dict[str, Any]) -> list[list[list[float]]]:
    corners = body.get("rough_corners")
    if corners is not None:
        if not isinstance(corners, list) or len(corners) != 4:
            raise ValueError("rough_corners must be four [x,y] points")
        pts = [[float(p[0]), float(p[1])] for p in corners]
    else:
        bbox = body.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("provide bbox [x0,y0,x1,y1] or rough_corners")
        x0, y0, x1, y1 = [float(v) for v in bbox]
        pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return [[pts[i], pts[(i + 1) % 4]] for i in range(4)]


def _mass_edges_from_vertices(body: dict[str, Any]) -> list[list[list[float]]]:
    vertices = body.get("ordered_vertices")
    if not isinstance(vertices, list) or len(vertices) < 4:
        raise ValueError("ordered_vertices must contain at least four [x,y] points")
    pts = [[float(p[0]), float(p[1])] for p in vertices]
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 4:
        raise ValueError("ordered_vertices must contain at least four distinct points")
    return [[pts[i], pts[(i + 1) % len(pts)]] for i in range(len(pts))]


def _upsert_mass_walls(
    *,
    key: str,
    file: str,
    source_edges: list[list[list[float]]],
    body: dict[str, Any],
    tool: str,
) -> dict[str, Any]:
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    import hashlib
    edge_digest = hashlib.sha1(json.dumps(source_edges, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    mass_id = str(body.get("mass_id") or body.get("label_group") or f"mass-{edge_digest}")
    mass_kind = str(body.get("kind") or "other")
    edge_policy = str(body.get("edge_policy") or "refine_to_ink")
    if edge_policy not in {"refine_to_ink", "use_given", "mixed"}:
        raise HTTPException(status_code=400, detail="edge_policy must be refine_to_ink, use_given, or mixed")
    min_confidence = float(body.get("min_confidence", 0.55))
    search_px = int(body.get("search_px", 32))
    n_samples = int(body.get("n_samples", 31))
    thickness_mm = body.get("thickness_mm")
    excluded_edges = set(int(i) for i in (body.get("excluded_edges") or []))
    try:
        from .scene_plan_state import preflight_label_write
        plan_preflight = preflight_label_write(
            DATASET_DIR,
            key,
            file,
            ["wall"],
            tool=tool,
            allow_override=bool(body.get("allow_plan_order_override", False)),
            override_reason=body.get("override_reason"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)

    from PIL import Image as PILImage
    from .wall_refine import refine_segment
    from .wall_geometry import connect_corners

    fitted_edges = []
    edge_reports = []
    with PILImage.open(img_path) as src_img:
        src = src_img.convert("RGB")
        for idx, edge in enumerate(source_edges):
            start = _as_point(edge[0])
            end = _as_point(edge[1])
            if start is None or end is None:
                raise HTTPException(status_code=400, detail=f"edge {idx} must be [[x,y],[x,y]]")
            excluded = idx in excluded_edges
            if edge_policy == "use_given" or excluded:
                refined_start, refined_end = start, end
                confidence = 1.0 if not excluded else 0.0
                thickness_px = None
            else:
                refined = refine_segment(src, start, end, search_px=search_px, n_samples=n_samples)
                refined_start = _as_point(refined.get("start")) or start
                refined_end = _as_point(refined.get("end")) or end
                confidence = float(refined.get("confidence") or 0.0)
                thickness_px = refined.get("thickness_px")
            fitted_edges.append((refined_start, refined_end))
            edge_reports.append({
                "edge_index": idx,
                "source": [[start[0], start[1]], [end[0], end[1]]],
                "fitted": [[refined_start[0], refined_start[1]], [refined_end[0], refined_end[1]]],
                "confidence": round(confidence, 3),
                "thickness_px": thickness_px,
                "accepted": (not excluded) and (edge_policy == "use_given" or confidence >= min_confidence),
                "excluded": excluded,
            })
    connected = connect_corners(fitted_edges, closed=True)
    doc = get_labels("dataset", key, file)
    labels = doc.setdefault("labels", [])
    existing_by_edge = {
        int((lab.get("attributes") or {}).get("mass_edge_index")): lab
        for lab in labels
        if lab.get("type") == "wall"
        and (lab.get("attributes") or {}).get("mass_id") == mass_id
        and isinstance((lab.get("attributes") or {}).get("mass_edge_index"), int)
    }
    changed_ids: list[str] = []
    wall_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for idx, wall in enumerate(connected):
        if idx in excluded_edges:
            continue
        existing = existing_by_edge.get(idx)
        label_id = str((existing or {}).get("id") or _wall_label_id())
        accepted = bool(edge_reports[idx]["accepted"])
        attrs = {
            **((existing or {}).get("attributes") or {}),
            "mass_id": mass_id,
            "mass_kind": mass_kind,
            "mass_tool": tool,
            "mass_edge_index": idx,
            "mass_edge_count": len(source_edges),
            "mass_role": "exterior",
            "edge_confidence": edge_reports[idx]["confidence"],
            "endpoint_reason_start": "mass_corner",
            "endpoint_reason_end": "mass_corner",
        }
        if thickness_mm is not None:
            attrs["thickness_mm"] = thickness_mm
        label = {
            "id": label_id,
            "type": "wall",
            "status": "readable" if accepted else "uncertain",
            "geometry": {"start": [wall[0][0], wall[0][1]], "end": [wall[1][0], wall[1][1]]},
            "attributes": attrs,
        }
        pos = next((i for i, lab in enumerate(labels) if lab.get("id") == label_id), None)
        if pos is None:
            labels.append(label)
        else:
            labels[pos] = label
        changed_ids.append(label_id)
        wall_segments.append(wall)
    put_labels("dataset", key, file, doc)

    score_summary: dict[str, Any] = {}
    topology_summary: dict[str, Any] = {}
    try:
        from .wall_score import score_walls
        with PILImage.open(img_path) as src_img:
            score = score_walls(src_img.convert("RGB"), wall_segments, min_wall_px=8, tol_px=12, close_px=40)
        score_summary = {k: score.get(k) for k in ("precision", "recall", "f1", "missing_region_count", "off_ink_count")}
    except Exception as e:  # noqa: BLE001
        score_summary = {"error": str(e)}
    try:
        from .wall_topology import wall_topology_qa
        topo = wall_topology_qa(doc.get("labels") or [])
        topology_summary = {
            "connected_components": len(topo.get("components") or []),
            "dangling_endpoints": len(topo.get("dangling_endpoints") or []),
            "near_miss_corners": len(topo.get("near_miss_corners") or []),
        }
    except Exception as e:  # noqa: BLE001
        topology_summary = {"error": str(e)}
    rejected = [e for e in edge_reports if not e["accepted"] and not e["excluded"]]
    warnings = []
    if rejected:
        warnings.append(f"{len(rejected)} edge(s) persisted uncertain due to low refine confidence")
    return {
        "mass_contract": "wall-mass-transaction/v1",
        "tool": tool,
        "mass_id": mass_id,
        "mass_kind": mass_kind,
        "edge_policy": edge_policy,
        "wall_label_ids": changed_ids,
        "changed_label_ids": changed_ids,
        "edge_reports": edge_reports,
        "rejected_edges": rejected,
        "warnings": warnings,
        "score_summary": score_summary,
        "topology_summary": topology_summary,
        "plan_preflight": plan_preflight,
    }


@router.post("/datasets/{key}/{file}/wall-masses/rect", tags=["pdfs"])
def upsert_rect_mass_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    """Upsert one rectangular exterior mass as four grouped wall labels."""
    try:
        edges = _mass_edges_from_rect(body)
        data = _upsert_mass_walls(key=key, file=file, source_edges=edges, body=body, tool="upsert_rect_mass")
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/wall-masses/stepped", tags=["pdfs"])
def upsert_stepped_mass_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    """Upsert one ordered rectilinear/stepped exterior mass as grouped walls."""
    try:
        edges = _mass_edges_from_vertices(body)
        data = _upsert_mass_walls(key=key, file=file, source_edges=edges, body=body, tool="upsert_stepped_mass")
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/wall-labels/anchoring-check", tags=["pdfs"])
def wall_label_anchoring_check_route(
    key: str,
    file: str,
    body: dict[str, Any] = Body(...),
) -> dict:
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
            min_wall_px=int(body.get("min_wall_px", WALL_SCORE_DEFAULTS["min_wall_px"])),
            tol_px=int(body.get("tol_px", WALL_SCORE_DEFAULTS["tol_px"])),
            thin_aware=bool(body.get("thin_aware", True)),
            close_px=int(body.get("close_px", WALL_SCORE_DEFAULTS["close_px"])),
        )
    data.update({
        "anchoring_status": data["status"],
        "recommended_tool": "upsert_wall_anchored",
        "must_verify_before_downstream": data["status"] == "off_ink",
    })
    return {"ok": True, "data": data}


@router.get("/datasets/{key}/{file}/score-walls", tags=["pdfs"])
def score_walls_route(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 8,
    tol_px: int = 9,
    thresh: int | None = None,
    thin_aware: bool = False,
    close_px: int = 0,
) -> dict:
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


@router.get("/datasets/{key}/{file}/score-measurements", tags=["pdfs"])
def score_measurements_route(
    key: str,
    file: str,
    tol_px: int = 8,
    axis_tol_px: int = 14,
) -> dict:
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


@router.get("/datasets/{key}/{file}/dimension-chain-candidates", tags=["pdfs"])
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
) -> dict:
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


@router.get("/datasets/{key}/{file}/dimension-station-graph", tags=["pdfs"])
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
) -> dict:
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


@router.get("/datasets/{key}/{file}/opening-candidates", tags=["pdfs"])
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
) -> dict:
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


@router.get("/datasets/{key}/{file}/view-geometry-candidates", tags=["pdfs"])
def view_geometry_candidates_route(
    key: str,
    file: str,
    region: str | None = None,
    thresh: int = 185,
    min_line_px: int = 80,
    min_rect_px: int = 18,
    max_candidates: int = 40,
) -> dict:
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


@router.get("/datasets/{key}/{file}/opening-candidates/{candidate_id}/overlay", tags=["pdfs"])
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


@router.post("/datasets/{key}/{file}/opening-candidates/{candidate_id}/apply", tags=["pdfs"])
def apply_opening_candidate_route(key: str, file: str, candidate_id: str, body: dict[str, Any] = Body(default={})) -> dict:
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    labels_doc = get_labels("dataset", key, file)
    try:
        from .scene_plan_state import preflight_label_write
        plan_preflight = preflight_label_write(
            DATASET_DIR,
            key,
            file,
            ["floorplan_opening"],
            tool="apply_opening_candidate",
            allow_override=bool(body.get("allow_plan_order_override", False)),
            override_reason=body.get("override_reason"),
        )
        candidate = _find_opening_candidate(labels_doc, img_path, candidate_id)
        if body.get("expected_candidate_kind") and body.get("expected_candidate_kind") != candidate.get("kind"):
            raise ValueError("candidate kind changed; refresh opening candidates")
        if body.get("expected_candidate_fingerprint") and body.get("expected_candidate_fingerprint") != candidate.get("candidate_fingerprint"):
            raise ValueError("candidate fingerprint changed; refresh opening candidates")
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
            "plan_preflight": plan_preflight,
        }
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/opening-candidates/{candidate_id}/decision", tags=["pdfs"])
def decide_opening_candidate_route(key: str, file: str, candidate_id: str, body: dict[str, Any] = Body(...)) -> dict:
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
        if body.get("expected_candidate_fingerprint") and body.get("expected_candidate_fingerprint") != candidate.get("candidate_fingerprint"):
            raise ValueError("candidate fingerprint changed; refresh opening candidates")
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


@router.get("/datasets/{key}/{file}/building-silhouette", tags=["pdfs"])
def building_silhouette_route(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 16,
    thresh: int | None = None,
    angle_tol_deg: float = 18.0,
    min_area_frac: float = 0.02,
) -> dict:
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


@router.get("/datasets/{key}/{file}/outer-wall-topology-context", tags=["pdfs"])
def outer_wall_topology_context_route(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 12,
    thresh: int | None = None,
) -> dict:
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


@router.get("/datasets/{key}/{file}/wall-topology-qa", tags=["pdfs"])
def wall_topology_qa_route(
    key: str,
    file: str,
    endpoint_tol_px: float = 18.0,
    near_miss_px: float = 60.0,
    collinear_tol_deg: float = 8.0,
    collinear_gap_px: float = 140.0,
    short_stub_px: float = 80.0,
) -> dict:
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


@router.get("/datasets/{key}/{file}/wall-continuity-check", tags=["pdfs"])
def wall_continuity_check_route(
    key: str,
    file: str,
    collinear_tol_deg: float = 8.0,
    gap_px: float = 180.0,
    line_tol_px: float = 24.0,
    opening_near_px: float = 80.0,
) -> dict:
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


@router.get("/datasets/{key}/{file}/ambiguous-line-context", tags=["pdfs"])
def ambiguous_line_context_route(
    key: str,
    file: str,
    bbox: str | None = None,
    line: str | None = None,
    pad_px: float = 120.0,
) -> dict:
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


@router.post("/datasets/{key}/{file}/propose-wall-edit", tags=["pdfs"])
def propose_wall_edit_route(
    key: str,
    file: str,
    body: dict[str, Any] = Body(...),
) -> dict:
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
        try:
            from .scene_plan_state import preflight_label_write
            res["plan_preflight"] = preflight_label_write(
                DATASET_DIR,
                key,
                file,
                ["wall"],
                tool="propose_wall_edit",
                allow_override=bool(body.get("allow_plan_order_override", False)),
                override_reason=body.get("override_reason"),
            )
        except Exception as e:  # noqa: BLE001
            _plan_http_error(e)
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


@router.post("/geometry/connect-corners", tags=["pdfs"])
def connect_corners_route(body: dict[str, Any] = Body(...)) -> dict:
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


@router.get("/datasets/{key}/{file}/grid-with-labels", tags=["pdfs"])
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
) -> Response:
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


@router.get("/datasets/{key}/{file}/resolve-point", tags=["pdfs"])
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
