from __future__ import annotations

import json
import subprocess
import sys

import mcp_server


def test_tool_profile_names_include_worker_roles() -> None:
    assert {"all", "inventory", "floorplan", "view", "review"} <= set(mcp_server.tool_profile_names())


def test_floorplan_tool_profile_reduces_catalog_in_subprocess() -> None:
    code = """
import asyncio, json
import mcp_server
async def main():
    result = await mcp_server.apply_tool_profile("floorplan")
    print(json.dumps(result))
asyncio.run(main())
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        text=True,
        capture_output=True,
    )
    result = json.loads(proc.stdout)

    assert result["profile"] == "floorplan"
    assert result["before_count"] > result["after_count"]
    assert "extract_scenes" in result["removed"]
