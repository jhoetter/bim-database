"""Server-side label rendering on top of the grid overlay.

Per agentic-labeling-followups-2-tracker §H5: the agent's biggest
quality problem is one-shot label placement — it picks coords from
the grid view, calls `upsert_label`, and never looks at its own
work. This module renders the saved labels back onto the grid image
so the agent can verify placement visually.

Rendering vocabulary (semantic audit renderer):

  wall                    — translucent thickness band + hatched fill +
                            orange centerline between start and end
  floorplan_opening       — filled quad + kind internals + attachment warning
  view_opening            — filled circle, polygon, or top/bottom body
  component_line          — polyline plus closed-region fill for areas
  height_mark             — datum marker; Bezug/±0.00 is visually distinct
  dimensioned_distance    — line + endpoint caps + value; reference dims get
                            the same amber/M1/star language as the UI
  dimension_number        — anchor/bbox + source text chip

All labels are drawn AT SOURCE PIXEL COORDS; the underlying grid
renderer translates to output pixels exactly the same way. So a wall
labelled with endpoints [200, 500] → [1100, 500] will visually line
up with the grid line marked "500" along the Y axis.
"""
from __future__ import annotations

import math
from typing import Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFont

from .grid_render import _load_font, render_grid_overlay

_WALL_COLOR = (31, 41, 55, 235)
_WALL_AXIS_HIGH = (255, 160, 40, 245)
_WALL_WIDTH = 3
_WALL_BAND_FILL = (31, 41, 55, 46)
_WALL_BAND_FILL_HIGH = (31, 41, 55, 72)
_WALL_HATCH_COLOR = (124, 58, 237, 70)
_WALL_PX_PER_MM = 0.05

_OPENING_KIND_COLOR = {
    "window": (2, 132, 199, 225),
    "door": (13, 148, 136, 225),
    "passage": (113, 113, 122, 225),
    "garage_door": (146, 64, 14, 225),
    "skylight": (8, 145, 178, 225),
    "dormer": (234, 88, 12, 225),
    "other": (2, 132, 199, 225),
}
_FLOORPLAN_OPENING_COLOR = (2, 132, 199, 225)
_VIEW_OPENING_COLOR = (2, 132, 199, 205)
_OPENING_WIDTH = 2

_LINE_KIND_COLOR = {
    "gebaeudekante": (31, 41, 55, 230),
    "dachschraege": (234, 88, 12, 230),
    "first": (180, 83, 9, 230),
    "traufe": (202, 138, 4, 230),
    "gelaende": (21, 128, 61, 230),
    "geschoss": (124, 58, 237, 230),
    "ok_ffb": (14, 116, 144, 230),
    "sockel": (87, 83, 78, 230),
    "firstkante": (180, 83, 9, 230),
    "kniestock": (126, 34, 206, 230),
    "other": (107, 114, 128, 230),
}
_COMPONENT_LINE_COLOR = (0, 160, 160, 220)
_COMPONENT_LINE_WIDTH = 2

_HEIGHT_MARK_COLOR = (21, 128, 61, 245)
_HEIGHT_BEZUG_COLOR = (245, 158, 11, 255)
_HEIGHT_MARK_RADIUS = 5

_DIM_COLOR = (147, 51, 234, 235)
_DIM_REF_COLOR = (245, 158, 11, 245)
_DIM_WIDTH = 3

_DIM_NUMBER_FG = (40, 40, 40, 255)
_DIM_NUMBER_BG = (255, 255, 230, 220)

_UNCERTAIN_RING_COLOR = (240, 160, 0, 255)
_MISSING_COLOR = (220, 38, 38, 255)
_NOT_READABLE_COLOR = (124, 58, 237, 255)
_UNCERTAIN_RING_WIDTH = 2
_RELATION_COLOR = (6, 95, 190, 235)
_WARNING_BG = (254, 242, 242, 230)
_WARNING_FG = (185, 28, 28, 255)

_LABEL_CHIP_BG = (255, 255, 255, 220)
_LABEL_CHIP_FG = (40, 40, 40, 255)


