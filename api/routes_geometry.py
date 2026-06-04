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
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, Response

from .region_contract import normalize_bbox_region
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

SEMANTIC_INK_CLASSES = {
    "structural_wall",
    "possible_wall",
    "opening_symbol",
    "dimension_annotation",
    "site_boundary",
    "furniture_fixture",
    "hatching_projection",
    "landscape_vehicle",
    "ignored_noise",
    "unknown",
}
NON_WALL_SEMANTIC_CLASSES = {
    "opening_symbol",
    "dimension_annotation",
    "site_boundary",
    "furniture_fixture",
    "hatching_projection",
    "landscape_vehicle",
    "ignored_noise",
}

VIEW_MODE_PRESETS: dict[str, dict[str, Any]] = {
    "analysis_view": {
        "tiers": "broad",
        "max_dim": 1600,
        "style": "standard",
        "background_opacity": 0.85,
        "clean": True,
        "contrast": "normal",
        "show_relations": "none",
        "show_openings": "outline",
    },
    "silhouette_view": {
        "tiers": "broad,finer",
        "max_dim": 1800,
        "style": "standard",
        "background_opacity": 0.9,
        "clean": True,
        "contrast": "high",
        "show_relations": "none",
        "show_openings": "hide",
    },
    "coordinate_pick_view": {
        "tiers": "broad,finer,detail",
        "max_dim": 1800,
        "style": "coordinate_multicolor",
        "background_opacity": 0.65,
        "clean": False,
        "contrast": "high",
        "show_relations": "required",
        "show_openings": "outline",
    },
    "edit_verify_view": {
        "tiers": "finer,detail",
        "max_dim": 1200,
        "style": "qa",
        "background_opacity": 0.25,
        "clean": True,
        "contrast": "high",
        "show_relations": "required",
        "show_openings": "outline",
    },
    "topology_qa_view": {
        "tiers": "broad",
        "max_dim": 1800,
        "style": "semantic",
        "background_opacity": 0.2,
        "clean": True,
        "contrast": "high",
        "show_relations": "required",
        "show_openings": "outline",
    },
    "measurement_read_view": {
        "tiers": "finer,detail",
        "max_dim": 1800,
        "style": "coordinate_multicolor",
        "background_opacity": 0.9,
        "clean": False,
        "contrast": "high",
        "show_relations": "none",
        "show_openings": "hide",
        "enhance": "auto",
    },
    "opening_candidate_view": {
        "tiers": "finer",
        "max_dim": 1400,
        "style": "ink_compare",
        "background_opacity": 0.2,
        "clean": True,
        "contrast": "high",
        "show_relations": "required",
        "show_openings": "full",
    },
    "final_overlay_view": {
        "tiers": "broad",
        "max_dim": 1800,
        "style": "semantic",
        "background_opacity": 0.25,
        "clean": True,
        "contrast": "high",
        "show_relations": "required",
        "show_openings": "full",
    },
}


def _view_mode_preset(view_mode: str | None) -> dict[str, Any]:
    mode = (view_mode or "").strip()
    if not mode:
        return {}
    if mode not in VIEW_MODE_PRESETS:
        raise HTTPException(status_code=400, detail=f"view_mode must be one of {sorted(VIEW_MODE_PRESETS)}")
    return dict(VIEW_MODE_PRESETS[mode])


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
    view_mode: str | None = None,
    grid: str | None = None,
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
    preset = _view_mode_preset(view_mode)
    tiers = str(preset.get("tiers", tiers))
    max_dim = int(preset.get("max_dim", max_dim))
    enhance = preset.get("enhance", enhance)
    format = str(preset.get("format", format)) if preset.get("format", format) is not None else None
    style = str(preset.get("style", style)) if preset.get("style", style) is not None else None
    if background_opacity is None and "background_opacity" in preset:
        background_opacity = float(preset["background_opacity"])
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
    # WS-B (legibility-first tracker): grid="none" renders a clean read crop
    # with no overlay mesh/labels/legend — the read verb uses it so a value is
    # taken off untouched pixels rather than a grid-occluded composite.
    draw_grid = (grid or "grid").lower() != "none"
    if not draw_grid:
        parsed_tiers = ()

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
        f"-vm{view_mode or 'raw'}"
        f"-g{target or 'none'}"
        f"-gl{parsed_target_line}"
        f"-o{parsed_opacity:g}x{int(opacity_explicit)}"
        f"-G{int(draw_grid)}"
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
                draw_grid=draw_grid,
            )
        _save_grid_png(overlay, out, parsed_format)
        sentinel.write_text(str(img_mtime))
    return FileResponse(str(out), media_type="image/png")


