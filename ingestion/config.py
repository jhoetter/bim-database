"""Thresholds + backend configuration.

Profiles are config-driven. The default profile passes everything the gate
will plausibly accept; 'strict-form' is the customer-facing profile that
re-prompts on borderline submissions.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class QualityThresholds:
    """All gate thresholds. Tweaked by the named profile."""

    min_long_side_px: int = 1200
    """A page narrower than this on the long side is rejected — too low to
    annotate. ~1200px ≈ a 5MP phone photo of a sheet of paper."""

    warn_long_side_px: int = 1800

    blur_reject_var: float = 30.0
    """Variance-of-Laplacian below this is clearly out of focus."""

    blur_warn_var: float = 80.0

    exposure_min: float = 40.0
    """Mean luminance below this is too dark to recover faithfully."""

    exposure_max: float = 235.0
    """Mean luminance above this is blown out."""

    glare_fraction_warn: float = 0.03
    glare_fraction_reject: float = 0.12

    skew_warn_deg: float = 5.0

    document_present_min_area_frac: float = 0.20
    """Detected document polygon must cover at least this fraction of the
    frame. Below this we suspect we're looking at the table around the page
    rather than the page itself."""


@dataclass(frozen=True)
class EnhancerConfig:
    backend: Literal[
        "noop",
        "replicate-real-esrgan",
        "replicate-swinir",
        "replicate-topaz-text-refine",
        "local",
    ] = "noop"
    upscale: float = 1.0
    """≤2.0× per the spec. 1.0 = no upscale."""

    replicate_api_token_env: str = "REPLICATE_API_TOKEN"


@dataclass(frozen=True)
class PipelineConfig:
    profile: str = "default"
    thresholds: QualityThresholds = field(default_factory=QualityThresholds)
    rectify_method: Literal[
        "passthrough", "perspective_contour", "perspective_lines", "learned"
    ] = "perspective_contour"
    enhancer: EnhancerConfig = field(default_factory=EnhancerConfig)

    page_render_dpi: int = 200
    """DPI used when rasterising PDF input pages for the rectifier."""


PROFILES: dict[str, PipelineConfig] = {
    "default": PipelineConfig(profile="default"),
    "strict-form": PipelineConfig(
        profile="strict-form",
        thresholds=QualityThresholds(
            min_long_side_px=1500,
            warn_long_side_px=2200,
            blur_reject_var=60.0,
            blur_warn_var=120.0,
            glare_fraction_warn=0.02,
            glare_fraction_reject=0.08,
            skew_warn_deg=4.0,
        ),
    ),
    "lenient-scrape": PipelineConfig(
        profile="lenient-scrape",
        thresholds=QualityThresholds(
            min_long_side_px=600,
            warn_long_side_px=900,
            blur_reject_var=10.0,
            blur_warn_var=30.0,
            glare_fraction_warn=0.10,
            glare_fraction_reject=0.30,
        ),
    ),
}


def load_profile(name: str | None = None) -> PipelineConfig:
    """Pick a profile by name. Defaults to env `BIM_INGEST_PROFILE` or 'default'."""
    if name is None:
        name = os.environ.get("BIM_INGEST_PROFILE", "default")
    cfg = PROFILES.get(name)
    if cfg is None:
        raise ValueError(f"unknown ingestion profile {name!r}; known: {sorted(PROFILES)}")
    return cfg