def render_grid_with_labels(
    image: Image.Image,
    labels: Sequence[dict],
    *,
    tiers: Sequence[str] = ("broad", "finer"),
    region: tuple[int, int, int, int] | None = None,
    max_dim: int = 1600,
    enhance: str | None = None,
    clean: bool = False,
    style: str = "standard",
    target: tuple[int, int] | None = None,
    target_line: str = "none",
    background_opacity: float = 0.5,
    background_opacity_explicit: bool = False,
    contrast: str = "high",
    px_per_mm: float | None = None,
    show_relations: str = "required",
    show_height_guides: str = "auto",
) -> Image.Image:
    """Render the source image + grid overlay + every label in `labels`.

    Coordinates in `labels` are SOURCE pixels (matches what
    upsert_label accepted). Translation to output pixels mirrors the
    grid renderer's logic so labels visually align with their grid
    addresses.

    `enhance` (issue #2) is forwarded to the grid renderer to lift faint
    scans; it changes only pixel intensity, so label positions are
    unaffected.
    """
    # Base image. In the default verify mode we reuse the grid renderer
    # (grid + corner legend, drawing faded to 0.5). In `clean` QA mode
    # (H5 quality-assurance, per the labeling-correctness work) the legacy
    # default keeps the drawing at full opacity with no grid; an explicit
    # background_opacity can now fade it for label-dominant QA loops.
    if clean:
        base = _clean_base(
            image,
            region=region,
            max_dim=max_dim,
            enhance=enhance,
            background_opacity=background_opacity if background_opacity_explicit else 1.0,
        )
    else:
        base = render_grid_overlay(
            image, tiers=tiers, region=region, max_dim=max_dim, enhance=enhance,
            background_opacity=background_opacity,
            background_opacity_explicit=background_opacity_explicit,
            style=style, target=target, target_line=target_line,  # type: ignore[arg-type]
        )
    src_w, src_h = image.size
    if region is not None:
        x0, y0, x1, y1 = region
        crop_src_w = x1 - x0
        crop_src_h = y1 - y0
        region_origin = (x0, y0)
    else:
        x0, y0 = 0, 0
        crop_src_w = src_w
        crop_src_h = src_h
        region_origin = (0, 0)
    out_w, out_h = base.size

    # The grid renderer's output size == cropped source size (when
    # under max_dim, post H4). When downsampled, scale labels by the
    # same ratio so they land on the right output pixels.
    sx = out_w / crop_src_w
    sy = out_h / crop_src_h

    def to_out(p: Sequence[float]) -> tuple[int, int]:
        if len(p) < 2:
            return (0, 0)
        ox = int((p[0] - region_origin[0]) * sx)
        oy = int((p[1] - region_origin[1]) * sy)
        return (ox, oy)

    def in_bounds(pt: tuple[int, int]) -> bool:
        return 0 <= pt[0] < out_w and 0 <= pt[1] < out_h

    # In clean QA mode draw labels onto a separate transparent layer so we
    # can composite them at reduced alpha (the underlying ink shows
    # through). In normal mode draw straight onto the base (unchanged).
    target = Image.new("RGBA", base.size, (0, 0, 0, 0)) if clean else base
    draw = ImageDraw.Draw(target, "RGBA")
    label_font = _load_font(11)
    chip_font = _load_font(10)

    contrast = (contrast or "high").lower()
    if contrast not in ("normal", "high"):
        raise ValueError("contrast must be normal or high")
    if show_relations not in ("required", "all", "none"):
        raise ValueError("show_relations must be required, all, or none")
    if show_height_guides not in ("auto", "always", "never"):
        raise ValueError("show_height_guides must be auto, always, or never")
    render_height_guides = (
        show_height_guides == "always"
        or (show_height_guides == "auto" and (clean or contrast == "high"))
    )

    label_by_id = {str(lab.get("id")): lab for lab in labels if lab.get("id") is not None}

    # Pass 0 — correctness-critical relation cues behind geometry.
    if show_relations in ("required", "all"):
        _draw_required_relation_cues(draw, labels, label_by_id, to_out, in_bounds, out_w, out_h)

    # Pass 1 — strokes / geometry.
    for lab in labels:
        t = lab.get("type")
        geom = lab.get("geometry") or {}
        status = lab.get("status")
        if t == "wall":
            start_src = geom.get("start") or [0, 0]
            end_src = geom.get("end") or [0, 0]
            start = to_out(start_src)
            end = to_out(end_src)
            if in_bounds(start) or in_bounds(end):
                thickness = _wall_thickness_mm(lab)
                band = _wall_band_points(start_src, end_src, thickness, px_per_mm=px_per_mm)
                if band:
                    band_out = [to_out(p) for p in band]
                    draw.polygon(band_out, fill=_WALL_BAND_FILL_HIGH if contrast == "high" else _WALL_BAND_FILL)
                    _draw_hatch(target, band_out)
                draw.line([start, end], fill=_WALL_AXIS_HIGH if contrast == "high" else _WALL_COLOR, width=_WALL_WIDTH)
        elif t == "floorplan_opening":
            quad = geom.get("quad") or []
            if len(quad) == 4:
                pts = [to_out(p) for p in quad]
                kind = (lab.get("attributes") or {}).get("opening_kind") or "window"
                color = _opening_color(kind)
                draw.polygon(pts, fill=_with_alpha(color, 36 if contrast == "normal" else 54), outline=color)
                _draw_floorplan_opening_inner(draw, pts, lab.get("attributes") or {}, color)
                if not _has_relation(lab, "belongs_to"):
                    _warn(draw, chip_font, "no parent wall", _poly_center(pts), out_w, out_h)
        elif t == "view_opening":
            kind = (lab.get("attributes") or {}).get("opening_kind") or "window"
            color = _opening_color(kind)
            frame_visible = (lab.get("attributes") or {}).get("frame_visible")
            if "circle" in geom:
                c = geom["circle"]
                center = to_out(c.get("center") or [0, 0])
                r = int((c.get("radius_px") or 0) * sx)
                if r > 0:
                    draw.ellipse(
                        [center[0] - r, center[1] - r, center[0] + r, center[1] + r],
                        fill=_with_alpha(color, 32 if contrast == "normal" else 48),
                        outline=color,
                        width=_OPENING_WIDTH,
                    )
                    _chip(draw, chip_font, str(kind), (center[0] + r + 2, center[1]), out_w, out_h)
                    if frame_visible:
                        draw.ellipse(
                            [center[0] - r - 4, center[1] - r - 4, center[0] + r + 4, center[1] + r + 4],
                            outline=color,
                            width=1,
                        )
            elif geom.get("shape") == "circle":
                center = to_out(geom.get("center") or [0, 0])
                r = int((geom.get("radius_px") or 0) * sx)
                if r > 0:
                    draw.ellipse(
                        [center[0] - r, center[1] - r, center[0] + r, center[1] + r],
                        fill=_with_alpha(color, 32 if contrast == "normal" else 48),
                        outline=color,
                        width=_OPENING_WIDTH,
                    )
                    _chip(draw, chip_font, str(kind), (center[0] + r + 2, center[1]), out_w, out_h)
                    if frame_visible:
                        draw.ellipse(
                            [center[0] - r - 4, center[1] - r - 4, center[0] + r + 4, center[1] + r + 4],
                            outline=color,
                            width=1,
                        )
            elif "polygon" in geom:
                pts = [to_out(p) for p in geom["polygon"]]
                if len(pts) >= 3:
                    draw.polygon(pts, fill=_with_alpha(color, 32 if contrast == "normal" else 48), outline=color)
                    _chip(draw, chip_font, str(kind), _poly_center(pts), out_w, out_h)
                    if frame_visible:
                        draw.line(pts + [pts[0]], fill=color, width=_OPENING_WIDTH + 1)
            elif "top_edge" in geom or "bottom_edge" in geom:
                top = [to_out(p) for p in (geom.get("top_edge") or [])]
                bottom = [to_out(p) for p in (geom.get("bottom_edge") or [])]
                if len(top) >= 2 and len(bottom) >= 2:
                    body = top + list(reversed(bottom))
                    draw.polygon(body, fill=_with_alpha(color, 32 if contrast == "normal" else 48), outline=color)
                    if frame_visible:
                        draw.line(body + [body[0]], fill=color, width=_OPENING_WIDTH + 1)
                for k in ("top_edge", "bottom_edge"):
                    edge = geom.get(k) or []
                    pts = [to_out(p) for p in edge]
                    if len(pts) >= 2:
                        draw.line(pts, fill=color, width=_OPENING_WIDTH)
        elif t == "component_line":
            raw_pts = geom.get("points") or geom.get("polyline") or []
            pts = [to_out(p) for p in raw_pts]
            if len(pts) >= 2:
                kind = (lab.get("attributes") or {}).get("line_kind") or "other"
                color = _line_color(kind)
                if len(pts) >= 3:
                    region_kind = (lab.get("attributes") or {}).get("region_kind")
                    draw.polygon(pts, fill=_with_alpha(color, 34 if contrast == "normal" else 52))
                    _draw_hatch(target, pts, color=_with_alpha(color, 70))
                    _chip(draw, chip_font, _region_label(region_kind) if region_kind else str(kind), _poly_center(pts), out_w, out_h)
                draw.line(pts, fill=color, width=_COMPONENT_LINE_WIDTH + 1, joint="curve")
                for p in pts:
                    draw.ellipse([p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3], fill=color)
        elif t == "height_mark":
            anchor = to_out(geom.get("anchor") or [0, 0])
            if in_bounds(anchor):
                _draw_height_mark(
                    draw,
                    anchor,
                    lab.get("attributes") or {},
                    chip_font,
                    out_w,
                    out_h,
                    show_guide=render_height_guides,
                )
        elif t == "dimensioned_distance":
            attrs = lab.get("attributes") or {}
            start = to_out(geom.get("start") or [0, 0])
            end = to_out(geom.get("end") or [0, 0])
            is_ref = bool(attrs.get("is_reference"))
            color = _DIM_REF_COLOR if is_ref else _DIM_COLOR
            if in_bounds(start) or in_bounds(end):
                if is_ref:
                    _draw_dashed_line(draw, start, end, color, width=_DIM_WIDTH + 1, dash=7, gap=4)
                else:
                    draw.line([start, end], fill=color, width=_DIM_WIDTH)
                # Arrow caps at both endpoints — small perpendicular
                # tick marks so the agent can see exactly where the
                # endpoints sit.
                _draw_dim_cap(draw, start, end, color)
                _draw_dim_cap(draw, end, start, color)
                if is_ref:
                    mid = ((start[0] + end[0]) // 2, (start[1] + end[1]) // 2)
                    _draw_star(draw, (mid[0], mid[1] - 18), 7, color)
                orientation = attrs.get("target_orientation")
                if orientation in ("horizontal", "vertical"):
                    _chip(draw, chip_font, "H" if orientation == "horizontal" else "V", (start[0] + 8, start[1] + 12), out_w, out_h)
        # dimension_number drawn in pass 2 (chip text on top of strokes)

        _draw_status_marker(draw, chip_font, lab, to_out, in_bounds, out_w, out_h)

    # Pass 2 — text chips on top.
    for lab in labels:
        t = lab.get("type")
        attrs = lab.get("attributes") or {}
        geom = lab.get("geometry") or {}
        if t == "dimensioned_distance":
            start = to_out(geom.get("start") or [0, 0])
            end = to_out(geom.get("end") or [0, 0])
            mid = ((start[0] + end[0]) // 2, (start[1] + end[1]) // 2)
            value_mm = attrs.get("value_mm")
            if value_mm is not None and in_bounds(mid):
                txt = f"{value_mm/1000:.2f}m"
                _chip(draw, label_font, txt, mid, out_w, out_h)
                if attrs.get("is_reference"):
                    _badge(draw, chip_font, "M1", (mid[0], mid[1] + 14), out_w, out_h, _DIM_REF_COLOR)
        elif t == "dimension_number":
            anchor = geom.get("anchor")
            bbox = geom.get("bbox")
            txt = str(attrs.get("text") or "")
            parsed = attrs.get("parsed_value_mm")
            if parsed is not None and txt:
                txt = f"{txt} ({parsed/1000:.2f}m)"
            elif parsed is not None:
                txt = f"{parsed/1000:.2f}m"
            if bbox:
                pts = [to_out(p) for p in bbox]
                if len(pts) >= 4:
                    draw.line(pts + [pts[0]], fill=_DIM_COLOR, width=2)
                    _chip(draw, chip_font, txt, _poly_center(pts), out_w, out_h)
            if anchor is not None:
                pt = to_out(anchor)
                if txt and in_bounds(pt):
                    _chip(draw, chip_font, txt, pt, out_w, out_h)
        elif t == "height_mark":
            anchor = to_out(geom.get("anchor") or [0, 0])
            v = attrs.get("value_mm")
            datum = attrs.get("datum")
            if v is not None and in_bounds(anchor):
                txt = f"{v/1000:+.2f}m"
                if datum:
                    txt += f" ({datum})"
                _chip(draw, chip_font, txt, (anchor[0] + 10, anchor[1]),
                      out_w, out_h)

    if clean:
        # Clean QA intentionally lets the source ink show through the labels.
        # In high-contrast mode keep the semantic colors strong enough that
        # wall bodies, openings, status markers, and relation cues remain
        # machine- and human-readable on a faded background.
        label_alpha = 0.9 if contrast == "high" else 0.62
        alpha = target.getchannel("A").point(lambda v: int(v * label_alpha))
        target.putalpha(alpha)
        base = Image.alpha_composite(base, target)

    return base


def _clean_base(
    image: Image.Image,
    *,
    region: tuple[int, int, int, int] | None,
    max_dim: int,
    enhance: str | None,
    background_opacity: float = 1.0,
) -> Image.Image:
    """Build the QA base: the drawing at FULL opacity, NO grid. Crop +
    downscale + optional enhance EXACTLY as render_grid_overlay does (same
    compute_output_size + _enhance_image) so the (sx, sy) mapping computed
    by the caller still lands labels on the right pixels."""
    from .grid_render import _enhance_image, compute_output_size

    if region is not None:
        x0, y0, x1, y1 = (int(v) for v in region)
        img = image.crop((x0, y0, x1, y1))
    else:
        img = image
    cw, ch = img.size
    ow, oh = compute_output_size(cw, ch, max_dim)
    if (ow, oh) != (cw, ch):
        img = img.resize((ow, oh), Image.LANCZOS)
    mode = (enhance or "none").lower()
    if mode != "none":
        img = _enhance_image(img, mode)
    if background_opacity < 1.0:
        from .grid_render import _blend_to_white
        img = _blend_to_white(img.convert("RGBA"), background_opacity)
    return img.convert("RGBA")


def _wall_thickness_mm(label: dict) -> float:
    attrs = label.get("attributes") or {}
    value = attrs.get("thickness_mm")
    try:
        thickness = float(value)
    except (TypeError, ValueError):
        thickness = 365.0
    return max(50.0, min(800.0, thickness))


def _wall_band_points(
    start: Sequence[float],
    end: Sequence[float],
    thickness_mm: float,
    *,
    px_per_mm: float | None = None,
) -> list[tuple[float, float]]:
    """Same visual wall-band model as AnnotatePage.

    Wall labels store a structural axis plus thickness. For verification the
    renderer must show the occupied wall body, not only the axis, or the agent
    misses overlap/opening mistakes. The visual px/mm scale intentionally
    mirrors the UI's pragmatic display scale rather than scene calibration.
    """
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    if length <= 0:
        return []
    ux = dx / length
    uy = dy / length
    px = -uy
    py = ux
    scale = px_per_mm if px_per_mm and px_per_mm > 0 else _WALL_PX_PER_MM
    half = (thickness_mm * scale) / 2.0
    sx = x0 - ux * half
    sy = y0 - uy * half
    ex = x1 + ux * half
    ey = y1 + uy * half
    return [
        (sx + px * half, sy + py * half),
        (ex + px * half, ey + py * half),
        (ex - px * half, ey - py * half),
        (sx - px * half, sy - py * half),
    ]


def _draw_hatch(
    target: Image.Image,
    polygon: list[tuple[int, int]],
    *,
    color: tuple[int, int, int, int] = _WALL_HATCH_COLOR,
) -> None:
    if len(polygon) < 3:
        return
    mask = Image.new("L", target.size, 0)
    ImageDraw.Draw(mask).polygon(polygon, fill=255)
    hatch = Image.new("RGBA", target.size, (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(hatch, "RGBA")
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    x0, x1 = max(0, min(xs) - 24), min(target.size[0], max(xs) + 24)
    y0, y1 = max(0, min(ys) - 24), min(target.size[1], max(ys) + 24)
    spacing = 12
    for offset in range(int(x0 - (y1 - y0)) - spacing, int(x1) + spacing, spacing):
        hdraw.line([(offset, y1), (offset + (y1 - y0), y0)], fill=color, width=1)
    hatch.putalpha(ImageChops.multiply(hatch.getchannel("A"), mask))
    target.alpha_composite(hatch)


def _with_alpha(color: tuple[int, int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return (color[0], color[1], color[2], alpha)


def _opening_color(kind: str) -> tuple[int, int, int, int]:
    return _OPENING_KIND_COLOR.get(kind or "window", _OPENING_KIND_COLOR["other"])


def _line_color(kind: str) -> tuple[int, int, int, int]:
    return _LINE_KIND_COLOR.get(kind or "other", _LINE_KIND_COLOR["other"])


def _has_relation(label: dict, kind: str) -> bool:
    return any((r or {}).get("kind") == kind for r in (label.get("relations") or []))


def _poly_center(pts: Sequence[tuple[int, int]]) -> tuple[int, int]:
    if not pts:
        return (0, 0)
    return (int(sum(p[0] for p in pts) / len(pts)), int(sum(p[1] for p in pts) / len(pts)))


def _label_center(label: dict, to_out) -> tuple[int, int] | None:
    geom = label.get("geometry") or {}
    t = label.get("type")
    if "anchor" in geom:
        return to_out(geom["anchor"])
    if "start" in geom and "end" in geom:
        a = to_out(geom["start"])
        b = to_out(geom["end"])
        return ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
    if "quad" in geom and geom["quad"]:
        return _poly_center([to_out(p) for p in geom["quad"]])
    pts = geom.get("points") or geom.get("polyline")
    if pts:
        return _poly_center([to_out(p) for p in pts])
    if t == "view_opening":
        if geom.get("shape") == "circle":
            return to_out(geom.get("center") or [0, 0])
        if "circle" in geom:
            return to_out((geom["circle"] or {}).get("center") or [0, 0])
        if "polygon" in geom:
            return _poly_center([to_out(p) for p in geom["polygon"]])
        top = geom.get("top_edge") or []
        bottom = geom.get("bottom_edge") or []
        if top or bottom:
            return _poly_center([to_out(p) for p in [*top, *bottom]])
    if "bbox" in geom and geom["bbox"]:
        return _poly_center([to_out(p) for p in geom["bbox"]])
    return None


def _draw_required_relation_cues(
    draw: ImageDraw.ImageDraw,
    labels: Sequence[dict],
    label_by_id: dict[str, dict],
    to_out,
    in_bounds,
    out_w: int,
    out_h: int,
) -> None:
    for lab in labels:
        t = lab.get("type")
        if t == "floorplan_opening":
            a = _label_center(lab, to_out)
            if not a:
                continue
            related_walls = [
                label_by_id.get(str((r or {}).get("other_id")))
                for r in (lab.get("relations") or [])
                if (r or {}).get("kind") == "belongs_to"
            ]
            for wall in related_walls:
                if not wall or wall.get("type") != "wall":
                    continue
                b = _label_center(wall, to_out)
                if b:
                    _draw_dashed_line(draw, a, b, _RELATION_COLOR, width=2, dash=5, gap=5)
                    for p in (a, b):
                        if in_bounds(p):
                            draw.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], fill=_RELATION_COLOR)
        elif t == "dimension_number":
            a = _label_center(lab, to_out)
            if not a:
                continue
            related = [
                label_by_id.get(str((r or {}).get("other_id")))
                for r in (lab.get("relations") or [])
                if (r or {}).get("kind") == "labels"
            ]
            for other in related:
                if not other or other.get("type") != "dimensioned_distance":
                    continue
                b = _label_center(other, to_out)
                if b:
                    _draw_dashed_line(draw, a, b, _RELATION_COLOR, width=2, dash=5, gap=5)
        elif t == "dimensioned_distance":
            a = _label_center(lab, to_out)
            if not a:
                continue
            for other in labels:
                if other.get("type") != "dimension_number":
                    continue
                if any((r or {}).get("kind") == "labels" and str((r or {}).get("other_id")) == str(lab.get("id"))
                       for r in (other.get("relations") or [])):
                    b = _label_center(other, to_out)
                    if b:
                        _draw_dashed_line(draw, a, b, _RELATION_COLOR, width=2, dash=5, gap=5)


def _warn(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    text: str,
    pos: tuple[int, int],
    canvas_w: int,
    canvas_h: int,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 3
    x = max(0, min(canvas_w - tw - 2 * pad - 1, pos[0] + 4))
    y = max(0, min(canvas_h - th - 2 * pad - 1, pos[1] + 4))
    draw.rectangle([x - pad, y - pad, x + tw + pad, y + th + pad], fill=_WARNING_BG, outline=_MISSING_COLOR)
    draw.text((x, y), text, font=font, fill=_WARNING_FG)


def _draw_floorplan_opening_inner(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[int, int]],
    attrs: dict,
    color: tuple[int, int, int, int],
) -> None:
    if len(pts) != 4:
        return
    a, b, _c, d = pts
    kind = attrs.get("opening_kind") or "window"
    len_x = b[0] - a[0]
    len_y = b[1] - a[1]
    length = math.hypot(len_x, len_y)
    dep_x = d[0] - a[0]
    dep_y = d[1] - a[1]
    depth = math.hypot(dep_x, dep_y)
    if length < 1 or depth < 1:
        return
    if kind == "window":
        for i, t in enumerate((0.25, 0.5, 0.75)):
            x1 = int(a[0] + dep_x * t)
            y1 = int(a[1] + dep_y * t)
            draw.line([(x1, y1), (int(x1 + len_x), int(y1 + len_y))], fill=color, width=2 if i != 1 else 1)
    elif kind == "door":
        swing_side = attrs.get("swing_side") or "left"
        swing = attrs.get("swing") or "in"
        hinge = b if swing_side == "right" else a
        closed_sign = -1 if swing_side == "right" else 1
        closed_tip = (hinge[0] + closed_sign * len_x, hinge[1] + closed_sign * len_y)
        dep_ux, dep_uy = dep_x / depth, dep_y / depth
        perp_sign = -1 if swing == "out" else 1
        open_tip = (hinge[0] + perp_sign * dep_ux * length, hinge[1] + perp_sign * dep_uy * length)
        draw.line([hinge, (int(open_tip[0]), int(open_tip[1]))], fill=color, width=2)
        start_ang = math.atan2(closed_tip[1] - hinge[1], closed_tip[0] - hinge[0])
        end_ang = math.atan2(open_tip[1] - hinge[1], open_tip[0] - hinge[0])
        delta = end_ang - start_ang
        while delta > math.pi:
            delta -= 2 * math.pi
        while delta < -math.pi:
            delta += 2 * math.pi
        arc = []
        for i in range(18):
            ang = start_ang + delta * (i / 17)
            arc.append((int(hinge[0] + length * math.cos(ang)), int(hinge[1] + length * math.sin(ang))))
        if len(arc) >= 2:
            draw.line(arc, fill=color, width=1)
    else:
        center = _poly_center(pts)
        draw.text((center[0] + 4, center[1] - 6), str(kind), font=_load_font(10), fill=color)


def _draw_height_mark(
    draw: ImageDraw.ImageDraw,
    anchor: tuple[int, int],
    attrs: dict,
    font: ImageFont.ImageFont,
    out_w: int,
    out_h: int,
    *,
    show_guide: bool,
) -> None:
    value = attrs.get("value_mm")
    datum = attrs.get("datum")
    is_bezug = value == 0
    color = _HEIGHT_BEZUG_COLOR if is_bezug else _HEIGHT_MARK_COLOR
    x, y = anchor
    if show_guide:
        draw.line([(0, y), (out_w - 1, y)], fill=_with_alpha(color, 120 if is_bezug else 130), width=2 if is_bezug else 1)
    tri = [(x, y + 4), (x - 10, y - 16), (x + 10, y - 16)]
    if is_bezug:
        draw.polygon(tri, fill=color, outline=(180, 83, 9, 255))
        draw.polygon([(x, y + 7), (x - 15, y - 21), (x + 15, y - 21)], outline=color)
    else:
        draw.polygon(tri, fill=_with_alpha(color, 225), outline=color)
    parts = []
    if datum:
        parts.append(_datum_label(str(datum)))
    if value is not None:
        parts.append("±0,00" if value == 0 else f"{value/1000:+.2f} m".replace(".", ","))
    if parts:
        _chip(draw, font, " ".join(parts), (x + 10, y), out_w, out_h)


def _datum_label(datum: str) -> str:
    return {
        "first": "First",
        "traufe": "Traufe",
        "gelaende": "Gelände",
        "geschoss": "Geschoss",
        "ok_ffb": "OK FFB",
        "sockel": "Sockel",
        "kniestock": "Kniestock",
        "other": "",
    }.get(datum, datum)


def _region_label(kind: str | None) -> str:
    return {
        "roof": "Dachfläche",
        "gable": "Giebel",
        "wall_body": "Wandfläche",
        "ground": "Geländefläche",
        "unknown": "Fläche",
    }.get(kind or "", str(kind or ""))


def _draw_status_marker(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    label: dict,
    to_out,
    in_bounds,
    out_w: int,
    out_h: int,
) -> None:
    status = label.get("status")
    if status in (None, "readable"):
        return
    anchor_pt = _label_center(label, to_out)
    if not anchor_pt or not in_bounds(anchor_pt):
        return
    if status == "uncertain":
        color = _UNCERTAIN_RING_COLOR
        text = "?"
    elif status == "missing":
        color = _MISSING_COLOR
        text = "missing"
    elif status == "not_readable":
        color = _NOT_READABLE_COLOR
        text = "not readable"
    else:
        color = _UNCERTAIN_RING_COLOR
        text = str(status)
    r = 12
    draw.ellipse([anchor_pt[0] - r, anchor_pt[1] - r, anchor_pt[0] + r, anchor_pt[1] + r], outline=color, width=3)
    _warn(draw, font, text, (anchor_pt[0] + r, anchor_pt[1] + r), out_w, out_h)


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    a: tuple[int, int],
    b: tuple[int, int],
    color: tuple[int, int, int, int],
    *,
    width: int = 1,
    dash: int = 6,
    gap: int = 4,
) -> None:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1:
        return
    ux, uy = dx / length, dy / length
    pos = 0.0
    while pos < length:
        end = min(length, pos + dash)
        p1 = (int(a[0] + ux * pos), int(a[1] + uy * pos))
        p2 = (int(a[0] + ux * end), int(a[1] + uy * end))
        draw.line([p1, p2], fill=color, width=width)
        pos += dash + gap


def _draw_star(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    radius: int,
    color: tuple[int, int, int, int],
) -> None:
    cx, cy = center
    pts = []
    for i in range(10):
        r = radius if i % 2 == 0 else radius * 0.45
        ang = -math.pi / 2 + i * math.pi / 5
        pts.append((int(cx + r * math.cos(ang)), int(cy + r * math.sin(ang))))
    draw.polygon(pts, fill=color)


def _draw_dim_cap(
    draw: ImageDraw.ImageDraw,
    at: tuple[int, int],
    away: tuple[int, int],
    color: tuple[int, int, int, int],
) -> None:
    """Draw a small perpendicular tick at `at`, on the line going from
    `at` away from `away`. So both endpoints get a visible marker."""
    dx = away[0] - at[0]
    dy = away[1] - at[1]
    length = math.hypot(dx, dy)
    if length < 1:
        return
    # Perpendicular unit vector.
    px = -dy / length
    py = dx / length
    cap = 6
    p1 = (int(at[0] + px * cap), int(at[1] + py * cap))
    p2 = (int(at[0] - px * cap), int(at[1] - py * cap))
    draw.line([p1, p2], fill=color, width=2)


def _chip(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    text: str,
    pos: tuple[int, int],
    canvas_w: int,
    canvas_h: int,
) -> None:
    """Draw a small text chip at `pos`. Stays inside the canvas."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 2
    x = max(0, min(canvas_w - tw - 2 * pad - 1, pos[0] + 4))
    y = max(0, min(canvas_h - th - 2 * pad - 1, pos[1] - th - pad - 2))
    draw.rectangle(
        [x - pad, y - pad, x + tw + pad, y + th + pad],
        fill=_LABEL_CHIP_BG,
    )
    draw.text((x, y), text, font=font, fill=_LABEL_CHIP_FG)


def _badge(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    text: str,
    pos: tuple[int, int],
    canvas_w: int,
    canvas_h: int,
    color: tuple[int, int, int, int],
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x = 5
    pad_y = 3
    w = tw + 2 * pad_x
    h = th + 2 * pad_y
    x = max(0, min(canvas_w - w - 1, pos[0] - w // 2))
    y = max(0, min(canvas_h - h - 1, pos[1] - h // 2))
    draw.rounded_rectangle([x, y, x + w, y + h], radius=3, fill=color, outline=(255, 255, 255, 245), width=1)
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=(255, 255, 255, 255))
