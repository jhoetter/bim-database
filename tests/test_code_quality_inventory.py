from __future__ import annotations

from pathlib import Path

from scripts.code_quality_inventory import (
    analyze_fastapi_routes,
    analyze_mcp_tools,
    classify_route,
    render_markdown,
)


def test_analyze_fastapi_routes_classifies_risk_and_category(tmp_path: Path) -> None:
    api = tmp_path / "api.py"
    api.write_text(
        """
class App:
    def get(self, *args, **kwargs):
        return lambda fn: fn
    def post(self, *args, **kwargs):
        return lambda fn: fn
    def delete(self, *args, **kwargs):
        return lambda fn: fn
app = App()

@app.get("/datasets/{key}", tags=["dataset"])
def get_house():
    pass

@app.post("/datasets/{key}/{file}/plan-state/evaluate-gates", tags=["dataset"])
def evaluate():
    pass

@app.delete("/labels/{scope}/{key}/{file}", tags=["labels"])
def delete_labels():
    pass
""",
        encoding="utf-8",
    )

    report = analyze_fastapi_routes(api)

    assert report["count"] == 3
    assert report["by_risk"] == {"read_only": 1, "mutating": 1, "destructive": 1}
    assert report["items"][1]["category"] == "scene_plans"
    assert report["items"][2]["category"] == "labels"


def test_analyze_mcp_tools_classifies_payloads(tmp_path: Path) -> None:
    server = tmp_path / "mcp.py"
    server.write_text(
        """
class Mcp:
    def tool(self):
        return lambda fn: fn
mcp = Mcp()

@mcp.tool()
async def get_scene_view(key: str, file: str):
    \"\"\"Returns one ImageContent and metadata.\"\"\"

@mcp.tool()
async def update_label_attrs(key: str, file: str, label_id: str):
    \"\"\"Patch label attributes.\"\"\"
""",
        encoding="utf-8",
    )

    report = analyze_mcp_tools(server)

    assert report["count"] == 2
    assert report["by_payload"] == {"image_or_large": 1, "json": 1}
    assert report["items"][0]["category"] == "datasets"
    assert report["items"][1]["category"] == "labels"


def test_analyze_mcp_tools_counts_registered_tool_modules(tmp_path: Path) -> None:
    module = tmp_path / "mcp_geometry_tools.py"
    module.write_text(
        '''
async def score_walls(key: str, file: str):
    """Scores wall labels."""

async def helper():
    pass

_GEOMETRY_TOOL_NAMES = ["score_walls"]
''',
        encoding="utf-8",
    )

    report = analyze_mcp_tools(module)

    assert report["count"] == 1
    assert report["items"][0]["name"] == "score_walls"
    assert report["items"][0]["category"] == "geometry_cv"


def test_render_markdown_includes_route_and_tool_counts(tmp_path: Path) -> None:
    markdown = render_markdown(
        {
            "fastapi_routes": {"count": 1, "by_risk": {"read_only": 1}, "by_category": {"dataset": 1}, "items": []},
            "mcp_tools": {"count": 2, "by_category": {"labels": 2}, "by_payload": {"json": 2}, "items": []},
        }
    )

    assert "Routes: 1" in markdown
    assert "Tools: 2" in markdown


def test_classify_route_treats_reset_as_destructive() -> None:
    assert classify_route("post", "/datasets/{key}/reset") == "destructive"
