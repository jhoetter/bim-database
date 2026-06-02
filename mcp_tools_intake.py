"""Intake MCP tools (extracted from mcp_server.py, H5).

Stable helpers imported by value; HTTP helpers, __file__, and inter-tool
calls go through `mcp_server.` so the test-harness patches still apply.
"""
from __future__ import annotations

import httpx
import time

import mcp_server
from mcp_server import (
    _api_unreachable_error,
    _err,
    _http_status_to_error,
    _ok,
    _wait_for_api,
    mcp,
)


@mcp.tool()
async def list_pdfs() -> dict:
    """Every incoming PDF bundle (data/pdfs/incoming/<key>/).

    USE when:
      - About to call extract_scenes — need to know which house has a
        consolidated PDF ready.

    Returns: `data.pdfs` = [{key, consolidated_pdf, source_filenames,
                             page_count, state, user_notes}]
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_get("/pdfs/incoming")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get("/pdfs/incoming")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok({"pdfs": body or []}, started_at=started, status_code=status)


@mcp.tool()
async def get_pdf_info(key: str) -> dict:
    """Page count + per-page width_pt/height_pt for the consolidated PDF.

    USE when:
      - You're about to render PDF pages for scene identification — the
        page count tells you how many `get_pdf_page_view` calls to make.
      - Sanity-checking a `bbox_pixels` is within the page.

    Args:
      key: house key.

    Returns: `data` = {key, page_count, pages: [{page, width_pt, height_pt}]}
    """
    started = time.time()
    try:
        status, body = await mcp_server._api_get(f"/pdfs/{key}/info")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_get(f"/pdfs/{key}/info")
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status)


@mcp.tool()
async def extract_scenes(
    key: str,
    items: list[dict],
    idempotency_key: str | None = None,
) -> dict:
    """Crop one or more scenes out of the consolidated PDF.

    USE when:
      - The agent has identified scene bboxes from `get_pdf_page_view`
        renders (W0/extract phase).
      - Re-extracting after adjusting a bbox (idempotent on (page, slug);
        re-extract overwrites the scene image and updates the manifest entry but
        preserves any existing labels.json).

    DON'T USE when:
      - The bundle has no consolidated PDF — `extract_scenes` returns
        409. Check via `get_pdf_info` first.

    Args:
      key: house key.
      items: list of crop specs. Each item:
        {
          "page": 1,                 // 1-indexed page in the PDF
          "bbox_pixels": [x0,y0,x1,y1],  // pixel coords AT THE DPI YOU SAW
          "dpi": 144,                 // view DPI used ONLY for bbox_pixels -> PDF units
          "bbox_pdf_units": [x0,y0,x1,y1], // alternative: PDF coords, no conversion
          "crop_dpi": 600,            // output raster DPI; default 600
          "format": "png",            // png|jpg; default png for lossless agent work
          "kind": "floorplan",        // floorplan|elevation|section|detail
          "view": "north",            // optional — for elevations/sections
          "floor": "eg",              // optional — for floorplans
          "title": "EG-Grundriss",    // optional human title
          "slug_override": null,      // optional slug
          "allow_blank": false,       // optional; bypass the blank-render guard
          "no_clip_expand": false,    // optional; bypass clip-detection bbox
                                      //   auto-expansion (issue #25)
          "bbox_is_authoritative": false // optional (V1.1); YOUR chosen bbox
                                      //   is final — never auto-expand it
        }
      idempotency_key: optional driver-supplied key for crash-replay safety.

    Issue #12: if a crop renders blank (a failed rasterization — e.g. a
    corrupt content stream in the merged PDF), extraction returns an error
    instead of writing an empty scene that would still report as
    `labeled`. Fix the merge / bbox, or pass `allow_blank: true` to force.

    Issue #25: the segmentation bbox can under-shoot a tall drawing
    (cutting the roof apex so the ridge/Firsthöhe is never captured). The
    API auto-expands the bbox toward any border the drawing's ink touches
    and re-crops until the drawing no longer hits an edge. To re-capture a
    clipped scene, re-extract it (idempotent on slug) — pass a wider bbox or
    just let the auto-expansion grow it. Set `no_clip_expand: true` to off.

    V1.1: when YOU chose the bbox deliberately (the vision-LLM-picks-the-
    extent flow — building + all dim chains + Nordpfeil + datum), pass
    `bbox_is_authoritative: true` so the auto-expansion never overrides your
    chosen crop. The recorded crop_from bbox then equals your input exactly.

    Returns: `data` = {extracted: [...new manifest entries...], intake_state: ...}

    Pixel→PDF conversion is handled here: the API takes bbox_pdf_units,
    so this tool multiplies bbox_pixels by (72 / dpi) before posting. Do not
    pass dpi=600 merely to request a 600 dpi crop when bbox_pixels came from a
    144 dpi page view; pass crop_dpi=600 instead. If bbox_pdf_units are used,
    dpi is accepted as a backwards-compatible output-DPI alias when crop_dpi is
    omitted.
    """
    started = time.time()
    if not items:
        return _err("schema_invalid", "items must be a non-empty list",
                    hint="pass at least one crop spec", started_at=started)
    api_items: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            return _err("schema_invalid", f"items must be objects, got {type(raw).__name__}",
                        started_at=started)
        bbox_px = raw.get("bbox_pixels") or raw.get("bbox_pdf_units")
        if not (isinstance(bbox_px, (list, tuple)) and len(bbox_px) == 4):
            return _err("bbox_zero_area", "bbox_pixels must be [x0,y0,x1,y1]",
                        started_at=started)
        has_bbox_pixels = "bbox_pixels" in raw
        view_dpi = int(raw.get("dpi", 144))
        if view_dpi <= 0:
            return _err("schema_invalid", "dpi must be > 0", started_at=started)
        crop_dpi_raw = raw.get("crop_dpi", raw.get("output_dpi"))
        crop_dpi = int(crop_dpi_raw) if crop_dpi_raw is not None else (
            600 if has_bbox_pixels else int(raw.get("dpi", 600))
        )
        if crop_dpi <= 0 or crop_dpi > 1200:
            return _err("schema_invalid", "crop_dpi must be in 1..1200", started_at=started)
        fmt = str(raw.get("format", "png")).strip().lower()
        if fmt == "jpeg":
            fmt = "jpg"
        if fmt not in {"png", "jpg"}:
            return _err("schema_invalid", "format must be 'png' or 'jpg'", started_at=started)
        x0, y0, x1, y1 = (float(v) for v in bbox_px)
        if not (x1 > x0 and y1 > y0):
            return _err("bbox_zero_area", f"bbox has non-positive area: {bbox_px}",
                        started_at=started)
        factor = 72.0 / view_dpi if has_bbox_pixels else 1.0
        api_items.append({
            "page": int(raw.get("page", 0)),
            "bbox_pdf_units": [x0 * factor, y0 * factor, x1 * factor, y1 * factor],
            "kind": raw.get("kind", "detail"),
            "view": raw.get("view"),
            "floor": raw.get("floor"),
            "title": raw.get("title"),
            "slug_override": raw.get("slug_override"),
            "dpi": crop_dpi,
            "format": fmt,
            "allow_blank": bool(raw.get("allow_blank", False)),
            "no_clip_expand": bool(raw.get("no_clip_expand", False)),
            # V1.1: when YOU (the vision-LLM) have chosen the crop extent
            # deliberately — building + all dim chains + Nordpfeil + datum —
            # set this so the #25 auto-expansion never overrides it.
            "bbox_is_authoritative": bool(raw.get("bbox_is_authoritative", False)),
        })
    try:
        status, body = await mcp_server._api_post(f"/pdfs/{key}/extract", json_body={"items": api_items})
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        status, body = await mcp_server._api_post(f"/pdfs/{key}/extract", json_body={"items": api_items})
    if status >= 400:
        return _http_status_to_error(status, body, started)
    return _ok(body, started_at=started, status_code=status,
               next_tool={
                   "name": "get_workflow_state",
                   "args": {"key": key},
                   "reason": "see what W0 needs next now that scenes exist",
               })


@mcp.tool()
async def split_scene(
    key: str,
    file: str,
    regions: list[dict],
    retire_parent: bool = True,
) -> dict:
    """Split an over-broad scene (a full-page lump holding several
    drawings) into one scene PER drawing (issue #11).

    A scene must be ONE drawing — lumping multiple drawings into a single
    scene makes scene_tag meaningless, breaks best-source routing, and
    makes calibration impossible (multiple coordinate frames in one image).

    Flow (region detection is YOUR job — the vision-LLM is the detector):
      1. View the lump with `mcp_server.get_scene_view(key, file)`.
      2. Identify each constituent drawing's bbox in the scene's own pixel
         frame (the SOURCE pixels the grid labels show).
      3. Call this tool with one region per drawing. Each region is
         re-cropped from the parent PDF page as a standalone scene, and the
         parent lump is retired (recycle-bin; restorable) unless
         retire_parent=false.

    USE when:
      - A just-extracted scene visibly contains 2+ distinct drawings
        (e.g. "4 facades on one sheet", or "EG+DG+Schnitt combined").
        Split BEFORE tagging — never tag a multi-drawing lump.

    DON'T USE when:
      - The scene is a single drawing — nothing to split.
      - The parent scene wasn't cropped from a PDF (no crop_from).

    Args:
      key:  house key.
      file: the over-broad parent scene file to split.
      regions: list of child specs, each:
        {
          "bbox_pixels": [x0,y0,x1,y1],  // in the PARENT scene's source px
          "kind": "elevation",           // floorplan|elevation|section|detail
          "view": "north",               // optional
          "floor": "eg",                 // optional
          "title": "Nordansicht",        // optional
          "slug_override": null           // optional
        }
      retire_parent: recycle the parent lump after the children are
        created (default true; restorable via the SPA / undo).

    Returns: `data` = {created: [...child manifest entries...],
      retired: <parent file or null>, parent_dims_px: [w,h]}.
    Blank child regions are rejected by the extract guard (issue #12), in
    which case nothing is retired.
    """
    started = time.time()
    from api.segment import scene_px_dims, scene_px_to_pdf, validate_region_px

    if not regions:
        return _err("schema_invalid", "regions must be a non-empty list",
                    hint="pass one region per constituent drawing", started_at=started)

    # Look up the parent scene's crop provenance.
    try:
        ds_status, ds = await mcp_server._api_get(f"/datasets/{key}")
    except (httpx.HTTPError, httpx.RequestError):
        if not await _wait_for_api():
            return _api_unreachable_error(started)
        ds_status, ds = await mcp_server._api_get(f"/datasets/{key}")
    if ds_status >= 400:
        return _http_status_to_error(ds_status, ds, started)
    parent = next((d for d in (ds or {}).get("drawings") or [] if d.get("file") == file), None)
    if parent is None:
        return _err("not_found", f"scene {file!r} not in dataset manifest", started_at=started)
    crop = parent.get("crop_from") or {}
    bbox = crop.get("bbox_pdf_units")
    page = crop.get("page")
    pdf_dpi = int(crop.get("dpi") or 0)
    if not (isinstance(bbox, list) and len(bbox) == 4 and page and pdf_dpi > 0):
        return _err(
            "not_splittable",
            f"scene {file!r} has no PDF crop_from (page/bbox/dpi) — cannot split",
            hint="split only applies to scenes extracted from a PDF page",
            started_at=started,
        )
    parent_dims = scene_px_dims(bbox, pdf_dpi)

    # Build one extract item per region, mapping parent-scene px -> PDF units.
    items: list[dict] = []
    for i, reg in enumerate(regions):
        if not isinstance(reg, dict):
            return _err("schema_invalid", f"regions[{i}] must be an object", started_at=started)
        err = validate_region_px(reg.get("bbox_pixels"), parent_dims)
        if err:
            return _err("bad_region", f"regions[{i}]: {err}", started_at=started)
        pdf_box = scene_px_to_pdf(reg["bbox_pixels"], bbox, pdf_dpi)
        items.append({
            "page": int(page),
            "bbox_pdf_units": pdf_box,
            "kind": reg.get("kind", "detail"),
            "view": reg.get("view"),
            "floor": reg.get("floor"),
            "title": reg.get("title"),
            "slug_override": reg.get("slug_override"),
            "dpi": pdf_dpi,
            "allow_blank": bool(reg.get("allow_blank", False)),
        })

    ex_status, ex_body = await mcp_server._api_post(f"/pdfs/{key}/extract", json_body={"items": items})
    if ex_status >= 400:
        # e.g. a child region rendered blank (issue #12 guard) — leave the
        # parent intact so nothing is lost.
        return _http_status_to_error(ex_status, ex_body, started)
    created = (ex_body or {}).get("extracted") or []

    retired = None
    if retire_parent:
        del_status, del_body = await mcp_server._api_delete(f"/pdfs/{key}/extract/{file}")
        if del_status >= 400:
            # Children exist; surface the retire failure but don't fail hard.
            return _ok(
                {"created": created, "retired": None, "parent_dims_px": list(parent_dims),
                 "warning": f"children created but parent not retired (status {del_status})"},
                started_at=started, status_code=ex_status,
            )
        retired = file

    return _ok(
        {"created": created, "retired": retired, "parent_dims_px": list(parent_dims)},
        started_at=started, status_code=ex_status,
    )


