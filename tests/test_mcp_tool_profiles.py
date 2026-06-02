from __future__ import annotations

import mcp_server


class _FakeManager:
    def __init__(self) -> None:
        self._tools = {
            "create_scene_plan_state_from_template": object(),
            "get_scene_view": object(),
            "verify_label_placement": object(),
            "extract_scenes": object(),
            "export_house": object(),
        }


class _FakeMcp:
    def __init__(self) -> None:
        self._tool_manager = _FakeManager()

    def remove_tool(self, name: str) -> None:
        self._tool_manager._tools.pop(name)


def test_apply_tool_profile_removes_tools_outside_profile(monkeypatch) -> None:
    fake = _FakeMcp()
    monkeypatch.setattr(mcp_server, "mcp", fake)

    removed = mcp_server._apply_tool_profile("floorplan")

    assert "extract_scenes" in removed
    assert "export_house" in removed
    assert "create_scene_plan_state_from_template" in fake._tool_manager._tools
    assert "get_scene_view" in fake._tool_manager._tools
    assert "verify_label_placement" in fake._tool_manager._tools


def test_apply_tool_profile_all_keeps_everything(monkeypatch) -> None:
    fake = _FakeMcp()
    monkeypatch.setattr(mcp_server, "mcp", fake)

    removed = mcp_server._apply_tool_profile("all")

    assert removed == []
    assert set(fake._tool_manager._tools) == {
        "create_scene_plan_state_from_template",
        "get_scene_view",
        "verify_label_placement",
        "extract_scenes",
        "export_house",
    }
