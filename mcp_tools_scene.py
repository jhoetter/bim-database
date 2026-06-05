"""Scene MCP tools (extracted from mcp_server.py, H5).

Stable helpers imported by value; HTTP helpers, __file__, and inter-tool
calls go through `mcp_server.` so the test-harness patches still apply.
"""
from __future__ import annotations

from mcp.types import ImageContent
from mcp.types import TextContent
from mcp_context_summary import compact_label
from mcp_context_summary import compact_plan_status
from mcp_context_summary import aggregate_house_quality, compact_scene_row
from mcp_context_summary import label_counts
from typing import Any
import httpx
import json
import time

import mcp_server
from mcp_server import (
    _api_unreachable_error,
    _compact_workflow_for_summary,
    _derive_workflow_state,
    _err,
    _http_status_to_error,
    _image_delivery_payload,
    _label_summary,
    _load_facts_and_scene_meta,
    _ok,
    _wait_for_api,
    _wrap_text,
    mcp,
)

# WS-B (legibility-first tracker) B4: detect flailing — the same crop fetched
# over and over instead of escalating strategy (the West-elevation pattern:
# one identical bbox pulled 12x while the grid mesh kept defeating the ink).
# In-process per-(verb,file,region,params) counter; returns a nudge at >=3.
_READ_REPEAT_COUNTS: dict[str, int] = {}
_READ_REPEAT_LIMIT = 3


def _parse_region_edges(region: str | None) -> int | None:
    """Return the long edge (px) of a 'x0,y0,x1,y1' region, or None if the
    string is malformed / non-positive area."""
    if not region:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in str(region).split(","))
    except (ValueError, TypeError):
        return None
    if not (x1 > x0 and y1 > y0):
        return None
    return int(round(max(x1 - x0, y1 - y0)))


def _note_repeat(signature: str) -> str | None:
    n = _READ_REPEAT_COUNTS.get(signature, 0) + 1
    _READ_REPEAT_COUNTS[signature] = n
    if n >= _READ_REPEAT_LIMIT:
        return (
            f"You have fetched this identical crop {n}x. Re-cropping the same "
            "pixels won't change what you see — CHANGE STRATEGY: escalate to "
            "zoom_read at higher dpi, change enhance (auto->threshold), or crop "
            "tighter onto the single feature you're reading."
        )
    return None


