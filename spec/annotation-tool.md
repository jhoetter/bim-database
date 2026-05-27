# Annotation tool for technical architectural drawings

**Status:** decision document — implementation not yet started
**Owner:** jhoetter
**Date:** 2026-05-27
**Location:** integrates into the existing bim-database stack (`ui/`, `api/`), surfaces under the existing `/synthetic/` section.

---

## 1. Mission

Build a web-based annotation tool that labels technical architectural drawings (initially synthetic, later real). The labels feed **two downstream vision models** trained from a single annotation layer:

1. **Reference-extraction model** — runs on the raw scanned image, locates the dimensional strokes whose endpoints + known mm values let us compute a planar homography that rectifies the page.
2. **Geometry model** — runs on the homography-rectified image, detects walls, openings, elevation lines, etc. in geometrically clean coordinates.

The annotator labels once. The pipeline derives **two** ground-truth tensors:
- `(raw_image, raw_labels)` → trains model 1
- `(rectified_image, rectified_labels)` → trains model 2 (labels mapped through the homography computed from model 1's outputs at runtime — at training time, from the user's labeled reference strokes).

The homography is **a derived computation, never an input preprocessing step.** That means the annotator does not work on an undistorted image; they work on the original scan and the rectification is computed from what they label.

---

## 2. Two-stage training pipeline (diagram)

```
                                          ┌─────────────────────────────┐
                                          │   User annotates ORIGINAL   │
                                          │   image (one .png per       │
                                          │   scene under /synthetic/). │
                                          │   Labels in pixel coords.   │
                                          └──────────────┬──────────────┘
                                                         │
                                  ┌──────────────────────┴──────────────────────┐
                                  │                                             │
                                  ▼                                             ▼
                  ┌─────────────────────────────┐               ┌────────────────────────────────┐
                  │  GROUND TRUTH SET A         │               │  GROUND TRUTH SET B            │
                  │  (raw + reference labels)   │               │  (rectified + geometry labels) │
                  │                             │               │                                │
                  │  Image:   raw .png           │               │  Image: rectify(raw, H)        │
                  │  Labels:  dimensioned        │               │  Labels: apply(H) to walls,    │
                  │           strokes only —     │               │          openings, lines,      │
                  │           the things model 1 │               │          height marks, …       │
                  │           must detect.       │               │                                │
                  └──────────────┬──────────────┘                └────────────────┬───────────────┘
                                 │                                                │
                                 ▼                                                ▼
                       trains Model 1 (refs)                            trains Model 2 (geometry)

H = homography(orthogonal-marked dimensioned strokes ∩ {status: readable})
```

Key consequence: every label except `dimensioned_distance` is irrelevant to Model 1's training. Every label is potentially relevant to Model 2's training (transformed through H).

---

## 3. Storage layout + integration with /synthetic/

`/synthetic/` already exists in the UI as a browse-and-detail section over `data/synthetic/<key>/*.png`. The annotation tool lives **inside** this section, not as a separate top-level area.

```
data/synthetic/house-1/
  manifest.json                              # existing
  house-1-syn-elevation-north.png            # existing scene
  house-1-syn-elevation-south.png
  house-1-syn-floorplan-eg.png
  …
  house-1-composite.png                      # NEW (M0)
  composite.json                             # NEW (M0) — per-scene bbox on the sheet
  labels/                                    # NEW (M1)
    house-1-syn-elevation-north.json         # one label set per scene
    house-1-syn-elevation-south.json
    house-1-syn-floorplan-eg.json
    …
```

- Labels are committed (small JSON, valuable hand-work).
- The composite PNG follows the same convention as scene PNGs (tracked in git).
- The label format is documented + schema-validated (see §6).

API additions (per §11: real + synthetic both labelable through one shared API):

| route | method | returns |
|---|---|---|
| `GET /labels/{scope}/{key}/{file}` | get the label set for one scene; scope ∈ {synthetic, house} |
| `PUT /labels/{scope}/{key}/{file}` | save a label set (overwrites; backend writes JSON to disk) |
| `GET /synthetics/{key}/composite` | composite PNG + scene-bbox metadata |
| `POST /labels/{scope}/{key}/{file}/export` | returns both compiled ground-truth sets (raw + rectified) as JSON |

