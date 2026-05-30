"""V1.1 — the caller's bbox is authoritative when it says so.

Decision (2026-05-30): the vision-LLM picks the scene extent. The #25
clip-detection auto-expansion must NOT override an explicitly-chosen
bbox (it was growing a deliberate crop to the whole page). Passing
`bbox_is_authoritative: true` (alias of `no_clip_expand`) preserves the
caller's bbox exactly; omitting it leaves the #25 behaviour unchanged.
(labeling-correctness-verification-tracker V1.1)
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def install_pdf(tmp_path, monkeypatch):
    """Install a one-page PDF + point DATASET_DIR / consolidated lookup at
    temp dirs so nothing touches the real corpus."""
    monkeypatch.setattr("api.main.DATASET_DIR", tmp_path / "dataset")
    pdf_path = tmp_path / "house-test.pdf"

    def _install(draw=None, size=(612, 792)):
        doc = fitz.open()
        page = doc.new_page(width=size[0], height=size[1])
        if draw is not None:
            draw(page)
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    monkeypatch.setattr("api.main._consolidated_path", lambda key: pdf_path)
    return _install


def _clipped_drawing(page):
    """A dense filled block that EXTENDS PAST the bottom of BBOX, so a crop
    at BBOX has heavy ink crossing the bottom edge and continuing inward —
    exactly the 'drawing is cut here' signal #25 detects (ink at the edge
    that penetrates inward, unlike a parallel frame line). There's page
    below BBOX's bottom (BBOX bottom=500 < page 792) so it can grow."""
    # solid black block from inside the bbox down past its bottom edge
    page.draw_rect(fitz.Rect(150, 300, 450, 620),
                   color=(0, 0, 0), fill=(0, 0, 0))


def _extract(client, bbox, **extra):
    body = {"items": [{"page": 1, "bbox_pdf_units": bbox,
                       "kind": "floorplan", "slug_override": "s", **extra}]}
    return client.post("/pdfs/house-test/extract", json=body)


def _recorded_bbox(resp):
    data = resp.json()
    entry = data["extracted"][0]
    return entry["crop_from"]["bbox_pdf_units"]


# bbox cuts through the block (block bottom=620 is below bbox bottom=500),
# with room on the page below to expand into.
BBOX = [100.0, 250.0, 500.0, 500.0]


def test_v1_1_authoritative_bbox_is_preserved_exactly(client, install_pdf):
    """With bbox_is_authoritative=true the recorded crop_from bbox equals
    the input — #25 never grows it, even though the drawing is clipped at
    the bottom edge (the control test proves it WOULD grow without it)."""
    install_pdf(draw=_clipped_drawing)
    resp = _extract(client, BBOX, bbox_is_authoritative=True)
    assert resp.status_code == 201, resp.text
    rec = _recorded_bbox(resp)
    assert rec == pytest.approx(BBOX, abs=0.5), f"bbox changed: {rec} != {BBOX}"


def test_v1_1_no_clip_expand_alias_also_preserves(client, install_pdf):
    """The existing no_clip_expand flag is the same guarantee."""
    install_pdf(draw=_clipped_drawing)
    resp = _extract(client, BBOX, no_clip_expand=True)
    assert resp.status_code == 201, resp.text
    assert _recorded_bbox(resp) == pytest.approx(BBOX, abs=0.5)


def test_v1_1_without_flag_still_auto_expands(client, install_pdf):
    """Control: without the flag, #25 behaviour is unchanged — a
    border-touching drawing grows the bbox (so the flag demonstrably
    matters). The grown bbox should be larger than the input."""
    install_pdf(draw=_clipped_drawing)
    resp = _extract(client, BBOX)
    assert resp.status_code == 201, resp.text
    rec = _recorded_bbox(resp)
    grew = (rec[0] < BBOX[0] - 0.5 or rec[1] < BBOX[1] - 0.5
            or rec[2] > BBOX[2] + 0.5 or rec[3] > BBOX[3] + 0.5)
    assert grew, f"expected auto-expansion without the flag; bbox stayed {rec}"
