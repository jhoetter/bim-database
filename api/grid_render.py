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
from typing import Literal, Sequence

from PIL import Image, ImageDraw, ImageFont

ALL_TIERS = ("broad", "finer", "detail")
DEFAULT_TIERS = ("broad", "finer")  # detail is opt-in

# Contrast enhancement modes for faint freehand/pencil scans (issue #2).
# "none" is the default no-op; the rest lift legibility before the grid
# overlay is composited. Enhancement only changes pixel INTENSITY, never
# position — so SOURCE-pixel coordinates stay valid.
ENHANCE_MODES = ("none", "auto", "clahe", "threshold")
DEFAULT_ENHANCE = "none"
GRID_STYLES = ("standard", "coordinate_audit", "coordinate_pair", "coordinate_multicolor")
TargetLine = Literal["vertical", "horizontal", "none"]

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

_AUDIT_VERTICAL = {
    "broad": (0, 118, 255, 235),
    "finer": (0, 190, 255, 165),
    "detail": (56, 210, 255, 75),
}
_AUDIT_HORIZONTAL = {
    "broad": (220, 0, 95, 235),
    "finer": (255, 50, 185, 165),
    "detail": (255, 120, 205, 75),
}
_PAIR_VERTICAL = {
    "broad": (0, 150, 80, 240),
    "finer": (22, 190, 115, 170),
    "detail": (110, 225, 170, 80),
}
_PAIR_HORIZONTAL = {
    "broad": (230, 35, 35, 240),
    "finer": (255, 95, 95, 170),
    "detail": (255, 160, 160, 80),
}
_MULTI_PALETTE = [
    (230, 57, 70, 235),    # red
    (244, 140, 6, 235),    # orange
    (255, 202, 40, 235),   # yellow
    (46, 204, 113, 235),   # green
    (0, 150, 136, 235),    # teal
    (0, 188, 212, 235),    # cyan
    (33, 150, 243, 235),   # blue
    (103, 58, 183, 235),   # violet
    (156, 39, 176, 235),   # purple
    (233, 30, 99, 235),    # pink
]
_TARGET_COLOR = (255, 190, 0, 255)
_TARGET_BG = (20, 20, 20, 230)
_TARGET_FG = (255, 255, 255, 255)


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


def compute_output_size(crop_w: int, crop_h: int, max_dim: int) -> tuple[int, int]:
    """Output (width, height) for a `crop_w`×`crop_h` source-pixel crop
    given `max_dim`. Mirrors the downscale rule in render_grid_overlay:
    native 1:1 unless the long edge exceeds max_dim, then scale down
    preserving aspect ratio. Shared with api.snap so the local-crop →
    source coordinate mapping (issue #10) agrees with the rendered image.
    """
    if max(crop_w, crop_h) > max_dim:
        scale = max_dim / max(crop_w, crop_h)
        return (max(1, int(crop_w * scale)), max(1, int(crop_h * scale)))
    return (crop_w, crop_h)