For real houses (`scope=house`), labels land under
`data/houses/<key>/labels/<scene>.json`; for synthetic, under
`data/synthetic/<key>/labels/<scene>.json`. The image URL family
(`/static/synthetic/...` vs `/scene/...`) stays whatever the scope dictates;
the labels layer is the same.

---

## 4. Core principles

### 4.1 Honesty
Every label carries an explicit `status` and `source`. Allowed status values:
- `readable` — annotator could read the value/feature directly from the image
- `not_readable` — the feature is present but its value is unreadable (faded, occluded, smudge)
- `missing` — the feature is absent from the image (explicitly marked as not-there, distinguishable from "not yet annotated")
- `uncertain` — feature visible but interpretation is ambiguous; the annotator made a judgment call

`source` is a free-text breadcrumb: which other label / area of the image / external knowledge the annotator used to determine the value.

### 4.2 Pixel coordinates everywhere
All geometry stored as `[x, y]` in pixel coordinates of the **original** image. The homography transforms pixels → rectified pixels. Boxes are stored as 4 corner points (not `[x, y, w, h]`) so they transform correctly under H.

### 4.3 Homography is derived, never an input preprocessing step
The annotator never works on a rectified image. The rectified ground-truth is computed at export time from the original labels + the computed homography.

### 4.4 Tag → tool gating
Each scene is tagged exactly once (`grundriss | ansicht | schnitt | sonstiges | nicht_klassifiziert`). The available labeling tools depend on the tag. A `schnitt`-tagged scene does not offer floorplan-only tools (walls, floorplan openings). This prevents accidental schema mixing.

### 4.5 Pause-and-verify
After each milestone, the build pauses and the user reviews the deliverable. The tracker (this document) enumerates milestones; the implementer never advances without an explicit "go" on the prior milestone.

---

## 5. Label types

### 5.1 Universal envelope

Every label, regardless of type, has:

```jsonc
{
  "id":          "uuid-v4",
  "type":        "wall | floorplan_opening | view_opening | component_line | height_mark | dimensioned_distance | dimension_number",
  "geometry":    "<see per-type below>",
  "attributes":  "<see per-type below>",
  "status":      "readable | not_readable | missing | uncertain",
  "source":      "<short free-text breadcrumb, optional>",
  "relations":   [{"other_id": "uuid", "kind": "labels | belongs_to | references"}],
  "notes":       "<free text, optional>",
  "created_at":  "ISO 8601",
  "updated_at":  "ISO 8601"
}
```

### 5.2 Per-type table

| type | applies to scene tags | geometry | core attributes |
|---|---|---|---|
| `wall` | grundriss | `start: [x,y]`, `end: [x,y]` | `thickness_mm: number?` |
| `floorplan_opening` | grundriss | 4 corner points (oriented rectangle) | `opening_kind: door|window|passage`, `width_mm: number?`, `swing: in|out|sliding|none`, `swing_side: left|right|none` |
| `view_opening` | ansicht, schnitt | `top_edge: [[x,y], ...]` polyline, `bottom_edge: [[x,y], ...]` polyline | `opening_kind: door|window|skylight|dormer|…`, `frame_visible: bool` |
| `component_line` | ansicht, schnitt | polyline `[[x,y], ...]` | `line_kind: first|traufe|gelaende|geschoss|ok_ffb|sockel|firstkante|…` |
| `height_mark` | ansicht, schnitt | `anchor: [x,y]` | `value_mm: number?`, `reference_line_id: uuid?` (a `component_line`) |
| `dimensioned_distance` | grundriss, ansicht, schnitt | `start: [x,y]`, `end: [x,y]` | `value_mm: number?`, `target_orientation: horizontal | vertical | angle_deg:<num> | unknown`, `is_reference: bool` (does this anchor the homography?) |
| `dimension_number` | all | `anchor: [x,y]` OR `bbox: [4 corners]` | `text: string`, `parsed_value_mm: number?` |

### 5.3 Critical relation: `dimension_number` ↔ `dimensioned_distance`

The user flagged this as "der wackligste Teil." Design:

