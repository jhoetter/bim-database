# Grid coordinate frame

Every image returned by `get_scene_view` or `get_pdf_page_view` carries
a three-tier grid overlay. The coordinate labels in the margins ALWAYS
reference SOURCE pixels — never the rendered output pixels, never any
internal cache scale. You can feed any label-frame coordinate you read
off the grid directly into a tool call like `upsert_label`.

Tiers (from bold to faint):

| Tier   | Cell size                            | Use for                                 |
|--------|--------------------------------------|-----------------------------------------|
| broad  | image_long_edge / 10 (~200–500 px)   | scoping which quadrant a feature is in  |
| finer  | image_long_edge / 50 (~40–100 px)    | naming a polygon vertex ±25 px          |
| detail | image_long_edge / 200 (~10–25 px)    | snap-style precision; no labels (noise) |

To zoom into a region, call `get_scene_view(file=..., region="x0,y0,x1,y1")`.
The labels in the zoom still read in source-pixel coords — so a vertex
you identify in a zoom at (1240, 670) maps to (1240, 670) in the
un-cropped scene without any translation.

Don't trace coordinates across the dense grid if you can avoid it
(issue #10): vision-LLMs are strong at "that feature, there" and weak at
"row 1797, col 232". Instead:

  - Point in the crop's LOCAL frame. Call
    `resolve_scene_point(point=[lx,ly], region=..., frame="crop")` with the
    point in the zoom's own pixel frame (0..w, 0..h). The server maps it
    back to source pixels for you.
  - Snap to the real mark. `resolve_scene_point(..., snap=true)` snaps the
    point to the nearest drawn feature (tick-triangle, line, dim arrow)
    within `snap_radius_px`. Place approximately; the server lands you on
    the feature. Feed the returned `source_point` into upsert_label /
    add_reference_dim.
  - Correct by a delta. After a write, `verify_label_placement` reports
    `offset_px` — the vector from your anchor to the nearest feature — so
    you nudge by an exact amount instead of eyeballing.
