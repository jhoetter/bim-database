"""Grid overlay rendering for the agentic-labeling path.

Per agentic-labeling-followups-tracker §G2 (rewritten 2026-05-29 to
drop the outer margin):

    broad  — every max(W,H)/10 px, bold black, every intersection
             labelled inline with a white-chip background
    finer  — every max(W,H)/50 px, medium grey, every 5th line
             labelled inline
    detail — every max(W,H)/200 px, very faint stipple, no labels

OUTPUT DIMENSIONS == SOURCE DIMENSIONS (or the cropped region, when
`region` is set). No 56-px padding. The SVG layer in AnnotatePage /
ExtractPage swaps the image href without any layout shift, so the
labels the agent reads off the grid map back to source pixels
cleanly (no preserveAspectRatio scaling needed).

Coordinate labels show SOURCE pixels even when the image was cropped,
so an agent reading a zoom can call back into upsert_label against
the un-cropped scene without any further translation.

Default tier set: `("broad", "finer")` — detail is opt-in. Per §8
decision 3 of the followups tracker: at full-image scale the detail
tier is visual noise and only earns its keep on zoomed crops.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

ALL_TIERS = ("broad", "finer", "detail")
DEFAULT_TIERS = ("broad", "finer")  # detail is opt-in

# Cell-size fraction of the long edge. 10 broad cells / 50 finer / 200 detail.
_TIER_FRACTION = {"broad": 1 / 10, "finer": 1 / 50, "detail": 1 / 200}

# Drawing weights.
_BROAD_COLOR = (0, 0, 0, 220)
_FINER_COLOR = (90, 90, 110, 160)
_DETAIL_COLOR = (140, 140, 160, 60)

_LABEL_BG = (255, 255, 255, 220)
_LABEL_FG = (0, 0, 0, 255)
_LABEL_PAD = 2

_LEGEND_BG = (255, 255, 255, 200)
_LEGEND_FG = (40, 40, 40, 255)


@dataclass
class _Spec:
    out_w: int
    out_h: int
    crop_src_w: int
    crop_src_h: int
    region_origin: tuple[int, int]
    source_size: tuple[int, int] | None
    broad_step: int
    finer_step: int
    detail_step: int

    @property
    def px_per_src_x(self) -> float:
        return self.out_w / self.crop_src_w

    @property
    def px_per_src_y(self) -> float:
        return self.out_h / self.crop_src_h


def render_grid_overlay(
    image: Image.Image,
    *,
    tiers: Sequence[str] = DEFAULT_TIERS,
    region: tuple[int, int, int, int] | None = None,
    max_dim: int = 1600,
    background_opacity: float = 0.5,
) -> Image.Image:
    """Composite the source image with a coordinate-anchored grid overlay.

    Args:
        image:              source PIL image (PDF page render or scene crop).
        tiers:              subset of ('broad', 'finer', 'detail').
        region:             pixel rect (x0,y0,x1,y1) to crop first; coords
                            in the source image's frame. Labels in the
                            output reference SOURCE pixels regardless.
        max_dim:            cap on longest side of the OUTPUT image.
        background_opacity: 0.5 by default; image fades to half so the
                            grid stays legible.

    Returns:
        New RGBA image. Dimensions == cropped source dims (possibly
        downscaled to max_dim). NO outer margin — the entire output is
        the image content, with grid + labels drawn over it.
    """
    if not tiers:
        raise ValueError("at least one tier required")
    unknown = set(tiers) - set(ALL_TIERS)
    if unknown:
        raise ValueError(f"unknown tier(s): {sorted(unknown)}")
    if not 0.0 < background_opacity <= 1.0:
        raise ValueError("background_opacity must be in (0, 1]")

    src_w, src_h = image.size

    if region is not None:
        x0, y0, x1, y1 = (int(v) for v in region)
        if not (0 <= x0 < x1 <= src_w and 0 <= y0 < y1 <= src_h):
            raise ValueError(
                f"region {region!r} out of image bounds (image is {src_w}x{src_h})"
            )
        cropped = image.crop((x0, y0, x1, y1))
        region_origin = (x0, y0)
        crop_src_w = x1 - x0
        crop_src_h = y1 - y0
        source_size: tuple[int, int] | None = (src_w, src_h)
    else:
        cropped = image
        region_origin = (0, 0)
        crop_src_w = src_w
        crop_src_h = src_h
        source_size = None

    cw, ch = cropped.size
    if max(cw, ch) > max_dim:
        scale = max_dim / max(cw, ch)
        out_w = max(1, int(cw * scale))
        out_h = max(1, int(ch * scale))
        cropped = cropped.resize((out_w, out_h), Image.LANCZOS)
    cw, ch = cropped.size

    # Canvas == image dims; no margin. Grid + labels drawn ON the image.
    canvas = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
    if cropped.mode != "RGBA":
        cropped = cropped.convert("RGBA")
    if background_opacity < 1.0:
        cropped = _blend_to_white(cropped, background_opacity)
    canvas.paste(cropped, (0, 0), cropped if cropped.mode == "RGBA" else None)

    long_src = max(crop_src_w, crop_src_h)
    spec = _Spec(
        out_w=cw,
        out_h=ch,
        crop_src_w=crop_src_w,
        crop_src_h=crop_src_h,
        region_origin=region_origin,
        source_size=source_size,
        broad_step=max(1, int(long_src * _TIER_FRACTION["broad"])),
        finer_step=max(1, int(long_src * _TIER_FRACTION["finer"])),
        detail_step=max(1, int(long_src * _TIER_FRACTION["detail"])),
    )

    draw = ImageDraw.Draw(canvas, "RGBA")
    label_font = _load_font(11)
    legend_font = _load_font(10)

    # Order: detail → finer → broad so darker tiers overdraw lighter ones.
    # Labels go in a SECOND pass after all lines so they sit on top.
    if "detail" in tiers:
        _draw_tier_lines(draw, spec, "detail")
    if "finer" in tiers:
        _draw_tier_lines(draw, spec, "finer")
    if "broad" in tiers:
        _draw_tier_lines(draw, spec, "broad")

    # Labels — broad first (one per intersection), finer second (every 5th).
    if "broad" in tiers:
        _draw_tier_labels(draw, label_font, spec, "broad")
    if "finer" in tiers:
        _draw_tier_labels(draw, label_font, spec, "finer")

    _draw_top_right_legend(draw, canvas.size, spec, legend_font)
    return canvas


def _blend_to_white(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 1.0:
        return img
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.blend(white, img, alpha)


def _draw_tier_lines(draw: ImageDraw.ImageDraw, spec: _Spec, tier: str) -> None:
    if tier == "broad":
        color, width, step_src = _BROAD_COLOR, 2, spec.broad_step
    elif tier == "finer":
        color, width, step_src = _FINER_COLOR, 1, spec.finer_step
    elif tier == "detail":
        color, width, step_src = _DETAIL_COLOR, 1, spec.detail_step
    else:
        return

    # Vertical lines.
    src_x = ((spec.region_origin[0] + step_src - 1) // step_src) * step_src
    src_x_end = spec.region_origin[0] + spec.crop_src_w
    while src_x <= src_x_end:
        out_x = int((src_x - spec.region_origin[0]) * spec.px_per_src_x)
        if 0 <= out_x < spec.out_w:
            draw.line([(out_x, 0), (out_x, spec.out_h - 1)], fill=color, width=width)
        src_x += step_src

    # Horizontal lines.
    src_y = ((spec.region_origin[1] + step_src - 1) // step_src) * step_src
    src_y_end = spec.region_origin[1] + spec.crop_src_h
    while src_y <= src_y_end:
        out_y = int((src_y - spec.region_origin[1]) * spec.px_per_src_y)
        if 0 <= out_y < spec.out_h:
            draw.line([(0, out_y), (spec.out_w - 1, out_y)], fill=color, width=width)
        src_y += step_src


def _draw_tier_labels(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    spec: _Spec,
    tier: str,
) -> None:
    """Draw coordinate labels on tier intersections with a white-chip
    background. Stays inside the image bounds; never spills outside."""
    if tier == "broad":
        step_src = spec.broad_step
        label_every_line = 1   # every intersection
    elif tier == "finer":
        step_src = spec.finer_step
        label_every_line = 5   # every 5th finer line
    else:
        return

    # X positions to label (vertical grid lines).
    src_xs: list[int] = []
    src_x = ((spec.region_origin[0] + step_src - 1) // step_src) * step_src
    src_x_end = spec.region_origin[0] + spec.crop_src_w
    line_idx = 0
    while src_x <= src_x_end:
        if line_idx % label_every_line == 0:
            src_xs.append(src_x)
        src_x += step_src
        line_idx += 1

    # Y positions to label (horizontal grid lines).
    src_ys: list[int] = []
    src_y = ((spec.region_origin[1] + step_src - 1) // step_src) * step_src
    src_y_end = spec.region_origin[1] + spec.crop_src_h
    line_idx = 0
    while src_y <= src_y_end:
        if line_idx % label_every_line == 0:
            src_ys.append(src_y)
        src_y += step_src
        line_idx += 1

    # X-axis labels along the TOP of the image (just below the top edge).
    for sx in src_xs:
        out_x = int((sx - spec.region_origin[0]) * spec.px_per_src_x)
        _draw_label_chip(
            draw, font, str(sx), (out_x, 0),
            anchor="top-center", canvas_w=spec.out_w, canvas_h=spec.out_h,
        )

    # Y-axis labels along the LEFT of the image (just inside the left edge).
    for sy in src_ys:
        out_y = int((sy - spec.region_origin[1]) * spec.px_per_src_y)
        _draw_label_chip(
            draw, font, str(sy), (0, out_y),
            anchor="left-middle", canvas_w=spec.out_w, canvas_h=spec.out_h,
        )


def _draw_label_chip(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    text: str,
    pos: tuple[int, int],
    *,
    anchor: str,
    canvas_w: int,
    canvas_h: int,
) -> None:
    """Draw `text` at `pos` with a white-chip background. Clamps to the
    canvas so labels along the edge are never cut off."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if anchor == "top-center":
        # Label sits horizontally centered on `pos[0]`, just below the top.
        x = pos[0] - tw // 2
        y = 2
    elif anchor == "left-middle":
        # Label sits vertically centered on `pos[1]`, just inside the left.
        x = 2
        y = pos[1] - th // 2
    else:
        x, y = pos
    # Clamp the chip rectangle to the canvas.
    rx0 = max(0, x - _LABEL_PAD)
    ry0 = max(0, y - _LABEL_PAD)
    rx1 = min(canvas_w - 1, x + tw + _LABEL_PAD)
    ry1 = min(canvas_h - 1, y + th + _LABEL_PAD)
    # Shift the text inside the clamp if pinned to an edge.
    x = rx0 + _LABEL_PAD
    y = ry0 + _LABEL_PAD
    draw.rectangle([rx0, ry0, rx1, ry1], fill=_LABEL_BG)
    draw.text((x, y), text, font=font, fill=_LABEL_FG)


