"""reset_house_labeling must clear plan-state by default, else a "fresh" run
resumes the old run's stale tasks/defects (the trap hit after the 2026-06-04
purge: labels emptied but 71 stale defects lingered in the EG plan)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import mcp_server  # noqa: E402
import mcp_tools_labels  # noqa: E402,F401  (registers the tool on mcp_server)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _capture_delete(monkeypatch):
    seen = {}

    async def fake_delete(path):
        seen["path"] = path
        return 200, {"ok": True, "plans_deleted": 9, "labels_reset": 9}

    monkeypatch.setattr(mcp_server, "_api_delete", fake_delete)
    return seen


def test_reset_clears_plans_by_default(monkeypatch):
    seen = _capture_delete(monkeypatch)
    r = _run(mcp_server.reset_house_labeling(key="house-22"))
    assert r["ok"]
    assert "reset_plans=true" in seen["path"]


def test_reset_can_keep_plans(monkeypatch):
    seen = _capture_delete(monkeypatch)
    _run(mcp_server.reset_house_labeling(key="house-22", reset_plans=False))
    assert "reset_plans=false" in seen["path"]
