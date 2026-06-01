from __future__ import annotations

import json
import subprocess
import sys


def test_compact_tool_descriptions_reduces_schema_text_in_subprocess() -> None:
    code = """
import json
import mcp_server
result = mcp_server.compact_tool_descriptions(enabled=True)
print(json.dumps(result))
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        text=True,
        capture_output=True,
    )
    result = json.loads(proc.stdout)

    assert result["enabled"] is True
    assert result["changed"] > 0
    assert result["after_chars"] < result["before_chars"] * 0.4
    assert result["saved_chars"] > 20_000


def test_compact_tool_descriptions_can_be_disabled_in_subprocess() -> None:
    code = """
import json
import mcp_server
result = mcp_server.compact_tool_descriptions(enabled=False)
print(json.dumps(result))
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        text=True,
        capture_output=True,
    )
    result = json.loads(proc.stdout)

    assert result["enabled"] is False
    assert result["changed"] == 0
    assert result["after_chars"] == result["before_chars"]