@router.get("/datasets/{key}/{file}/zoom", tags=["pdfs"])
def zoom_read_scene(
    key: str,
    file: str,
    region: str,
    dpi: int = 1000,
    enhance: str | None = None,
) -> Response:
    """WS-B (legibility-first tracker): re-render a SMALL scene region from the
    PDF VECTOR source at high DPI.

    A scene raster is fixed at its extraction DPI, so upscaling it adds no
    detail — this is the only path to *higher-than-native* legibility. The
    requested source-pixel region is mapped back to PDF units via the scene's
    `crop_from`, and just that rect is rasterized at `dpi` (<=1200). Lossless
    PNG, no grid. Use to read a marginal dimension number / confirm a faint
    wall edge. Bounded: small input region + bounded output so it can't bloat.
    """
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    from PIL import Image  # noqa: F401  (fitz pil_image needs PIL importable)

    from .grid_render import _enhance_image
    from .routes_pdf import MAX_EXTRACT_DPI, _consolidated_path, _render_crop
    from .segment import scene_px_dims, scene_px_to_pdf, validate_region_px

    if dpi <= 0 or dpi > MAX_EXTRACT_DPI:
        raise HTTPException(status_code=400, detail=f"dpi must be in (0, {MAX_EXTRACT_DPI}]")
    parsed_region = _parse_region(region)
    if parsed_region is None:
        raise HTTPException(status_code=400, detail="region 'x0,y0,x1,y1' required for zoom")
    parsed_enhance = _parse_enhance(enhance)

    manifest = _load_dataset_manifest(key)
    crop_from: dict | None = None
    for d in (manifest or {}).get("drawings") or []:
        if d.get("file") == file:
            crop_from = d.get("crop_from") or {}
            break
    if not crop_from or not crop_from.get("bbox_pdf_units") or not crop_from.get("page"):
        raise HTTPException(
            status_code=400,
            detail=f"scene {file!r} has no PDF crop_from — cannot zoom (re-extract it first)",
        )
    parent_bbox = crop_from["bbox_pdf_units"]
    parent_dpi = int(crop_from.get("dpi") or 0)
    page_n = int(crop_from["page"])
    if parent_dpi <= 0:
        raise HTTPException(status_code=400, detail=f"scene {file!r} crop_from missing dpi")

    px0, py0, px1, py1 = parsed_region
    parent_w, parent_h = scene_px_dims(parent_bbox, parent_dpi)
    err = validate_region_px([px0, py0, px1, py1], (parent_w, parent_h))
    if err:
        raise HTTPException(status_code=400, detail=err)

    long_edge = max(px1 - px0, py1 - py0)
    ZOOM_MAX_SRC_EDGE = 1500
    if long_edge > ZOOM_MAX_SRC_EDGE:
        raise HTTPException(status_code=400, detail=(
            f"zoom region long edge {int(long_edge)}px > {ZOOM_MAX_SRC_EDGE}px. "
            "zoom_read magnifies a SMALL feature — crop to one dim number / wall edge "
            "(use read for a larger native crop)."
        ))
    out_long = long_edge * (dpi / parent_dpi)
    ZOOM_MAX_OUT_EDGE = 3500
    if out_long > ZOOM_MAX_OUT_EDGE:
        raise HTTPException(status_code=400, detail=(
            f"zoom output would be ~{int(out_long)}px long edge (> {ZOOM_MAX_OUT_EDGE}). "
            "lower dpi or crop tighter so the read stays bounded."
        ))

    pdf_bbox = scene_px_to_pdf([px0, py0, px1, py1], parent_bbox, parent_dpi)
    pdf = _consolidated_path(key)
    pdf_mtime = pdf.stat().st_mtime_ns
    cache_root = GRID_CACHE / "zoom" / key
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_name = f"{Path(file).stem}-r{region}-d{dpi}-e{parsed_enhance}.png"
    out = cache_root / cache_name
    sentinel = out.with_suffix(".mtime")
    if not out.exists() or not sentinel.exists() or sentinel.read_text() != str(pdf_mtime):
        import fitz
        with fitz.open(pdf) as doc:
            if page_n < 1 or page_n > doc.page_count:
                raise HTTPException(status_code=404, detail=f"page {page_n} out of range")
            page = doc.load_page(page_n - 1)
            img = _render_crop(page, pdf_bbox, dpi)
        if parsed_enhance and parsed_enhance != "none":
            img = _enhance_image(img, parsed_enhance)
        img.save(str(out), format="PNG", optimize=True)
        sentinel.write_text(str(pdf_mtime))
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
    detail_mode = body.get("detail_mode")
    if detail_mode is not None:
        detail_mode = str(detail_mode)
        if detail_mode not in {"detail_refinement", "mass_exception", "repair_candidate"}:
            raise HTTPException(status_code=400, detail="detail_mode must be detail_refinement, mass_exception, or repair_candidate")
        if not evidence_id:
            raise HTTPException(status_code=400, detail="detail_mode requires evidence_id")
        endpoint_reasons = candidate.get("endpoint_reasons")
        attrs = candidate.get("attributes") or {}
        has_start_reason = isinstance(endpoint_reasons, dict) and bool(endpoint_reasons.get("start"))
        has_end_reason = isinstance(endpoint_reasons, dict) and bool(endpoint_reasons.get("end"))
        has_start_reason = has_start_reason or bool(attrs.get("endpoint_reason_start"))
        has_end_reason = has_end_reason or bool(attrs.get("endpoint_reason_end"))
        if not (attrs.get("mass_id") or (has_start_reason and has_end_reason)):
            raise HTTPException(status_code=400, detail="detail_mode requires endpoint_reasons.start/end or existing endpoint reason attributes")
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
        "detail_mode": detail_mode,
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
    if detail_mode is not None:
        attrs["detail_mode"] = detail_mode
    label = {
        "id": label_id,
        "type": "wall",
        "status": status,
        "geometry": {"start": [refined_start[0], refined_start[1]], "end": [refined_end[0], refined_end[1]]},
        "attributes": attrs,
    }
    for provenance_key in ("run_id", "agent_id", "subagent_id"):
        if body.get(provenance_key) is not None:
            label[provenance_key] = body.get(provenance_key)
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


