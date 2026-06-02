from __future__ import annotations

import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main  # noqa: E402


def test_dimension_chain_candidates_route_returns_prior_not_500():
    key = "house-zzdimchain"
    file = f"{key}-scene.jpg"
    ds = api_main.DATASET_DIR / key
    ds.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (500, 300), "white")
    draw = ImageDraw.Draw(img)
    draw.line([60, 220, 440, 220], fill="black", width=2)
    for x in [60, 180, 300, 440]:
        draw.line([x, 195, x, 245], fill="black", width=3)
    img.save(ds / file)
    try:
        client = TestClient(api_main.app)
        r = client.get(
            f"/datasets/{key}/{file}/dimension-chain-candidates",
            params={"region": "20,170,470,270", "orientation": "horizontal"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["found"] is True
        assert data["orientation"] == "horizontal"
        assert data["tick_count"] >= 4
    finally:
        shutil.rmtree(ds, ignore_errors=True)