- A `dimensioned_distance` always carries its own `value_mm`. That's the structured truth.
- A `dimension_number` is the visible text on the drawing (e.g. `1,75`) — separately labeled because the OCR/detection model needs to learn the text↔location pairing too.
- The link is an explicit `relation` of kind `labels`: the `dimension_number` has `relations: [{"other_id": "<distance_id>", "kind": "labels"}]`.
- UI affordance: select a `dimensioned_distance`, then click on a `dimension_number` to link them (or vice versa). Visual: when one is selected, linked counterpart highlights. Multiple `dimension_number`s can refer to one distance (e.g. an overall + an inner). A `dimensioned_distance` may have zero linked numbers (status `not_readable`).
- Export rule: if `parsed_value_mm` of the linked number doesn't match the distance's `value_mm`, the export flags a `consistency_warning`. We never silently reconcile.

### 5.4 What is NOT a label type (deferred)

These are not in scope for the initial tool, listed here to keep the schema honest about its boundary:
- Room / Raum polygons (we infer rooms from walls later, not from a region label)
- Material hatches (treated as visual style, not a labeled feature)
- Furniture symbols (not architecturally meaningful for this model)
- Title block contents (handled by a separate OCR pass, not the geometry annotator)
- Stair runs (postponed to a future schema rev — they require a separate geometry primitive)

---

## 6. JSON schema (draft for one scene)

```jsonc
{
  "schema_version": "1.0",
  "scene_key": "house-1",
  "scene_file": "house-1-syn-floorplan-eg.png",
  "scene_tag": "grundriss",                    // see §4.4
  "image_size_px": [1024, 1024],
  "annotated_by": "jhoetter",
  "annotated_at": "2026-05-27T16:42:00Z",
  "labels": [
    {
      "id": "0b34…",
      "type": "wall",
      "geometry": {"start": [120, 200], "end": [120, 800]},
      "attributes": {"thickness_mm": 365},
      "status": "readable",
      "relations": [],
      "created_at": "…", "updated_at": "…"
    },
    {
      "id": "9c12…",
      "type": "dimensioned_distance",
      "geometry": {"start": [100, 900], "end": [820, 900]},
      "attributes": {
        "value_mm": 9900,
        "target_orientation": "horizontal",
        "is_reference": true
      },
      "status": "readable",
      "relations": [],
      "created_at": "…", "updated_at": "…"
    },
    {
      "id": "a78d…",
      "type": "dimension_number",
      "geometry": {"anchor": [460, 920]},
      "attributes": {"text": "9,90", "parsed_value_mm": 9900},
      "status": "readable",
      "relations": [{"other_id": "9c12…", "kind": "labels"}],
      "created_at": "…", "updated_at": "…"
    }
    // … more labels …
  ],
  "homography": {
    "matrix": [[h00, h01, h02], [h10, h11, h12], [h20, h21, h22]],
    "computed_from": ["9c12…", "<other-distance-id>", "…"],
    "rectified_size_px": [3000, 2000],
    "rms_residual_px": 4.2,
    "status": "ok | insufficient_references | degenerate"
  },
  "anomalies": [
    "dimension_number a78d… parsed 9,90 vs linked distance 9c12… value 9.900 — consistent within tolerance"
  ]
}
```

A JSON Schema (Draft 2020-12) at `schema/scene_labels.schema.json` validates this. `make validate` will check label files alongside house records.

---

## 7. Homography computation

### 7.1 Requirements

For a planar perspective homography we need **4 point correspondences** (image_pt ↔ world_pt) or equivalent constraints.

The annotator does not give direct correspondences; they give **dimensioned strokes with a target orientation and a real-world length**. Each such stroke gives us:
- 2 image points (`start`, `end`)
- A constraint on the world geometry: the line is horizontal / vertical / at angle θ, with length L mm

### 7.2 Practical minimum

The minimum useful labeling for rectification:
- **2 orthogonal reference strokes** (one horizontal, one vertical), both with `is_reference: true` and `value_mm` set
- This pins down translation + scale + axis-aligned rotation, but the homography is under-determined for perspective foreshortening
- Result: affine-only rectification, still useful for orthographic-style scans

