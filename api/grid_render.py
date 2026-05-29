"""Grid overlay rendering for the agentic-labeling path.

Per the agentic-labeling tracker §C2: agent vision needs a
coordinate-anchored grid so it can point at pixels precisely. We
de-emphasise the original image (alpha=0.5 over white) and overlay
three nested grid tiers:

    broad  — every max(W,H)/10 px, bold black, every intersection labeled
    finer  — every max(W,H)/50 px, medium grey, every 5th line labeled
    detail — every max(W,H)/200 px, very faint stipple, no labels

`render_grid_overlay(...)` is the single public function — pure, no
I/O. HTTP endpoints in api/main.py wrap it with disk caching.

Coordinate labels show SOURCE pixels even when the image was cropped,
so an agent reading a zoom can call back into upsert_label against the
un-cropped scene without any further translation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

ALL_TIERS = ("broad", "finer", "detail")
DEFAULT_TIERS = ALL_TIERS

_TIER_FRACTION = {"broad": 1 / 10, "finer": 1 / 50, "detail": 1 / 200}

_BROAD_COLOR = (0, 0, 0, 220)
_FINER_COLOR = (90, 90, 110, 160)
_DETAIL_COLOR = (140, 140, 160, 60)
_LABEL_BG = (255, 255, 255, 230)
_LABEL_FG = (0, 0, 0, 255)

_MARGIN_PX = 56  # room for coordinate labels on the broad tier


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
        New RGBA image, dimensions ≤ max_dim+margin on each side.
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

    canvas = Image.new(
        "RGBA",
        (cw + _MARGIN_PX, ch + _MARGIN_PX),
        (255, 255, 255, 255),
    )
    if cropped.mode != "RGBA":
        cropped = cropped.convert("RGBA")
    if background_opacity < 1.0:
        cropped = _blend_to_white(cropped, background_opacity)
    canvas.paste(
        cropped,
        (_MARGIN_PX, _MARGIN_PX),
        cropped if cropped.mode == "RGBA" else None,
    )

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
    font = _load_font(11)

    # Detail first so darker tiers overdraw it.
    if "detail" in tiers:
        _draw_tier(draw, spec, "detail")
    if "finer" in tiers:
        _draw_tier(draw, spec, "finer", label_font=font)
    if "broad" in tiers:
        _draw_tier(draw, spec, "broad", label_font=font)

    _draw_corner_legend(draw, canvas.size, spec, font)
    return canvas


def _blend_to_white(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 1.0:
        return img
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.blend(white, img, alpha)


def _draw_tier(
    draw: ImageDraw.ImageDraw,
    spec: _Spec,
    tier: str,
    *,
    label_font: ImageFont.ImageFont | None = None,
) -> None:
    if tier == "broad":
        color, width, step_src, label_every = _BROAD_COLOR, 2, spec.broad_step, 1
    elif tier == "finer":
        color, width, step_src, label_every = _FINER_COLOR, 1, spec.finer_step, 5
    elif tier == "detail":
        color, width, step_src, label_every = _DETAIL_COLOR, 1, spec.detail_step, 0
    else:
        return

    image_x0, image_y0 = _MARGIN_PX, _MARGIN_PX
    image_x1 = _MARGIN_PX + spec.out_w
    image_y1 = _MARGIN_PX + spec.out_h

    # Vertical grid lines — iterate at step_src in source-frame.
    src_x_start = ((spec.region_origin[0] + step_src - 1) // step_src) * step_src
    src_x = src_x_start
    line_idx = 0
    src_x_end = spec.region_origin[0] + spec.crop_src_w
    while src_x <= src_x_end:
        out_x = image_x0 + int((src_x - spec.region_origin[0]) * spec.px_per_src_x)
        draw.line([(out_x, image_y0), (out_x, image_y1 - 1)], fill=color, width=width)
        if label_font is not None and label_every and line_idx % label_every == 0:
            _draw_axis_label(draw, label_font, str(src_x), (out_x, image_y0), "top")
        src_x += step_src
        line_idx += 1

    # Horizontal grid lines.
    src_y_start = ((spec.region_origin[1] + step_src - 1) // step_src) * step_src
    src_y = src_y_start
    line_idx = 0
    src_y_end = spec.region_origin[1] + spec.crop_src_h
    while src_y <= src_y_end:
        out_y = image_y0 + int((src_y - spec.region_origin[1]) * spec.px_per_src_y)
        draw.line([(image_x0, out_y), (image_x1 - 1, out_y)], fill=color, width=width)
        if label_font is not None and label_every and line_idx % label_every == 0:
            _draw_axis_label(draw, label_font, str(src_y), (image_x0, out_y), "left")
        src_y += step_src
        line_idx += 1


def _draw_axis_label(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    text: str,
    pos: tuple[int, int],
    side: str,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 2
    if side == "top":
        x = pos[0] - tw // 2
        y = pos[1] - th - pad - 4
    else:  # left
        x = pos[0] - tw - pad - 4
        y = pos[1] - th // 2
    draw.rectangle(
        [x - pad, y - pad, x + tw + pad, y + th + pad],
        fill=_LABEL_BG,
    )
    draw.text((x, y), text, font=font, fill=_LABEL_FG)


def _draw_corner_legend(
    draw: ImageDraw.ImageDraw,
    canvas_size: tuple[int, int],
    spec: _Spec,
    font: ImageFont.ImageFont,
) -> None:
    lines = [
        f"grid: source pixels",
        f"broad/finer/detail = {spec.broad_step}/{spec.finer_step}/{spec.detail_step}px",
    ]
    if spec.source_size:
        lines.insert(
            0,
            (
                f"crop ({spec.region_origin[0]},{spec.region_origin[1]})"
                f" of {spec.source_size[0]}x{spec.source_size[1]}"
            ),
        )
    cw, ch = canvas_size
    pad = 6
    line_h = 14
    block_h = line_h * len(lines) + pad * 2
    block_w = max(draw.textbbox((0, 0), ln, font=font)[2] for ln in lines) + pad * 2
    x0 = cw - block_w - 8
    y0 = ch - block_h - 8
    draw.rectangle(
        [x0, y0, x0 + block_w, y0 + block_h],
        fill=(255, 255, 255, 240),
        outline=(0, 0, 0, 200),
    )
    for i, ln in enumerate(lines):
        draw.text(
            (x0 + pad, y0 + pad + i * line_h),
            ln,
            font=font,
            fill=_LABEL_FG,
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