def render_grid_overlay(
    image: Image.Image,
    *,
    tiers: Sequence[str] = DEFAULT_TIERS,
    region: tuple[int, int, int, int] | None = None,
    max_dim: int = 1600,
    background_opacity: float = 0.5,
    background_opacity_explicit: bool = False,
    enhance: str | None = DEFAULT_ENHANCE,
    source_dpi: int | None = None,
    style: str = "standard",
    target: tuple[int, int] | None = None,
    target_line: TargetLine = "none",
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
                            grid stays legible. When `enhance` is active and
                            the opacity was not explicitly requested, the
                            fade floor is raised so the lifted contrast
                            survives the composite. Explicit opacity wins.
        enhance:            contrast lift for faint freehand/pencil scans
                            (issue #2): one of ENHANCE_MODES. "none"
                            (default) is a no-op. "clahe"/"auto" apply
                            contrast-limited adaptive histogram
                            equalization; "threshold" additionally
                            binarizes via adaptive thresholding. Only pixel
                            intensity changes — coordinates are unaffected.

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
    enhance = (enhance or DEFAULT_ENHANCE).lower()
    if enhance not in ENHANCE_MODES:
        raise ValueError(f"unknown enhance mode {enhance!r}; allowed {list(ENHANCE_MODES)}")
    style = (style or "standard").lower()
    if style not in GRID_STYLES:
        raise ValueError(f"unknown grid style {style!r}; allowed {list(GRID_STYLES)}")
    target_line = target_line or "none"
    if target_line not in ("vertical", "horizontal", "none"):
        raise ValueError("target_line must be vertical, horizontal, or none")

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
    # H4 (followups-2 tracker): crop-aware max_dim. Keep the crop at
    # native 1:1 resolution if it fits — small zooms shouldn't be capped
    # at 1600px and lose readability on small text. Full-image renders
    # still cap at max_dim so we don't dump a 6000px PNG into the agent's
    # context. compute_output_size is shared with api.snap so the
    # local-crop → source coordinate mapping (issue #10) agrees with the
    # rendered image exactly.
    out_w, out_h = compute_output_size(cw, ch, max_dim)
    if (out_w, out_h) != (cw, ch):
        cropped = cropped.resize((out_w, out_h), Image.LANCZOS)
    cw, ch = cropped.size

    # Issue #2: lift faint pencil/freehand BEFORE the grid composite so
    # the vision-LLM reads enhanced contrast. Applied to the (already
    # cropped + downscaled) image; positions are untouched.
    if enhance != "none":
        cropped = _enhance_image(cropped, enhance)
        # Don't let the half-fade swallow the contrast we just added unless
        # the caller intentionally requested a QA/labeling fade.
        if not background_opacity_explicit:
            background_opacity = max(background_opacity, 0.85)

    # Canvas == image dims; no margin. Grid + labels drawn ON the image.
    canvas = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
    if cropped.mode != "RGBA":
        cropped = cropped.convert("RGBA")
    if background_opacity < 1.0:
        cropped = _blend_to_white(cropped, background_opacity)
    canvas.paste(cropped, (0, 0), cropped if cropped.mode == "RGBA" else None)

    long_src = max(crop_src_w, crop_src_h)
    # Nest the tiers so they coincide EXACTLY where it remains legible:
    # broad = 5×finer, finer ≈ 5×detail. Detail must never collapse into a
    # 1–2 px mesh on tight crops; at that density it hides the drawing instead
    # of helping coordinate reads. Keep at least a 6 source-px interval, which
    # is still dense enough for fine placement on full-res scans but visually
    # separable from source ink and label geometry.
    # Deriving each tier independently from a fraction of long_src lets integer
    # rounding split them — e.g. long=1080 → broad=int(1080/10)=108 but
    # 5×finer=5×int(1080/50)=5×21=105. The broad labels then land 3–9 px off
    # the finer-every-5th labels and print DOUBLED, overlapping numbers
    # (315/324, 420/432, 525/540) that are unreadable and easy to MISREAD —
    # a real label-placement hazard. Anchoring broad to 5×finer guarantees a
    # broad label sits exactly on a finer-every-5th line, so each coordinate
    # prints once. Coordinates themselves were already correct (content maps to
    # source px at 0-px offset); this is a label-legibility fix.
    finer_step = max(1, round(long_src * _TIER_FRACTION["finer"]))
    broad_step = 5 * finer_step
    detail_step = max(6, finer_step // 5)
    spec = _Spec(
        out_w=cw,
        out_h=ch,
        crop_src_w=crop_src_w,
        crop_src_h=crop_src_h,
        region_origin=region_origin,
        source_size=source_size,
        broad_step=broad_step,
        finer_step=finer_step,
        detail_step=detail_step,
    )

    draw = ImageDraw.Draw(canvas, "RGBA")
    label_font = _load_font(11)
    legend_font = _load_font(10)

    # Order: detail → finer → broad so darker tiers overdraw lighter ones.
    # Labels go in a SECOND pass after all lines so they sit on top.
    if "detail" in tiers:
        _draw_tier_lines(draw, spec, "detail", style=style)
    if "finer" in tiers:
        _draw_tier_lines(draw, spec, "finer", style=style)
    if "broad" in tiers:
        _draw_tier_lines(draw, spec, "broad", style=style)

    # Labels — broad first (one per intersection), finer second (every 5th).
    if "broad" in tiers:
        _draw_tier_labels(draw, label_font, spec, "broad", style=style)
    if "finer" in tiers:
        _draw_tier_labels(draw, label_font, spec, "finer", style=style)

    if target is not None:
        _draw_target_guides(draw, label_font, spec, target, target_line)

    _draw_top_right_legend(draw, canvas.size, spec, legend_font, source_dpi=source_dpi)
    return canvas


def _blend_to_white(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 1.0:
        return img
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.blend(white, img, alpha)


def _enhance_image(img: Image.Image, mode: str) -> Image.Image:
    """Lift faint freehand/pencil scans to readable contrast (issue #2).

    Works on a grayscale projection (where pencil legibility lives) and
    returns an image in the same mode as the input. Pixel POSITIONS are
    unchanged, so SOURCE-pixel coordinates stay valid — this is purely a
    contrast/threshold pass, a preprocessing step for the vision-LLM
    reader, not OCR.

    Modes:
      auto / clahe — contrast-limited adaptive histogram equalization.
                     Gentle, reversible-looking lift that keeps tonal
                     detail; good default for faint-but-present strokes.
      threshold    — CLAHE then adaptive (Gaussian) thresholding to a
                     near-binary black-on-white. Strongest; best when the
                     scan is so faint that CLAHE alone isn't enough, at
                     the cost of losing soft gradients.
    """
    import cv2
    import numpy as np

    orig_mode = img.mode
    alpha = img.getchannel("A") if orig_mode == "RGBA" else None
    gray = np.asarray(img.convert("L"))

    if mode in ("auto", "clahe"):
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        out = clahe.apply(gray)
    elif mode == "threshold":
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        eq = clahe.apply(gray)
        out = cv2.adaptiveThreshold(
            eq, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            blockSize=31, C=10,
        )
    else:  # pragma: no cover - guarded by caller
        return img

    result = Image.fromarray(out, mode="L").convert("RGB")
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
    elif orig_mode not in ("RGB", "RGBA"):
        result = result.convert(orig_mode)
    return result


def _with_alpha(color: tuple[int, int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return (color[0], color[1], color[2], alpha)


def _tier_style(tier: str, style: str) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], int]:
    if tier == "broad":
        color, width = _BROAD_COLOR, 2
    elif tier == "finer":
        color, width = _FINER_COLOR, 1
    elif tier == "detail":
        color, width = _DETAIL_COLOR, 1
    else:
        color, width = _DETAIL_COLOR, 1
    if style == "coordinate_audit":
        return _AUDIT_VERTICAL[tier], _AUDIT_HORIZONTAL[tier], width
    if style == "coordinate_pair":
        return _PAIR_VERTICAL[tier], _PAIR_HORIZONTAL[tier], width
    return color, color, width


def _multicolor_line(
    src_coord: int,
    step_src: int,
    *,
    tier: str,
    orientation: str,
) -> tuple[int, int, int, int]:
    idx = int(round(src_coord / max(1, step_src)))
    # Offset horizontal colors so an x/y intersection is a recognizable pair,
    # not the same color twice at equal line indices.
    if orientation == "horizontal":
        idx += 3
    color = _MULTI_PALETTE[idx % len(_MULTI_PALETTE)]
    if tier == "broad":
        return _with_alpha(color, 245)
    if tier == "finer":
        return _with_alpha(color, 165)
    return _with_alpha(color, 35)


def _draw_tier_lines(
    draw: ImageDraw.ImageDraw,
    spec: _Spec,
    tier: str,
    *,
    style: str = "standard",
) -> None:
    if tier == "broad":
        step_src = spec.broad_step
    elif tier == "finer":
        step_src = spec.finer_step
    elif tier == "detail":
        step_src = spec.detail_step
    else:
        return
    v_color, h_color, width = _tier_style(tier, style)

    # Vertical lines.
    src_x = ((spec.region_origin[0] + step_src - 1) // step_src) * step_src
    src_x_end = spec.region_origin[0] + spec.crop_src_w
    while src_x <= src_x_end:
        out_x = int((src_x - spec.region_origin[0]) * spec.px_per_src_x)
        if 0 <= out_x < spec.out_w:
            if style == "coordinate_multicolor":
                v_color = _multicolor_line(src_x, step_src, tier=tier, orientation="vertical")
            draw.line([(out_x, 0), (out_x, spec.out_h - 1)], fill=v_color, width=width)
        src_x += step_src

    # Horizontal lines.
    src_y = ((spec.region_origin[1] + step_src - 1) // step_src) * step_src
    src_y_end = spec.region_origin[1] + spec.crop_src_h
    while src_y <= src_y_end:
        out_y = int((src_y - spec.region_origin[1]) * spec.px_per_src_y)
        if 0 <= out_y < spec.out_h:
            if style == "coordinate_multicolor":
                h_color = _multicolor_line(src_y, step_src, tier=tier, orientation="horizontal")
            draw.line([(0, out_y), (spec.out_w - 1, out_y)], fill=h_color, width=width)
        src_y += step_src


def _draw_target_guides(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    spec: _Spec,
    target: tuple[int, int],
    target_line: TargetLine,
) -> None:
    tx, ty = int(target[0]), int(target[1])
    if not (
        spec.region_origin[0] <= tx <= spec.region_origin[0] + spec.crop_src_w
        and spec.region_origin[1] <= ty <= spec.region_origin[1] + spec.crop_src_h
    ):
        return
    ox = int((tx - spec.region_origin[0]) * spec.px_per_src_x)
    oy = int((ty - spec.region_origin[1]) * spec.px_per_src_y)
    if target_line in ("vertical", "none"):
        draw.line([(ox, 0), (ox, spec.out_h - 1)], fill=_TARGET_COLOR, width=2)
    if target_line in ("horizontal", "none"):
        draw.line([(0, oy), (spec.out_w - 1, oy)], fill=_TARGET_COLOR, width=2)
    r = 9
    draw.ellipse([ox - r, oy - r, ox + r, oy + r], outline=_TARGET_COLOR, width=2)
    draw.line([(max(0, ox - 24), oy), (min(spec.out_w - 1, ox + 24), oy)], fill=_TARGET_COLOR, width=2)
    draw.line([(ox, max(0, oy - 24)), (ox, min(spec.out_h - 1, oy + 24))], fill=_TARGET_COLOR, width=2)

    near_x = round(tx / spec.finer_step) * spec.finer_step
    near_y = round(ty / spec.finer_step) * spec.finer_step
    if spec.region_origin[0] <= near_x <= spec.region_origin[0] + spec.crop_src_w:
        nx = int((near_x - spec.region_origin[0]) * spec.px_per_src_x)
        draw.line([(nx, max(0, oy - 14)), (nx, min(spec.out_h - 1, oy + 14))], fill=(0, 0, 0, 230), width=2)
    if spec.region_origin[1] <= near_y <= spec.region_origin[1] + spec.crop_src_h:
        ny = int((near_y - spec.region_origin[1]) * spec.px_per_src_y)
        draw.line([(max(0, ox - 14), ny), (min(spec.out_w - 1, ox + 14), ny)], fill=(0, 0, 0, 230), width=2)

    text = f"x={tx} y={ty}"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = min(max(4, ox + 12), max(4, spec.out_w - tw - 10))
    y = min(max(4, oy + 12), max(4, spec.out_h - th - 10))
    draw.rectangle([x - 4, y - 3, x + tw + 4, y + th + 3], fill=_TARGET_BG)
    draw.text((x, y), text, font=font, fill=_TARGET_FG)


def _draw_tier_labels(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    spec: _Spec,
    tier: str,
    *,
    style: str = "standard",
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
        fg = None
        if style == "coordinate_multicolor":
            fg = _multicolor_line(sx, step_src, tier=tier, orientation="vertical")
        _draw_label_chip(
            draw, font, str(sx), (out_x, 0),
            anchor="top-center", canvas_w=spec.out_w, canvas_h=spec.out_h,
            fg=fg,
        )

    # Y-axis labels along the LEFT of the image (just inside the left edge).
    for sy in src_ys:
        out_y = int((sy - spec.region_origin[1]) * spec.px_per_src_y)
        fg = None
        if style == "coordinate_multicolor":
            fg = _multicolor_line(sy, step_src, tier=tier, orientation="horizontal")
        _draw_label_chip(
            draw, font, str(sy), (0, out_y),
            anchor="left-middle", canvas_w=spec.out_w, canvas_h=spec.out_h,
            fg=fg,
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
    fg: tuple[int, int, int, int] | None = None,
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
    if fg is not None:
        draw.rectangle([rx0, ry0, rx1, ry1], outline=fg, width=2)
    draw.text((x, y), text, font=font, fill=fg or _LABEL_FG)


def _draw_top_right_legend(
    draw: ImageDraw.ImageDraw,
    canvas_size: tuple[int, int],
    spec: _Spec,
    font: ImageFont.ImageFont,
    source_dpi: int | None = None,
) -> None:
    """Tiny faint chip in the top-right corner. Smaller font than the
    axis labels so it doesn't compete with content."""
    cw, ch = canvas_size
    lines = [
        f"grid (src px): b/f/d = {spec.broad_step}/{spec.finer_step}/{spec.detail_step}",
    ]
    if source_dpi:
        # Calibration safety: every grid self-describes the dpi of its
        # coordinate frame so px<->PDF-unit conversion (1 px = 72/dpi pt)
        # is always verifiable and can never silently mis-calibrate.
        lines.append(f"dpi {source_dpi}  (1 px = {72.0/source_dpi:.4f} pt)")
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
