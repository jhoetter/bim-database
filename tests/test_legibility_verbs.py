"""WS-B (legibility-first-quality tracker): the survey / read / zoom_read
viewing contract.

- read stays bounded (region long edge <= 2000 src px) so fidelity x area
  is constant and reads never bloat context.
- grid="none" renders a clean read substrate (no overlay) equal to the source.
- zoom_read re-renders a SMALL region from the PDF vector at higher DPI and is
  bounded so a higher dpi forces a tighter crop.
- repeated-identical-crop nudges the agent to change strategy (B4).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.main import DATASET_DIR, app  # noqa: E402
from api.segment import scene_px_to_pdf  # noqa: E402
from mcp_tools_scene import _note_repeat, _parse_region_edges  # noqa: E402

client = TestClient(app)
HOUSE22 = DATASET_DIR / "house-22" / "house-22-floorplan-eg.png"


# ── pure helpers ─────────────────────────────────────────────────────────
def test_parse_region_edges_long_edge_and_rejects_bad():
    assert _parse_region_edges("100,50,1600,300") == 1500  # max(1500, 250)
    assert _parse_region_edges("0,0,100,400") == 400
    assert _parse_region_edges(None) is None
    assert _parse_region_edges("1,2,3") is None
    assert _parse_region_edges("100,100,50,400") is None  # non-positive width


def test_repeat_nudge_fires_on_third_identical_only():
    sig = "read|f.png|10,10,20,20|threshold"
    assert _note_repeat(sig) is None
    assert _note_repeat(sig) is None
    msg = _note_repeat(sig)
    assert msg and "CHANGE STRATEGY" in msg
    # a different crop is independent
    assert _note_repeat("read|f.png|99,99,120,120|threshold") is None


def test_scene_px_to_pdf_maps_linearly():
    # parent bbox 0..720pt at 72dpi => 720px; pixel p -> pt p*72/dpi.
    pdf = scene_px_to_pdf([100, 0, 200, 50], [0, 0, 720, 720], 72)
    assert pdf == [100.0, 0.0, 200.0, 50.0]
    # at 600 dpi a 600px offset is 72pt.
    pdf2 = scene_px_to_pdf([600, 0, 1200, 0], [0, 0, 1000, 1000], 600)
    assert pdf2[0] == pytest.approx(72.0)


# ── grid="none" clean read substrate (synthetic, hermetic) ──────────────
def test_grid_none_is_clean_equal_to_source(tmp_path):
    key, file = "ztest-legib", "scene.png"
    sdir = DATASET_DIR / key
    sdir.mkdir(parents=True, exist_ok=True)
    src_path = sdir / file
    try:
        arr = np.full((400, 600, 3), 255, np.uint8)
        arr[:, ::40] = (90, 90, 90)  # faint vertical lines
        Image.fromarray(arr, "RGB").save(src_path)

        region = (50, 30, 450, 330)
        r = client.get(
            f"/datasets/{key}/{file}/grid",
            params={"region": "50,30,450,330", "grid": "none", "format": "png",
                    "max_dim": 8000, "background_opacity": 1.0},
        )
        assert r.status_code == 200
        import io
        got = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
        expected = np.asarray(Image.open(src_path).convert("RGB"))[
            region[1]:region[3], region[0]:region[2]
        ]
        assert got.shape == expected.shape
        assert np.array_equal(got, expected), "grid=none must equal the source crop"
    finally:
        import shutil
        shutil.rmtree(sdir, ignore_errors=True)


# ── zoom_read route: bounds + real higher-than-native render ─────────────
def test_zoom_rejects_oversized_region():
    if not HOUSE22.exists():
        pytest.skip("house-22 dataset not present")
    r = client.get(
        "/datasets/house-22/house-22-floorplan-eg.png/zoom",
        params={"region": "0,0,3000,2000", "dpi": 1000},
    )
    assert r.status_code == 400
    assert "1500" in r.json()["detail"]


def test_zoom_renders_higher_than_native_from_pdf():
    if not HOUSE22.exists():
        pytest.skip("house-22 dataset not present")
    # 500x160 src px at the scene's 600 dpi, re-rendered at 1000 dpi.
    r = client.get(
        "/datasets/house-22/house-22-floorplan-eg.png/zoom",
        params={"region": "650,70,1150,230", "dpi": 1000, "enhance": "none"},
    )
    assert r.status_code == 200
    import io
    w, h = Image.open(io.BytesIO(r.content)).size
    # 1000/600 magnification ~ 1.67x => ~834x267
    assert w == pytest.approx(834, abs=4)
    assert h == pytest.approx(267, abs=4)


def test_zoom_requires_crop_from():
    """A scene with no PDF provenance can't be zoom-rendered."""
    key, file = "ztest-nocrop", "scene.png"
    sdir = DATASET_DIR / key
    sdir.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (200, 200), (255, 255, 255)).save(sdir / file)
        r = client.get(f"/datasets/{key}/{file}/zoom", params={"region": "0,0,100,100"})
        assert r.status_code == 400
        assert "crop_from" in r.json()["detail"]
    finally:
        import shutil
        shutil.rmtree(sdir, ignore_errors=True)