@router.post("/datasets/{key}/{file}/walls/{label_id}/centerline-review", tags=["pdfs"])
def review_wall_centerline_route(
    key: str,
    file: str,
    label_id: str,
    body: dict[str, Any] = Body(...),
) -> dict:
    """Mark a scorer-off-ink wall as reviewed centerline-plausible.

    This is for faint/double-rail floorplan walls where the saved wall is the
    intended wall centerline but `score_walls` sees one or both rails as
    off-ink/missing. It records review evidence and keeps the wall uncertain,
    but changes `quality_status` from hard `off_ink` to
    `centerline_plausible` so downstream openings can use the parent wall with
    explicit review debt.
    """
    _safe_key(key)
    if "/" in file or ".." in file or "/" in label_id or ".." in label_id:
        raise HTTPException(status_code=400, detail="bad file or label id")
    doc = get_labels("dataset", key, file)
    labels = doc.setdefault("labels", [])
    wall = next((lab for lab in labels if lab.get("id") == label_id and lab.get("type") == "wall"), None)
    if wall is None:
        raise HTTPException(status_code=404, detail=f"wall label not found: {label_id}")
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required")
    rail_evidence = body.get("rail_evidence") or []
    if not isinstance(rail_evidence, list) or not rail_evidence:
        raise HTTPException(status_code=400, detail="rail_evidence must be a non-empty list")
    review_region = body.get("review_region")
    if not isinstance(review_region, list) or len(review_region) < 4:
        raise HTTPException(status_code=400, detail="review_region must be [x0,y0,x1,y1]")
    confidence = str(body.get("confidence") or "medium")
    if confidence not in {"low", "medium", "high"}:
        raise HTTPException(status_code=400, detail="confidence must be low, medium, or high")
    confidence_reason = str(body.get("confidence_reason") or "faint_double_rail_centerline")
    expected_version = body.get("expected_version")

    attrs = wall.setdefault("attributes", {})
    previous_quality_status = attrs.get("quality_status")
    wall["status"] = "uncertain"
    attrs["quality_status"] = "centerline_plausible"
    attrs["confidence_reason"] = confidence_reason
    attrs["review_required"] = True
    attrs["centerline_review"] = {
        "method": "wall_centerline_review",
        "decision": "centerline_plausible",
        "confidence": confidence,
        "review_region": review_region,
        "rail_evidence": rail_evidence,
        "reason": reason,
        "previous_quality_status": previous_quality_status,
    }
    put_labels("dataset", key, file, doc)

    try:
        from .scene_plan_state import add_evidence, read_plan_state, write_plan_state

        evidence_result = add_evidence(
            DATASET_DIR,
            key,
            file,
            {
                "kind": "wall_centerline_review",
                "mode": "verification",
                "summary": reason,
                "tool": "review_wall_centerline_between_rails",
                "task_ids": body.get("task_ids") or [],
                "result": {
                    "wall_id": label_id,
                    "decision": "centerline_plausible",
                    "review_region": review_region,
                    "rail_evidence": rail_evidence,
                    "reason": reason,
                    "confidence": confidence,
                    "confidence_reason": confidence_reason,
                    "previous_quality_status": previous_quality_status,
                },
            },
            expected_version=expected_version,
        )
        evidence_id = evidence_result["state"]["evidence"][-1]["id"]
        state_doc = read_plan_state(DATASET_DIR, key, file)
        state = state_doc.get("state") or {}
        closed_defect_ids: list[str] = []
        now = _dt.datetime.now(_dt.UTC).isoformat()
        for defect in state.get("defects") or []:
            if defect.get("status") not in {"open", "in_progress"}:
                continue
            if defect.get("category") != "wall_off_ink":
                continue
            payload = defect.get("payload") if isinstance(defect.get("payload"), dict) else {}
            if payload.get("wall_id") != label_id:
                continue
            defect["status"] = "accepted_source_limited"
            defect["classification"] = "centerline_plausible_double_rail"
            defect["terminal_reason"] = reason
            defect["updated_at"] = now
            existing = defect.setdefault("evidence_ids", [])
            if evidence_id not in existing:
                existing.append(evidence_id)
            closed_defect_ids.append(str(defect.get("id")))
        if closed_defect_ids:
            state.setdefault("decision_log", []).append({
                "time": now,
                "mode": "verification",
                "evidence_ids": [evidence_id],
                "decision": f"Accepted {len(closed_defect_ids)} wall off-ink defect(s) as centerline-plausible",
                "result": reason,
                "defect_ids": closed_defect_ids,
            })
            write_plan_state(DATASET_DIR, state)
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)

    return {
        "ok": True,
        "data": {
            "label_id": label_id,
            "quality_status": "centerline_plausible",
            "status": wall.get("status"),
            "evidence_id": locals().get("evidence_id"),
            "closed_defect_ids": locals().get("closed_defect_ids", []),
            "review_region": review_region,
            "review_required": True,
        },
    }


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


