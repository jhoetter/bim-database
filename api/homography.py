"""R4 — server-side homography + rectification.

Mirrors ui/src/lib/homography.ts (affine-only, longest-H × longest-V).
Used by /exports/{key}/{file}/preview to compute the rectified image
+ a transformed copy of every label for Set B.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Affine:
    """x_w = a*x_px + c*y_px + tx; y_w = b*x_px + d*y_px + ty"""
    a: float
    b: float
    c: float
    d: float
    tx: float
    ty: float


@dataclass
class Rectification:
    affine: Affine
    matrix: list[list[float]]
    computed_from: list[str]
    rectified_size_px: tuple[int, int]
    display_scale: float
    rms_residual_px: float
    status: str  # 'ok' | 'insufficient_references' | 'degenerate'
    reason: str | None = None
    # Issue #26: True when this calibration used a single reference dim
    # and derived the second-axis scale from the same px-per-mm under the
    # square-pixel (isotropic) assumption — valid for axis-aligned
    # orthographic drawings (German Ansicht/Schnitt). The harness
    # vision-LLM makes the axis-aligned judgement and opts in via
    # `assume_isotropic`; the engine just honours it and records it here.
    single_ref_assumed_isotropic: bool = False


def _orient_is_horiz(o: Any) -> bool:
    if o == "horizontal":
        return True
    if isinstance(o, str) and o.startswith("angle_deg:"):
        try:
            v = float(o.split(":", 1)[1])
            return abs(v) < 0.5
        except ValueError:
            return False
    return False


def _orient_is_vert(o: Any) -> bool:
    if o == "vertical":
        return True
    if isinstance(o, str) and o.startswith("angle_deg:"):
        try:
            v = float(o.split(":", 1)[1])
            return abs(v - 90.0) < 0.5
        except ValueError:
            return False
    return False


def _pick_longest(refs: list[dict], axis: str) -> dict | None:
    best, best_len = None, -1.0
    for s in refs:
        attrs = s.get("attributes") or {}
        geom = s.get("geometry") or {}
        if axis == "h" and not _orient_is_horiz(attrs.get("target_orientation")):
            continue
        if axis == "v" and not _orient_is_vert(attrs.get("target_orientation")):
            continue
        if attrs.get("value_mm") is None:
            continue
        try:
            sx, sy = geom["start"]
            ex, ey = geom["end"]
        except (KeyError, TypeError, ValueError):
            continue
        vx, vy = ex - sx, ey - sy
        length = (vx * vx + vy * vy) ** 0.5
        if length > best_len:
            best, best_len = s, length
    return best


def compute_rectification(
    labels: list[dict],
    image_size: tuple[int, int],
    target_max_dim: int = 1200,
    assume_isotropic: bool = False,
) -> Rectification:
    """Affine fit from longest H + longest V is_reference dim_distances.
    Returns a typed result with status='ok'/'insufficient_references'/'degenerate'."""
    refs = [
        l for l in labels
        if l.get("type") == "dimensioned_distance"
        and (l.get("attributes") or {}).get("is_reference") is True
        and (l.get("attributes") or {}).get("value_mm") is not None
    ]
    H = _pick_longest(refs, "h")
    V = _pick_longest(refs, "v")
    identity = Affine(1, 0, 0, 1, 0, 0)

    # Issue #26 — single-ref isotropic calibration.
    # Axis-aligned orthographic drawings (all German Ansicht/Schnitt)
    # have square pixels, so one reliable reference dim fixes the scale
    # on BOTH axes: px-per-mm is identical horizontally and vertically.
    # When the caller (the harness vision-LLM, having judged the drawing
    # orthographic) opts in via assume_isotropic, synthesise the missing
    # axis from the present one — a unit-aspect partner rotated 90 deg
    # with the same mm-per-px — so the existing two-ref affine math runs
    # unchanged and yields an isotropic transform instead of erroring.
    single_ref_isotropic = False
    if assume_isotropic and (H is None) != (V is None):
        present = H if H is not None else V
        pg = present["geometry"]
        psx, psy = pg["start"]
        pex, pey = pg["end"]
        pvx, pvy = pex - psx, pey - psy
        plen_px = (pvx * pvx + pvy * pvy) ** 0.5
        plen_mm = float(present["attributes"]["value_mm"])
        if plen_px >= 1e-6 and plen_mm > 0:
            # Rotate the present pixel vector +90 deg; same length in px
            # AND in mm (square pixels => identical px-per-mm scale).
            rot_vx, rot_vy = -pvy, pvx
            synth = {
                "id": "synthetic_isotropic",
                "geometry": {
                    "start": [psx, psy],
                    "end": [psx + rot_vx, psy + rot_vy],
                },
                "attributes": {"value_mm": plen_mm},
            }
            if H is None:
                H = synth
            else:
                V = synth
            single_ref_isotropic = True

    if H is None or V is None:
        reason = (
            "Mindestens 1 horizontale + 1 vertikale Referenz-Strecke benötigt."
            if H is None and V is None
            else ("Es fehlt eine horizontale Referenz-Strecke." if H is None
                  else "Es fehlt eine vertikale Referenz-Strecke.")
        )
        return Rectification(
            affine=identity,
            matrix=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            computed_from=[],
            rectified_size_px=image_size,
            display_scale=1.0,
            rms_residual_px=0.0,
            status="insufficient_references",
            reason=reason,
        )
    hx, hy = H["geometry"]["end"][0] - H["geometry"]["start"][0], H["geometry"]["end"][1] - H["geometry"]["start"][1]
    vx, vy = V["geometry"]["end"][0] - V["geometry"]["start"][0], V["geometry"]["end"][1] - V["geometry"]["start"][1]
    Lh = float(H["attributes"]["value_mm"])
    Lv = float(V["attributes"]["value_mm"])

    det = hx * vy - vx * hy
    if abs(det) < 1e-6:
        return Rectification(
            affine=identity,
            matrix=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            computed_from=[H["id"], V["id"]],
            rectified_size_px=image_size,
            display_scale=1.0,
            rms_residual_px=0.0,
            status="degenerate",
            reason="Horizontale und vertikale Referenz stehen fast parallel zueinander.",
        )

    m11 = (Lh * vy) / det
    m12 = (-Lh * vx) / det
    m21 = (-Lv * hy) / det
    m22 = (Lv * hx) / det

    img_w, img_h = image_size
    corners = [(0.0, 0.0), (img_w, 0.0), (img_w, img_h), (0.0, img_h)]
    mm_corners = [(m11 * x + m12 * y, m21 * x + m22 * y) for x, y in corners]
    xs = [p[0] for p in mm_corners]
    ys = [p[1] for p in mm_corners]
    min_x, min_y = min(xs), min(ys)
    max_x, max_y = max(xs), max(ys)
    mm_w = max_x - min_x
    mm_h = max_y - min_y
    scale = target_max_dim / max(mm_w, mm_h)

    a = m11 * scale; c = m12 * scale
    b = m21 * scale; d = m22 * scale
    tx = -min_x * scale
    ty = -min_y * scale

    def project(pt: tuple[float, float]) -> tuple[float, float]:
        return a * pt[0] + c * pt[1] + tx, b * pt[0] + d * pt[1] + ty

    wH0 = project((H["geometry"]["start"][0], H["geometry"]["start"][1]))
    wH1 = project((H["geometry"]["end"][0], H["geometry"]["end"][1]))
    wV0 = project((V["geometry"]["start"][0], V["geometry"]["start"][1]))
    wV1 = project((V["geometry"]["end"][0], V["geometry"]["end"][1]))
    horiz_len = ((wH1[0] - wH0[0]) ** 2 + (wH1[1] - wH0[1]) ** 2) ** 0.5
    vert_len  = ((wV1[0] - wV0[0]) ** 2 + (wV1[1] - wV0[1]) ** 2) ** 0.5
    exp_h = Lh * scale
    exp_v = Lv * scale
    rms = (((horiz_len - exp_h) ** 2 + (vert_len - exp_v) ** 2) / 2) ** 0.5

    return Rectification(
        affine=Affine(a, b, c, d, tx, ty),
        matrix=[[a, c, tx], [b, d, ty], [0, 0, 1]],
        computed_from=[H["id"], V["id"]],
        rectified_size_px=(round(mm_w * scale), round(mm_h * scale)),
        display_scale=scale,
        rms_residual_px=rms,
        status="ok",
        single_ref_assumed_isotropic=single_ref_isotropic,
    )


def apply_affine(A: Affine, x: float, y: float) -> tuple[float, float]:
    return A.a * x + A.c * y + A.tx, A.b * x + A.d * y + A.ty


def transform_label(A: Affine, l: dict) -> dict:
    """Return a deep-ish copy of `l` with geometry coordinates pushed
    through the affine. Status / attributes are preserved verbatim.
    Caller passes Set-B labels only."""
    out = {**l, "geometry": {**l.get("geometry", {})}}
    g = out["geometry"]
    t = l.get("type")
    if t in ("wall", "dimensioned_distance") and "start" in g and "end" in g:
        g["start"] = list(apply_affine(A, *g["start"]))
        g["end"]   = list(apply_affine(A, *g["end"]))
    elif t == "floorplan_opening" and "quad" in g:
        g["quad"] = [list(apply_affine(A, x, y)) for x, y in g["quad"]]
    elif t == "view_opening":
        if "shape" in g and g["shape"] == "circle" and "center" in g:
            g["center"] = list(apply_affine(A, *g["center"]))
        elif "shape" in g and g["shape"] == "polygon" and "polygon" in g:
            g["polygon"] = [list(apply_affine(A, x, y)) for x, y in g["polygon"]]
        else:
            if "top_edge" in g:
                g["top_edge"] = [list(apply_affine(A, x, y)) for x, y in g["top_edge"]]
            if "bottom_edge" in g:
                g["bottom_edge"] = [list(apply_affine(A, x, y)) for x, y in g["bottom_edge"]]
    elif t == "component_line" and "polyline" in g:
        g["polyline"] = [list(apply_affine(A, x, y)) for x, y in g["polyline"]]
    elif t == "height_mark" and "anchor" in g:
        g["anchor"] = list(apply_affine(A, *g["anchor"]))
    elif t == "dimension_number":
        if "anchor" in g and g["anchor"]:
            g["anchor"] = list(apply_affine(A, *g["anchor"]))
        if "bbox" in g and g["bbox"]:
            g["bbox"] = [list(apply_affine(A, x, y)) for x, y in g["bbox"]]
    return out


def rectify_image(src_path, dst_path, A: Affine, out_size: tuple[int, int]) -> None:
    """Apply the affine via PIL.Image.transform(AFFINE). PIL takes the
    INVERSE affine — given output pixel (u, v) it computes the source
    pixel (a*u + b*v + c, d*u + e*v + f)."""
    from PIL import Image
    det = A.a * A.d - A.c * A.b
    if abs(det) < 1e-9:
        raise ValueError("affine is degenerate; cannot invert")
    ia =  A.d / det
    ib = -A.c / det
    ic = -(ia * A.tx + ib * A.ty)
    id_ = -A.b / det
    ie =  A.a / det
    if_ = -(id_ * A.tx + ie * A.ty)
    with Image.open(src_path) as im:
        im = im.convert("RGB")
        out = im.transform(out_size, Image.AFFINE, (ia, ib, ic, id_, ie, if_), Image.BICUBIC)
        out.save(dst_path, format="JPEG", quality=88)
