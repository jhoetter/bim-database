from pathlib import Path


def test_methodology_and_tool_contract_digests_exist() -> None:
    prompt_dir = Path(__file__).resolve().parent.parent / "prompts"
    methodology = (prompt_dir / "methodology-digest.md").read_text()
    tool_contract = (prompt_dir / "tool-contract-digest.md").read_text()

    assert "get_scene_workbench_state" in methodology
    assert "view_mode" in methodology
    assert "upsert_rect_mass" in tool_contract
    assert "dimension_chain_transaction" in tool_contract
