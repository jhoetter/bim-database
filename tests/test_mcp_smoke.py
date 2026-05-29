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


# ── §5.7b Building-global facts (issue #8) ───────────────────────────────


def _snapshot_house_facts(key):
    """Return (path, original_text_or_None) for restore in a finally."""
    p = api_main.DATASET_DIR / key / "house_facts.json"
    return p, (p.read_text() if p.exists() else None)


def _restore_house_facts(p, original):
    if original is not None:
        p.write_text(original)
    elif p.exists():
        p.unlink()


def test_building_global_fact_round_trip_with_provenance():
    """Issue #8: set building-global heights with provenance, read them
    back propagated to all scenes, and confirm the deterministic müNN
    derivation (EG datum + relative FH)."""
    key, file = _first_scene_with_label_file()
    if key is None:
        pytest.skip("no scene")
    p, original = _snapshot_house_facts(key)
    try:
        r1 = _run(mcp_server.set_building_global_fact(
            key=key, fact="EG_munn_mm", value=843800, source_scene=file,
            source_label_id="hm:lab-eg", confidence="high",
        ))
        assert r1["ok"], r1.get("error")
        r2 = _run(mcp_server.set_building_global_fact(
            key=key, fact="FH_mm", value=7210, source_scene=file,
            source_label_id="hm:lab-fh", confidence="high",
        ))
        assert r2["ok"], r2.get("error")

        g = _run(mcp_server.get_building_global_facts(key=key))
        assert g["ok"], g.get("error")
        facts = g["data"]["facts"]
        assert facts["EG_munn_mm"]["value"] == 843800
        assert facts["EG_munn_mm"]["source"]["scene"] == file
        assert facts["FH_mm"]["source"]["label_id"] == "hm:lab-fh"
        assert facts["FH_mm"]["confidence"] == "high"

        derived = {d["name"]: d for d in g["data"]["derived"]}
        assert derived["FH_munn_mm"]["value"] == 851010
        assert derived["FH_munn_mm"]["needs_cross_check"] is True

        # Propagation: building-wide, available on every scene.
        assert file in g["data"]["propagation"]["applies_to_scenes"]
    finally:
        _restore_house_facts(p, original)


def test_building_global_fact_rejects_unknown_name():
    key, file = _first_scene_with_label_file()
    if key is None:
        pytest.skip("no scene")
    p, original = _snapshot_house_facts(key)
    try:
        r = _run(mcp_server.set_building_global_fact(
            key=key, fact="NONSENSE_mm", value=1, source_scene=file,
        ))
        assert not r["ok"]
        assert r["error"]["code"] == "unknown_fact"
    finally:
        _restore_house_facts(p, original)


def test_building_global_fact_requires_provenance():
    key, file = _first_scene_with_label_file()
    if key is None:
        pytest.skip("no scene")
    # Empty source_scene is rejected before any disk write.
    r = _run(mcp_server.set_building_global_fact(
        key=key, fact="FH_mm", value=7210, source_scene="",
    ))
    assert not r["ok"]
    assert r["error"]["code"] == "missing_provenance"


# ── §5.8 Export ──────────────────────────────────────────────────────────


def test_validate_export_readiness_smoke():
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    key = rs["data"]["houses"][0]["key"]
    r = _run(mcp_server.validate_export_readiness(key=key))
    assert r["ok"], r.get("error")
    d = r["data"]
    assert isinstance(d["ready"], bool)
    assert isinstance(d["blockers"], list)
    # Issue #6: enriched honest-completeness contract.
    assert isinstance(d["honest_complete"], bool)
    assert isinstance(d["minimal_export_ok"], bool)
    assert d["ready"] == d["honest_complete"]
    # ready must agree with blockers being empty.
    assert d["ready"] == (len(d["blockers"]) == 0)
    pc = d["phase_completeness"]
    for p in ("W0", "W1", "W2", "W3", "W4", "W5"):
        assert p in pc and "status" in pc[p] and "required" in pc[p]
    # Honest completeness implies every *required* phase is done.
    if d["honest_complete"]:
        for p in d["required_phases"]:
            assert pc[p]["status"] == "done", f"{p} not done but honest_complete"
    else:
        # At least one required phase must be unfinished to justify it.
        assert any(pc[p]["status"] != "done" for p in d["required_phases"]) \
            or not d["minimal_export_ok"]