def _mass_edge_quality_status(
    *,
    accepted: bool,
    excluded: bool,
    edge_policy: str,
    mass_mode: str,
) -> str:
    if excluded or mass_mode == "projection_non_wall":
        return "rejected"
    if accepted:
        return "centerline_plausible" if edge_policy == "use_given" else "ink_anchored"
    if mass_mode == "structural_confirmed":
        return "rejected"
    return "off_ink"


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
    mass_mode = str(body.get("mass_mode") or "structural_confirmed")
    if mass_mode not in {"structural_confirmed", "structural_uncertain", "projection_non_wall", "partial_mass_hypothesis"}:
        raise HTTPException(
            status_code=400,
            detail="mass_mode must be structural_confirmed, structural_uncertain, projection_non_wall, or partial_mass_hypothesis",
        )
    if mass_mode == "structural_confirmed" and edge_policy != "use_given" and min_confidence < 0.75:
        raise HTTPException(
            status_code=400,
            detail="confirmed structural masses require min_confidence >= 0.75; use mass_mode='structural_uncertain' for hypotheses",
        )
    semantic_overlap_warnings: list[str] = []
    try:
        pts = [pt for edge in source_edges for pt in edge if isinstance(pt, list) and len(pt) >= 2]
        draft_bbox = [
            min(float(p[0]) for p in pts),
            min(float(p[1]) for p in pts),
            max(float(p[0]) for p in pts),
            max(float(p[1]) for p in pts),
        ] if pts else None
        if draft_bbox:
            for semantic in _semantic_regions_from_plan(key, file):
                if semantic.get("semantic_class") not in NON_WALL_SEMANTIC_CLASSES:
                    continue
                sb = semantic.get("bbox_xyxy") or semantic.get("region")
                if not (isinstance(sb, list) and len(sb) >= 4):
                    continue
                overlaps = not (
                    draft_bbox[2] < float(sb[0])
                    or float(sb[2]) < draft_bbox[0]
                    or draft_bbox[3] < float(sb[1])
                    or float(sb[3]) < draft_bbox[1]
                )
                if overlaps:
                    semantic_overlap_warnings.append(
                        f"mass overlaps semantic {semantic.get('semantic_class')} region {semantic.get('evidence_id')}"
                    )
        if semantic_overlap_warnings and mass_mode == "structural_confirmed" and not bool(body.get("allow_semantic_overlap_override")):
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "confirmed structural mass overlaps non-wall semantic context",
                    "required_override": "allow_semantic_overlap_override=true",
                    "warnings": semantic_overlap_warnings,
                },
            )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        semantic_overlap_warnings = []
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
            accepted = (not excluded) and (edge_policy == "use_given" or confidence >= min_confidence)
            edge_reports.append({
                "edge_index": idx,
                "source": [[start[0], start[1]], [end[0], end[1]]],
                "fitted": [[refined_start[0], refined_start[1]], [refined_end[0], refined_end[1]]],
                "confidence": round(confidence, 3),
                "thickness_px": thickness_px,
                "accepted": accepted,
                "quality_status": _mass_edge_quality_status(
                    accepted=accepted,
                    excluded=excluded,
                    edge_policy=edge_policy,
                    mass_mode=mass_mode,
                ),
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
    before_edges = []
    for idx, lab in sorted(existing_by_edge.items()):
        seg = _wall_segment(lab)
        if seg is not None:
            before_edges.append({
                "edge_index": idx,
                "label_id": lab.get("id"),
                "edge": [[seg[0][0], seg[0][1]], [seg[1][0], seg[1][1]]],
            })
    changed_ids: list[str] = []
    wall_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    after_edges = []
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
            "mass_mode": mass_mode,
            "mass_tool": tool,
            "mass_edge_index": idx,
            "mass_edge_count": len(source_edges),
            "mass_role": "exterior",
            "edge_confidence": edge_reports[idx]["confidence"],
            "quality_status": edge_reports[idx]["quality_status"],
            "endpoint_reason_start": "mass_corner",
            "endpoint_reason_end": "mass_corner",
        }
        if thickness_mm is not None:
            attrs["thickness_mm"] = thickness_mm
        label = {
            "id": label_id,
            "type": "wall",
            "status": "readable" if accepted and mass_mode == "structural_confirmed" else "uncertain",
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
        after_edges.append({
            "edge_index": idx,
            "label_id": label_id,
            "edge": [[wall[0][0], wall[0][1]], [wall[1][0], wall[1][1]]],
            "accepted": accepted,
            "quality_status": edge_reports[idx]["quality_status"],
        })
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
    warnings.extend(semantic_overlap_warnings)
    if rejected:
        if mass_mode == "structural_confirmed":
            warnings.append(f"{len(rejected)} edge(s) rejected due to low refine confidence; no confirmed structural mass was persisted")
        else:
            warnings.append(f"{len(rejected)} edge(s) persisted uncertain due to low refine confidence")
    persisted = mass_mode != "projection_non_wall" and not (mass_mode == "structural_confirmed" and bool(rejected))
    if persisted:
        put_labels("dataset", key, file, doc)
    else:
        changed_ids = []
        after_edges = []
        if mass_mode == "projection_non_wall":
            warnings.append("projection_non_wall records no structural wall labels")
    transaction_verification = {
        "verification_contract": "wall-mass-transaction-verification/v1",
        "view_mode": "topology_qa_view",
        "source_edges": [
            {"edge_index": idx, "edge": [[float(v) for v in edge[0]], [float(v) for v in edge[1]]]}
            for idx, edge in enumerate(source_edges)
        ],
        "before_edges": before_edges,
        "after_edges": after_edges,
        "accepted_edges": [e for e in edge_reports if e["accepted"]],
        "rejected_edges": rejected,
        "changed_label_ids": changed_ids,
        "overlay_guidance": [
            "source_edges=thin draft silhouette",
            "accepted_edges=green fitted/connected walls",
            "rejected_edges=amber uncertain edges requiring detail refinement",
            "before_edges=gray existing mass walls when replacing a mass",
        ],
    }
    return {
        "mass_contract": "wall-mass-transaction/v1",
        "tool": tool,
        "mass_id": mass_id,
        "mass_kind": mass_kind,
        "mass_mode": mass_mode,
        "persisted": persisted,
        "edge_policy": edge_policy,
        "wall_label_ids": changed_ids,
        "changed_label_ids": changed_ids,
        "edge_reports": edge_reports,
        "rejected_edges": rejected,
        "warnings": warnings,
        "transaction_verification": transaction_verification,
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
    semantic_exclusions: bool = False,
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
    exclusions = _semantic_exclusion_regions(key, file) if semantic_exclusions else []
    with PILImage.open(img_path) as src:
        src = src.convert("RGB")
        res = score_walls(src, walls, region=parsed,
                          min_wall_px=min_wall_px, tol_px=tol_px, thresh=thresh,
                          thin_aware=thin_aware, close_px=close_px,
                          exclusion_regions=exclusions)
    res["n_walls"] = len(walls)
    res["semantic_exclusion_count"] = len(exclusions)
    return {"ok": True, "data": res}


def _semantic_regions_from_plan(key: str, file: str) -> list[dict[str, Any]]:
    try:
        from .scene_plan_state import read_plan_state
        plan = read_plan_state(DATASET_DIR, key, file)
    except Exception:  # noqa: BLE001
        return []
    state = plan.get("state") or {}
    regions = []
    for evidence in state.get("evidence") or []:
        if evidence.get("kind") != "semantic_ink_region":
            continue
        result = evidence.get("result") or {}
        semantic_class = result.get("semantic_class")
        region = result.get("region")
        if semantic_class and isinstance(region, list) and len(region) >= 4:
            bbox_xyxy = result.get("bbox_xyxy")
            bbox_format = result.get("bbox_format") or "xywh"
            if not isinstance(bbox_xyxy, list) or len(bbox_xyxy) < 4:
                try:
                    bbox_xyxy = normalize_bbox_region(
                        region,
                        bbox_format=bbox_format,
                        reject_out_of_bounds=False,
                    ).bbox_xyxy
                except ValueError:
                    continue
            regions.append({
                "evidence_id": evidence.get("id"),
                "semantic_class": semantic_class,
                "region": bbox_xyxy[:4],
                "bbox_format": "xyxy",
                "bbox_xyxy": bbox_xyxy[:4],
                "confidence": result.get("confidence"),
            })
    return regions


def _semantic_exclusion_regions(key: str, file: str) -> list[dict[str, Any]]:
    return [r for r in _semantic_regions_from_plan(key, file) if r.get("semantic_class") in NON_WALL_SEMANTIC_CLASSES]


@router.post("/datasets/{key}/{file}/classify-ink-region", tags=["pdfs"])
def classify_ink_region_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    """Record one semantic ink/exclusion region in scene-plan evidence."""
    _ensure_dataset_scene(key, file)
    semantic_class = str(body.get("semantic_class") or "")
    if semantic_class not in SEMANTIC_INK_CLASSES:
        raise HTTPException(status_code=400, detail=f"semantic_class must be one of {sorted(SEMANTIC_INK_CLASSES)}")
    region = body.get("region")
    if not isinstance(region, list) or len(region) < 4:
        raise HTTPException(status_code=400, detail="region must be a four-value bbox")
    from PIL import Image as PILImage
    img_path = _scene_image_path("dataset", key, file)
    with PILImage.open(img_path) as src_img:
        image_size = (int(src_img.width), int(src_img.height))
    try:
        normalized = normalize_bbox_region(
            region,
            bbox_format=body.get("bbox_format"),
            image_size=image_size,
            reject_out_of_bounds=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    result = {
        "semantic_class": semantic_class,
        "region": normalized.region,
        "bbox_format": normalized.bbox_format,
        "bbox_xyxy": normalized.bbox_xyxy,
        "image_size_px": list(image_size),
        "confidence": body.get("confidence") or "medium",
        "applies_to_wall_score": semantic_class in NON_WALL_SEMANTIC_CLASSES,
        "note": body.get("note") or "",
    }
    evidence = {
        "kind": "semantic_ink_region",
        "mode": body.get("mode") or "analysis",
        "summary": body.get("summary") or f"Classified ink region as {semantic_class}",
        "tool": "classify_ink_region",
        "params": {
            "region": result["region"],
            "bbox_format": result["bbox_format"],
            "semantic_class": semantic_class,
        },
        "result": result,
        "task_ids": body.get("task_ids") or [],
        "image_url": body.get("image_url"),
        "expected_version": body.get("expected_version"),
    }
    from .scene_plan_state import add_evidence
    try:
        data = add_evidence(DATASET_DIR, key, file, evidence, expected_version=body.get("expected_version"))
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    state = data.get("state") or {}
    evidence_id = ((state.get("evidence") or [{}])[-1] or {}).get("id")
    return {"ok": True, "data": {
        "semantic_region_contract": "semantic-ink-region/v1",
        "evidence_id": evidence_id,
        "semantic_class": semantic_class,
        "region": result["region"],
        "bbox_format": result["bbox_format"],
        "bbox_xyxy": result["bbox_xyxy"],
        "applies_to_wall_score": result["applies_to_wall_score"],
    }}


@router.get("/datasets/{key}/{file}/score-walls-structural", tags=["pdfs"])
def score_walls_structural_route(
    key: str,
    file: str,
    region: str | None = None,
    min_wall_px: int = 8,
    tol_px: int = 9,
    thresh: int | None = None,
    thin_aware: bool = False,
    close_px: int = 0,
) -> dict:
    """Compact wall score against structural wall ink after semantic exclusions."""
    base = score_walls_route(
        key,
        file,
        region=region,
        min_wall_px=min_wall_px,
        tol_px=tol_px,
        thresh=thresh,
        thin_aware=thin_aware,
        close_px=close_px,
        semantic_exclusions=True,
    )["data"]
    semantic_regions = _semantic_regions_from_plan(key, file)
    possible = [r for r in semantic_regions if r.get("semantic_class") in {"possible_wall", "unknown"}]
    compact_missing = (base.get("missing_regions") or [])[:8]
    compact_off_ink = (base.get("off_ink_segments") or [])[:8]
    return {"ok": True, "data": {
        "score_contract": "score-walls-structural/v1",
        "precision": base.get("precision"),
        "recall": base.get("recall"),
        "f1": base.get("f1"),
        "n_walls": base.get("n_walls"),
        "semantic_exclusion_count": base.get("semantic_exclusion_count"),
        "missing_region_count": len(base.get("missing_regions") or []),
        "off_ink_count": len(base.get("off_ink_segments") or []),
        "missing_regions": compact_missing,
        "off_ink_segments": compact_off_ink,
        "unresolved_possible_wall_regions": possible[:8],
        "unresolved_possible_wall_count": len(possible),
        "params": base.get("params"),
    }}


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
    view_mode: str | None = None,
):
    """Render current labels plus one opening candidate quad/axis."""
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    img_path = _scene_image_path("dataset", key, file)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"scene image not found: {file}")
    preset = _view_mode_preset(view_mode)
    max_dim = int(preset.get("max_dim", max_dim))
    clean = bool(preset.get("clean", clean))
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


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _point2(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _label_by_id(labels_doc: dict[str, Any], label_id: str) -> dict[str, Any] | None:
    for lab in labels_doc.get("labels") or []:
        if isinstance(lab, dict) and lab.get("id") == label_id:
            return lab
    return None


def _opening_axes_from_quad(quad: list[Any]) -> tuple[tuple[tuple[float, float], tuple[float, float]], tuple[tuple[float, float], tuple[float, float]]] | None:
    pts = [_point2(p) for p in quad]
    if len(pts) != 4 or any(p is None for p in pts):
        return None
    p0, p1, p2, p3 = pts  # type: ignore[misc]
    axis = (((p0[0] + p3[0]) / 2.0, (p0[1] + p3[1]) / 2.0), ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0))
    depth = (((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0), ((p3[0] + p2[0]) / 2.0, (p3[1] + p2[1]) / 2.0))
    return axis, depth


def _opening_local_qa(labels_doc: dict[str, Any], opening: dict[str, Any], *, expected_depth_px: float | None = None) -> dict[str, Any]:
    parent_ids = [
        rel.get("other_id")
        for rel in (opening.get("relations") or [])
        if isinstance(rel, dict) and rel.get("kind") == "belongs_to"
    ]
    parent = None
    for parent_id in parent_ids:
        if isinstance(parent_id, str):
            lab = _label_by_id(labels_doc, parent_id)
            if lab and lab.get("type") == "wall":
                parent = lab
                break
    if parent is None:
        return {
            "ok": False,
            "defects": [{"category": "missing_parent_wall", "message": "opening has no existing wall parent"}],
            "parent_wall_id": parent_ids[0] if parent_ids else None,
        }
    parent_seg = _wall_segment(parent)
    quad = ((opening.get("geometry") or {}).get("quad") or [])
    axes = _opening_axes_from_quad(quad)
    if parent_seg is None or axes is None:
        return {
            "ok": False,
            "defects": [{"category": "bad_geometry", "message": "opening or parent wall geometry is invalid"}],
            "parent_wall_id": parent.get("id"),
        }
    from .geometry_checks import floorplan_opening_quality
    quality = floorplan_opening_quality(
        axes[0],
        axes[1],
        parent_seg,
        tol_px=30.0,
        is_garage_door=(opening.get("attributes") or {}).get("opening_kind") == "garage_door",
        expected_depth_px=expected_depth_px,
    )
    quality["parent_wall_id"] = parent.get("id")
    quality["relation_ok"] = True
    return quality


def _opening_label_from_wall_span(
    labels_doc: dict[str, Any],
    *,
    parent_wall_id: str,
    span_start: list[float],
    span_end: list[float],
    opening_kind: str,
    width_mm: float | None = None,
    swing: str | None = None,
    swing_side: str | None = None,
    wall_half_width_px: float = 12.0,
    transaction_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    parent = _label_by_id(labels_doc, parent_wall_id)
    if parent is None or parent.get("type") != "wall":
        raise ValueError(f"parent wall {parent_wall_id!r} does not exist")
    parent_seg = _wall_segment(parent)
    a = _point2(span_start)
    b = _point2(span_end)
    if parent_seg is None or a is None or b is None:
        raise ValueError("parent wall and span endpoints must be valid points")
    if math.hypot(b[0] - a[0], b[1] - a[1]) < 1:
        raise ValueError("opening span must be non-degenerate")
    w0, w1 = parent_seg
    dx, dy = w1[0] - w0[0], w1[1] - w0[1]
    length = math.hypot(dx, dy)
    if length < 1:
        raise ValueError("parent wall is degenerate")
    nx, ny = -dy / length, dx / length
    half = max(1.0, float(wall_half_width_px))
    quad = [
        [round(a[0] + nx * half, 1), round(a[1] + ny * half, 1)],
        [round(b[0] + nx * half, 1), round(b[1] + ny * half, 1)],
        [round(b[0] - nx * half, 1), round(b[1] - ny * half, 1)],
        [round(a[0] - nx * half, 1), round(a[1] - ny * half, 1)],
    ]
    now = _now_iso()
    attrs = {
        "opening_kind": opening_kind,
        "parent_wall_id": parent_wall_id,
        "parent_wall_quality_status": (parent.get("attributes") or {}).get("quality_status"),
        "opening_axis": [[round(a[0], 1), round(a[1], 1)], [round(b[0], 1), round(b[1], 1)]],
        "transaction_id": transaction_id or f"opening-txn-{_next_label_id(labels_doc, 'tmp')}",
    }
    if width_mm is not None:
        attrs["width_mm"] = width_mm
    if swing is not None:
        attrs["swing"] = swing
    if swing_side is not None:
        attrs["swing_side"] = swing_side
    label = {
        "id": _next_label_id(labels_doc, "opening"),
        "type": "floorplan_opening",
        "status": "readable",
        "geometry": {"quad": quad},
        "attributes": attrs,
        "relations": [{"kind": "belongs_to", "other_id": parent_wall_id}],
        "created_at": now,
        "updated_at": now,
    }
    return label, _opening_local_qa({"labels": (labels_doc.get("labels") or []) + [label]}, label, expected_depth_px=half * 2.0)


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
            run_id=body.get("run_id"),
            agent_id=body.get("agent_id"),
            subagent_id=body.get("subagent_id"),
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
            run_id=body.get("run_id"),
            agent_id=body.get("agent_id"),
            subagent_id=body.get("subagent_id"),
            expected_version=body.get("expected_version"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    return {"ok": True, "data": data}


@router.post("/datasets/{key}/{file}/opening-candidates/{candidate_id}/review", tags=["pdfs"])
def review_opening_candidate_route(key: str, file: str, candidate_id: str, body: dict[str, Any] = Body(...)) -> dict:
    """Unified candidate review flow.

    `outcome=accepted_applied` persists the suggested opening and records the
    decision. Reject/manual outcomes only write the plan-state decision.
    """
    outcome = str(body.get("outcome") or "")
    if outcome == "accepted_applied":
        return apply_opening_candidate_route(key, file, candidate_id, body)
    return decide_opening_candidate_route(key, file, candidate_id, body)


@router.post("/datasets/{key}/{file}/opening-candidates/review-batch", tags=["pdfs"])
def review_opening_candidates_batch_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    reviews = body.get("reviews")
    if not isinstance(reviews, list) or not reviews:
        raise HTTPException(status_code=400, detail="reviews must be a non-empty list")
    results = []
    for review in reviews:
        if not isinstance(review, dict):
            results.append({"ok": False, "error": "review item must be an object"})
            continue
        candidate_id = str(review.get("candidate_id") or "")
        if not candidate_id:
            results.append({"ok": False, "error": "candidate_id is required"})
            continue
        try:
            res = review_opening_candidate_route(key, file, candidate_id, {**body, **review})
            results.append({
                "ok": True,
                "candidate_id": candidate_id,
                "outcome": review.get("outcome"),
                "data": res.get("data"),
            })
        except HTTPException as e:
            results.append({"ok": False, "candidate_id": candidate_id, "status_code": e.status_code, "error": e.detail})
        except Exception as e:  # noqa: BLE001
            results.append({"ok": False, "candidate_id": candidate_id, "error": str(e)})
    return {
        "ok": True,
        "data": {
            "transaction_contract": "opening-candidate-review-batch/v1",
            "count": len(results),
            "applied": len([r for r in results if r.get("ok") and r.get("outcome") == "accepted_applied"]),
            "failed": len([r for r in results if not r.get("ok")]),
            "results": results,
        },
    }


@router.post("/datasets/{key}/{file}/openings/on-wall", tags=["pdfs"])
def upsert_opening_on_wall_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    labels_doc = get_labels("dataset", key, file)
    try:
        from .scene_plan_state import preflight_label_write
        plan_preflight = preflight_label_write(
            DATASET_DIR,
            key,
            file,
            ["floorplan_opening"],
            tool="upsert_opening_on_wall",
            allow_override=bool(body.get("allow_plan_order_override", False)),
            override_reason=body.get("override_reason"),
        )
        opening_kind = str(body.get("opening_kind") or "door")
        if opening_kind not in {"door", "window", "passage", "garage_door", "other"}:
            raise ValueError("opening_kind must be door, window, passage, garage_door, or other")
        transaction_id = body.get("transaction_id") or f"opening-txn-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d%H%M%S')}"
        label, qa = _opening_label_from_wall_span(
            labels_doc,
            parent_wall_id=str(body.get("parent_wall_id") or ""),
            span_start=body.get("span_start") or body.get("start"),
            span_end=body.get("span_end") or body.get("end"),
            opening_kind=opening_kind,
            width_mm=body.get("width_mm"),
            swing=body.get("swing"),
            swing_side=body.get("swing_side"),
            wall_half_width_px=float(body.get("wall_half_width_px") or 12.0),
            transaction_id=transaction_id,
        )
        label.setdefault("attributes", {})["qa_status"] = "passed" if qa.get("ok") else "failed"
        label.setdefault("attributes", {})["parent_wall_verification"] = {
            "parent_wall_id": body.get("parent_wall_id"),
            "quality_status": label.get("attributes", {}).get("parent_wall_quality_status"),
            "local_qa_ok": bool(qa.get("ok")),
        }
        if not qa.get("ok") and not bool(body.get("persist_failed", False)):
            return {
                "ok": True,
                "data": {
                    "transaction_contract": "opening-on-wall/v1",
                    "persisted": False,
                    "label_preview": label,
                    "local_qa": qa,
                    "plan_preflight": plan_preflight,
                },
            }
        new_doc = json.loads(json.dumps(labels_doc))
        # Replace same id if caller supplied one, otherwise append.
        if isinstance(body.get("label_id"), str) and body.get("label_id"):
            label["id"] = body["label_id"]
        replaced = False
        for idx, existing in enumerate(new_doc.get("labels") or []):
            if isinstance(existing, dict) and existing.get("id") == label.get("id"):
                new_doc["labels"][idx] = label
                replaced = True
                break
        if not replaced:
            new_doc.setdefault("labels", []).append(label)
        put_labels("dataset", key, file, new_doc)
        qa = _opening_local_qa(new_doc, label, expected_depth_px=float(body.get("wall_half_width_px") or 12.0) * 2.0)
        return {
            "ok": True,
            "data": {
                "transaction_contract": "opening-on-wall/v1",
                "persisted": True,
                "label_id": label.get("id"),
                "parent_wall_id": body.get("parent_wall_id"),
                "local_qa": qa,
                "plan_preflight": plan_preflight,
            },
        }
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)


DIMENSION_SEMANTICS = {"building", "site_setback", "elevation_datum", "unknown"}
CALIBRATION_ROLES = {"none", "building_metric", "site_metric", "transferred", "assumed_isotropic"}
CONFIDENCE_VALUES = {"low", "medium", "high"}


def _dimension_semantic(value: Any) -> str:
    v = str(value or "unknown")
    if v not in DIMENSION_SEMANTICS:
        raise ValueError("dimension_semantic must be building, site_setback, elevation_datum, or unknown")
    return v


def _calibration_role(value: Any, *, is_reference: bool) -> str:
    v = str(value or ("building_metric" if is_reference else "none"))
    if v not in CALIBRATION_ROLES:
        raise ValueError("calibration_role must be none, building_metric, site_metric, transferred, or assumed_isotropic")
    return v


@router.post("/datasets/{key}/{file}/dimension-chain-transaction", tags=["pdfs"])
def dimension_chain_transaction_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    labels_doc = get_labels("dataset", key, file)
    spans = body.get("spans")
    if not isinstance(spans, list) or not spans:
        raise HTTPException(status_code=400, detail="spans must be a non-empty list")
    try:
        from .scene_plan_state import preflight_label_write
        plan_preflight = preflight_label_write(
            DATASET_DIR,
            key,
            file,
            ["dimensioned_distance", "dimension_number"],
            tool="dimension_chain_transaction",
            allow_override=bool(body.get("allow_plan_order_override", False)),
            override_reason=body.get("override_reason"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    orientation = str(body.get("orientation") or "unknown")
    if orientation not in {"horizontal", "vertical", "unknown"}:
        raise HTTPException(status_code=400, detail="orientation must be horizontal, vertical, or unknown")
    transaction_id = str(body.get("transaction_id") or f"dim-chain-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d%H%M%S')}")
    chain_id = str(body.get("chain_id") or "CHAIN-001")
    now = _now_iso()
    new_doc = json.loads(json.dumps(labels_doc))
    written = []
    values_sum = 0.0
    for idx, span in enumerate(spans, start=1):
        if not isinstance(span, dict):
            raise HTTPException(status_code=400, detail="each span must be an object")
        start = _point2(span.get("start"))
        end = _point2(span.get("end"))
        if start is None or end is None:
            raise HTTPException(status_code=400, detail="span start/end must be [x,y]")
        value_mm = span.get("value_mm")
        if value_mm is not None:
            value_mm = float(value_mm)
            values_sum += value_mm
        is_reference = bool(span.get("is_reference", body.get("is_reference", False)))
        semantic = _dimension_semantic(span.get("dimension_semantic", body.get("dimension_semantic")))
        role = _calibration_role(span.get("calibration_role", body.get("calibration_role")), is_reference=is_reference)
        confidence = str(span.get("calibration_confidence", body.get("calibration_confidence", "medium")))
        if confidence not in CONFIDENCE_VALUES:
            raise HTTPException(status_code=400, detail="calibration_confidence must be low, medium, or high")
        dim_id = str(span.get("dimension_id") or _next_label_id(new_doc, "dim"))
        num_id = str(span.get("number_id") or _next_label_id(new_doc, "dimnum"))
        station_ids = span.get("station_ids") or []
        if not isinstance(station_ids, list):
            station_ids = []
        attrs = {
            "value_mm": value_mm,
            "target_orientation": orientation,
            "is_reference": is_reference,
            "dimension_semantic": semantic,
            "calibration_role": role,
            "calibration_confidence": confidence,
            "transaction_id": transaction_id,
            "span_id": str(span.get("span_id") or f"DSP-{idx:03d}"),
            "chain_id": chain_id,
            "station_ids": [str(s) for s in station_ids],
        }
        if span.get("reference_review"):
            attrs["reference_review"] = str(span.get("reference_review"))
        dim_label = {
            "id": dim_id,
            "type": "dimensioned_distance",
            "status": "readable",
            "geometry": {"start": [start[0], start[1]], "end": [end[0], end[1]]},
            "attributes": attrs,
            "created_at": now,
            "updated_at": now,
        }
        anchor = span.get("number_anchor")
        if _point2(anchor) is None:
            anchor = [(start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0]
        num_label = {
            "id": num_id,
            "type": "dimension_number",
            "status": "readable",
            "geometry": {"anchor": [float(anchor[0]), float(anchor[1])]},
            "attributes": {
                "text": str(span.get("dimension_text") or span.get("text") or ""),
                "parsed_value_mm": value_mm,
            },
            "relations": [{"kind": "labels", "other_id": dim_id}],
            "created_at": now,
            "updated_at": now,
        }
        new_doc.setdefault("labels", []).extend([dim_label, num_label])
        written.append({"span_id": attrs["span_id"], "dimension_id": dim_id, "number_id": num_id, "value_mm": value_mm, "is_reference": is_reference, "dimension_semantic": semantic, "calibration_role": role})
    overall = body.get("overall_value_mm")
    sum_check = None
    if overall is not None:
        overall = float(overall)
        tolerance_mm = float(body.get("sum_tolerance_mm") or 10.0)
        delta = values_sum - overall
        sum_check = {
            "ok": abs(delta) <= tolerance_mm,
            "parts_sum_mm": round(values_sum, 2),
            "overall_value_mm": overall,
            "delta_mm": round(delta, 2),
            "tolerance_mm": tolerance_mm,
        }
    put_labels("dataset", key, file, new_doc)
    from .fact_derivation import compute_scene_calibration
    calibration = compute_scene_calibration(new_doc.get("labels") or [])
    return {
        "ok": True,
        "data": {
            "transaction_contract": "dimension-chain-transaction/v1",
            "transaction_id": transaction_id,
            "chain_id": chain_id,
            "persisted": True,
            "written": written,
            "sum_check": sum_check,
            "calibration_after": calibration,
            "plan_preflight": plan_preflight,
        },
    }


@router.post("/datasets/{key}/{file}/reference-dim-review", tags=["pdfs"])
def reference_dim_review_route(key: str, file: str, body: dict[str, Any] = Body(...)) -> dict:
    _safe_key(key)
    if "/" in file or ".." in file:
        raise HTTPException(status_code=400, detail="bad file")
    label_id = str(body.get("label_id") or "")
    if not label_id:
        raise HTTPException(status_code=400, detail="label_id is required")
    labels_doc = get_labels("dataset", key, file)
    new_doc = json.loads(json.dumps(labels_doc))
    target = _label_by_id(new_doc, label_id)
    if target is None or target.get("type") != "dimensioned_distance":
        raise HTTPException(status_code=404, detail="dimensioned_distance label not found")
    try:
        from .scene_plan_state import preflight_label_write
        plan_preflight = preflight_label_write(
            DATASET_DIR,
            key,
            file,
            ["dimensioned_distance"],
            tool="reference_dim_review",
            allow_override=bool(body.get("allow_plan_order_override", False)),
            override_reason=body.get("override_reason"),
        )
    except Exception as e:  # noqa: BLE001
        _plan_http_error(e)
    is_reference = bool(body.get("is_reference", True))
    semantic = _dimension_semantic(body.get("dimension_semantic"))
    role = _calibration_role(body.get("calibration_role"), is_reference=is_reference)
    confidence = str(body.get("calibration_confidence") or "medium")
    if confidence not in CONFIDENCE_VALUES:
        raise HTTPException(status_code=400, detail="calibration_confidence must be low, medium, or high")
    attrs = target.setdefault("attributes", {})
    attrs["is_reference"] = is_reference
    attrs["dimension_semantic"] = semantic
    attrs["calibration_role"] = role
    attrs["calibration_confidence"] = confidence
    attrs["reference_review"] = str(body.get("review") or body.get("reference_review") or "")
    attrs["reference_review_evidence_ids"] = [str(e) for e in (body.get("evidence_ids") or [])]
    target["updated_at"] = _now_iso()
    put_labels("dataset", key, file, new_doc)
    from .fact_derivation import compute_scene_calibration
    calibration = compute_scene_calibration(new_doc.get("labels") or [])
    return {
        "ok": True,
        "data": {
            "review_contract": "reference-dim-review/v1",
            "label_id": label_id,
            "dimension_semantic": semantic,
            "calibration_role": role,
            "calibration_confidence": confidence,
            "calibration_after": calibration,
            "plan_preflight": plan_preflight,
        },
    }


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
    view_mode: str | None = None,
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
    preset = _view_mode_preset(view_mode)
    tiers = str(preset.get("tiers", tiers))
    max_dim = int(preset.get("max_dim", max_dim))
    enhance = preset.get("enhance", enhance)
    format = str(preset.get("format", format)) if preset.get("format", format) is not None else None
    clean = bool(preset.get("clean", clean))
    style = str(preset.get("style", style)) if preset.get("style", style) is not None else None
    if background_opacity is None and "background_opacity" in preset:
        background_opacity = float(preset["background_opacity"])
    contrast = str(preset.get("contrast", contrast)) if preset.get("contrast", contrast) is not None else None
    show_relations = str(preset.get("show_relations", show_relations)) if preset.get("show_relations", show_relations) is not None else None
    show_openings = str(preset.get("show_openings", show_openings)) if preset.get("show_openings", show_openings) is not None else None
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
        f"-vm{view_mode or 'raw'}"
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