For full perspective rectification:
- **2 horizontal + 2 vertical reference strokes**, ideally near the 4 corners of the sheet
- This gives 4 directional constraints + 2 scale constraints → enough for an 8-DOF homography
- The implementation uses `cv2.findHomography` after converting strokes to point correspondences via a virtual canonical rectangle (e.g., the building footprint's bounding box).

### 7.3 UI feedback

The compilation preview (M6) always shows the homography state:
- `< 2 reference strokes`: "Noch keine Entzerrung möglich — markiere mindestens 1 horizontale + 1 vertikale Bezugsstrecke."
- `2-3 reference strokes`: "Affine Entzerrung berechnet (RMS-Residuum: X px). Für volle Perspektivkorrektur 4 Bezüge nahe der Blattecken markieren."
- `≥ 4 reference strokes`: "Vollständige Homographie berechnet (RMS-Residuum: X px)."

We never auto-fill or guess if references are insufficient. The right side of the side-by-side preview shows a placeholder with the hint above instead of a fake rectification.

### 7.4 RMS residual

After computing H, project each reference stroke's endpoints through H and measure how far they are from their target orientations/lengths. Display as an RMS pixel error. A value > 20 px on a 1500x1000 image is a smell — surface as a warning chip.

### 7.5 Multiple reference sets

If the annotator marks 5+ reference strokes, the homography is overdetermined. We compute the best least-squares H and report residuals per stroke; outlier strokes (large per-stroke residual) get a UI flag so the annotator can review whether their orientation was mislabeled or their value was wrong.

---

## 8. UI workflow

### 8.1 Entry — house list (already exists)
`/synthetic/` shows all houses with their drawings. Each tile shows a coverage bar: how many of this house's scenes have labels (none / partial / complete).

### 8.2 House detail (already exists)
`/synthetic/:key` shows all scenes for one house. Each scene tile shows its current `scene_tag` (color-coded chip: blue=Grundriss, amber=Ansicht, violet=Schnitt, zinc=Sonstiges, gray=untagged) and its label count.

### 8.3 Scene editor — NEW
`/synthetic/:key/scene/:file/annotate` opens the annotation editor. Layout:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ ← back to house    Scene: house-1-syn-floorplan-eg.png    [save] [export]  │
├──────────┬─────────────────────────────────────┬───────────────────────────┤
│  Tools   │                                      │  Right rail:              │
│  (scene- │                                      │   Selected label panel   │
│  tag     │           CANVAS                     │     (type-specific       │
│  gated)  │       (the scene image,              │      attribute editor,   │
│          │        labels overlaid)              │      status, source,     │
│  Tag:    │                                      │      relations, notes)   │
│  [grund- │                                      │                          │
│   riss]  │                                      │   Live angle readout      │
│          │                                      │   (when drawing a         │
│  Wand    │                                      │    dimensioned_distance)  │
│  Öffnung │                                      │                          │
│  Bemaß…  │                                      │   Homography status       │
│  Maßzahl │                                      │   chip ("not enough refs",│
│          │                                      │    "affine ok", "full ok")│
│          │                                      │                          │
│  ────    │                                      │                          │
│  Label-  │                                      │                          │
│  Liste   │                                      │                          │
│  (selec- │                                      │                          │
│   table) │                                      │                          │
└──────────┴─────────────────────────────────────┴───────────────────────────┘
```

### 8.4 Step-by-step UX

1. User opens scene → sees image, no labels yet.
2. User clicks a tag (Grundriss / Ansicht / Schnitt / Sonstiges). Tag is saved immediately. Toolbox updates to show only relevant tools.
3. User picks a tool (e.g. "Bemaßte Strecke"). Cursor becomes a crosshair.
4. User clicks-drags to draw the geometry. While dragging:
   - Live angle readout shows the current pixel-angle in the right rail.
   - If the user chose a `target_orientation` from a sub-menu before drawing, the readout also shows deviation from target.
5. On release: a modal/popover appears for the type's required attributes (e.g., value in mm, target orientation if not pre-chosen, is_reference?). Status defaults to `readable`. After confirm, label is saved.
6. User can click an existing label to select. Right rail shows full edit panel. Geometry-level edits via drag handles on the canvas.
7. To relate a dimension_number to a dimensioned_distance: select either, click "🔗 Link" tool, click the counterpart. Done. The link appears as a thin dashed line on canvas between them.
8. To delete: select + Del key, or trashcan in the right-rail panel.
9. "Save" persists to disk via the PUT endpoint. "Export" opens the compilation preview (M6).

### 8.5 Keyboard shortcuts (proposed)
- `W` — Wand tool
- `O` — Öffnung (cycles between floorplan / view based on tag)
- `D` — Dimensioned distance
- `N` — Dimension number
- `L` — Link tool
- `Esc` — cancel current drawing / deselect
- `Del` — delete selected
- `Cmd/Ctrl+S` — save
- `[` / `]` — toggle sidebars (consistent with the existing Shell)
- `+` / `-` — zoom
- `Space + drag` — pan

### 8.6 Compilation preview (M6)

Modal or dedicated route `/synthetic/:key/scene/:file/preview`. Two-pane view:

| pane | shows |
|---|---|
| **Left — Original + reference labels** | The raw scene with only `dimensioned_distance` labels overlaid. This is the Model-1 ground truth. |
| **Right — Rectified + geometry labels** | The rectified image (homography applied) with walls, openings, lines, height marks, dimension numbers overlaid. This is the Model-2 ground truth. If homography state is insufficient, this pane shows the "not enough references" hint instead. |

Both panes are zoom/pan-synced (zoom in on the left → corresponding region zooms on the right). Below the panes: a JSON preview of the exported labels.

A "Download both ground truths" button packages `{raw.png, raw_labels.json, rectified.png, rectified_labels.json, homography.json}` into a zip.

---

## 9. Milestones

Each milestone is a stop-and-show point. The deliverable is concrete and reviewable. The implementer commits + pushes the milestone's work, shows the result to the user, and waits for explicit "go" before the next.

### M0 — Composite sheet ("fake whole document") ✅ shipped 2026-05-27

**Goal:** From the scenes of one house, produce one large image that looks like a scanned multi-drawing architect's sheet. This becomes the future training data for scene-detection (S-1, separately).

**Approach:**
- Read `data/synthetic/<key>/manifest.json`, get all drawings.
- Order: floorplan(s) first, then sections, then elevations, then details, then doc pages.
- Layout: a fixed sheet (e.g. 4000×2800 px, A1 landscape proportion). Place items with the floorplan center-large, elevations as a row above, details corner-tiled.
- For each placement, record bbox + scaling factor in pixel coords.
- Add light paper texture overlay (reuse the same vibe as the scene style refs — wrinkle, fold, slight tint).
- Optional: a faked Bauvorhaben / Architekt title block somewhere on the sheet.

**Output:**
- `data/synthetic/<key>/<key>-composite.png` — the sheet
- `data/synthetic/<key>/composite.json` — `{ scenes: [{file, bbox_px: [x,y,w,h], scale, rotation_deg}], sheet_size_px: [w,h], generated_at }`

**Code:** `scripts/compose_house_sheet.py KEY` — reads manifest, lays out, writes both files. Idempotent: re-running overwrites the composite. CLI flags: `--all`, `--seed N` for layout variation.

**UI surface:** the existing `/synthetic/:key` page gets a new "Composite" tab/section above the per-scene gallery. Shows the composite image with semi-transparent overlay boxes per scene (clickable → opens the scene editor for that scene).

**Stop:** show me one composite (h-1). Then proceed.

### M1 — Data model + JSON schema for labels ✅ shipped 2026-05-27

**Goal:** Lock the schema before any UI work.

**Approach:**
- Write `schema/scene_labels.schema.json` per Draft 2020-12.
- Cover all label types from §5.2, the universal envelope, the homography block, and the anomalies array.
- Add validation to `scripts/validate.py`: any `data/synthetic/<key>/labels/*.json` is validated against the schema during `make validate`.
- Write a worked-example label file for one h-1 scene (hand-crafted JSON, no UI yet) — this exercises the schema against real shapes.

**Output:**
- `schema/scene_labels.schema.json`
- `data/synthetic/house-1/labels/house-1-syn-floorplan-eg.json` — example
- `scripts/validate.py` updated
- This tracker doc updated with the finalized schema (in §6).

**Stop:** review schema. **No UI yet.** Approve schema, then proceed.

### M2 — Scene editor v0: canvas + tagging + simplest labels ✅ shipped 2026-05-27

**Goal:** Open a scene image on a canvas, tag it, draw the two simplest label types (`dimensioned_distance` + `dimension_number`), save back to disk via the new PUT endpoint.

**In scope:**
- Route `/synthetic/:key/scene/:file/annotate`
- API: `GET/PUT /synthetics/:key/labels/:file`
- Canvas component (use a small Konva or pure-DOM SVG overlay — decide during implementation)
- Tag chip switcher
- Tool: dimensioned_distance (just draw, default attributes)
- Tool: dimension_number (just place + type the text)
- Right rail showing selected-label attributes (read/write)
- Label list panel
- Pan + zoom on the canvas
- Save persists to disk

**Out of scope for M2:** scene-tag-specific gating (M3), other label types (M3+), live angle (M4), relations (M5), compilation preview (M6).

**Stop:** play with it. Then proceed.

### M3 — Scene-tag-specific label sets + remaining label types ✅ shipped 2026-05-27

**Goal:** All label types from §5.2 implemented. Tag determines which tools appear.

**In scope:**
- Wall, floorplan_opening, view_opening, component_line, height_mark
- Tool palette gates on scene_tag value
- Each type's modal/popover for required attributes
- Schema-aware: invalid combinations refused with a helpful message

**Stop:** review.

### M4 — Dimensioned distance with full attributes + live angle ✅ shipped 2026-05-27

**Goal:** The dimensioned_distance tool gets its full feature set.

**In scope:**
- `target_orientation` selector (horizontal | vertical | known angle θ | unknown)
- `is_reference` toggle
- Live angle readout while drawing (right rail or near cursor)
- Visual indicator on the canvas: reference strokes get a distinct color so the homography-anchor set is obvious at a glance

**Stop:** review.

### M5 — dimension_number ↔ dimensioned_distance relation ✅ shipped 2026-05-27

**Goal:** Linking UI works, exports correctly, consistency warnings surface.

**In scope:**
- "🔗 Link" tool
- Selecting either side shows the linked counterpart highlighted
- Consistency check: if `parsed_value_mm` ≠ linked distance's `value_mm`, surface a warning chip
- Export: relations array populated, JSON validates

**Stop:** review — this is the wackliest part.

### M6 — Compilation preview + JSON export

**Goal:** Side-by-side preview of both ground truths + downloadable export.

**In scope:**
- Compute homography from reference strokes (`cv2.findHomography` or equivalent)
- Render rectified image (warp the original through H)
- Apply H to every label's geometry to produce rectified_labels
- Show side-by-side panes (§8.6)
- Download-as-zip with both ground truths + homography metadata
- The "not enough references" state shows clearly when insufficient

**Stop:** end-to-end working tool.

### Beyond M6 (out of scope for the initial build, listed for awareness)

- Multi-user concurrent annotation (locking, conflict resolution)
- Label versioning / history
- Auto-suggest labels from a pretrained model (active-learning style)
- Batch operations (label N similar scenes at once)
- Validation rules beyond schema (e.g. "wall thicknesses must cluster around standard values")
- Export to COCO / YOLO formats
- Composite sheet detection labels (separate annotation pass for S-1)
- Confidence calibration per annotator
- Mobile / touch-screen support

---

## 10. Decisions I'm making in this tracker (worth flagging)

1. **Labels live colocated with scenes**, under `data/synthetic/<key>/labels/<scene>.json`. Not a separate top-level `annotations/` dir. Rationale: one folder per house keeps the manifest, source images, composite, and labels together; cross-references stay simple; backup/clone semantics are obvious.

2. **One scene-tag per scene, decided up front.** Tag changes are allowed but trigger a confirmation if labels exist that the new tag wouldn't support. Rationale: prevents the schema from drifting into a "this label was made under tag X but its scene is tag Y" inconsistency.

3. **Pixel coordinates only.** No mm-as-primary-storage. mm values live in `attributes`, never in geometry. Rationale: §4.2.

4. **Boxes stored as 4 corner points**, not `[x, y, w, h]`. Rationale: 4 corners transform correctly under H; an axis-aligned bbox does not.

5. **Homography computed on demand** (at export / preview time), not stored as a separate label. The cached homography in the JSON is a *derived* field that re-computes when reference strokes change. Stored only because the export needs it for reproducibility.

6. **Status defaults to `readable`.** Implicit assumption: if the annotator labels something, they could read/interpret it. `not_readable` / `missing` / `uncertain` are deliberate downgrades.

7. **`source` field is free text**, not an enum. Rationale: too many possible sources (other label, area of the image, external knowledge, gut judgment); enumerating them would constrain the annotator unnecessarily. The export can mine `source` strings into clusters later.

8. **Tag values are German** (`grundriss`, `ansicht`, `schnitt`, `sonstiges`) to match the rest of the codebase + the user's working language. Label types are English (matches the rest of `api/`).

9. **Live angle is display-only.** It doesn't affect the saved geometry. If the annotator wants to snap to 0° or 90°, they hold Shift (proposed) — the snap is a UI affordance, the saved value is still raw.

10. **Composite (M0) is a separate artifact**, not a label primitive. It feeds the future scene-detection model (S-1). The annotation tool itself never works on the composite directly.

---

## 11. Resolved decisions (jhoetter, 2026-05-27)

All open questions resolved with the recommended values:

- **Real-house scenes labelable: YES.** The same annotation tool reads both
  PDF-sourced (h21/h22/h23) and synthetic scenes. Internally that means the
  scene editor route accepts both `/synthetic/<key>/scene/<file>` and
  `/house/<key>/scene/<file>` as labeling targets. Label JSON files live in
  the corresponding folder (`data/synthetic/<key>/labels/` for synthetic,
  `data/houses/<key>/labels/` for real). The API endpoints in §3 are
  generalized: `GET/PUT /labels/<scope>/<key>/<file>` where scope is
  `synthetic | house`. Tooling treats them uniformly.

- **Single-user. No login + no per-label attribution.** The `annotated_by`
  field stays in the schema as a static string (`"jhoetter"`) so future
  multi-user is forward-compatible, but M2-M6 ship with no auth, no
  concurrent-edit handling, no locking. One user, one machine.

- **Save semantics: dirty indicator + explicit Save + N-step undo.** No
  async background-saves. The editor maintains an in-memory label set, marks
  dirty on every change, and only persists on Cmd/Ctrl+S or the Save button.
  N-step undo (proposed N=50) covers accidental clicks. Closing the page
  with unsaved changes prompts a browser warning.

- **Composite layout: deterministic by default.** Seed = `house_id * 31337`.
  The `--seed N` flag lets you resample if you want a different layout for
  the same house. Same input + same seed → byte-identical composite. The
  per-scene bboxes in `composite.json` are the canonical S-1 ground truth.

- **"Missing" is permanent metadata.** If an annotator declares an opening
  `missing` and later a colleague spots one, the original `missing` label
  is kept (with `created_at` + `updated_at`) and a new label is added.
  Hard-delete only on explicit user action; soft-conflict resolution lives
  in the `notes` field of both labels.

- **Schema versioning: `schema_version: "1.0"` with explicit migrations.**
  Breaking changes bump the major version. A `scripts/migrate_labels.py`
  script lives at the repo root; `make migrate-labels` applies pending
  migrations and writes a backup of the prior version under
  `<scene>.json.v<prev>.bak`. Schema-mismatch on load = hard error with a
  pointer to the migration command.

- **Composite ground truth: `composite.json` is the clean truth.** S-1 trains
  on the deterministic bboxes from M0's composite generation. A future
  `composite_noise` flag (post-M6) perturbs bboxes for robustness; not in
  scope for the initial build.

Final implication of these decisions: M2 reads both real + synthetic scenes
via a shared scene-fetching layer in the UI, the save semantics are simple
and explicit, and the schema is forward-compatible without overengineering.

---

## 12. Risks + known unknowns

- **Homography may not exist** for a given scene (no orthogonal reference strokes possible — e.g. a perspective sketch, or a detail without dimensions). The compilation preview must degrade gracefully (§7.3). Model 2's training set just won't include that scene's labels — that's correct, not a bug.
- **The dimension_number ↔ dimensioned_distance link is the wackliest part** (user's words). UX risk: annotators forget to link, or link the wrong number. Mitigation: a sidebar checklist showing "X dimension_numbers without a linked distance" — a soft warning, not a blocker, but visible.
- **Live angle is easy to mis-implement** as "real-world angle" when it's actually "pixel-space angle." Make sure the readout is labeled "Pixel-Winkel" or similar in the UI so annotators don't mistake it.
- **Composite layout may overlap scenes** if a house has many drawings and not enough sheet space. M0's first cut should detect overflow and either downscale items uniformly or generate a second composite page. Recommendation: downscale to fit on one page; add multi-page support later if needed.
- **AVIF / PNG mismatch.** Scene crops in the real-house path serve as AVIF via `/scene/`; synthetic scenes are PNG via `/static/synthetic/`. The annotation canvas must handle both formats. Browsers do; just make sure CORS / preload / decode paths work.
- **Schema migrations are expensive.** Once labels are committed to git, breaking schema changes require migration code. Bias the v1.0 schema toward "have all the fields you might need, optional by default" rather than minimalism.

---

## 13. Implementation checklist (for after sign-off)

- [x] M0 — `scripts/compose_house_sheet.py` + h-1 example composite + `/synthetic/:key` UI surface (shipped 2026-05-27)
- [x] M1 — `schema/scene_labels.schema.json` (incl. `schema_version: "1.0"`) + hand-crafted h-1 example label file + `scripts/validate.py` integration (shipped 2026-05-27)
- [x] M2 — annotation editor route (works for both `/synthetic/:key/scene/:file/annotate` AND `/house/:key/scene/:file/annotate` via the shared scope-aware label API) + canvas + tag chip + dimensioned_distance + dimension_number tools + dirty-indicator + explicit save + N=50 undo stack (shipped 2026-05-27)
- [x] M3 — wall, floorplan_opening, view_opening, component_line, height_mark + tag-gated tool palette (shipped 2026-05-27)
- [x] M4 — dimensioned_distance full attributes + live pixel-angle readout (display only) (shipped 2026-05-27)
- [x] M5 — link tool + consistency check + export shape (shipped 2026-05-27)
- [ ] M6 — homography compute + side-by-side preview + zip export

---

## 14. Self-audit — is this tracker exhaustive?

Cross-checking the German spec against this document:

| spec item | covered in |
|---|---|
| Web-based annotation tool, integrated stack | §1, §3 |
| `/synthetic/` is the labeling UI | §3, §8.1 |
| Two ground truths from one labeling | §1, §2, §8.6 |
| Homography is a derived computation, not preprocessing | §4.3, §7 |
| Honesty: status + provenance, "fehlt" allowed | §4.1, §5.1 |
| Workflow: house list → scene → classify → tag-specific tools | §8 |
| Compilation preview side-by-side | §8.6 |
| Homography from orthogonal dimensioned strokes | §7 |
| "< 2 brauchbare Bezugsstrecken" → hint, not guess | §7.3 |
| Preview is also QA | §8.6 |
| Three core pieces (label sets, dimensioned distance, relation) | §5.2, §5.3 |
| Wall, floorplan opening, view opening, component line, height mark | §5.2 |
| Export: clean JSON, pixel coords, boxes as 4-corner | §6, §10.4 |
| Each label: type/geometry/attributes/status/relations | §5.1 |
| Plus homography + both ground truth sets exportable | §6, §8.6 |
| M0–M6 iterative milestones | §9 |
| Stop after each milestone | §4.5, §9 |
| TODO Nr. 1: composite document | §9 M0 |
| M1 schema before UI | §9 M1 |
| M4 live angle, display only | §9 M4, §10.9 |
| M5 relation is the wackliest part | §5.3, §12 |

Items I deliberately added beyond the spec, with rationale:
- §4.4 Tag → tool gating (mentioned in spec, given dedicated principle)
- §5.4 explicit out-of-scope list (prevents scope creep arguments later)
- §7.4 RMS residual (catches bad annotations early)
- §10 numbered decisions (each is a place a reasonable person could disagree — surfaced as decisions, not buried in prose)
- §11 open questions (things only the user can answer)
- §12 risks (failure modes worth naming)
- §13 implementation checklist (the actual TODO list for after sign-off)
- §14 this self-audit

Item I am explicitly NOT covering in this v1:
- The training pipeline downstream of export (Model 1 + Model 2 training code lives elsewhere)
- The scene-detection model (S-1) and its training data — only the composite generation that *feeds* S-1
- Real-house annotation via the same UI (mentioned as an open question, deferred)
- Multi-user collaboration (open question)
- Schema versioning beyond `schema_version: "1.0"` (open question)

If you read this and notice something the German spec asked for that isn't covered, that's a tracker-completeness bug — flag it and I'll patch §6 / §8 / §9 accordingly before any code is written.
