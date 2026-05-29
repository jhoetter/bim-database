"""Server-side label rendering on top of the grid overlay.

Per agentic-labeling-followups-2-tracker §H5: the agent's biggest
quality problem is one-shot label placement — it picks coords from
the grid view, calls `upsert_label`, and never looks at its own
work. This module renders the saved labels back onto the grid image
so the agent can verify placement visually.

Rendering vocabulary (deliberately minimal — this isn't the full
AnnotatePage SVG; it's a sanity-check view):

  wall                    — thick orange stroke between start and end
  floorplan_opening       — magenta quad outline
  view_opening            — magenta circle, polygon, or top/bottom edges
  component_line          — teal polyline
  height_mark             — dark blue dot + value chip
  dimensioned_distance    — green stroke + arrow caps; ref dims get a
                            thicker red stroke so the agent can spot a
                            misplaced reference at a glance
  dimension_number        — small grey text chip at anchor

All labels are drawn AT SOURCE PIXEL COORDS; the underlying grid
renderer translates to output pixels exactly the same way. So a wall
labelled with endpoints [200, 500] → [1100, 500] will visually line
up with the grid line marked "500" along the Y axis.
"""
from __future__ import annotations

import math
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from .grid_render import _load_font, render_grid_overlay

_WALL_COLOR = (255, 140, 0, 220)
_WALL_WIDTH = 4

_FLOORPLAN_OPENING_COLOR = (200, 0, 200, 220)
_VIEW_OPENING_COLOR = (200, 0, 200, 200)
_OPENING_WIDTH = 2

_COMPONENT_LINE_COLOR = (0, 160, 160, 220)
_COMPONENT_LINE_WIDTH = 2

_HEIGHT_MARK_COLOR = (30, 30, 200, 255)
_HEIGHT_MARK_RADIUS = 5

_DIM_COLOR = (0, 160, 0, 230)
_DIM_REF_COLOR = (220, 30, 30, 230)
_DIM_WIDTH = 3

_DIM_NUMBER_FG = (40, 40, 40, 255)
_DIM_NUMBER_BG = (255, 255, 230, 220)

_UNCERTAIN_RING_COLOR = (240, 160, 0, 255)
_UNCERTAIN_RING_WIDTH = 2

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
    # Reuse the grid renderer for the base. It handles region cropping +
    # max_dim downscaling + the coordinate-anchored grid + corner legend.
    base = render_grid_overlay(
        image, tiers=tiers, region=region, max_dim=max_dim, enhance=enhance,
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

    draw = ImageDraw.Draw(base, "RGBA")
    label_font = _load_font(11)
    chip_font = _load_font(10)

    # Pass 1 — strokes / geometry.
    for lab in labels:
        t = lab.get("type")
        geom = lab.get("geometry") or {}
        status = lab.get("status")
        if t == "wall":
            start = to_out(geom.get("start") or [0, 0])
            end = to_out(geom.get("end") or [0, 0])
            if in_bounds(start) or in_bounds(end):
                draw.line([start, end], fill=_WALL_COLOR, width=_WALL_WIDTH)
        elif t == "floorplan_opening":
            quad = geom.get("quad") or []
            if len(quad) == 4:
                pts = [to_out(p) for p in quad]
                draw.polygon(pts, outline=_FLOORPLAN_OPENING_COLOR)
        elif t == "view_opening":
            if "circle" in geom:
                c = geom["circle"]
                center = to_out(c.get("center") or [0, 0])
                r = int((c.get("radius_px") or 0) * sx)
                if r > 0:
                    draw.ellipse(
                        [center[0] - r, center[1] - r, center[0] + r, center[1] + r],
                        outline=_VIEW_OPENING_COLOR,
                        width=_OPENING_WIDTH,
                    )
            elif "polygon" in geom:
                pts = [to_out(p) for p in geom["polygon"]]
                if len(pts) >= 3:
                    draw.polygon(pts, outline=_VIEW_OPENING_COLOR)
            elif "top_edge" in geom or "bottom_edge" in geom:
                for k in ("top_edge", "bottom_edge"):
                    edge = geom.get(k) or []
                    pts = [to_out(p) for p in edge]
                    if len(pts) >= 2:
                        draw.line(pts, fill=_VIEW_OPENING_COLOR, width=_OPENING_WIDTH)
        elif t == "component_line":
            pts = [to_out(p) for p in (geom.get("points") or [])]
            if len(pts) >= 2:
                draw.line(pts, fill=_COMPONENT_LINE_COLOR, width=_COMPONENT_LINE_WIDTH)
        elif t == "height_mark":
            anchor = to_out(geom.get("anchor") or [0, 0])
            if in_bounds(anchor):
                r = _HEIGHT_MARK_RADIUS
                draw.ellipse(
                    [anchor[0] - r, anchor[1] - r, anchor[0] + r, anchor[1] + r],
                    fill=_HEIGHT_MARK_COLOR,
                )
                # Thin horizontal line across the canvas at this Y
                # — the SPA uses these as implied Bezugslinien. Faint
                # so they don't overwhelm.
                draw.line(
                    [(0, anchor[1]), (out_w - 1, anchor[1])],
                    fill=(30, 30, 200, 60),
                    width=1,
                )
        elif t == "dimensioned_distance":
            start = to_out(geom.get("start") or [0, 0])
            end = to_out(geom.get("end") or [0, 0])
            is_ref = bool((lab.get("attributes") or {}).get("is_reference"))
            color = _DIM_REF_COLOR if is_ref else _DIM_COLOR
            if in_bounds(start) or in_bounds(end):
                draw.line([start, end], fill=color, width=_DIM_WIDTH)
                # Arrow caps at both endpoints — small perpendicular
                # tick marks so the agent can see exactly where the
                # endpoints sit.
                _draw_dim_cap(draw, start, end, color)
                _draw_dim_cap(draw, end, start, color)
        # dimension_number drawn in pass 2 (chip text on top of strokes)

        # Uncertain markers — wrap a ring around the first endpoint /
        # anchor so reviewers can see "this label is flagged uncertain"
        # at a glance.
        if status == "uncertain":
            anchor_pt = None
            if "start" in geom:
                anchor_pt = to_out(geom["start"])
            elif "anchor" in geom:
                anchor_pt = to_out(geom["anchor"])
            elif "points" in geom and geom["points"]:
                anchor_pt = to_out(geom["points"][0])
            elif "quad" in geom and geom["quad"]:
                anchor_pt = to_out(geom["quad"][0])
            if anchor_pt and in_bounds(anchor_pt):
                r = 10
                draw.ellipse(
                    [anchor_pt[0] - r, anchor_pt[1] - r,
                     anchor_pt[0] + r, anchor_pt[1] + r],
                    outline=_UNCERTAIN_RING_COLOR,
                    width=_UNCERTAIN_RING_WIDTH,
                )

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
                if attrs.get("is_reference"):
                    txt = "REF " + txt
                _chip(draw, label_font, txt, mid, out_w, out_h)
        elif t == "dimension_number":
            anchor = geom.get("anchor")
            if anchor is not None:
                pt = to_out(anchor)
                txt = str(attrs.get("text") or "")
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

    return base


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
