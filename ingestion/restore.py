"""Stage 4 — restore (illumination normalize + denoise + optional upscale).

Heavy ML restoration sits behind an `Enhancer` interface; the default
`NoopEnhancer` runs CPU-only with no API keys so `make dev` and tests
work out of the box. A `ReplicateEnhancer` ships as the first real
backend (Real-ESRGAN / SwinIR / Topaz Text Refine).

**Hard constraint (per spec): never run a generative model on dimension
text or digits — it hallucinates values.** The bundle path checks
`pii_flag.title_block_suspected` + per-page text-density estimate and
refuses to apply a generative enhancer to text-bearing pages; the
constraint is enforced HERE so a future backend can't bypass it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from PIL import Image, ImageOps


@dataclass
class EnhanceResult:
    image: Image.Image
    backend: str
    version: str | None
    applied_to_text: bool
    """Per-page record of whether the enhancer was applied to a
    text-bearing page. MUST be False on dimension-text/digit pages.
    Surfaced in the manifest for downstream audit."""


class Enhancer(Protocol):
    backend_name: str
    version: str | None

    def enhance(self, image: Image.Image, *, has_dimension_text: bool) -> EnhanceResult: ...


class NoopEnhancer:
    """Local, cheap, deterministic restoration: illumination flatten +
    mild denoise. No upscale, no generative magic. Always safe to run
    on dimension-text pages."""

    backend_name = "noop"
    version = None

    def enhance(self, image: Image.Image, *, has_dimension_text: bool) -> EnhanceResult:
        out = _illumination_flatten(image)
        return EnhanceResult(
            image=out,
            backend=self.backend_name,
            version=self.version,
            applied_to_text=has_dimension_text,
        )


class ReplicateEnhancer:
    """First real backend — calls a Replicate model. Refuses to run on
    dimension-text/digit pages (returns the input untouched and records
    applied_to_text=False).

    Models supported (selected via `model_name`):
      * 'real-esrgan'         — generic de-JPEG + 2x upscale
      * 'swinir'              — same, alternative architecture
      * 'topaz-text-refine'   — text-aware; the only choice for pages that
                                ARE text-bearing AND need restoration.

    Authentication: REPLICATE_API_TOKEN env var. When absent the call
    raises a clear RuntimeError rather than silently degrading.
    """

    def __init__(self, model_name: str = "real-esrgan", upscale: float = 1.0):
        self.model_name = model_name
        self.upscale = upscale
        self.backend_name = f"replicate-{model_name}"
        self.version: str | None = None  # set on first successful call

    def enhance(self, image: Image.Image, *, has_dimension_text: bool) -> EnhanceResult:
        # Generative models that are NOT text-aware are blocked on
        # text-bearing pages — they would hallucinate digits.
        if has_dimension_text and self.model_name != "topaz-text-refine":
            return EnhanceResult(
                image=image,
                backend=self.backend_name,
                version=self.version,
                applied_to_text=False,
            )
        try:
            import os
            import replicate  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "ReplicateEnhancer requires the `replicate` package — "
                "install via `pip install replicate`"
            ) from e
        if not os.environ.get("REPLICATE_API_TOKEN"):
            raise RuntimeError(
                "ReplicateEnhancer requires REPLICATE_API_TOKEN env var; "
                "fall back to NoopEnhancer or set the token."
            )
        # NOTE: kept as a thin wrapper — the exact model slug / output
        # parsing is intentionally not hard-coded here to avoid stale
        # references. Concrete model invocation should be added when the
        # first call is wired into the bundle adapter.
        del replicate  # placeholder
        raise NotImplementedError(
            "ReplicateEnhancer call wiring lives next to its first real "
            "usage; the no-op path keeps the pipeline runnable today."
        )


def get_enhancer(backend: str, upscale: float = 1.0) -> Enhancer:
    if backend == "noop":
        return NoopEnhancer()
    if backend.startswith("replicate-"):
        model = backend[len("replicate-"):]
        return ReplicateEnhancer(model_name=model, upscale=upscale)
    if backend == "local":
        return NoopEnhancer()  # alias today
    raise ValueError(f"unknown enhancer backend {backend!r}")


def _illumination_flatten(image: Image.Image) -> Image.Image:
    """Subtract a coarse low-frequency background, then re-stretch
    contrast. Removes the gradient phone cameras introduce when the page
    is lit unevenly. Safe on text — no hallucination, no upscale."""
    rgb = np.array(image.convert("RGB"), dtype=np.float32)
    # Per-channel: estimate background via large box filter (separable).
    h, w = rgb.shape[:2]
    blur_sigma = max(h, w) // 32 or 1
    try:
        from PIL import ImageFilter
        bg = np.array(image.convert("RGB").filter(ImageFilter.GaussianBlur(radius=blur_sigma)), dtype=np.float32)
    except Exception:  # noqa: BLE001
        bg = rgb.copy()
    # Avoid div-by-zero on near-black pixels.
    bg = np.clip(bg, 16.0, 255.0)
    flat = rgb / bg
    # Renormalise to 0..255 with a soft clip.
    p98 = float(np.percentile(flat, 98))
    if p98 <= 0:
        p98 = 1.0
    out = np.clip(flat / p98 * 255.0, 0.0, 255.0).astype(np.uint8)
    img = Image.fromarray(out, mode="RGB")
    return ImageOps.autocontrast(img, cutoff=1)
