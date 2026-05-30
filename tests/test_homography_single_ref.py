"""Issue #26 — single reference dim for axis-aligned orthographic drawings.

For German Ansicht/Schnitt the projection is axis-aligned orthographic:
pixels are square, so one reliable reference dim calibrates both axes under
the isotropic (square-pixel) assumption. The harness vision-LLM judges the
drawing orthographic and opts in via `assume_isotropic`; the deterministic
engine honours the flag, synthesises the missing axis from the same
px-per-mm, and stamps `single_ref_assumed_isotropic=True` for honesty.
"""
from api.fact_derivation import compute_scene_calibration
from api.homography import compute_rectification

IMG = (1000, 800)


def _hdim(value_mm, _id="h1", start=(0, 0), end=(400, 0)):
    return {
        "id": _id,
        "type": "dimensioned_distance",
        "geometry": {"start": list(start), "end": list(end)},
        "attributes": {
            "is_reference": True,
            "value_mm": value_mm,
            "target_orientation": "horizontal",
        },
    }


def _vdim(value_mm, _id="v1", start=(0, 0), end=(0, 300)):
    return {
        "id": _id,
        "type": "dimensioned_distance",
        "geometry": {"start": list(start), "end": list(end)},
        "attributes": {
            "is_reference": True,
            "value_mm": value_mm,
            "target_orientation": "vertical",
        },
    }


# ── compute_rectification (the homography engine) ────────────────────────


def test_single_horizontal_ref_without_optin_stays_insufficient():
    """Default behaviour is unchanged: one ref dim is not enough."""
    rect = compute_rectification([_hdim(10000)], IMG)
    assert rect.status == "insufficient_references"
    assert rect.single_ref_assumed_isotropic is False


def test_single_horizontal_ref_with_optin_is_valid_and_isotropic():
    rect = compute_rectification([_hdim(10000)], IMG, assume_isotropic=True)
    assert rect.status == "ok"
    assert rect.single_ref_assumed_isotropic is True
    # square pixels: |a| == |d| and |b| == |c|
    a = rect.affine
    assert abs(abs(a.a) - abs(a.d)) < 1e-9
    assert abs(abs(a.b) - abs(a.c)) < 1e-9
    # the synthetic partner is recorded in provenance, not hidden
    assert "synthetic_isotropic" in rect.computed_from
    assert rect.rectified_size_px[0] > 0 and rect.rectified_size_px[1] > 0


def test_single_vertical_ref_with_optin_is_valid_and_isotropic():
    rect = compute_rectification([_vdim(9000)], IMG, assume_isotropic=True)
    assert rect.status == "ok"
    assert rect.single_ref_assumed_isotropic is True


def test_single_ref_isotropic_scale_matches_the_measured_axis():
    """The synthesised axis reuses the measured px-per-mm; RMS ~ 0."""
    rect = compute_rectification([_hdim(10000)], IMG, assume_isotropic=True)
    assert rect.rms_residual_px < 1e-6


def test_two_ref_path_unchanged_flag_false():
    rect = compute_rectification([_hdim(10000), _vdim(9000)], IMG)
    assert rect.status == "ok"
    assert rect.single_ref_assumed_isotropic is False


def test_two_ref_with_optin_ignores_isotropic_flag():
    """opt-in only matters in the single-ref case; two refs => measured."""
    rect = compute_rectification(
        [_hdim(10000), _vdim(9000)], IMG, assume_isotropic=True
    )
    assert rect.status == "ok"
    assert rect.single_ref_assumed_isotropic is False


def test_no_refs_with_optin_stays_insufficient():
    rect = compute_rectification([], IMG, assume_isotropic=True)
    assert rect.status == "insufficient_references"
    assert rect.single_ref_assumed_isotropic is False


def test_genuinely_degenerate_two_ref_still_errors():
    """Two refs that are near-parallel still hit the degenerate guard."""
    h = _hdim(10000, "h1", start=(0, 0), end=(400, 0))
    # a 'vertical' dim that is actually almost horizontal -> parallel pair.
    # _pick_longest keys off target_orientation, so this is selected as V.
    near_parallel = {
        "id": "v1",
        "type": "dimensioned_distance",
        "geometry": {"start": [0, 5], "end": [400, 5]},
        "attributes": {
            "is_reference": True,
            "value_mm": 9000,
            "target_orientation": "vertical",
        },
    }
    rect = compute_rectification([h, near_parallel], IMG)
    assert rect.status == "degenerate"
    assert rect.single_ref_assumed_isotropic is False


# ── fact-level calibration provenance (calibration_per_scene) ────────────


def test_fact_calibration_single_axis_sets_isotropic_flag():
    cal = compute_scene_calibration([_hdim(10000)])
    assert cal is not None
    assert cal["computed_from"] == "M1-H-Bezug"
    assert cal["single_ref_assumed_isotropic"] is True


def test_fact_calibration_single_vertical_axis_sets_isotropic_flag():
    cal = compute_scene_calibration([_vdim(9000)])
    assert cal is not None
    assert cal["computed_from"] == "M1-V-Bezug"
    assert cal["single_ref_assumed_isotropic"] is True


def test_fact_calibration_two_axes_clears_isotropic_flag():
    cal = compute_scene_calibration([_hdim(10000), _vdim(9000)])
    assert cal is not None
    assert cal["computed_from"] == "M1-both"
    assert cal["single_ref_assumed_isotropic"] is False
