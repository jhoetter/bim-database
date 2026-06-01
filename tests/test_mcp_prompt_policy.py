from __future__ import annotations

import mcp_server


def test_label_house_prompt_contains_context_bloat_policy() -> None:
    prompt = mcp_server.mcp._prompt_manager._prompts["label-house"].fn("house-x")

    assert "get_house_context_summary" in prompt
    assert "image_delivery=\"auto\"" in prompt
    assert "write_scene_handoff_summary" in prompt
    assert "max_items" in prompt
    assert "summary_only=true" in prompt
