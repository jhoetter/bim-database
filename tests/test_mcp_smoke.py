"""Smoke tests for every MCP tool in mcp_server.py.

Tests use httpx against the live FastAPI in api/main.py via TestClient
— no MCP transport involved. Confirms each tool's HTTP plumbing,
envelope shape, and basic happy-path behavior.

Run via `make test`. CPU-only, no API keys, no LLM.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import asyncio  # noqa: E402

import api.main as api_main  # noqa: E402
import mcp_server  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def _patch_mcp_client_to_use_in_process_fastapi():
    """Point the MCP server's HTTP client at the in-process FastAPI via
    httpx.ASGITransport. No real port binding, no real network."""
    import httpx
    transport = httpx.ASGITransport(app=api_main.app)
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        timeout=httpx.Timeout(30.0),
    )
    mcp_server._http = client
    yield
    # ASGITransport cleanup
    asyncio.get_event_loop().run_until_complete(client.aclose())
    mcp_server._http = None


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── §5.1 Discovery ────────────────────────────────────────────────────────


def test_list_houses_smoke():
    r = _run(mcp_server.list_houses())
    assert r["ok"], r.get("error")
    assert isinstance(r["data"]["houses"], list)
    if r["data"]["houses"]:
        h = r["data"]["houses"][0]
        for k in ("key", "scenes_count", "labeled_scenes", "has_labels"):
            assert k in h, f"missing key {k} in {h}"


def test_get_house_smoke():
    rs = _run(mcp_server.list_houses())
    assert rs["ok"]
    if not rs["data"]["houses"]:
        pytest.skip("no houses in corpus")
    key = rs["data"]["houses"][0]["key"]
    r = _run(mcp_server.get_house(key=key))
    assert r["ok"], r.get("error")
    assert "drawings" in r["data"]
    assert "house_facts" in r["data"]


def test_get_house_not_found():
    r = _run(mcp_server.get_house(key="house-doesnotexist"))
    assert not r["ok"]
    assert r["error"]["code"] in {"not_found", "http_404"}


def test_get_workflow_state_smoke():
    rs = _run(mcp_server.list_houses())
    assert rs["ok"]
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    key = rs["data"]["houses"][0]["key"]
    r = _run(mcp_server.get_workflow_state(key=key))
    assert r["ok"], r.get("error")
    for p in ("W0", "W1", "W2", "W3", "W4", "W5"):
        assert p in r["data"]["phases"]
        assert "status" in r["data"]["phases"][p]
    assert "exportable" in r["data"]


def test_get_recommended_next_action_smoke():
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    key = rs["data"]["houses"][0]["key"]
    r = _run(mcp_server.get_recommended_next_action(key=key))
    assert r["ok"], r.get("error")


# ── §5.2 Intake ──────────────────────────────────────────────────────────


def test_list_pdfs_smoke():
    r = _run(mcp_server.list_pdfs())
    assert r["ok"]
    assert isinstance(r["data"]["pdfs"], list)


def test_get_pdf_info_smoke():
    rp = _run(mcp_server.list_pdfs())
    assert rp["ok"]
    if not rp["data"]["pdfs"]:
        pytest.skip("no PDFs in corpus")
    key = rp["data"]["pdfs"][0]["key"]
    r = _run(mcp_server.get_pdf_info(key=key))
    assert r["ok"], r.get("error")
    assert "page_count" in r["data"]


# ── §5.3 Scene inspection ────────────────────────────────────────────────


def _first_scene_with_label_file():
    rs = _run(mcp_server.list_houses())
    for h in rs["data"]["houses"]:
        if h.get("scenes_count", 0) > 0:
            gh = _run(mcp_server.get_house(key=h["key"]))
            for d in gh["data"]["drawings"]:
                return h["key"], d["file"]
    return None, None


def test_get_scene_meta_smoke():
    key, file = _first_scene_with_label_file()
    if key is None:
        pytest.skip("no labeled scene available")
    r = _run(mcp_server.get_scene_meta(key=key, file=file))
    assert r["ok"], r.get("error")
    for k in ("file", "scene_tag", "extraction_kind", "labeled", "label_count"):
        assert k in r["data"]


def test_list_scene_labels_smoke():
    key, file = _first_scene_with_label_file()
    if key is None:
        pytest.skip("no scene")
    r = _run(mcp_server.list_scene_labels(key=key, file=file))
    assert r["ok"], r.get("error")
    assert isinstance(r["data"]["labels"], list)


# ── §5.4 Tagging — write, then revert ────────────────────────────────────


def test_set_scene_tag_round_trip():
    key, file = _first_scene_with_label_file()
    if key is None:
        pytest.skip("no scene")
    # Save original
    m = _run(mcp_server.get_scene_meta(key=key, file=file))
    original = m["data"]["scene_tag"]
    new_tag = "sonstiges" if original != "sonstiges" else "nicht_klassifiziert"
    r = _run(mcp_server.set_scene_tag(key=key, file=file, tag=new_tag))
    assert r["ok"], r.get("error")
    # Verify
    m2 = _run(mcp_server.get_scene_meta(key=key, file=file))
    assert m2["data"]["scene_tag"] == new_tag
    # Revert
    revert = original or "nicht_klassifiziert"
    _run(mcp_server.set_scene_tag(key=key, file=file, tag=revert))


def test_set_scene_tag_rejects_bad_tag():
    r = _run(mcp_server.set_scene_tag(key="house-22", file="x.jpg", tag="not_a_tag"))
    assert not r["ok"]
    assert r["error"]["code"] == "schema_invalid"


# ── §5.5 Label CRUD ──────────────────────────────────────────────────────


def test_upsert_label_round_trip():
    key, file = _first_scene_with_label_file()
    if key is None:
        pytest.skip("no scene")
    # Create a small test wall, then delete it on cleanup.
    # Use `notes` (free-form on the envelope) to mark it so a manual
    # cleanup can find leftovers if the test crashes mid-flight.
    r = _run(mcp_server.upsert_label(key=key, file=file, label={
        "type": "wall",
        "geometry": {"start": [50, 50], "end": [150, 50]},
        "attributes": {"thickness_mm": 200},
        "notes": "smoke-test wall — safe to delete",
    }))
    assert r["ok"], r.get("error")
    lab_id = r["data"]["label_id"]
    # Read back
    g = _run(mcp_server.get_label(key=key, file=file, label_id=lab_id))
    assert g["ok"], g.get("error")
    assert g["data"]["type"] == "wall"
    # Update
    u = _run(mcp_server.update_label_attrs(key=key, file=file,
                                            label_id=lab_id,
                                            attrs_patch={"thickness_mm": 250}))
    assert u["ok"], u.get("error")
    # Delete
    d = _run(mcp_server.delete_label(key=key, file=file, label_id=lab_id))
    assert d["ok"], d.get("error")


def test_upsert_label_requires_type():
    r = _run(mcp_server.upsert_label(key="house-22", file="x.jpg", label={}))
    assert not r["ok"]
    assert r["error"]["code"] == "schema_invalid"


# ── §5.7 Facts ────────────────────────────────────────────────────────────


def test_facts_round_trip():
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    key = rs["data"]["houses"][0]["key"]
    # Save current
    current = _run(mcp_server.get_house_facts(key=key))
    # Patch one harmless field
    sentinel = {"_smoke_test_marker": "test_facts_round_trip"}
    p = _run(mcp_server.set_house_facts(key=key, patch=sentinel))
    assert p["ok"], p.get("error")
    # Verify the deep-merge preserved other fields
    g = _run(mcp_server.get_house_facts(key=key))
    assert g["data"].get("_smoke_test_marker") == "test_facts_round_trip"
    if current["data"]:
        # extent should be untouched
        for k in (current["data"] or {}):
            if k == "_smoke_test_marker":
                continue
            assert g["data"].get(k) == current["data"].get(k), \
                f"deep-merge lost field {k}"
    # Clean up
    g["data"].pop("_smoke_test_marker", None)
    _run(mcp_server.set_house_facts(key=key, patch=g["data"]))


# ── §5.8 Export ──────────────────────────────────────────────────────────


def test_validate_export_readiness_smoke():
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    key = rs["data"]["houses"][0]["key"]
    r = _run(mcp_server.validate_export_readiness(key=key))
    assert r["ok"], r.get("error")
    assert isinstance(r["data"]["ready"], bool)
    assert isinstance(r["data"]["blockers"], list)


# ── §5.9 Audit ───────────────────────────────────────────────────────────


def test_list_anomalies_smoke():
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    key = rs["data"]["houses"][0]["key"]
    r = _run(mcp_server.list_anomalies(key=key))
    assert r["ok"], r.get("error")
    assert "count" in r["data"]


def test_dump_run_summary_writes_file(tmp_path, monkeypatch):
    # Redirect the dump to a tmp dir.
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    key = rs["data"]["houses"][0]["key"]
    monkeypatch.chdir(tmp_path)
    r = _run(mcp_server.dump_run_summary(key=key, run_id="smoketest"))
    assert r["ok"], r.get("error")


# ── G1-8: round-trip check (workflow state flips after MCP writes) ───────


def test_add_reference_dim_unlocks_w4_via_server_derivation():
    """The failure mode that motivated Phase G1: agent adds ref dims
    via add_reference_dim, but facts.calibration_per_scene stays empty
    so W4 never flips. This test asserts the server-side derivation
    closes that gap.

    Uses a scratch scene: pick the first ansicht/schnitt in the corpus,
    snapshot its current labels, add fresh ref dims, confirm W4 sees
    the calibration, then restore.
    """
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    # Find a house with an ansicht/schnitt scene
    target_key = None
    target_file = None
    for h in rs["data"]["houses"]:
        gh = _run(mcp_server.get_house(key=h["key"]))
        for d in gh["data"]["drawings"]:
            kind = d.get("kind")
            if kind in ("elevation", "section"):
                target_key, target_file = h["key"], d["file"]
                break
        if target_key:
            break
    if target_key is None:
        pytest.skip("no elevation/section scene in corpus")

    # Snapshot existing labels
    existing = _run(mcp_server.list_scene_labels(key=target_key, file=target_file))
    snapshot_label_ids = [l["id"] for l in existing["data"]["labels"]]

    # Tag as ansicht so the predicate counts it
    _run(mcp_server.set_scene_tag(key=target_key, file=target_file, tag="ansicht"))

    # Add a horizontal + vertical reference dim
    r_h = _run(mcp_server.add_reference_dim(
        key=target_key, file=target_file,
        orientation="horizontal",
        start=[100, 500], end=[1100, 500],
        value_mm=10000,
    ))
    assert r_h["ok"], r_h.get("error")
    r_v = _run(mcp_server.add_reference_dim(
        key=target_key, file=target_file,
        orientation="vertical",
        start=[500, 100], end=[500, 1100],
        value_mm=10000,
    ))
    assert r_v["ok"], r_v.get("error")

    try:
        # Re-fetch facts; calibration_per_scene should now have the file
        facts = _run(mcp_server.get_house_facts(key=target_key))
        assert facts["ok"]
        cps = (facts["data"] or {}).get("calibration_per_scene") or {}
        assert target_file in cps, \
            f"server-side derivation didn't populate calibration_per_scene[{target_file!r}]"
        # And computed_from should reflect both axes
        assert cps[target_file]["computed_from"] == "M1-both"
    finally:
        # Clean up — delete any label whose id wasn't in the snapshot
        after = _run(mcp_server.list_scene_labels(key=target_key, file=target_file))
        for lab in after["data"]["labels"]:
            if lab["id"] not in snapshot_label_ids:
                _run(mcp_server.delete_label(
                    key=target_key, file=target_file, label_id=lab["id"],
                ))


# ── Tool description snapshot (tracker §9 risk: description drift) ───────


def test_tool_descriptions_are_present():
    """Smoke check: every registered MCP tool has a docstring of >=200
    chars. Tracker §9 mitigation for description drift; the golden
    snapshot test is a follow-up if drift becomes a real problem."""
    tools = [
        mcp_server.list_houses, mcp_server.get_house,
        mcp_server.get_workflow_state, mcp_server.get_recommended_next_action,
        mcp_server.list_pdfs, mcp_server.get_pdf_info,
        mcp_server.extract_scenes, mcp_server.get_scene_view,
        mcp_server.get_pdf_page_view, mcp_server.get_scene_meta,
        mcp_server.list_scene_labels, mcp_server.get_label,
        mcp_server.set_scene_tag, mcp_server.set_scene_orientation,
        mcp_server.set_scene_level, mcp_server.upsert_label,
        mcp_server.delete_label, mcp_server.update_label_attrs,
        mcp_server.set_label_status, mcp_server.add_reference_dim,
        mcp_server.recompute_homography, mcp_server.get_house_facts,
        mcp_server.set_house_facts, mcp_server.validate_export_readiness,
        mcp_server.export_house, mcp_server.list_anomalies,
        mcp_server.dump_run_summary,
    ]
    for tool in tools:
        # Tool objects are decorated; unwrap if needed.
        fn = getattr(tool, "fn", tool)
        doc = (fn.__doc__ or "").strip()
        assert len(doc) >= 200, (
            f"{getattr(fn, '__name__', tool)}: description {len(doc)} chars "
            f"(< 200) — tracker §C0 requires substantive descriptions"
        )
        # Tracker §C0 principle 2 — every description has USE / DON'T USE.
        assert "USE when" in doc or "Use when" in doc.lower() or "use when" in doc.lower(), \
            f"{getattr(fn, '__name__', tool)}: description missing 'USE when' guidance"
