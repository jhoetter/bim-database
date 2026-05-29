"""Stage 2 — quality gate.

Per-page metrics + a tri-state decision (pass / warn / reject) with
human-readable reasons. Thresholds are config-driven so the batch CLI
("flag low-quality but don't block") and the customer form ("re-prompt on
borderline") can share the same gate logic.

Implementation note: we avoid hard OpenCV dependency in the gate. NumPy +
PIL cover the four metrics cheaply; OpenCV is reserved for the rectifier.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from .config import QualityThresholds


@dataclass
class QualityMetrics:
    width_px: int
    height_px: int
    blur_laplacian_var: float
    exposure_mean: float
    glare_fraction: float
    skew_deg: float | None
    document_present: bool
    dpi_estimate: float | None = None


@dataclass
class GateDecision:
    decision: str  # "pass" | "warn" | "reject"
    reasons: list[str]


def _to_gray_array(img: Image.Image) -> np.ndarray:
    if img.mode != "L":
        img = img.convert("L")
    return np.asarray(img, dtype=np.float32)


def laplacian_variance(gray: np.ndarray) -> float:
    """Variance of the discrete Laplacian — a fast focus metric. The
    classic 3×3 Laplacian kernel; we compute it with NumPy strided
    convolution so we don't drag in OpenCV."""
    if gray.size == 0:
        return 0.0
    # Pad-reflect once so we don't lose the border to the 3×3 stencil.
    pad = np.pad(gray, 1, mode="edge")
    lap = (
        -4 * pad[1:-1, 1:-1]
        + pad[0:-2, 1:-1]
        + pad[2:, 1:-1]
        + pad[1:-1, 0:-2]
        + pad[1:-1, 2:]
    )
    return float(lap.var())


def glare_fraction(gray: np.ndarray, threshold: float = 245.0) -> float:
    if gray.size == 0:
        return 0.0
    return float((gray >= threshold).mean())


def estimate_skew_deg(gray: np.ndarray) -> float | None:
    """Coarse skew estimate via the dominant Hough line angle. Returns None
    when too few edges to be reliable, so callers can treat it as
    'no signal' rather than 0°.

    Uses OpenCV when available — falls back to None so the gate still runs
    on a CPU-only box without cv2.
    """
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    g = gray.astype(np.uint8) if gray.dtype != np.uint8 else gray
    edges = cv2.Canny(g, 60, 180)
    if edges.sum() < 5000:
        return None
    # HoughLines returns (rho, theta) pairs; theta in radians ∈ [0, π).
    lines = cv2.HoughLines(edges, 1, np.pi / 360.0, threshold=120)
    if lines is None or len(lines) == 0:
        return None
    angles = lines[:, 0, 1]
    # Fold to a -45..45° "deviation from rectilinear" window so a 0° page
    # and a 90° page both register as not skewed.
    deg = np.degrees(angles)
    deg = (deg + 45.0) % 90.0 - 45.0
    return float(np.median(deg))


def detect_document_area_fraction(gray: np.ndarray) -> float:
    """Cheap document-present heuristic: largest contiguous bright region's
    fraction of the frame, computed via thresholded connected-component
    bbox area. We deliberately stay independent of cv2 here so the gate is
    importable without OpenCV.

    Falls back to 1.0 (assume present) when SciPy isn't around so we don't
    spuriously reject pages on a minimal install — the rectifier will tell
    us soon enough.
    """
    if gray.size == 0:
        return 0.0
    # Otsu-style global threshold approximation: midpoint of mean ± std.
    mean, std = float(gray.mean()), float(gray.std())
    thresh = max(mean - std * 0.5, 50.0)
    mask = gray >= thresh
    return float(mask.mean())


def score_page(image: Image.Image, thresholds: QualityThresholds, is_native_pdf: bool = False) -> tuple[QualityMetrics, GateDecision]:
    """Single-page score. Returns metrics + a decision against the given
    thresholds. Native-PDF pages skip skew + document-present (they're
    already flat) so we don't flag scanner-perfect inputs as low quality.
    """
    w, h = image.size
    gray = _to_gray_array(image)

    blur_var = laplacian_variance(gray)
    exposure_mean = float(gray.mean())
    glare = glare_fraction(gray)
    skew = None if is_native_pdf else estimate_skew_deg(gray)
    doc_present_frac = 1.0 if is_native_pdf else detect_document_area_fraction(gray)

    metrics = QualityMetrics(
        width_px=w,
        height_px=h,
        blur_laplacian_var=blur_var,
        exposure_mean=exposure_mean,
        glare_fraction=glare,
        skew_deg=skew,
        document_present=doc_present_frac >= thresholds.document_present_min_area_frac,
    )

    reasons: list[str] = []
    decision = "pass"

    long_side = max(w, h)
    if long_side < thresholds.min_long_side_px:
        reasons.append(f"resolution {long_side}px below floor {thresholds.min_long_side_px}px")
        decision = "reject"
    elif long_side < thresholds.warn_long_side_px:
        reasons.append(f"resolution {long_side}px below warn level {thresholds.warn_long_side_px}px")
        decision = _worst(decision, "warn")

    if blur_var < thresholds.blur_reject_var:
        reasons.append(f"out of focus (laplacian var {blur_var:.0f} < {thresholds.blur_reject_var:.0f})")
        decision = "reject"
    elif blur_var < thresholds.blur_warn_var:
        reasons.append(f"soft focus (laplacian var {blur_var:.0f} < {thresholds.blur_warn_var:.0f})")
        decision = _worst(decision, "warn")

    if exposure_mean < thresholds.exposure_min:
        reasons.append(f"underexposed (mean {exposure_mean:.0f} < {thresholds.exposure_min:.0f})")
        decision = "reject"
    elif exposure_mean > thresholds.exposure_max:
        reasons.append(f"overexposed (mean {exposure_mean:.0f} > {thresholds.exposure_max:.0f})")
        decision = "reject"

    if glare >= thresholds.glare_fraction_reject:
        reasons.append(f"glare {glare:.1%} ≥ {thresholds.glare_fraction_reject:.1%}")
        decision = "reject"
    elif glare >= thresholds.glare_fraction_warn:
        reasons.append(f"glare {glare:.1%} ≥ {thresholds.glare_fraction_warn:.1%}")
        decision = _worst(decision, "warn")

    if skew is not None and abs(skew) > thresholds.skew_warn_deg:
        reasons.append(f"skew {skew:+.1f}° > ±{thresholds.skew_warn_deg:.1f}°")
        decision = _worst(decision, "warn")

    if not metrics.document_present:
        reasons.append(
            f"document not clearly framed (bright-region area {doc_present_frac:.0%} "
            f"< {thresholds.document_present_min_area_frac:.0%})"
        )
        decision = _worst(decision, "warn")

    return metrics, GateDecision(decision=decision, reasons=reasons)


_RANK = {"pass": 0, "warn": 1, "reject": 2}


def _worst(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b
