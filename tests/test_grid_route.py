"""Grid-toggle coordinate-frame invariant.

Regression guard for the AnnotatePage "Raster" (grid) toggle drift bug:
toggling the grid ON visibly MOVED the label graphics off the drawing ink.

Root cause: the SPA had a single SVG <image> whose href was SWAPPED between
the clean scene crop (grid OFF, served by /static/dataset/<key>/<file> at the
crop's true pixel size) and the /grid render (grid ON). The /grid route runs
the crop through compute_output_size, which caps the long edge at max_dim — so
a crop whose long edge exceeds the requested max_dim comes back DOWNSCALED to
a smaller extent. That smaller image was then forced into the fixed
width/height box by preserveAspectRatio="none", stretching the ink while the
labels (drawn in the un-stretched source-pixel frame) stayed put.

The SPA fix makes the base <image> always the clean crop and overlays the grid
separately, but the contract these tests lock is on the route: when the SPA
asks for the grid with max_dim == max(image_size) (exactly what AnnotatePage
sends), the grid PNG MUST come back at the same pixel dimensions as the scene
crop — no downscale, so no stretch, so no drift.
"""
import io
import shutil
import sys
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.main import app, DATASET_DIR  # noqa: E402


def _seed_scene(key: str, file: str, size=(2318, 1723)) -> Path:
    """Write a scene crop JPG to disk exactly where /static and /grid both
    read it from (DATASET_DIR/<key>/<file>). The default size has a long
    edge > the legacy 1600 max_dim cap, which is where the drift bites."""
    sdir = DATASET_DIR / key
    sdir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(sdir / file)
    return sdir


def _cleanup(key: str):
    sdir = DATASET_DIR / key
    if sdir.exists():
        shutil.rmtree(sdir, ignore_errors=True)


def test_grid_dims_equal_scene_dims_when_max_dim_covers_long_edge():
    """The no-drift contract: with max_dim == max(image_size) — exactly what
    AnnotatePage requests for the grid overlay — the grid PNG dimensions
    equal the scene-crop dimensions the labels were authored against."""
    key, file = "gridframe", "scene.jpg"
    W, H = 2318, 1723
    _seed_scene(key, file, size=(W, H))
    try:
        client = TestClient(app)
        r = client.get(
            f"/datasets/{key}/{file}/grid?max_dim={max(W, H)}&format=png"
        )
        assert r.status_code == 200, r.text
        grid = Image.open(io.BytesIO(r.content))
        assert grid.size == (W, H), (
            f"grid {grid.size} != scene {(W, H)} — labels would drift on toggle"
        )
    finally:
        _cleanup(key)


def test_grid_downscales_when_max_dim_below_long_edge():
    """Documents the mechanism the SPA must NOT rely on: a max_dim below the
    crop's long edge yields a downscaled grid (smaller extent). The SPA fix
    avoids drift by overlaying the grid in the fixed source-pixel box rather
    than swapping the base href to such a downscaled render."""
    key, file = "gridframe2", "scene.jpg"
    W, H = 2318, 1723
    _seed_scene(key, file, size=(W, H))
    try:
        client = TestClient(app)
        r = client.get(f"/datasets/{key}/{file}/grid?max_dim=1600&format=png")
        assert r.status_code == 200, r.text
        grid = Image.open(io.BytesIO(r.content))
        assert max(grid.size) == 1600
        assert grid.size != (W, H)
    finally:
        _cleanup(key)
