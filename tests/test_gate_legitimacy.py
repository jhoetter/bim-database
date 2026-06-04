"""WS-D (legibility-first-quality tracker): gate legitimacy — stop the
pipeline manufacturing false blockers.

- D1: wall scoring thresholds auto-scale to the scene's extraction DPI.
- D2: a zero-delta reanchor routes to the centerline escape, not a loop.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main  # noqa: E402,F401  (load the package root first to avoid the main<->routes cycle)
from api.routes_geometry import zero_delta_escape_hint  # noqa: E402
from api.wall_score import score_walls  # noqa: E402


def _wall_image(w=1200, h=400, *, y=200, thick=12) -> Image.Image:
    arr = np.full((h, w), 255, np.uint8)
    arr[y - thick // 2 : y + thick // 2, 50 : w - 50] = 0  # one horizontal wall
    return Image.fromarray(arr, "L").convert("RGB")


# ── D1 ───────────────────────────────────────────────────────────────────
def test_d1_source_dpi_scales_pixel_thresholds():
    img = _wall_image()
    wall = [((50.0, 200.0), (1150.0, 200.0))]
    at600 = score_walls(img, wall, tol_px=18, min_wall_px=8, close_px=80, source_dpi=600)
    at300 = score_walls(img, wall, tol_px=18, min_wall_px=8, close_px=80, source_dpi=300)
    # 600 == ref => unchanged; 300 => halved.
    assert at600["params"]["tol_px"] == 18
    assert at300["params"]["tol_px"] == 9
    assert at300["params"]["min_wall_px"] == 4
    # no source_dpi => defaults used as-is (back-compat).
    plain = score_walls(img, wall, tol_px=18, min_wall_px=8)
    assert plain["params"]["tol_px"] == 18


def test_d1_floors_prevent_degenerate_thresholds():
    img = _wall_image()
    wall = [((50.0, 200.0), (1150.0, 200.0))]
    # an extreme low dpi must not drive tol/min below the safe floor (3).
    res = score_walls(img, wall, tol_px=9, min_wall_px=8, source_dpi=60)
    assert res["params"]["tol_px"] >= 3
    assert res["params"]["min_wall_px"] >= 3


# ── D2 ───────────────────────────────────────────────────────────────────
def test_d2_zero_delta_failing_wall_gets_escape():
    hint = zero_delta_escape_hint(passes=False, dx=0.3, dy=-0.4)
    assert hint and hint["recommended_tool"] == "review_wall_centerline_between_rails"
    assert hint["reanchor_zero_delta"] is True


def test_d2_no_escape_when_anchored_or_moved():
    # passed anchoring => no escape needed
    assert zero_delta_escape_hint(passes=True, dx=0.0, dy=0.0) is None
    # failed but reanchor MOVED it => there was better ink; keep refining
    assert zero_delta_escape_hint(passes=False, dx=8.0, dy=0.0) is None
