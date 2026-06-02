"""MCP resources + prompts for the bim-database server (L2 / H5).

The prompt bodies and the grid-coordinates doc used to be ~750 lines of
hardcoded f-strings inside mcp_server.py. They now live as templates under
prompts/*.md and are loaded here. Templates use string.Template placeholders
`$key` / `$file` / `$spec_notice`; literal `$` never appears in the text, so
substitution is unambiguous.

This module imports only the stdlib and exposes `register(mcp, ...)`, which
mcp_server calls after constructing its FastMCP instance. (It deliberately
does NOT `import mcp_server`, because the server is launched as `python
mcp_server.py` — i.e. as `__main__` — so importing it by name would
re-execute the whole module a second time.)
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from string import Template

_PROMPT_DIR = Path(__file__).parent / "prompts"
_SCHEMA_DIR = Path(__file__).parent / "schema"


@lru_cache(maxsize=None)
def _template(name: str) -> Template:
    return Template((_PROMPT_DIR / f"{name}.md").read_text())


@lru_cache(maxsize=None)
def _spec_notice() -> str:
    return (_PROMPT_DIR / "_spec_notice.md").read_text()


def _render(name: str, *, key: str = "", file: str = "") -> str:
    return _template(name).safe_substitute(
        key=key, file=file, spec_notice=_spec_notice()
    )


def register(mcp, *, server_version: str, api_base: str) -> None:
    """Register all bim-db resources and prompts on the given FastMCP."""

    # ── §5.10 MCP resources (read-only context) ──────────────────────────

    @mcp.resource("bim-db://version")
    def resource_version() -> str:
        return json.dumps({
            "server_version": server_version,
            "api_base": api_base,
            "tool_count": "phase-A subset (4 tools; Phase B adds 18)",
        }, indent=2)

    @mcp.resource("bim-db://schema/scene_labels")
    def resource_scene_labels_schema() -> str:
        p = _SCHEMA_DIR / "scene_labels.schema.json"
        return p.read_text() if p.exists() else "{}"

    @mcp.resource("bim-db://schema/intake_manifest")
    def resource_intake_manifest_schema() -> str:
        p = _SCHEMA_DIR / "intake_manifest.schema.json"
        return p.read_text() if p.exists() else "{}"

    @mcp.resource("bim-db://docs/grid-coordinates")
    def resource_grid_coordinates() -> str:
        return (_PROMPT_DIR / "grid-coordinates.md").read_text()

    # ── §5.11 MCP prompts (adapter playbooks) ────────────────────────────
    # Transport/discovery adapters for MCP clients — NOT the source of truth
    # for labeling methodology (that lives in bim-agent/spec/). Bodies are in
    # prompts/*.md; these wrappers fill in $key/$file/$spec_notice.

    @mcp.prompt(name="label-house")
    def prompt_label_house(key: str) -> str:
        return _render("label-house", key=key)

    @mcp.prompt(name="W0-inventory")
    def prompt_w0_inventory(key: str) -> str:
        return _render("W0-inventory", key=key)

    @mcp.prompt(name="W1-height-anchor")
    def prompt_w1_height_anchor(key: str) -> str:
        return _render("W1-height-anchor", key=key)

    @mcp.prompt(name="W2-footprint")
    def prompt_w2_footprint(key: str) -> str:
        return _render("W2-footprint", key=key)

    @mcp.prompt(name="W3-orientation")
    def prompt_w3_orientation(key: str) -> str:
        return _render("W3-orientation", key=key)

    @mcp.prompt(name="W4-calibration")
    def prompt_w4_calibration(key: str) -> str:
        return _render("W4-calibration", key=key)

    @mcp.prompt(name="W5-detail")
    def prompt_w5_detail(key: str) -> str:
        return _render("W5-detail", key=key)

    @mcp.prompt(name="diagnose-failed-export")
    def prompt_diagnose_failed_export(key: str) -> str:
        return _render("diagnose-failed-export", key=key)

    @mcp.prompt(name="diagnose-degenerate-homography")
    def prompt_diagnose_degenerate_homography(key: str, file: str) -> str:
        return _render("diagnose-degenerate-homography", key=key, file=file)