def test_validate_export_readiness_rejects_w0_only_house():
    """Issue #6 regression: a house with W0 tags + labeled scenes but NO
    ground-truth geometry (heights/extent/wall/calibration) must NOT be
    reported export-ready, even though the minimal sanity gate accepts it.
    """
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    # Find a house whose W1/W2 geometry is absent.
    for h in rs["data"]["houses"]:
        key = h["key"]
        r = _run(mcp_server.validate_export_readiness(key=key))
        assert r["ok"], r.get("error")
        d = r["data"]
        pc = d["phase_completeness"]
        geometry_missing = any(
            pc[p]["status"] != "done" for p in ("W1", "W2") if p in d["required_phases"]
        )
        if geometry_missing:
            assert d["ready"] is False, f"{key} ready despite missing geometry"
            assert d["blockers"], f"{key} not ready but reported no blockers"
            return
    pytest.skip("no house with missing geometry in corpus")


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

    # G4-1 introduced a 2-ref-dim-per-orientation cap. If a prior test
    # left ref dims behind, our adds would hit the cap. Defensive prune
    # of any existing is_reference dims so this test always starts clean.
    existing = _run(mcp_server.list_scene_labels(key=target_key, file=target_file))
    for lab in existing["data"]["labels"]:
        if lab.get("type") == "dimensioned_distance":
            _run(mcp_server.delete_label(
                key=target_key, file=target_file, label_id=lab["id"],
            ))
    # Re-snapshot post-prune
    existing = _run(mcp_server.list_scene_labels(key=target_key, file=target_file))
    snapshot_label_ids = [l["id"] for l in existing["data"]["labels"]]

    # Tag as ansicht so the predicate counts it
    _run(mcp_server.set_scene_tag(key=target_key, file=target_file, tag="ansicht"))

    # Add a horizontal + vertical reference dim. Coords keyed to the
    # actual image size so G4-2 (endpoint-out-of-image) doesn't bite.
    meta = _run(mcp_server.get_scene_meta(key=target_key, file=target_file))
    iw, ih = meta["data"].get("image_size_px") or [2000, 1000]
    r_h = _run(mcp_server.add_reference_dim(
        key=target_key, file=target_file,
        orientation="horizontal",
        start=[iw * 0.1, ih * 0.5], end=[iw * 0.9, ih * 0.5],
        value_mm=10000,
    ))
    assert r_h["ok"], r_h.get("error")
    r_v = _run(mcp_server.add_reference_dim(
        key=target_key, file=target_file,
        orientation="vertical",
        start=[iw * 0.5, ih * 0.1], end=[iw * 0.5, ih * 0.9],
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


# ── G4: tool-side guards (followups-tracker §G4-5) ────────────────────────


def _scratch_scene_for_guards():
    """Pick a scene we can safely write+revert against."""
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    for h in rs["data"]["houses"]:
        gh = _run(mcp_server.get_house(key=h["key"]))
        for d in gh["data"]["drawings"]:
            if d.get("kind") in ("elevation", "section"):
                return h["key"], d["file"]
    pytest.skip("no elevation/section in corpus")


def test_g4_2_add_reference_dim_rejects_out_of_bounds_endpoints():
    """G4-2: refuse start/end outside image_size_px."""
    key, file = _scratch_scene_for_guards()
    meta = _run(mcp_server.get_scene_meta(key=key, file=file))
    size = meta["data"].get("image_size_px")
    if not size or len(size) != 2:
        pytest.skip("scene has no image_size_px")
    w, h = size
    # Try start at (w+100, 0) — clearly outside.
    r = _run(mcp_server.add_reference_dim(
        key=key, file=file, orientation="horizontal",
        start=[w + 100.0, 0.0], end=[w + 200.0, 0.0],
        value_mm=10000,
    ))
    assert not r["ok"], "expected reject for out-of-bounds endpoint"
    assert r["error"]["code"] == "endpoint_out_of_image"
    assert "outside image bounds" in r["error"]["message"]


def test_g4_1_add_reference_dim_rejects_third_in_same_orientation():
    """G4-1: refuse a 3rd is_reference dim in the same orientation."""
    key, file = _scratch_scene_for_guards()
    meta = _run(mcp_server.get_scene_meta(key=key, file=file))
    size = meta["data"].get("image_size_px") or [2000, 1200]
    w, h = size
    # Defensive: clear any existing dim labels so we always start at 0.
    existing = _run(mcp_server.list_scene_labels(key=key, file=file))
    for lab in existing["data"]["labels"]:
        if lab.get("type") == "dimensioned_distance":
            _run(mcp_server.delete_label(key=key, file=file, label_id=lab["id"]))
    existing = _run(mcp_server.list_scene_labels(key=key, file=file))
    keep_ids = {l["id"] for l in existing["data"]["labels"]}
    # Add 2 horizontal ref dims.
    r1 = _run(mcp_server.add_reference_dim(
        key=key, file=file, orientation="horizontal",
        start=[100.0, h * 0.3], end=[min(w - 100, 800), h * 0.3],
        value_mm=10000,
    ))
    r2 = _run(mcp_server.add_reference_dim(
        key=key, file=file, orientation="horizontal",
        start=[100.0, h * 0.4], end=[min(w - 100, 800), h * 0.4],
        value_mm=10000,
    ))
    try:
        assert r1["ok"], r1.get("error")
        assert r2["ok"], r2.get("error")
        # 3rd horizontal should be rejected.
        r3 = _run(mcp_server.add_reference_dim(
            key=key, file=file, orientation="horizontal",
            start=[100.0, h * 0.5], end=[min(w - 100, 800), h * 0.5],
            value_mm=10000,
        ))
        assert not r3["ok"], "expected reject for 3rd ref dim same orientation"
        assert r3["error"]["code"] == "too_many_reference_dims"
        # But a VERTICAL one in the same scene should still go through.
        r4 = _run(mcp_server.add_reference_dim(
            key=key, file=file, orientation="vertical",
            start=[w * 0.5, 100.0], end=[w * 0.5, min(h - 100, 800)],
            value_mm=8000,
        ))
        assert r4["ok"], r4.get("error")
    finally:
        # Restore — delete every label we created.
        after = _run(mcp_server.list_scene_labels(key=key, file=file))
        for lab in after["data"]["labels"]:
            if lab["id"] not in keep_ids:
                _run(mcp_server.delete_label(key=key, file=file, label_id=lab["id"]))


def test_g4_3_set_house_facts_forces_assumed_true_without_edge():
    """G4-3: north_angle_deg without north_edge_label_id forces assumed=true."""
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    key = rs["data"]["houses"][0]["key"]
    # Snapshot
    snap = _run(mcp_server.get_house_facts(key=key))
    snap_orient = (snap["data"] or {}).get("orientation")
    try:
        r = _run(mcp_server.set_house_facts(key=key, patch={
            "orientation": {"north_angle_deg": 45.0, "assumed": False},
        }))
        assert r["ok"], r.get("error")
        # The auto_corrections meta should call out the correction.
        warnings = r["_meta"].get("auto_corrections", [])
        assert any("assumed" in w.lower() for w in warnings), \
            f"expected auto-correction warning; got {warnings!r}"
        # Verify on disk the assumed flag is True.
        check = _run(mcp_server.get_house_facts(key=key))
        assert check["data"]["orientation"]["assumed"] is True
    finally:
        # Restore
        if snap_orient is not None:
            _run(mcp_server.set_house_facts(key=key, patch={"orientation": snap_orient}))


def test_g4_4_set_house_facts_warns_on_heights_without_labels():
    """G4-4: heights set without matching height_mark labels surfaces a
    warning in _meta.warnings (non-blocking by default)."""
    rs = _run(mcp_server.list_houses())
    if not rs["data"]["houses"]:
        pytest.skip("no houses")
    key = rs["data"]["houses"][0]["key"]
    snap = _run(mcp_server.get_house_facts(key=key))
    snap_heights = (snap["data"] or {}).get("heights")
    try:
        # Try to set first_mm without any height_mark label.
        r = _run(mcp_server.set_house_facts(key=key, patch={
            "heights": {"first_mm": 12345},
        }))
        # Should succeed (non-strict mode) but include a warning.
        assert r["ok"], r.get("error")
        warnings = r["_meta"].get("warnings", [])
        if not warnings:
            # House may already have a height_mark; check.
            gh = _run(mcp_server.get_house(key=key))
            has_first = False
            for d in gh["data"]["drawings"]:
                lbls = _run(mcp_server.list_scene_labels(key=key, file=d["file"]))
                for lab in lbls["data"]["labels"]:
                    if lab.get("type") == "height_mark":
                        # We'd need attrs to confirm — skip in that case.
                        pytest.skip("house already has height_mark labels; can't test warning path")
            assert has_first
        else:
            assert any("first_mm" in w for w in warnings), \
                f"expected first_mm warning; got {warnings!r}"
    finally:
        if snap_heights is not None:
            _run(mcp_server.set_house_facts(key=key, patch={"heights": snap_heights}))


# ── Tool description snapshot (tracker §9 risk: description drift) ───────


def test_h6_add_reference_dim_rejects_zero_length():
    """H6 (followups-2): refuse start == end on add_reference_dim — a
    0-px line is not a usable dim and silently breaks the homography."""
    key, file = _scratch_scene_for_guards()
    # Both endpoints identical.
    r = _run(mcp_server.add_reference_dim(
        key=key, file=file, orientation="horizontal",
        start=[400.0, 400.0], end=[400.0, 400.0],
        value_mm=10000,
    ))
    assert not r["ok"], "expected reject for zero-length dim line"
    assert r["error"]["code"] == "degenerate_dim_line"
    # Slightly offset but still < 2 px should also fail.
    r2 = _run(mcp_server.add_reference_dim(
        key=key, file=file, orientation="horizontal",
        start=[400.0, 400.0], end=[401.0, 400.0],
        value_mm=10000,
    ))
    assert not r2["ok"]
    assert r2["error"]["code"] == "degenerate_dim_line"


def test_h6_add_reference_dim_rejects_axis_mismatch():
    """H6 (followups-2): refuse a horizontal-declared dim that's
    clearly vertical (and vice versa). Catches the agent passing
    endpoints from a vertical line under orientation='horizontal'."""
    key, file = _scratch_scene_for_guards()
    meta = _run(mcp_server.get_scene_meta(key=key, file=file))
    size = meta["data"].get("image_size_px") or [2000, 1200]
    w, h = size
    # Declared horizontal but the line is vertical (dy >> dx).
    r = _run(mcp_server.add_reference_dim(
        key=key, file=file, orientation="horizontal",
        start=[w * 0.4, 100.0], end=[w * 0.4, min(h - 100, 800)],
        value_mm=8000,
    ))
    assert not r["ok"], "expected reject for axis mismatch"
    assert r["error"]["code"] == "orientation_mismatch"


def test_h5_get_scene_view_with_labels_renders_labels():
    """H5-1/H5-2: the verify view must DIFFER from a clean-grid render
    when a label has been placed (proving the label render actually
    drew something an agent could spot-check)."""
    import json as _json

    key, file = _scratch_scene_for_guards()
    # Clear any pre-existing geometry-bearing labels for a clean start.
    existing = _run(mcp_server.list_scene_labels(key=key, file=file))
    for lab in existing["data"]["labels"]:
        if lab.get("type") in (
            "wall", "height_mark", "dimensioned_distance",
            "dimension_number", "component_line",
            "floorplan_opening", "view_opening",
        ):
            _run(mcp_server.delete_label(key=key, file=file, label_id=lab["id"]))

    meta = _run(mcp_server.get_scene_meta(key=key, file=file))
    size = meta["data"].get("image_size_px") or [2000, 1200]
    w, h = size

    # Baseline: render the verify view with no relevant labels.
    baseline = _run(mcp_server.get_scene_view_with_labels(
        key=key, file=file, tiers="broad", max_dim=400,
    ))
    assert isinstance(baseline, list) and len(baseline) == 2
    baseline_img, _ = baseline
    baseline_data = baseline_img.data

    # Add a wall label — visible orange stroke through the middle.
    wall_payload = {
        "type": "wall",
        "geometry": {
            "start": [w * 0.1, h * 0.5],
            "end": [w * 0.9, h * 0.5],
        },
        "attributes": {"thickness_mm": 365},
        "status": "readable",
    }
    add = _run(mcp_server.upsert_label(key=key, file=file, label=wall_payload))
    assert add["ok"], add.get("error")
    new_id = add["data"]["label_id"]

    try:
        # Re-render — must differ because the wall stroke is now drawn.
        with_labels = _run(mcp_server.get_scene_view_with_labels(
            key=key, file=file, tiers="broad", max_dim=400,
        ))
        with_labels_img, with_labels_text = with_labels
        assert with_labels_img.type == "image"
        assert with_labels_img.mimeType == "image/png"
        assert with_labels_img.data != baseline_data, (
            "verify view must change after a wall label is placed — "
            "H5 visual verification is broken"
        )
        envelope = _json.loads(with_labels_text.text)
        assert envelope["ok"]
        ids = [lab["id"] for lab in envelope["data"]["labels_in_view"]]
        assert new_id in ids, (
            f"labels_in_view should mention {new_id}; saw {ids}"
        )
    finally:
        _run(mcp_server.delete_label(key=key, file=file, label_id=new_id))


def test_get_scene_view_enhance_passthrough():
    """Issue #2: enhance flows MCP tool -> API -> renderer and the
    envelope echoes the applied mode."""
    import json as _json
    key, file = _first_scene_with_label_file()
    if key is None:
        pytest.skip("no scene")
    res = _run(mcp_server.get_scene_view(
        key=key, file=file, tiers="broad", max_dim=400, enhance="clahe",
    ))
    img, text = res
    assert img.type == "image" and img.mimeType == "image/png"
    env = _json.loads(text.text)
    assert env["ok"], env.get("error")
    assert env["data"]["enhance"] == "clahe"


def test_get_scene_view_enhance_rejects_bad_mode():
    """Issue #2: an unknown enhance mode is a 400 from the API, surfaced
    as an error envelope."""
    import json as _json
    key, file = _first_scene_with_label_file()
    if key is None:
        pytest.skip("no scene")
    res = _run(mcp_server.get_scene_view(
        key=key, file=file, tiers="broad", max_dim=400, enhance="bogus",
    ))
    # Error path returns a single TextContent envelope.
    env = _json.loads(res[0].text)
    assert not env["ok"]


def test_h5_7_verify_label_placement_auto_crops():
    """H5-7: verify_label_placement should look up the label, compute
    a tight crop around its geometry, and return a verify view."""
    import json as _json

    key, file = _scratch_scene_for_guards()
    meta = _run(mcp_server.get_scene_meta(key=key, file=file))
    size = meta["data"].get("image_size_px") or [2000, 1200]
    w, h = size

    wall = {
        "type": "wall",
        "geometry": {
            "start": [w * 0.3, h * 0.4],
            "end": [w * 0.7, h * 0.4],
        },
        "attributes": {"thickness_mm": 365},
        "status": "readable",
    }
    add = _run(mcp_server.upsert_label(key=key, file=file, label=wall))
    assert add["ok"], add.get("error")
    new_id = add["data"]["label_id"]
    try:
        result = _run(mcp_server.verify_label_placement(
            key=key, file=file, label_id=new_id, pad_px=40, max_dim=400,
        ))
        assert isinstance(result, list) and len(result) == 2
        img, txt = result
        assert img.type == "image"
        envelope = _json.loads(txt.text)
        assert envelope["ok"], envelope.get("error")
        # The padded crop must enclose the wall — verify the region
        # in the envelope matches what we computed.
        region = envelope["data"]["region"]
        x0, y0, x1, y1 = (int(v) for v in region.split(","))
        assert x0 <= w * 0.3 and x1 >= w * 0.7
        assert y0 <= h * 0.4 <= y1
        # And the new label is listed in labels_in_view.
        ids = [lab["id"] for lab in envelope["data"]["labels_in_view"]]
        assert new_id in ids
    finally:
        _run(mcp_server.delete_label(key=key, file=file, label_id=new_id))


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
        mcp_server.set_building_global_fact,  # issue #8
        mcp_server.get_building_global_facts,  # issue #8
        mcp_server.export_house, mcp_server.list_anomalies,
        mcp_server.dump_run_summary,
        mcp_server.get_scene_view_with_labels,  # H5-2
        mcp_server.verify_label_placement,  # H5-7
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
