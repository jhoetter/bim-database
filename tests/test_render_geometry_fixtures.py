from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.label_render import _wall_band_points


FIXTURES = Path(__file__).parent / "fixtures" / "render_geometry" / "wall_band_cases.json"


@pytest.mark.parametrize("case", json.loads(FIXTURES.read_text()))
def test_wall_band_points_match_shared_fixture(case: dict) -> None:
    actual = _wall_band_points(
        case["start"],
        case["end"],
        case["thickness_mm"],
        px_per_mm=case["px_per_mm"],
    )
    rounded = [[round(v, 3) for v in p] for p in actual]
    assert rounded == case["expected"]