@mcp.tool()
async def get_scene_view(
    key: str,
    file: str,
    region: str | None = None,
    tiers: str = "broad,finer",
    max_dim: int = 1600,
    enhance: str | None = None,
    format: str = "png8",
    style: str = "standard",
    target: str | None = None,
    target_line: str = "none",
    background_opacity: float | None = None,
    view_mode: str | None = None,
    dpi: int | None = None,
    image_delivery: str = "auto",
) -> list[ImageContent | TextContent]:
    """SURVEY-TIER scene view: image + three-tier coordinate grid overlay.
    Cheap by default (downscaled to max_dim, png8, grid on) — for ORIENTATION:
    locating features, understanding layout, picking the region to read next.

    For READING A VALUE or ANCHORING geometry (a dimension number, height mark,
    wall edge) DON'T read it off this survey view — use `read_scene_region`
    (tight, native, lossless, grid-free) or `zoom_read_scene_region` (higher
    DPI from the PDF vector). A value read off a downscaled/grid view is
    low-fidelity and will not count toward gold (the fidelity gate flags it).

    USE when:
      - Orienting on a scene / picking the next region to read.
      - Identifying scene_tag at W0 (without region; full image).

    DON'T USE when:
      - You are reading a value or anchoring a wall — use read_scene_region.
      - You only need scene metadata — call `get_scene_meta`.

    Args:
      key:     house key, e.g. "house-22".
      file:    scene filename, e.g. "house-22-ansicht-sued.jpg".
      region:  optional 'x0,y0,x1,y1' (source-pixel coords) — agent zoom.
      tiers:   comma list of {broad, finer, detail}; default broad+finer.
               Pass detail only on small, intentional coordinate crops; it is
               too dense for overview or label QA views.
      max_dim: cap on the longer side of the output PNG; default 1600.
      enhance: contrast lift for faint scans (issue #2): one of
               none|auto|clahe|threshold (default none). "auto"/"clahe"
               apply CLAHE; "threshold" additionally binarizes. This is
               preprocessing for the vision-LLM reader, not OCR. Pixel
               positions are unchanged — coordinates stay SOURCE-pixel,
               so readings still map 1:1 to the un-cropped scene.
      format:  png|png8 (issue #3). Default png8 — a 256-colour palette
               PNG, typically 2-4x fewer bytes (and tokens) than RGBA at
               near-identical legibility. The verify-after-place loop
               reads one image per write, so this multiplies how much of
               a drive fits in context. Pass format="png" only when you
               need full-fidelity colour.
      style:   standard|coordinate_multicolor|coordinate_audit|coordinate_pair.
               Use coordinate_multicolor for hard coordinate reads: every
               tier's grid lines cycle through distinct colours and the
               coordinate labels are colour-matched to their lines, making
               it easier to trace a point back to its x/y labels.
      background_opacity:
               Optional source drawing opacity in (0,1]. Use about 0.5 for
               labeling/placement and 0.2 for QA when you want labels and
               grid to dominate. If omitted, enhanced views keep the legacy
               contrast-preserving fade.

    Per H4 (followups-2 tracker): when `region` is given, the output
    keeps 1:1 native resolution up to `max_dim`. A 400×400 crop comes
    back as 400×400, NOT scaled up. Small rotated dim text stays
    readable. Full-image renders (no region) still cap at `max_dim`.

    dpi (PLACEMENT zoom): with a `region`, re-renders that region from the PDF
    VECTOR at this DPI and draws the grid on the higher-res crop — higher-than-
    native legibility PLUS the coordinate grid in one view, so you can place a
    wall/dim on faint ink and read its exact SOURCE coordinates off the grid.
    This is the combination `zoom_read_scene_region` (zoom, no grid) and the
    native grid view (grid, no zoom) each lack. Crop large, then dpi-zoom each
    feature to place it. Bounded: region ≤2000 src px, output ≤4000, dpi ≤ the
    extractor max. Omit for cheap orientation.

    Returns: one ImageContent (PNG) and one TextContent with the image
    metadata (source dimensions, region applied, tier step sizes,
    image_bytes so you can see the payload cost). Grid labels show
    SOURCE pixels — use them directly in `upsert_label` against the
    un-cropped scene.
    """
    started = time.time()
    params: dict[str, Any] = {
        "tiers": tiers, "max_dim": max_dim, "format": format,
        "style": style, "target_line": target_line,
    }
    if view_mode:
        params["view_mode"] = view_mode
    if region:
        params["region"] = region
    if enhance:
        params["enhance"] = enhance
    if target:
        params["target"] = target
    if background_opacity is not None:
        params["background_opacity"] = background_opacity
    if dpi is not None:
        params["dpi"] = dpi
    try:
        status, content, ctype = await mcp_server._api_get_bytes(f"/datasets/{key}/{file}/grid", params=params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _wrap_text(_api_unreachable_error(started))
        status, content, ctype = await mcp_server._api_get_bytes(f"/datasets/{key}/{file}/grid", params=params)
    if status >= 400:
        try:
            err_body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            err_body = {}
        return _wrap_text(_http_status_to_error(status, err_body, started))
    # Also fetch meta so the agent gets dimensions + cache key context.
    # NOTE: `extraction_kind` here is the dataset manifest's `kind`
    # (floorplan/elevation/section/detail) — a SEPARATE vocabulary
    # from the workflow's `scene_tag` (grundriss/ansicht/schnitt/
    # sonstiges/nicht_klassifiziert). Field renamed in G6 to stop
    # tripping the agent (and me) into thinking they're the same.
    meta_status, meta_body = await mcp_server._api_get(f"/datasets/{key}")
    scene_meta = {}
    if meta_status == 200:
        for d in (meta_body or {}).get("drawings") or []:
            if d.get("file") == file:
                scene_meta = {
                    "file": file,
                    "extraction_kind": d.get("kind"),
                    "view": d.get("view"),
                    "floor": d.get("floor"),
                    "labeled": d.get("labeled"),
                    "label_count": d.get("label_count"),
                }
                break
    return _image_delivery_payload(
        content=content,
        ctype=ctype,
        metadata={
            "image_format": "PNG",
            "scene_meta": scene_meta,
            "region": region,
            "tiers": tiers.split(","),
            "max_dim": max_dim,
            "enhance": enhance or "none",
            "format": format,
            "style": style,
            "target": target,
            "target_line": target_line,
            "background_opacity": background_opacity,
            "view_mode": view_mode,
            "active_layers": {
                "source": True,
                "grid": True,
                "labels": False,
                "relations": False,
            },
        },
        started_at=started,
        status_code=status,
        image_delivery=image_delivery,
    )


@mcp.tool()
async def read_scene_region(
    key: str,
    file: str,
    region: str,
    enhance: str | None = "threshold",
    image_delivery: str = "auto",
) -> list[ImageContent | TextContent]:
    """READ-TIER view: a tight crop at full native resolution, lossless, with
    NO grid mesh — the legibility-optimal recipe in one verb.

    USE when you are about to RECORD A VALUE or ANCHOR GEOMETRY: reading a
    dimension number, a height mark, confirming a wall edge sits on the ink.
    `get_scene_view` is the cheap SURVEY tier (downscaled, png8, grid on) — fine
    for locating features, but NOT for reading values: a value read off a
    downscaled/grid-occluded overview is low-fidelity and will not count toward
    gold (the fidelity gate flags it).

    Bounded by design (fidelity x area ~ constant): the region's long edge must
    be <= 2000 source px so the read stays at 1:1 and cheap. Crop tighter, or
    use `zoom_read_scene_region` for higher-than-native detail.

    Args:
      region:  REQUIRED 'x0,y0,x1,y1' source-pixel rect — the one feature you
               are reading. Coordinates returned stay in the source frame.
      enhance: contrast lift for faint pencil — none|auto|clahe|threshold.
               Default 'threshold' (strongest) since reads target the faintest
               marks; drop to 'auto'/'none' if threshold over-binarizes.

    Returns lossless PNG at native resolution + metadata. Read your value off
    THIS image, then record it with an evidence pointer (region/dpi) and let the
    crop fall out of context — pixels are transient, the recorded fact is truth.
    """
    started = time.time()
    parsed = _parse_region_edges(region)
    if parsed is None:
        return _wrap_text(_err(
            "schema_invalid",
            "region must be 'x0,y0,x1,y1' (the single feature to read)",
            started_at=started, status_code=400,
        ))
    long_edge = parsed
    READ_MAX_EDGE = 2000
    if long_edge > READ_MAX_EDGE:
        return _wrap_text(_err(
            "region_too_large_for_read",
            f"region long edge {long_edge}px > {READ_MAX_EDGE}px. read stays at "
            "1:1 native to stay legible+cheap — crop tighter onto the one feature, "
            "or use zoom_read_scene_region for higher-than-native detail.",
            started_at=started, status_code=400,
        ))
    params: dict[str, Any] = {
        "region": region, "format": "png", "grid": "none",
        "max_dim": 8000, "background_opacity": 1.0,
    }
    if enhance:
        params["enhance"] = enhance
    try:
        status, content, ctype = await mcp_server._api_get_bytes(f"/datasets/{key}/{file}/grid", params=params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _wrap_text(_api_unreachable_error(started))
        status, content, ctype = await mcp_server._api_get_bytes(f"/datasets/{key}/{file}/grid", params=params)
    if status >= 400:
        try:
            err_body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            err_body = {}
        return _wrap_text(_http_status_to_error(status, err_body, started))
    warning = _note_repeat(f"read|{file}|{region}|{enhance}")
    return _image_delivery_payload(
        content=content, ctype=ctype,
        metadata={
            "image_format": "PNG", "tier": "read", "lossless": True,
            "region": region, "enhance": enhance or "none",
            "evidence_pointer": {
                "scene": file, "region_bbox": region, "dpi": "native",
                "enhance": enhance or "none", "grid": "none", "fidelity": "read",
            },
            "legibility_warning": warning,
        },
        started_at=started, status_code=status, image_delivery=image_delivery,
    )


@mcp.tool()
async def zoom_read_scene_region(
    key: str,
    file: str,
    region: str,
    dpi: int = 1000,
    enhance: str | None = "threshold",
    image_delivery: str = "auto",
) -> list[ImageContent | TextContent]:
    """ZOOM-READ tier: re-render a SMALL region from the PDF VECTOR source at
    higher-than-native DPI. The escape hatch when `read_scene_region` (native)
    is still marginal.

    A scene raster is fixed at its extraction DPI — upscaling it adds no detail.
    This rasterizes the underlying PDF for just your region at `dpi` (<=1200),
    so faint dimension text / a thin wall edge resolves cleanly. NO OCR — you
    still read the pixels; this only gives you more of them.

    Bounded: input region long edge <= 1500 source px and the output is capped,
    so a higher dpi forces a tighter crop (fidelity x area stays constant).

    Args:
      region:  REQUIRED 'x0,y0,x1,y1' source-pixel rect (one dim number / edge).
      dpi:     render DPI, default 1000, max 1200.
      enhance: none|auto|clahe|threshold (default 'threshold').

    Returns lossless PNG. Record the read value with an evidence pointer
    (region + this dpi) so it is reproducible and counts as high fidelity.
    """
    started = time.time()
    if _parse_region_edges(region) is None:
        return _wrap_text(_err(
            "schema_invalid", "region must be 'x0,y0,x1,y1'",
            started_at=started, status_code=400,
        ))
    params: dict[str, Any] = {"region": region, "dpi": dpi}
    if enhance:
        params["enhance"] = enhance
    try:
        status, content, ctype = await mcp_server._api_get_bytes(f"/datasets/{key}/{file}/zoom", params=params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _wrap_text(_api_unreachable_error(started))
        status, content, ctype = await mcp_server._api_get_bytes(f"/datasets/{key}/{file}/zoom", params=params)
    if status >= 400:
        try:
            err_body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            err_body = {}
        return _wrap_text(_http_status_to_error(status, err_body, started))
    warning = _note_repeat(f"zoom|{file}|{region}|{dpi}|{enhance}")
    return _image_delivery_payload(
        content=content, ctype=ctype,
        metadata={
            "image_format": "PNG", "tier": "zoom_read", "lossless": True,
            "region": region, "dpi": dpi, "enhance": enhance or "none",
            "evidence_pointer": {
                "scene": file, "region_bbox": region, "dpi": dpi,
                "enhance": enhance or "none", "grid": "none", "fidelity": "zoom_read",
            },
            "legibility_warning": warning,
        },
        started_at=started, status_code=status, image_delivery=image_delivery,
    )


@mcp.tool()
async def get_scene_view_with_labels(
    key: str,
    file: str,
    region: str | None = None,
    tiers: str = "broad",
    max_dim: int = 900,
    enhance: str | None = None,
    format: str = "png8",
    style: str = "qa",
    target: str | None = None,
    target_line: str = "none",
    background_opacity: float | None = None,
    clean: bool = True,
    contrast: str = "high",
    show_relations: str = "required",
    show_height_guides: str = "auto",
    show_openings: str = "full",
    include_hidden: bool = False,
    view_mode: str | None = None,
    image_delivery: str = "auto",
) -> list[ImageContent | TextContent]:
    """Scene image + grid overlay + EVERY LABEL CURRENTLY SAVED rendered
    on top. This is the agent's verify view — call it after every
    geometry-bearing label write to confirm the label landed on the
    intended feature.

    USE when:
      - You need global or multi-label QA after several edits, topology
        review, or a wider relation check.
      - You're suspicious of an earlier label and want to spot-check
        without opening the SPA in a browser.

    DON'T USE when:
      - You haven't placed any labels yet — use `get_scene_view` for
        a clean image.
      - You just wrote or updated ONE label — prefer
        `verify_label_placement`, which auto-crops tightly around that
        label and keeps context smaller.

    Args:
      key:     house key.
      file:    scene filename.
      region:  optional 'x0,y0,x1,y1' (source-pixel coords) — zoom
               around the just-placed label for the closest look.
      tiers:   comma list of {broad, finer, detail}. Default 'broad'.
               Use denser tiers only for coordinate-reading views, not QA.
      max_dim: cap on the longer side of the output PNG; default 1600.
               Per H4, small region crops keep 1:1 native resolution.
      enhance: contrast lift for faint scans (issue #2):
               none|auto|clahe|threshold (default none). Coordinates stay
               SOURCE-pixel; labels still render at their saved positions.
      format:  png|png8 (issue #3). Default png8 — the cheaper palette
               PNG. Use it for the verify-after-place loop to keep each
               read affordable; pass format="png" for full-fidelity RGBA.
      style:   qa|ink_compare|semantic|standard|coordinate_multicolor|
               coordinate_audit|coordinate_pair.
               qa/ink_compare render labels lightly so source ink remains
               visible; semantic renders full wall/opening bodies.
               coordinate_multicolor is the preferred coordinate-audit
               style when verifying exact placement because line colours
               repeat as landmarks and labels match their line colour.
      background_opacity:
               Optional source drawing opacity in (0,1]. Use about 0.5 for
               normal labeling and 0.2 for visual QA so saved labels stand
               out strongly against faint source ink.
      clean:   When true, render semantic labels without the coordinate grid.
               Defaults true because verification/QA must distinguish source
               ink from saved labels without grid noise.
      contrast:
               normal|high. High contrast keeps the same semantics but makes
               labels/chips stronger for agent QA.
      show_relations:
               required|all|none. Required shows correctness-critical links
               such as opening→wall and dimension number→distance.
      show_height_guides:
               auto|always|never. Auto shows datum guide lines in Agent View /
               clean QA contexts and keeps normal editor views quieter.
      show_openings:
               full|outline|hide. Use outline/hide when opening quads obscure
               wall ink during detail QA.
      include_hidden:
               When false, respect display.hidden_label_ids like the UI.

    Returns: one ImageContent (PNG) + one TextContent envelope.

    Render vocabulary:
      wall body band + axis    — wall; qa/ink_compare use light bands
      opening body + internals — opening; qa/ink_compare use cut outlines
      polyline/region          — component_line
      datum marker + line      — height_mark (Bezug is visually distinct)
      dimension + caps + value — dimensioned_distance
      text chip / bbox         — dimension_number
      warning chips/rings      — uncertain/missing/not_readable

    Per the context-bloat policy, use this full labeled view deliberately
    for global QA. For routine verify-after-write, call
    `verify_label_placement` first. If the rendered geometry doesn't land
    on the intended feature, `update_label_attrs` or `delete_label` +
    re-place. Budget 3 attempts per label; flag `status: uncertain` on
    the closest if it still misses.
    """
    started = time.time()
    effective_background_opacity = background_opacity
    if clean and effective_background_opacity is None:
        effective_background_opacity = 0.2
    params: dict[str, Any] = {
        "tiers": tiers, "max_dim": max_dim, "format": format,
        "style": style, "target_line": target_line,
        "clean": clean, "contrast": contrast,
        "show_relations": show_relations,
        "show_height_guides": show_height_guides,
        "show_openings": show_openings,
        "include_hidden": include_hidden,
    }
    if view_mode:
        params["view_mode"] = view_mode
    if region:
        params["region"] = region
    if enhance:
        params["enhance"] = enhance
    if target:
        params["target"] = target
    if effective_background_opacity is not None:
        params["background_opacity"] = effective_background_opacity
    try:
        status, content, ctype = await mcp_server._api_get_bytes(
            f"/datasets/{key}/{file}/grid-with-labels", params=params,
        )
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _wrap_text(_api_unreachable_error(started))
        status, content, ctype = await mcp_server._api_get_bytes(
            f"/datasets/{key}/{file}/grid-with-labels", params=params,
        )
    if status >= 400:
        try:
            err_body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            err_body = {}
        return _wrap_text(_http_status_to_error(status, err_body, started))
    # Also fetch the scene's labels list so the agent has a textual
    # accompaniment to the image (label_id ↔ what's drawn).
    lbl_status, lbl_body = await mcp_server._api_get(f"/labels/dataset/{key}/{file}")
    label_summaries: list[dict] = []
    if lbl_status == 200 and isinstance(lbl_body, dict):
        for lab in (lbl_body.get("labels") or []):
            attrs = lab.get("attributes") or {}
            label_summaries.append({
                "id": lab.get("id"),
                "type": lab.get("type"),
                "status": lab.get("status"),
                "is_reference": attrs.get("is_reference") if lab.get("type") == "dimensioned_distance" else None,
                "value_mm": attrs.get("value_mm"),
                "summary": _label_summary(lab),
            })
    return _image_delivery_payload(
        content=content,
        ctype=ctype,
        metadata={
            "image_format": "PNG",
            "format": format,
            "labels_in_view": label_summaries,
            "region": region,
            "tiers": tiers.split(","),
            "style": style,
            "target": target,
            "target_line": target_line,
            "background_opacity": effective_background_opacity,
            "clean": clean,
            "contrast": contrast,
            "show_relations": show_relations,
            "show_height_guides": show_height_guides,
            "show_openings": show_openings,
            "include_hidden": include_hidden,
            "view_mode": view_mode,
            "active_layers": {
                "source": True,
                "grid": not clean,
                "labels": True,
                "relations": show_relations != "none",
                "height_guides": show_height_guides,
                "openings": show_openings,
            },
            "render_contract_version": "labeling-render-contract/2026-05-31",
            "hint": (
                "Verify the rendered geometry lands on the intended feature. "
                "If a label is off, update_label_attrs (preferred for small "
                "shifts) or delete_label + re-place. Budget 3 attempts per "
                "label, then set status='uncertain' on the closest miss."
            ),
        },
        started_at=started,
        status_code=status,
        image_delivery=image_delivery,
    )


@mcp.tool()
async def verify_label_placement(
    key: str,
    file: str,
    label_id: str,
    pad_px: int = 80,
    tiers: str = "finer,detail",
    max_dim: int = 1600,
    enhance: str | None = None,
    format: str = "png8",
    snap_radius_px: int = 18,
    background_opacity: float | None = None,
    contrast: str = "high",
    show_relations: str = "required",
    show_height_guides: str = "auto",
    show_openings: str = "full",
    include_hidden: bool = False,
    view_mode: str | None = "edit_verify_view",
    image_delivery: str = "auto",
) -> list[ImageContent | TextContent]:
    """H5-7 — sugar over `get_scene_view_with_labels`: auto-crop around
    a single label so the agent doesn't have to compute the region.

    Reads the label's geometry, computes a tight bbox around all its
    points + `pad_px` margin, clamps to image bounds, and returns the
    verify view of that crop. Useful as the single tool call right
    after `upsert_label` / `add_reference_dim` / `update_label_attrs`.

    USE when:
      - You just placed or updated a label and want one tool call to
        confirm the placement. Pair with the 3-attempt verify budget
        from operating principle #9.

    DON'T USE when:
      - You're verifying multiple labels at once — call
        `get_scene_view_with_labels` directly with a wider region.

    Args:
      key, file: scene identifier.
      label_id:  the label to zoom into.
      pad_px:    margin around the label's bbox (source pixels).
      tiers:     grid tiers to draw; defaults to 'finer,detail' for
                 the closest-possible look.
      max_dim:   max output dim; default 900 keeps normal verification
                 crops compact while preserving local detail. Per H4 small
                 crops stay 1:1.
      enhance:   contrast lift for faint scans (issue #2):
                 none|auto|clahe|threshold (default none).
      format:    png|png8 (issue #3). Default png8 — the cheaper palette
                 PNG; ideal for the verify-after-place loop.
      background_opacity:
                 Optional source drawing opacity in (0,1]. For placement
                 verification use 0.2 to make saved geometry dominate while
                 retaining enough source ink to spot misses.
      contrast:  normal|high. Defaults high for QA.
      show_relations:
                 required|all|none relation cues. Defaults required.
      show_height_guides:
                 auto|always|never datum guide lines for height marks.
      show_openings:
                 full|outline|hide opening rendering for this verification crop.
      include_hidden:
                 Include labels hidden in the UI display preferences.
      snap_radius_px: search radius for the numeric offset check (issue
                 #10). The envelope reports `offset_px` — the vector from
                 the label's anchor to the nearest drawn feature — so you
                 correct by a precise delta instead of eyeballing.

    Returns: image + envelope with the same shape as
    `get_scene_view_with_labels`, PLUS (issue #10) `offset_px`,
    `nearest_feature_px`, `nearest_feature_distance_px`, and an
    `offset_hint`. The envelope's `labels_in_view` will typically contain
    just this one label (plus any neighbours in the padded crop).
    """
    started = time.time()
    # Look up the label to read its geometry.
    label_resp = await mcp_server.get_label(key=key, file=file, label_id=label_id)
    if not label_resp.get("ok"):
        return _wrap_text(label_resp)
    lab = label_resp["data"]
    geom = lab.get("geometry") or {}
    pts: list[tuple[float, float]] = []
    for k in ("start", "end", "anchor"):
        v = geom.get(k)
        if isinstance(v, list) and len(v) >= 2:
            pts.append((float(v[0]), float(v[1])))
    for k in ("points", "polygon", "quad", "top_edge", "bottom_edge"):
        seq = geom.get(k)
        if isinstance(seq, list):
            for p in seq:
                if isinstance(p, list) and len(p) >= 2:
                    pts.append((float(p[0]), float(p[1])))
    if "circle" in geom:
        c = geom["circle"]
        center = c.get("center") or [0, 0]
        r = float(c.get("radius_px") or 0)
        pts.append((center[0] - r, center[1] - r))
        pts.append((center[0] + r, center[1] + r))
    if not pts:
        return _wrap_text(_err(
            "label_has_no_geometry",
            f"label {label_id!r} carries no positional geometry — nothing to verify",
            started_at=started,
        ))
    # Clamp the crop to image bounds.
    meta = await get_scene_meta(key=key, file=file)
    if not meta.get("ok"):
        return _wrap_text(meta)
    img_w, img_h = meta["data"].get("image_size_px") or [None, None]
    if img_w is None:
        return _wrap_text(_err(
            "scene_missing_image_size",
            "scene_meta has no image_size_px — cannot clamp crop",
            started_at=started,
        ))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0 = max(0, int(min(xs)) - pad_px)
    y0 = max(0, int(min(ys)) - pad_px)
    x1 = min(int(img_w), int(max(xs)) + pad_px)
    y1 = min(int(img_h), int(max(ys)) + pad_px)
    # Ensure non-degenerate region — pad if the label is a point.
    if x1 - x0 < 20:
        x1 = min(int(img_w), x0 + 20)
    if y1 - y0 < 20:
        y1 = min(int(img_h), y0 + 20)
    region = f"{x0},{y0},{x1},{y1}"
    view = await get_scene_view_with_labels(
        key=key, file=file, region=region, tiers=tiers, max_dim=max_dim,
        enhance=enhance,
        format=format,
        style="ink_compare",
        background_opacity=background_opacity,
        clean=True,
        contrast=contrast,
        show_relations=show_relations,
        show_height_guides=show_height_guides,
        show_openings=show_openings,
        include_hidden=include_hidden,
        view_mode=view_mode,
        image_delivery=image_delivery,
    )

    # Issue #10: numeric offset feedback. How far is the label's anchor
    # from the nearest drawn feature? The agent can then correct by a
    # precise delta instead of eyeballing the visual crop.
    if isinstance(geom.get("anchor"), list) and len(geom["anchor"]) >= 2:
        anchor = [float(geom["anchor"][0]), float(geom["anchor"][1])]
    elif isinstance(geom.get("start"), list) and len(geom["start"]) >= 2:
        anchor = [float(geom["start"][0]), float(geom["start"][1])]
    else:
        anchor = [sum(xs) / len(xs), sum(ys) / len(ys)]
    try:
        rp_status, rp_body = await mcp_server._api_get(
            f"/datasets/{key}/{file}/resolve-point",
            params={
                "point": f"{anchor[0]},{anchor[1]}",
                "frame": "source",
                "snap": "true",
                "snap_radius_px": snap_radius_px,
            },
        )
    except (httpx.HTTPError, httpx.RequestError):
        rp_status, rp_body = 0, None
    if (
        rp_status == 200 and isinstance(rp_body, dict)
        and view and isinstance(view[-1], TextContent)
    ):
        try:
            env = json.loads(view[-1].text)
            data = env.get("data") or {}
            data["anchor_checked"] = anchor
            if rp_body.get("snapped"):
                data["offset_px"] = rp_body.get("offset_px")
                data["nearest_feature_px"] = rp_body.get("feature_point")
                data["nearest_feature_distance_px"] = rp_body.get("distance_px")
                data["offset_hint"] = (
                    "offset_px is the vector FROM the label's anchor TO the "
                    "nearest drawn feature. To center the anchor on that "
                    "feature, shift it by offset_px (update_label_attrs for a "
                    f"small move). Searched within {snap_radius_px}px."
                )
            else:
                data["offset_px"] = None
                data["offset_hint"] = (
                    f"No drawn feature within {snap_radius_px}px of the anchor — "
                    "the anchor may already be clear of ink, or widen "
                    "snap_radius_px and re-check."
                )
            env["data"] = data
            view[-1].text = json.dumps(env, indent=2)
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    return view


@mcp.tool()
async def resolve_scene_point(
    key: str,
    file: str,
    point: list[float],
    region: str | None = None,
    max_dim: int = 1600,
    frame: str = "source",
    snap: bool = True,
    snap_radius_px: int = 14,
) -> dict:
    """Issue #10 — turn a roughly-placed point into a precise SOURCE-pixel
    coordinate, so you DON'T have to read absolute coords off a dense grid.

    Two inversions of the hard part:
      1. Local-crop coordinates. If you called `get_scene_view(region=...)`
         and want to point at something in that crop, pass frame="crop"
         with `point` in the CROP's local pixel frame (0..w, 0..h) and the
         same `region`/`max_dim` you used. The server maps it back to
         source pixels — short tracing distance in a small crop, low error.
      2. Snap-to-feature. With snap=true (default) the mapped point is
         snapped to the nearest drawn feature (Höhenkote tick-triangle,
         line, dim arrow) within `snap_radius_px`. Place approximately;
         the server lands you on the real mark.

    USE when:
      - About to `upsert_label` / `add_reference_dim`: resolve each
        endpoint/anchor here first, then pass the returned `source_point`.
      - You read a feature in a zoom crop and want its source coordinate
        without interpolating across gridlines.

    Args:
      key, file:      scene identifier.
      point:          [x, y]. Source pixels when frame='source'; the crop's
                      local pixel frame when frame='crop'.
      region:         'x0,y0,x1,y1' source-pixel crop (required for
                      frame='crop') — the same rect you passed to
                      get_scene_view.
      max_dim:        the same max_dim you used for the crop (so a
                      downscaled crop maps back correctly).
      frame:          'source' | 'crop'.
      snap:           snap the mapped point to the nearest feature.
      snap_radius_px: snap search radius (source pixels). Use a small
                      radius near dense content so it doesn't grab a
                      neighbour.

    Returns: `data` = {source_point:[x,y], mapped_point:[x,y],
      snapped:bool, offset_px:[dx,dy], distance_px, feature_point, frame}.
      Feed `source_point` straight into the write tools.
    """
    started = time.time()
    if frame not in ("source", "crop"):
        return _err("bad_frame", "frame must be 'source' or 'crop'", started_at=started)
    if not (isinstance(point, list) and len(point) == 2):
        return _err("bad_point", "point must be [x, y]", started_at=started)
    params: dict[str, Any] = {
        "point": f"{point[0]},{point[1]}",
        "max_dim": max_dim,
        "frame": frame,
        "snap": "true" if snap else "false",
        "snap_radius_px": snap_radius_px,
    }
    if region:
        params["region"] = region
    try:
        status, body = await mcp_server._api_get(f"/datasets/{key}/{file}/resolve-point", params=params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get(f"/datasets/{key}/{file}/resolve-point", params=params)
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def get_pdf_page_view(
    key: str,
    page: int,
    dpi: int = 144,
    region: str | None = None,
    tiers: str = "broad,finer,detail",
    max_dim: int = 1600,
    image_delivery: str = "auto",
) -> list[ImageContent | TextContent]:
    """PDF page render with grid overlay — used for scene identification.

    USE when:
      - Identifying scenes at W0 / extract-time: render each page,
        emit bboxes, call `extract_scenes`.
      - Debugging a misextracted scene by viewing the source PDF page.

    Args:
      key:     house key.
      page:    1-indexed page number in the consolidated PDF.
      dpi:     render DPI; default 144. The `extract_scenes` tool needs
               to know the DPI the agent saw to convert bbox pixels →
               PDF units. PASS THIS SAME DPI THROUGH.
      region:  optional 'x0,y0,x1,y1' to zoom (pixel coords at `dpi`).
      tiers:   comma list of {broad, finer, detail}.
      max_dim: cap on longer side; default 1600.

    Returns image + metadata text. The text envelope includes the
    rendered DPI so the agent can store it for the matching
    `extract_scenes` call.
    """
    started = time.time()
    params: dict[str, Any] = {"dpi": dpi, "tiers": tiers, "max_dim": max_dim}
    if region:
        params["region"] = region
    try:
        status, content, ctype = await mcp_server._api_get_bytes(f"/pdfs/{key}/page/{page}/grid", params=params)
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _wrap_text(_api_unreachable_error(started))
        status, content, ctype = await mcp_server._api_get_bytes(f"/pdfs/{key}/page/{page}/grid", params=params)
    if status >= 400:
        try:
            err_body = json.loads(content) if content else {}
        except json.JSONDecodeError:
            err_body = {}
        return _wrap_text(_http_status_to_error(status, err_body, started))
    pdf_status, pdf_body = await mcp_server._api_get(f"/pdfs/{key}/info")
    page_meta = {}
    if pdf_status == 200:
        for p in (pdf_body or {}).get("pages") or []:
            if p.get("page") == page:
                page_meta = p
                break
    return _image_delivery_payload(
        content=content,
        ctype=ctype,
        metadata={
            "image_format": "PNG",
            "page": page,
            "dpi": dpi,
            "page_pdf_size": page_meta,
            "region": region,
            "tiers": tiers.split(","),
            "hint": "If you emit a bbox from this view, remember to pass the same dpi to extract_scenes so pixel→PDF conversion is correct.",
        },
        started_at=started,
        status_code=status,
        image_delivery=image_delivery,
    )


@mcp.tool()
async def cleanup_image_handles(max_age_seconds: int = 86_400) -> dict:
    """Garbage-collect rendered image handles by age.

    USE when:
      - A long labeling run used `image_delivery="handle"` and you want to
        remove old handle files from `tmp/mcp-image-handles`.

    DON'T USE when:
      - A current worker may still need recently returned handle paths.
        Increase `max_age_seconds` or wait until the run handoff is written.
    """
    started = time.time()
    cutoff = time.time() - max(0, int(max_age_seconds))
    removed = []
    kept = 0
    for path in mcp_server.IMAGE_HANDLE_DIR.glob("*"):
        if not path.is_file():
            continue
        if path.stat().st_mtime < cutoff:
            size = path.stat().st_size
            path.unlink()
            removed.append({"path": str(path), "bytes": size})
        else:
            kept += 1
    return _ok({
        "directory": str(mcp_server.IMAGE_HANDLE_DIR),
        "max_age_seconds": max_age_seconds,
        "removed_count": len(removed),
        "removed_bytes": sum(item["bytes"] for item in removed),
        "kept_count": kept,
        "removed": removed[:20],
        "truncated": len(removed) > 20,
    }, started_at=started)


@mcp.tool()
async def get_scene_meta(key: str, file: str) -> dict:
    """Compact metadata for one scene.

    USE when:
      - Checking the current scene_tag / view / floor / labeled status
        without pulling the whole house manifest.

    Returns: `data` = {file, scene_tag, view, floor, title, image_size_px,
                       labeled, label_count, calibration_status}
    """
    started = time.time()
    try:
        status, ds = await mcp_server._api_get(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, ds = await mcp_server._api_get(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, ds, started)
    target = next((d for d in (ds.get("drawings") or []) if d.get("file") == file), None)
    if target is None:
        return _err("scene_not_found", f"no scene {file!r} in {key!r}", started_at=started)
    # Labels JSON carries the workflow-time scene_tag + orientation + level +
    # image_size_px. The manifest carries the extraction-time `kind` (a
    # separate vocabulary: floorplan/elevation/section/detail).
    lbl_status, lbl = await mcp_server._api_get(f"/labels/dataset/{key}/{file}")
    if lbl_status == 200 and isinstance(lbl, dict):
        scene_tag = lbl.get("scene_tag")
        scene_orientation = lbl.get("scene_orientation")
        scene_level = lbl.get("scene_level")
        image_size = lbl.get("image_size_px")
    else:
        scene_tag = scene_orientation = scene_level = image_size = None
    facts_status, facts = await mcp_server._api_get(f"/datasets/{key}/house_facts")
    calibration = (facts.get("calibration_per_scene") or {}).get(file) if facts_status == 200 else None
    return _ok({
        "file": file,
        "scene_tag": scene_tag,                # workflow discriminator
        "extraction_kind": target.get("kind"), # extraction-time category
        "view": target.get("view"),
        "floor": target.get("floor"),
        "scene_orientation": scene_orientation,
        "scene_level": scene_level,
        "title": target.get("title"),
        "image_size_px": image_size,
        "labeled": bool(target.get("labeled")),
        "label_count": target.get("label_count", 0),
        "calibration_status": "calibrated" if calibration else "not_calibrated",
    }, started_at=started, status_code=status)


@mcp.tool()
async def list_scene_labels(key: str, file: str, max_labels: int = 100) -> dict:
    """Compact list of labels on one scene — id, type, status, summary.

    USE when:
      - You want to see what's already on a scene without the full
        geometry payload. Cheap; ≤ 200 bytes per label.

    DON'T USE when:
      - You need the actual coordinates — use `get_label`.

    Returns: `data.labels` = [{id, type, status, summary}] plus
      labels_total / labels_truncated. Pass a larger max_labels only when
      you really need the whole compact list.
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_get(f"/labels/dataset/{key}/{file}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get(f"/labels/dataset/{key}/{file}")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    labels = body.get("labels") or []
    max_labels = max(0, int(max_labels))
    summaries = [compact_label(lab) for lab in labels[:max_labels]]
    return _ok({
        "scene_tag": body.get("scene_tag"),
        "scene_orientation": body.get("scene_orientation"),
        "scene_level": body.get("scene_level"),
        "image_size_px": body.get("image_size_px"),
        "label_counts": label_counts(labels),
        "labels_total": len(labels),
        "labels_truncated": len(labels) > max_labels,
        "labels": summaries,
    }, started_at=started, status_code=status)


@mcp.tool()
async def get_scene_context_summary(
    key: str,
    file: str,
    include_label_summaries: bool = True,
    include_plan_status: bool = True,
    max_labels: int = 20,
    max_blockers: int = 3,
) -> dict:
    """Compact routing summary for one scene.

    USE for normal scene routing before deciding whether you need full
    labels, full plan state, or fresh pixels. This intentionally omits
    geometry arrays and full Markdown by default.
    """
    started = time.time()
    ds_status, ds = await mcp_server._api_get(f"/datasets/{key}")
    if ds_status >= 400:
        return _http_status_to_error(ds_status, ds, started)
    drawing = next((d for d in (ds.get("drawings") or []) if d.get("file") == file), None)
    if drawing is None:
        return _err("scene_not_found", f"no scene {file!r} in {key!r}", started_at=started)
    lbl_status, lbl = await mcp_server._api_get(f"/labels/dataset/{key}/{file}")
    if lbl_status >= 400:
        return _http_status_to_error(lbl_status, lbl, started)
    labels = lbl.get("labels") or []
    meta = {
        "scene_tag": lbl.get("scene_tag"),
        "scene_level": lbl.get("scene_level"),
        "scene_orientation": lbl.get("scene_orientation"),
        "label_count": len(labels),
        "label_types": sorted(label_counts(labels)),
    }
    plan = None
    if include_plan_status:
        plan_status, plan_body = await mcp_server._api_get(f"/datasets/{key}/{file}/plan-state/status")
        if plan_status == 200:
            plan = compact_plan_status(plan_body, max_blockers=max_blockers)
        elif plan_status == 404:
            plan = compact_plan_status(None, max_blockers=max_blockers)
        else:
            return _http_status_to_error(plan_status, plan_body, started)
    max_labels = max(0, int(max_labels))
    data = {
        "summary_contract": "mcp-context-bloat/scene-context-summary-v1",
        "scene": compact_scene_row(drawing, meta),
        "image_size_px": lbl.get("image_size_px"),
        "label_counts": label_counts(labels),
        "labels_total": len(labels),
        "labels_truncated": include_label_summaries and len(labels) > max_labels,
        "plan": plan,
    }
    if include_label_summaries:
        data["labels"] = [compact_label(lab) for lab in labels[:max_labels]]
    return _ok(data, started_at=started, status_code=ds_status)


@mcp.tool()
async def get_house_context_summary(
    key: str,
    include_plan_status: bool = False,
    max_blockers_per_scene: int = 3,
) -> dict:
    """Compact house dashboard for routing.

    Prefer this over fetching the full house, every labels file, house
    facts, and every plan state when deciding the next scene/phase.
    """
    started = time.time()
    status, ds = await mcp_server._api_get(f"/datasets/{key}")
    if status >= 400:
        return _http_status_to_error(status, ds, started)
    facts, scene_meta = await _load_facts_and_scene_meta(key, ds or {})
    workflow = _derive_workflow_state(ds or {}, facts, scene_meta)
    scenes = []
    total_labels = 0
    for drawing in ds.get("drawings") or []:
        file_name = drawing.get("file")
        if not file_name:
            continue
        meta = dict(scene_meta.get(file_name) or {})
        total_labels += int(meta.get("label_count") or 0)
        row = compact_scene_row(drawing, meta)
        row["label_counts"] = meta.get("label_counts") or {}
        if include_plan_status:
            plan_status, plan_body = await mcp_server._api_get(f"/datasets/{key}/{file_name}/plan-state/status")
            row["plan"] = (
                compact_plan_status(plan_body, max_blockers=max_blockers_per_scene)
                if plan_status == 200
                else compact_plan_status(None, max_blockers=max_blockers_per_scene)
            )
        scenes.append(row)
    return _ok({
        "summary_contract": "mcp-context-bloat/house-context-summary-v1",
        "key": key,
        "scene_count": len(scenes),
        "total_labels": total_labels,
        "workflow": _compact_workflow_for_summary(workflow, max_blockers=max_blockers_per_scene),
        "quality": aggregate_house_quality(scenes) if include_plan_status else None,
        "scenes": scenes,
    }, started_at=started, status_code=status)
