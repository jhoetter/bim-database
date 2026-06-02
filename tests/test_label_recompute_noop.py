"""H2 (code-quality-tracker): the per-label full-house fact recompute.

Every PUT /labels used to trigger an O(scenes) recompute unconditionally.
A byte-identical re-PUT (common in the agent loop) now skips both the write
and the recompute — but only when facts already exist, so a missing
house_facts is always rebuilt.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main  # noqa: E402


def _seed_house(key: str) -> tuple[str, str]:
    file = f"{key}-scene.jpg"
    ds = api_main.DATASET_DIR / key
    shutil.rmtree(ds, ignore_errors=True)
    (ds / "labels").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), (255, 255, 255)).save(ds / file)
    (ds / "manifest.json").write_text(json.dumps({
        "key": key,
        "drawings": [{"file": file, "kind": "floorplan"}],
    }))
    return key, file


def test_identical_reput_skips_recompute_but_missing_facts_rebuilds():
    key, file = _seed_house("house-zztest-noop")
    try:
        client = TestClient(api_main.app)
        payload = api_main._label_skeleton("dataset", key, file)
        payload["scene_tag"] = "grundriss"
        payload["scene_level"] = "eg"

        # First write: persists labels and runs the recompute.
        r1 = client.put(f"/labels/dataset/{key}/{file}", json=payload)
        assert r1.status_code == 200, r1.text
        assert r1.json().get("unchanged") is not True
        facts_path = api_main.DATASET_DIR / key / "house_facts.json"
        assert facts_path.exists()
        mtime1 = facts_path.stat().st_mtime_ns

        # Identical re-PUT: skipped — flagged unchanged, facts file untouched.
        r2 = client.put(f"/labels/dataset/{key}/{file}", json=payload)
        assert r2.status_code == 200, r2.text
        assert r2.json().get("unchanged") is True
        assert facts_path.stat().st_mtime_ns == mtime1, "recompute ran on a no-op"

        # Safety guard: identical content but facts missing → must rebuild.
        facts_path.unlink()
        r3 = client.put(f"/labels/dataset/{key}/{file}", json=payload)
        assert r3.status_code == 200, r3.text
        assert r3.json().get("unchanged") is not True
        assert facts_path.exists(), "missing facts were not rebuilt"
    finally:
        shutil.rmtree(api_main.DATASET_DIR / key, ignore_errors=True)