def _draw_top_right_legend(
    draw: ImageDraw.ImageDraw,
    canvas_size: tuple[int, int],
    spec: _Spec,
    font: ImageFont.ImageFont,
) -> None:
    """Tiny faint chip in the top-right corner. Smaller font than the
    axis labels so it doesn't compete with content."""
    cw, ch = canvas_size
    lines = [
        f"grid (src px): b/f/d = {spec.broad_step}/{spec.finer_step}/{spec.detail_step}",
    ]
    if spec.source_size:
        lines.append(
            f"crop ({spec.region_origin[0]},{spec.region_origin[1]}) "
            f"of {spec.source_size[0]}×{spec.source_size[1]}"
        )
    pad = 4
    line_h = 12
    bbox_widths = [draw.textbbox((0, 0), ln, font=font)[2] for ln in lines]
    block_w = max(bbox_widths) + pad * 2
    block_h = line_h * len(lines) + pad * 2
    x0 = max(0, cw - block_w - 4)
    y0 = 4
    draw.rectangle(
        [x0, y0, x0 + block_w, y0 + block_h],
        fill=_LEGEND_BG,
    )
    for i, ln in enumerate(lines):
        draw.text(
            (x0 + pad, y0 + pad + i * line_h),
            ln,
            font=font,
            fill=_LEGEND_FG,
        )


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Monaco.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()
