# End-to-end readiness (R) tracker

**Status:** Revised 2026-05-29 — self-audit gaps folded in (R0 audit
of mcp/PreviewPage/Badge, R2 draft persistence + bbox editing + slug
uniqueness, R5 coordinate convention + Schnitt usage + door swing,
R6 explicit export folder layout, new §10 engineering hygiene with
testing / perf / auth defaults). R0 and R1 are implementation-ready
as the next step.
**Owner:** jhoetter
**Predecessors:**
  - [`spec/annotation-tool.md`](annotation-tool.md) — schema, M0–M6, **two-stage training pipeline §2** (preserved verbatim)
  - [`spec/annotation-ux.md`](annotation-ux.md) — M7–M13 + X1–X11
  - [`spec/annotation-visualisation.md`](annotation-visualisation.md) — V0–V3
  - [`spec/annotation-workflow.md`](annotation-workflow.md) — W0–W9

**Goal:** make `bim-database` a single coherent pipeline from **a PDF
the user drops on the page** to **a 3D mental-model of the labeled
house** they can rotate in the browser. Everything that currently
duplicates between "dataset" and "houses" mode goes away — there is
**only the dataset path**. Everything new (PDF intake, scene
extraction, export preview, 3D preview) plugs in *at the edges* of
that path so the W-tracker annotation core stays unchanged.

The pipeline in one diagram:

```
   ┌──────────────┐       ┌────────────────┐       ┌────────────────┐
   │  PDF intake  │       │   Scene        │       │   Annotation   │
   │   (R1)       │ ─→    │   extraction   │ ─→    │   (W0-W9 +     │
   │              │       │   (R2)         │       │    V0-V3 +     │
   │              │       │                │       │    M / K / N)  │
   └──────────────┘       └────────────────┘       └────────┬───────┘
                                                            │
                          ┌─────────────────────────────────┘
                          │
                          ▼
                  ┌────────────────┐       ┌────────────────┐
                  │  Export        │ ─→    │  3D preview    │
                  │  preview (R4)  │       │  (R5)          │
                  └────────────────┘       └────────────────┘
                          │                        ▲
                          ▼                        │
                  Two-stage training pipeline ─────┘
                  (annotation-tool.md §2, raw + rectified)
```

---

## 1. Scope decisions

### 1.1 Drop "houses" mode entirely

The current app has two parallel data paths:

- **"house"** — `data/houses/<key>/` with scraped catalog data, `/houses` API + UI, `HouseCard`, the `dataset_starred` button that materializes scanned plans into the dataset
- **"dataset"** — `data/dataset/<key>/` with `manifest.json` + drawings, the annotation editor, the `scope='dataset'` save path

The catalog side existed because we were pulling houses from manufacturer
websites + scoring them with `reconstructability_tier` /
`modelable_in_bim_ai`. The actual *labeling* work always happened in
dataset mode. Going forward only the labeling matters — the catalog
side becomes dead weight.

**Decision:** delete the `houses` path.

| Remove | Keep |
|---|---|
| `data/houses/*` (except 21 / 22 / 23 — preserved as PDF intake source) | `data/dataset/*` |
| `/houses`, `/houses/{key}`, `/houses/{key}/pdf`, `/houses/{key}/images`, `/houses/{key}/dataset_starred`, `/scene/{key}/{file}` API routes | `/datasets`, `/datasets/{key}`, `/labels/*` API routes |
| `ui/src/pages/HousesPage.tsx`, `ui/src/pages/HousePage.tsx` | `ui/src/pages/DatasetPage.tsx`, `ui/src/pages/AnnotatePage.tsx` |
| `ui/src/components/HouseCard.tsx` | `WorkflowPhaseBadge.tsx`, `DatasetPage`'s inline `HouseCard` |
| `/house/...` route handling in `App.tsx` + `main.tsx` | `/dataset/...` routes |
| `scope: 'house'` branch in `labels` API + UI | `scope: 'dataset'` everywhere |
| `LabelScope` type → narrowed to `'dataset'` constant | — |
| `fetchHouses`, `fetchHouse`, `fetchScene` (house-scoped) | `fetchDatasets`, `fetchDataset`, `fetchLabels`, `saveLabels` |
| `Sidebar` filters for catalog metadata (energy_standard, year_built, …) | Workflow-phase filter (new — W9.3 deferred) |
| `house_facts` keyed by `'house'` scope → unused | `house_facts` keyed only by `'dataset'` |

Anything that currently differentiates the two modes via `LabelScope`
turns into a constant. Anything `scope`-parameterized collapses to one
branch.

### 1.2 Preserve houses 21 / 22 / 23

These three become the **seed PDFs** for the new R1 intake path. We
preserve their original catalog dirs so the test corpus survives the
"houses" cleanup:

- Pre-cleanup: copy `data/houses/house-{21,22,23}/` → some preservation
  area (decision below in §11 open questions).
- Recommended: a new `data/pdfs/incoming/` directory where intake PDFs
  live before extraction. The pre-extraction copies of 21/22/23 land
  there as one PDF per house (re-built from their existing source PDFs
  using the consolidation logic from R1.3).

Why those three: they're the houses I've been hand-labeling in
recent sessions; they have heights + walls + Bezugsmaße already
captured in localStorage, so they're useful for testing R5 (3D
preview) without re-labeling from zero.

### 1.3 What the two-stage training pipeline still means

`spec/annotation-tool.md §2` is **load-bearing** for R4 and R5 and is
preserved without change:

- The user labels the **original image** (one `<scene>.jpg` per scene,
  now produced by R2 from PDF crops instead of synthetic generation).
- On export we produce **two ground-truth sets per scene**:
  - **Set A** (Model 1 training): raw image + only dimensioned
    distances (the things the model has to read first to discover the
    homography).
  - **Set B** (Model 2 training): rectified image (via H computed from
    Set A) + every label transformed through H (walls, openings, height
    marks, component lines, …).

R4 (export preview) shows both sets side-by-side. R5 (3D preview)
consumes Set B (rectified, geometry-faithful) plus the W tracker's
house-wide facts to lift the 2D annotation into a 3D mental model.

---

## 2. Phase decomposition (the six R waves)

Each wave: builds independently, type-checks, commits, pushes. Most are
heavy-ish standalone deliverables — this isn't W-tracker-sized atomic
waves. Some waves will themselves split into sub-waves during /loop.

### Phase R0 — Strip "houses" mode (destructive but pure cleanup)

**Goal:** make the app exclusively dataset-scoped.

- R0.1 Delete `ui/src/pages/HousesPage.tsx`, `HousePage.tsx`, and the
  `/houses` + `/house/...` routes from `App.tsx`. Redirect the root `/`
  to `/dataset`.
- R0.2 Inline `HouseCard.tsx`'s catalog-card use-cases — replace any
  remaining usage with `DatasetPage`'s already-inline house card.
- R0.3 Delete the corresponding FastAPI routes in `api/main.py`:
  `/houses`, `/houses/{key}`, `/houses/{key}/pdf`,
  `/houses/{key}/images`, `/houses/{key}/dataset_starred`,
  `/scene/{key}/{file}`.
- R0.4 Narrow `LabelScope = 'dataset'`. Delete every branch that
  guarded on `scope === 'house'`. Search across UI + API + scripts.
- R0.5 Move `data/houses/{21,22,23}/` → `data/pdfs/incoming/{house-21,
  house-22, house-23}/` (one folder per house, each containing the
  source PDFs we already have for them).
- R0.6 Delete every other `data/houses/<key>/` directory. Keep a
  one-shot script in `scripts/cleanup_houses_legacy.py` so the
  destructive step is auditable; the script `rm -rf`s on confirm.
- R0.7 Delete `scripts/{new_house.py, refresh_issue_state.py,
  derive_data_quality.py}` — all driven by the catalog data we just
  removed. Keep `build_houses.py` only if it has dataset-relevant
  fragments; otherwise delete.
- R0.8 Clean up the project sidebar (`SidebarFilters.tsx` — the catalog
  filters become dead weight); replace with a minimal
  Workflow-phase / search filter for dataset houses.
- R0.9 Update `README.md` / `AGENTS.md` to drop the houses path. Add a
  short pipeline diagram (same as the §0 ASCII above).
- R0.10 **Audit `mcp_server.py`.** It currently exposes catalog data
  via MCP tools (`list_houses`, `get_house`, etc.). Tools that
  query catalog fields → delete. Tools that query dataset state →
  keep + rename to drop the `house_` prefix where it's misleading.
  Add the new R1/R2/R4/R5 surfaces only after those waves ship.
- R0.11 **Audit `ui/src/pages/PreviewPage.tsx`.** Currently it
  renders catalog-side previews keyed on the catalog model.
  Decision: delete entirely; its scene-rendering helpers are
  duplicated in `AnnotatePage.tsx` and `DatasetPage.tsx` already.
  Re-add a dataset-equivalent only if R4 surfaces a use-case that
  AnnotatePage's existing preview can't cover.
- R0.12 **Audit `ui/src/components/Badge.tsx` + the `tier-N` /
  `reconstructability_tier` / `modelable_in_bim_ai` paths.** These
  are catalog-only signals. Strip every `tier-*` Badge tone, the
  `BIM-AI ✓ / ✗ / ?` corner badge on HouseCard, and the
  `constructionTone()` mapping if construction-as-fact disappears.
  Keep only badges driven by dataset state (e.g. `WorkflowPhaseBadge`).
- R0.13 **localStorage cleanup for in-flight browsers.** Existing
  users have `bim-db:annotate:*:house:<key>:*` entries that will
  point at deleted state once R0.5/6 runs. On first load of any
  post-R0 build, scan `window.localStorage` for keys matching
  `*:house:*` and remove them. Surface a one-time toast:
  *"23 alte House-Einträge entfernt — Dataset bleibt erhalten."*
  Idempotent; runs once per browser via a sentinel key
  `bim-db:houses-removed:v1`.

Verification gate: `npm run build` clean + `pytest` clean (if any
tests reference houses, delete those too) + a manual smoke that
`/dataset` lists houses and `/dataset/<key>/scene/<file>/annotate`
opens the editor unchanged. Plus: open a fresh browser profile to
confirm no `:house:` localStorage entries reappear.

### Phase R1 — PDF intake

**Goal:** the user drops one or many PDFs into the app; we organize
them into per-house bundles and queue them for scene extraction.

- R1.1 New page `/dataset/intake` (a tab on the dataset overview, or a
  new top-level page — recommend a new top-level page with a clear
  "Hochladen" CTA on `/dataset`). A drag-and-drop zone accepts PDF /
  folder / multiple PDFs.
- R1.2 New API: `POST /pdfs` — multipart upload, accepts one or more
  PDFs, optionally a target-house-key per file (from a per-file picker
  in the upload UI). Saves to `data/pdfs/incoming/<house-key>/<file>.pdf`.
- R1.3 Bulk path: a single PDF that contains *multiple* houses gets a
  per-page house-assignment UI. For each page, the user picks the
  target house key (with a "new house" option that auto-allocates the
  next free key like `house-24`). Pages assigned to the same house get
  consolidated into one PDF via a server-side merge (pypdf2 /
  pdfplumber merge). The user can preview the consolidation before
  committing.
- R1.4 New API: `GET /pdfs/incoming` — lists every per-house PDF
  bundle plus its page count + intake state ("pending extraction",
  "extracted", "annotated").
- R1.5 Per-PDF metadata sidecar: `data/pdfs/incoming/<key>/manifest.json`
  records source filename, upload timestamp, page count, intake state,
  user-provided notes (e.g. "Architekt Müller, Plan vom 2025-03-12").
- R1.6 Dataset overview page surfaces incoming PDFs as cards with a
  "→ Szenen extrahieren" button → links to R2's page.
- R1.7 De-duplication: refuse upload when a PDF with identical
  byte-hash already exists in `incoming/` (skip + toast the existing
  bundle's house-key).

Design note: we deliberately store PDFs as files on disk rather than
in a database. The dataset is small (≤ a few hundred PDFs in the
foreseeable future), and file-on-disk + manifest survives any tool the
user wants to throw at it (rsync, git-annex, S3).

### Phase R2 — Scene extraction via bbox labeling

**Goal:** for each PDF page, the user draws bounding boxes around each
"scene" (each drawing on the page — typically 1–6 per page); each bbox
becomes a `DatasetDrawing` entry in the house's `manifest.json` and a
cropped JPG / PNG appears under `data/dataset/<key>/`.

- R2.1 New page `/dataset/<key>/extract`. Loads the consolidated PDF via
  PDF.js, renders one page at a time at viewport scale; a thumbnail
  strip on the left shows all pages with extraction progress.
- R2.2 Canvas tool: click-and-drag draws a bbox. Bbox attributes set
  inline:
  - `kind`: `floorplan` | `elevation` | `section` | `detail` (the same
    enum as `DatasetDrawing.kind`).
  - `view`: `north` | `south` | `east` | `west` (for elevations).
  - `floor`: `kg` | `ug` | `eg` | `og` | `dg` | `spitzboden` (for
    floorplans).
  - `title`: optional user-typed caption.
- R2.3 New API: `POST /pdfs/<key>/extract` — takes an array of
  `{page, bbox, kind, view, floor, title}` entries. Server-side:
  - For each entry: render the PDF page at high DPI (recommend 300
    DPI), crop the bbox, save as
    `data/dataset/<key>/<key>-pdf-p<N>-<slug>.jpg` (slug derived from
    `kind` + `view` / `floor` + a sequence number).
  - Append a `DatasetDrawing` entry to `manifest.json` with
    `source: 'real'`, `imported_at`, `source_path: 'incoming/<file>.pdf'`,
    and a new field `crop_from: { page, bbox_pdf_units }` so the
    extraction can be repeated if higher resolution is needed later.
- R2.4 Idempotent re-extraction: if the user adjusts a bbox and
  re-extracts, the system detects the same `(page, slug)` already
  exists and overwrites the JPG (with a confirm toast). Existing labels
  for that scene are *preserved* — we only replace the image. The user
  can also explicitly "delete this scene" which removes both the image
  and the labels JSON.
- R2.5 Sub-page navigation: ⌨ ←/→ to page-step in the PDF; bbox tool
  primary tool; Esc cancels in-flight bbox draw.
- R2.6 Progress indicator: "12 von 18 Seiten bearbeitet, 23 Szenen
  extrahiert". Per-page badge: "○ ausstehend / ✓ erledigt / ⊘
  ohne Szenen (z. B. Titelseite)".
- R2.7 Cross-house batch: from the intake overview, a "Alle PDFs
  extrahieren" mode opens the extractor sequentially across all
  incoming PDFs — useful when there are many small bundles.
- R2.8 **Draft persistence (autosave-as-you-draw).** Every bbox the
  user places lives in a draft, NOT in a committed dataset entry,
  until they click "Extrahieren". Drafts persist to localStorage at
  `bim-db:extract-draft:<scope>:<houseKey>` after every bbox add /
  edit / delete; a debounced ~500 ms write keeps it cheap. On page
  reload the draft re-hydrates and the user picks up exactly where
  they left off. Drafts are wiped on successful extract (replaced
  by the committed manifest entries) or on explicit "Verwerfen".
  This is the single most likely workflow disruption point in the
  whole pipeline — losing 30 bboxes to a tab crash is unacceptable.
- R2.9 **Editing already-extracted bboxes.** Every committed scene
  on the R2 page renders its bbox as a colored rectangle on the
  PDF page (using the `crop_from.bbox_pdf_units` from the dataset
  manifest). Click the rectangle → handles appear (8-handle drag
  resize + drag-to-move). Adjust → click "Erneut extrahieren"
  → R2.4's idempotent re-extraction runs. The same rectangle is
  also clickable in the page thumbnail strip for fast navigation
  back to a specific scene's bbox.
- R2.10 **Rotation** (deferred to R2 v2). PDFs sometimes contain
  drawings rotated 90° (legacy plotter output, landscape pages
  embedded in portrait PDFs). R2 v1 ignores rotation — the bbox
  is always axis-aligned to the PDF page. v2 adds an explicit
  rotation handle on the bbox. Until then, the user works around
  by rotating the PDF before upload. Tracked as a known limitation
  in §11 (Risks).
- R2.11 **Cross-page batch tagging.** Shift-click multiple page
  thumbnails → "Diese Seiten als Grundriss EG markieren" applies
  the kind/floor combo to every bbox on selected pages in one go.
  Avoids retyping the same metadata 12 times for a multi-floor
  plan set.
- R2.12 **Crop quality preflight.** After bbox draw, the panel
  shows the rendered crop at preview size with a heuristic
  warning ("Auflösung sehr niedrig — < 800 px") when the bbox is
  small enough that the 300 DPI output would be under a useful
  threshold. User can override.
- R2.13 **Slug uniqueness.** When multiple bboxes on the same page
  have identical `(kind, view, floor)` — common for detail crops
  — the slug auto-appends `-2`, `-3`, … in the order the bboxes
  were drawn. Slug is locked at first extract; subsequent
  re-extractions of the same bbox use the locked slug. Stored in
  the draft so it persists across reloads.

### Phase R3 — Cross-step navigation

**Goal:** stitch R1 / R2 / W tracker into a single coherent flow with
no dead ends.

- R3.1 Top-of-app step indicator (when a house is open):
  ```
  Hochladen ─→ Szenen extrahieren ─→ Annotieren ─→ Export
  [✓]          [✓]                   [Schritt 4 / 6]   [-]
  ```
- R3.2 Per-house side rail across pages: the same `WorkflowGuide` we
  built in W tracker is visible on R1 and R2 too (just with the new
  upstream steps prepended) so the user always knows where they are.
- R3.3 "Open editor for this scene" buttons next to every extracted
  scene on the R2 page — opens AnnotatePage on that scene.
- R3.4 Resume-where-you-left-off: every step writes "last visited" so
  next time the user clicks a house from `/dataset`, they land on the
  step that's still open.
- R3.5 Breadcrumbs everywhere: `Dataset › house-21 › PDF Seite 3 ›
  Floorplan EG`.

### Phase R4 — Export preview per scene

**Goal:** before training-data export, the user can preview what a
scene's exported pair (Set A + Set B from §1.3) looks like.

- R4.1 New page `/dataset/<key>/scene/<file>/export-preview`. Two
  side-by-side panels:
  - **Set A**: raw scene image with only the `dimensioned_distance`
    labels overlaid (the model-1 input).
  - **Set B**: rectified image (apply the homography computed from the
    is_reference dims) with every other label overlaid, transformed
    through H.
- R4.2 New API: `POST /exports/<key>/<file>/preview` — server computes
  the homography from is_reference dims, returns:
  - the rectified image URL (computed on the fly, cached)
  - the transformed-coordinate label set for Set B
  - a homography object (`{matrix, rms_residual_px, status}`)
  - per-label inclusion flag for Set A vs Set B
- R4.3 Homography health badge: green if `status='ok' AND
  rms_residual_px < 4`, amber if `< 8`, red otherwise. Click → opens a
  diagnostic showing which dims contributed.
- R4.4 Set-A / Set-B JSON download (per-scene) for spot-checking that
  the exported shape matches what the model trainer expects. No bulk
  export yet — that lives in R6.
- R4.5 Coverage report per house: how many scenes have a passing
  homography. The W tracker's calibration_per_scene gives us 90 % of
  this for free.

### Phase R5 — 3D annotation preview

**Goal:** stand the labels up in 3D so the user can visually validate
the house's reconstructed geometry. Not photorealistic — minimal
colored geometry that uses every fact the labels carry.

#### 5.1 What goes into the scene

Inputs (all already in `house_facts`):

| Fact | Source | Used to construct |
|---|---|---|
| `extent.{width_mm, depth_mm, height_mm}` | W3 | building bounding box dimensions |
| `heights.{first_mm, traufe_mm, gelaende_mm, ok_ffb_eg_mm, ok_ffb_og_mm, ok_ffb_dg_mm}` | W2 | floor slabs + roof apex |
| `orientation.north_edge_label_id` (resolved via `resolveOrientationBasis`) | W4 | rotate compass + assign Ansicht faces |
| `wall_thickness.{outer_mm, inner_mm}` | W3 | wall thickness in 3D |
| `openings_catalog` | N4 | window / door rectangle sizes |
| Per-scene walls (Grundriss-EG) | M0 | outer footprint polygon — not just the bbox |
| Per-scene view_openings (each Ansicht) | M0 | window/door positions on each wall face |
| Per-scene component_lines (each Ansicht/Schnitt) | M0 | roof slopes + ridge + eave |

#### 5.2 Coordinate convention

**Y is up.** Three.js + react-three-fiber default; matches the
`bezug_y_px → world_y` formula already in V0.1/V3 (negate-pixel-y
gives world-y). X runs along the building's ê axis (east from the
orientation graph), Z runs along n̂ (north). Units: **millimetres**
throughout — same as everything in `house_facts`, no conversion
boundary, no unit drift.

Camera, light, and grid all reference (0, 0, 0) = the user's
Bezugshöhe in image space mapped to the origin in 3D space. The
ground plane sits at world-y = `gelaende_mm`, which is negative
when Gelände is below ±0,00 (typical).

#### 5.3 What the 3D scene contains

A minimal, label-faithful model:

1. **Ground plane** at y = `gelaende_mm`. A 30 m square, light tan
   gradient, with a north arrow + cardinal compass etched in.
2. **Building footprint** = the EG-Grundriss outer wall polygon
   (closed loop of `wall` labels, in mm units), projected to the
   ground plane.
3. **Walls** = footprint extruded from y = `gelaende_mm` to
   y = `traufe_mm` (or `first_mm` if no Traufe), thickness from
   `wall_thickness.outer_mm`. Material: light beige with subtle
   normal shading.
4. **Floor slabs** at every `ok_ffb_*_mm` height: thin horizontal
   slabs spanning the footprint, semi-transparent so you can see
   the wall structure through them.
5. **Roof** = two-piece gabled mesh:
   - For each Ansicht's `component_line` with `line_kind='dachschraege'`,
     map the line into 3D using the Ansicht's facing direction (from
     `scene_orientation` + the orientation graph). The pair of slopes
     (typically two, mirror-symmetric) meets at the ridge.
   - For irregular roofs (one-sided sloping, hipped) — fall back to a
     flat ceiling at `traufe_mm` with a "vereinfacht" badge in the
     scene corner. User-confirmable.
6. **Openings** = per-Ansicht view_openings:
   - Each opening's `(x, y)` pixel-space center → mapped to 3D via the
     Ansicht's calibration: x = (pixel_x / px_per_mm) along the face's
     in-plane axis; y_world = bezug_y_px - pixel_y / px_per_mm
     (the same formula V0.1 already uses for height inference).
   - Render as a cutout rectangle in the wall face, colored by
     `opening_kind` from V tracker.
   - Floorplan openings position the *same* opening from above; we
     trust the Ansicht for the y-position, the Floorplan for the
     x-position (when both agree; conflict → refine queue).
7. **Cardinal-direction overlay** — N/S/E/W labels float at the
   midpoint of each wall face, scaled with zoom.
8. **Bezugshöhe marker** — a horizontal disc at y = 0 (±0,00) with the
   triangle glyph from V1, rotating to face the camera.
9. **Height marks** — for each `height_mark` with a `datum`, a thin
   horizontal line at y = `value_mm` with a floating label.
10. **Door swing** — for each `floorplan_opening` with
    `opening_kind='door'` AND `swing_side` set: render a quarter-arc
    *on the floor slab* indicating the swing direction. Hinge at the
    swing_side corner of the opening rect; arc sweeps to the
    perpendicular based on `swing` (in / out / sliding gets a
    straight line, not an arc). Color = the door's `LEGEND` colour
    at 60 % opacity. Floor-projection (not animated in 3D) keeps
    the read instant.
11. **Ground use of Schnitt scenes (R5 v1 limited).** A Schnitt
    contributes IF it has a `dachschraege` `component_line` that
    the Ansicht roofs disagree with — that's a refine-queue signal,
    not a geometry contribution. Schnitts that show interior
    structure (stairs, internal walls, slabs) are NOT used by the
    R5 v1 geometry builder. The "Vereinfacht — Schnitt-Daten
    ignoriert" badge surfaces this explicitly so the user knows
    the simplification.

#### 5.4 Tech choices

- **react-three-fiber + drei** for the React-friendly Three.js
  layer. Tree-shake friendly; we don't need most of drei but
  `OrbitControls`, `Edges`, `Text` are heavy hitters.
- Single page `/dataset/<key>/3d`. Camera defaults: orbit around
  building center, start at SW azimuth at 30° elevation, distance
  set so the building fills ~70 % of the viewport.
- Sidebar toggles per fact category (walls / roof / openings /
  floors / ground / compass) so the user can isolate parts.
- A "what's missing" panel: facts that *would* render but are
  unset (e.g. "no roof — Phase 4 dachschräge needed in Ansicht
  Süd"). The W tracker already knows this; we surface it here too.
- Keyboard: `R` reset camera, `O` orbit / `F` fit, `[` / `]`
  toggle slab visibility per floor.

#### 5.5 Render fidelity gradient

Different parts of the 3D have different confidence levels:

| Confidence | What | Visual treatment |
|---|---|---|
| Solid | Walls + footprint + floor slabs + heights when all from house_facts | opaque, fully shaded |
| Approximate | Roof when only one Ansicht's roof slopes are known | semi-transparent + "?" badge |
| Guessed | Openings positioned on faces the user hasn't actually labeled (cross-scene transfer) | wireframe only |
| Missing | Anything without source labels | renders as a placeholder geometry with an info badge |

The user always knows whether they're looking at a labeled fact or a
gap-fill. Hovering any geometry shows a tooltip naming the source
label IDs that contributed (so the user can click → jump to that
scene in the annotation editor).

#### 5.6 What 3D preview is NOT

- Not photorealistic. No textures, no shadows beyond simple ambient
  occlusion.
- Not editable. Click-to-modify lives in the W tracker AnnotatePage,
  not here. The 3D view is read-only.
- Not BIM export. No IFC, no STL, no glTF. Those are downstream of
  this tracker.

### Phase R6 — End-to-end smoke + bulk export

**Goal:** validate the pipeline works end-to-end on one house from
PDF intake through 3D preview, then add bulk export for training.

- R6.1 Smoke script `scripts/e2e_smoke.py`: takes a single PDF,
  uploads it, asserts an `incoming/<key>/` is created, calls the
  extraction API with a canned bbox list, asserts the dataset
  manifest has the expected drawings, asserts `house_facts.json` (in
  localStorage — exposed via the W0.4 debug getter we already have)
  has a workflow state. Manual step: the user does the labeling.
- R6.2 Bulk export: `POST /exports/<key>` produces a zip with both
  Set A and Set B per scene, plus a top-level `house_facts.json`
  and a manifest. **Explicit folder layout** (canonical training-
  corpus shape):

  ```
  data/exports/<key>/
    manifest.json              # version, generated_at, scene index
    house_facts.json           # frozen snapshot of localStorage HouseFacts
    setA/                      # Model-1 inputs (raw + dim-only labels)
      <scene_file>.jpg         # original image, unchanged
      <scene_file>.json        # SceneLabels with labels filtered to
                               #   dimensioned_distance only
    setB/                      # Model-2 inputs (rectified + all labels
                               #   transformed through H)
      <scene_file>.jpg         # rectified via homography from setA
      <scene_file>.json        # SceneLabels with every label's
                               #   geometry transformed; status fields
                               #   preserved; sources still reference the
                               #   ORIGINAL label ids so back-traceability
                               #   survives the transform
      <scene_file>.homography.json   # {matrix, rms_residual_px, status,
                               #   computed_from: [label_ids]}
    diagnostics/
      coverage.txt             # per-scene readiness summary
      anomalies.txt            # any extent/height sanity flags
  ```

  Sets are produced per scene that PASSES sanity (§R6.4). Scenes
  that fail land in `diagnostics/skipped.txt` with the reason.
  Image format JPG at quality 90; rectified images sized to the
  smaller of (2× original, 4096 px on long edge).

- R6.3 Cross-house bulk export: `POST /exports` — every house with
  phase ≥ 5 (or skipped) gets exported. A per-export job id; status
  endpoint `GET /exports/<job_id>` returns
  `{state: 'queued'|'running'|'done'|'failed', houses_done, total, log_tail}`.
  Cross-house ZIP top-level layout: `exports/<job_id>/<key>/…`
  mirroring R6.2. Streamed download (don't buffer the whole ZIP
  in memory).
- R6.4 Export-quality sanity checks (block, unless `--force`):
  - Pending refine-queue items of kind `height_conflict` or
    `extent_mismatch` (W8)
  - `extent.width_mm` or `depth_mm` outside [3000, 30000] mm
    (single-family-house range)
  - `heights.first_mm > 20000` (>20 m above ±0,00 is suspicious)
  - Any `display.export_skip` scene that's the ONLY scene of its
    `kind` for the house (would yield a partial training pair)
  - `calibration_per_scene[file].rms_residual_px > 8` (homography
    degenerate)
- R6.5 Re-export versioning: `POST /exports/<key>?version=N` allows
  the caller to pin a numbered version; default behavior overwrites
  `exports/<key>/`. Old versions are preserved at
  `exports/<key>.v<N>/` when the caller explicitly requests it. No
  auto-timestamping — keeps the file tree clean for the common
  "re-export once after labeling" case.

---

## 3. Data-model changes

### 3.1 New: `data/pdfs/incoming/<key>/`

Per-house intake bundle:

```
data/pdfs/incoming/house-21/
  house-21.pdf                # consolidated source PDF
  manifest.json               # {source_filenames, uploaded_at, page_count, state}
  source/                     # original uploads if we want to keep them
    Plan_2025-03-12.pdf
    Detail_Erdgeschoss.pdf
```

`manifest.json` schema:

```jsonc
{
  "schema_version": "1.0",
  "house_key": "house-21",
  "consolidated_pdf": "house-21.pdf",
  "source_filenames": ["Plan_2025-03-12.pdf", "Detail_Erdgeschoss.pdf"],
  "uploaded_at": "2026-05-29T14:00:00Z",
  "page_count": 18,
  "state": "extracted",          // pending | partial | extracted | annotated
  "user_notes": "Architekt Müller",
  "extracted_scenes": [
    { "page": 3, "bbox_pdf_units": [...], "scene_file": "house-21-pdf-p3-fp-eg.jpg" }
  ]
}
```

### 3.2 Extended: `data/dataset/<key>/manifest.json`

Existing `DatasetDrawing` interface gets:

```ts
interface DatasetDrawing {
  // existing fields...
  source?: 'generated' | 'real' | 'pdf';  // add 'pdf' for R2-extracted
  crop_from?: {
    pdf_file: string;
    page: number;
    bbox_pdf_units: [number, number, number, number];
  };
}
```

### 3.3 No changes to `house_facts.ts`

R5 reads `house_facts` exactly as W0–W9 wrote it. Zero new fields.
The 3D view is a derivation, not a new fact source.

### 3.4 SceneLabels schema unchanged

Same `scene_labels.schema.json` as W7. PDF-source scenes look
identical to AI-generated scenes from the schema's perspective.

---

## 4. API surface changes

| New | Purpose |
|---|---|
| `POST /pdfs` | R1 — upload one or many PDFs, optional per-file house-key |
| `GET /pdfs/incoming` | R1 — list incoming PDFs across houses |
| `GET /pdfs/incoming/<key>` | R1 — single-house intake state + manifest |
| `PUT /pdfs/incoming/<key>/manifest` | R1 — edit user notes, change state |
| `DELETE /pdfs/incoming/<key>` | R1 — remove an intake bundle |
| `POST /pdfs/<key>/extract` | R2 — batch extract scenes from bboxes |
| `GET /pdfs/<key>/page/<n>` | R2 — render PDF page at given DPI (JPEG) |
| `POST /exports/<key>/<file>/preview` | R4 — compute rectified scene + H |
| `GET /exports/<key>/<file>/preview/image` | R4 — rectified scene JPG |
| `POST /exports/<key>` | R6 — bulk export per house |
| `POST /exports` | R6 — cross-house bulk export |
| `GET /exports/<job_id>` | R6 — job status |

| Removed | Replacement |
|---|---|
| `/houses`, `/houses/{key}`, `/houses/{key}/pdf`, `/houses/{key}/images` | none (dataset side replaces) |
| `/houses/{key}/dataset_starred` | dropping; star-to-include flow becomes "upload to intake" |
| `/scene/{key}/{file}` (house-scoped) | unchanged: dataset side already has `/datasets/{key}` for thumbnails |

PDF rendering on the server: **pypdf2** for merge + page extraction
(metadata), **pdf2image** or **fitz/PyMuPDF** for raster rendering.
Recommend PyMuPDF for speed + quality. Already a transitive dep
elsewhere? Check during R1; if not, single new install.

---

## 5. UI navigation map (post-cleanup)

```
/dataset                         # top-level house list (with phase badges)
  ├── /intake                    # R1 — upload PDFs
  ├── /<key>/extract             # R2 — bbox scenes from PDF
  ├── /<key>/3d                  # R5 — 3D preview
  ├── /<key>                     # existing — dataset house overview
  └── /<key>/scene/<file>
       ├── /annotate             # existing — AnnotatePage (W0-W9)
       └── /export-preview       # R4 — per-scene Set A / Set B
```

No `/house` routes. Root `/` redirects to `/dataset`.

---

## 6. Migration plan

A destructive cleanup at the front of the work, then incremental
adds. Each wave commits independently so we can roll back.

1. **R0.0 — Backup branch.** Tag the current `main` as
   `pre-r-tracker` so the catalog code stays recoverable from git.
2. **R0.1–R0.9 — Strip houses.** One commit, type-check, build,
   smoke-test the dataset path end-to-end. Branch + open PR for
   review since this is the largest deletion.
3. **R1 — PDF intake.** New page, new API. House-21/22/23's source
   PDFs land in `incoming/` as a smoke fixture.
4. **R2 — Scene extraction.** PDF.js client + server crop pipeline.
   On commit, extracting house-21's PDF should produce the dataset
   drawings that previously existed pre-cleanup (the existing dataset
   files act as ground truth for the extraction).
5. **R3 — Cross-step navigation.** Top stepper + resume-where-left-off.
6. **R4 — Export preview.** Server-side homography + rectification.
7. **R5 — 3D preview.** react-three-fiber + drei. Single page.
8. **R6 — Bulk export + smoke.** End-to-end test fixture.

Estimated commits: ~30–40 across the six waves (R5 alone will probably
take 6–8 commits given the 3D scene complexity).

---

## 7. Open design decisions

| Question | Default | Why |
|---|---|---|
| Should we ship a 1-shot data migration that converts old `/houses` localStorage entries to `/dataset` scope? | No — they're already separately stored. We just delete the unused `scope='house'` ones. | Less code; the keys collide only on house-21/22/23 which already exist in both scopes. |
| `data/houses/` cleanup: hard-delete vs. archive to `data/_legacy_houses/`? | Hard-delete via the auditable cleanup script (R0.6) so the repo doesn't carry dead bytes. Git history preserves them. | Cleaner repo, no temptation to revive. |
| PDF rendering on the server: PyMuPDF vs. pdf2image+poppler? | PyMuPDF. | Pure-Python wheel, no system poppler dep, faster, supports the bbox crop API directly. |
| Where does the 3D scene live: a panel inside AnnotatePage, or a separate page? | Separate page `/dataset/<key>/3d`. | The 3D view needs all six AnnotatePage scenes' labels; rendering it inside the editor would couple it to whichever scene is open. |
| Should the 3D preview include a wireframe/floorplan-only mode for performance? | Yes — keyboard `W` toggles. Default = full. | Useful for quick orientation checks; performance is a non-issue at these polycounts. |
| Do we support exporting *during* annotation (the "live" preview from R4) or only at the end? | Live. R4 fires re-export when save() touches the scene. | Tight feedback loop catches homography degeneracy early. |
| 3D camera: orbit / fly / first-person? | Orbit only. | First-person is a UX hole — users get lost. |
| PDF intake: allow URL-based imports (paste a URL → server fetches)? | No, file upload only. | URL-fetching is a security surface; defer. |
| Per-scene "skip me from export" flag? | Yes, on `SceneLabels.display.export_skip: boolean`. Useful for detail scenes that have no Bezugsmaß. | Otherwise R6 fails the whole house when one detail scene lacks calibration. |
| Should R5's 3D view persist camera state per-house? | Yes — same localStorage idiom as W0's display prefs. Camera distance/azimuth/elevation per `(scope, houseKey)`. | Users will return to the same view; respecting their orientation memory is cheap. |
| The two-stage training pipeline export format: ZIP or directory? | Directory `data/exports/<key>/{setA,setB}/`. ZIP only for downloads (R6.2 returns a streaming zip). | Easier to incrementally update + diff. |

---

## 8. 3D rendering — deeper dive on the math

The hardest correctness question in R5 is "how do I place an opening
labeled in Ansicht-Süd at the right 3D position on the south wall?"

### 8.1 Per-Ansicht face transform

For each Ansicht with `scene_orientation = o` and known
`scene_metadata[file].bezug_y_px`:

```
1. Get faces in 3D space.
   - footprint = closed wall polygon in (x, z) at y = 0
   - Pick the face whose outward normal points in direction `o`:
       face_north  = the edge perpendicular to ê (north arrow)
       face_east   = the edge perpendicular to n̂
       etc.

2. The face is a rectangle (assume): two ground corners (Pa, Pb) +
   two top corners (Pa', Pb') at y = traufe_mm.

3. The face's local 2D basis:
       u_axis = (Pb - Pa) / |Pb - Pa|       # horizontal along face
       v_axis = (0, 1, 0)                   # vertical
       n_axis = u × v                        # outward normal

4. For each label on the Ansicht with anchor (px, py):
   3D position =
       Pa + u_axis * (px / px_per_mm) + (0, bezug_y_world_mm - py / px_per_mm, 0)
   where bezug_y_world_mm = 0  (the ±0,00 line IS y=0 in 3D world)

   Or more usefully:
       y_world = bezug_y_px_to_world_y(py) = (bezug_y_px - py) / px_per_mm
       x_world_along_face = px / px_per_mm
       3D position = Pa + u_axis * x_world_along_face + (0, y_world, 0)
```

The key insight: the Ansicht's image is a side-on photograph of one
face. Pixel-x maps to *the in-face horizontal axis*, pixel-y maps to
*world y* via the Bezugshöhe's pixel-y as the y=0 reference and
px_per_mm as the scale.

### 8.2 Per-Grundriss footprint construction

For the EG-Grundriss only (R5 v1):

1. Collect every `wall` label.
2. Run `lib/rooms.ts`'s `detectRooms` logic but at the *outer*
   perimeter — the longest closed cycle (rather than the smallest
   face).
3. Convert each wall's two endpoints from pixel → mm via the
   Grundriss calibration. Each vertex is `(x_mm, 0, z_mm)`.
4. The polygon is the building's outer footprint.

Edge cases:
- Non-orthogonal walls: kept as drawn; the 3D wall isn't constrained
  to ortho.
- Open polygons (walls don't form a complete loop): convex hull
  fallback + amber warning badge.
- Floors with floorplans that disagree (OG perimeter ≠ EG): out of
  scope for R5 v1; we only use EG.

### 8.3 Roof construction

Simplest case — gable roof with two slopes:

1. From the Ansicht with `scene_orientation` perpendicular to the
   ridge (typically the gable-facing one), find the
   `component_line` with `line_kind = 'dachschraege'`. There will be
   two such lines forming an inverted V.
2. Compute the apex (their intersection) in pixel coords; convert to
   3D via the Ansicht's face transform.
3. The apex y is the ridge height. The ridge runs perpendicular to
   the gable-face's in-face horizontal axis — i.e. along the
   building's long axis.
4. Two roof panels: each is a quad from `(traufe_edge, apex_edge)`
   on each side.

Hipped, mansard, flat: future. In R5 v1, anything not a gable
displays as a "vereinfacht" flat ceiling with a chip the user can
click to confirm or override.

### 8.4 Opening placement

For each `view_opening` on an Ansicht:

1. Compute its bounding box center in pixel coords.
2. Transform to 3D via §8.1.
3. Render as a rectangle in the wall plane (set back 1 mm from the
   wall surface so z-fighting doesn't flicker).
4. Color from `opening_kind` → `LEGEND` color (already in V tracker).
5. Floorplan position cross-check: for each floorplan_opening,
   transform its quad center → 3D x/z. If the |Δ| from the Ansicht-
   derived position is > 100 mm AND both labels are `is_reference=
   false`, flag as a refine issue (W8 already has the right shape).

---

## 9. Risks + known unknowns

| Risk | Mitigation |
|---|---|
| Bulk PDF deletion is irreversible | R0.0 backup branch + tagged commit before any data deletion |
| PDF rendering performance for large PDFs (50+ pages) | Cache rendered page JPGs on the server side (`data/pdfs/cache/<key>/<page>.jpg`); render-on-demand via R1's API |
| PDF.js bundle size in the client | Code-split by route; only `/dataset/intake` and `/dataset/<key>/extract` pull PDF.js |
| Homography degenerate / `rms_residual_px` high | Already handled in spec §2 — exports flag scenes with `status='degenerate'`; R4's red badge surfaces it pre-export |
| 3D rendering crashes browser tab when walls are malformed | Try/catch in the geometry builder; on failure render an info panel "Geometrie konnte nicht aufgebaut werden" instead of crashing |
| Future multi-floor floorplan support | R5 v1 ignores OG/DG floorplans; the structure should make adding them later additive |

---

## 10. Engineering hygiene (cross-cutting, applies to every wave)

These don't fit a single wave but matter throughout. Setting defaults
here so /loop doesn't have to invent them per commit.

### 10.1 Testing strategy

- **Pure-function modules** (`lib/workflow.ts`, `lib/region_kind.ts`,
  `lib/rooms.ts`, new R5 geometry builder): unit tests via the
  existing vitest setup. Co-located `*.test.ts` next to source.
  Target: every public export has at least one happy-path test.
- **API routes** (R1/R2/R4/R6): smoke via `pytest` + `httpx.AsyncClient`,
  one happy-path per endpoint hitting a tiny fixture PDF and a tiny
  fixture house. Target: each new route has at least one test.
- **UI flows**: no Playwright/Cypress yet — the cost-to-value ratio
  isn't there for a single-user app. Manual smoke checklists per
  wave in the commit message ("opened /dataset/intake, dropped X.pdf,
  saw incoming/<key>/, extracted scene Y, …"). If the app gains
  multi-user later, revisit.
- **R5 geometry**: visual diff fixture. Save a labeled snapshot of
  house-21's facts; the geometry builder run against it produces a
  deterministic Three.js scene JSON (vertex/face counts, bbox
  dimensions). Snapshot-compare in CI; mismatch fails the build.

### 10.2 Performance budgets

Set targets so we notice regressions:

| Surface | Budget |
|---|---|
| PDF page render (300 DPI, A3 page) | < 800 ms server-side, cached to JPG after first render |
| PDF.js client render (on-screen preview, 96 DPI) | < 300 ms per page; lazy-load adjacent pages |
| R2 bbox draw → visible | < 16 ms (60 fps interaction) |
| R4 rectified preview generation | < 2 s per scene |
| R5 3D scene first paint | < 1.5 s for a fully-labeled house (15 scenes, ~80 walls, ~30 openings) |
| R5 60 fps orbit | < 5 k draw calls, < 200 k triangles for the typical SFH |
| PDF.js client bundle | < 800 KB minified (code-split per route) |
| react-three-fiber + drei bundle | < 600 KB minified (tree-shake drei to OrbitControls + Text + Edges only) |

When any budget is missed by > 2×, open an issue rather than
shipping the regression.

### 10.3 Auth + multi-user posture

The app is single-user-on-localhost by default. The new mutating
routes (`POST /pdfs`, `POST /pdfs/<key>/extract`, `DELETE /pdfs/incoming/<key>`,
`POST /exports/*`) are **un-authed** to match the existing routes.

This is acceptable because the dev server binds to localhost, but
we document the constraint explicitly:

- README warns: "Do not expose this server on a LAN or VPN without
  fronting it with auth."
- The server logs a startup warning when it binds to anything other
  than `127.0.0.1` / `localhost`.
- Multi-user support is explicitly out of scope; if it ever
  becomes desired, the right move is a reverse-proxy with bearer
  tokens, not bolting auth onto FastAPI directly.

### 10.4 Error states + recovery

For every new route + UI surface:

- **Upload failure** (R1): toast with HTTP status + first 200 chars
  of server response; PDF stays selected so user can retry without
  re-picking files.
- **Extraction failure** (R2): per-scene error reported in the
  bbox draft so the user sees WHICH bbox failed; other bboxes are
  still committed atomically (server-side: per-scene try/except,
  partial-success response).
- **3D crash** (R5): the geometry builder runs inside an error
  boundary; on failure the panel shows "Geometrie konnte nicht
  aufgebaut werden" with a "Diagnose anzeigen" button that surfaces
  the missing facts (delegates to W5.4 "what's missing" panel).
- **Export sanity-block** (R6): toast + a list of the failing
  scenes, each with a "fix me" link to the offending refine queue
  issue.

### 10.5 Telemetry

Single-user app — minimal. We add a developer console object
`window.__bimReadinessDebug` exposing:

- `lastExtractDraft`: the most-recently-saved R2 draft from
  localStorage
- `geometryStats`: R5's last render stats (vertex / triangle / draw
  call counts + frame time)
- `exportLastRun`: the last R6 invocation's summary

Removed before any first non-dev release. Until then it's invaluable
for diagnosing user-reported issues.

### 10.6 Accessibility + i18n + mobile

- **Accessibility**: keep keyboard nav working through every new
  page (already the K tracker contract). Don't add new mouse-only
  flows. Skip screen-reader spec — out of scope until requested.
- **i18n**: German strings only (consistent with the existing app).
  Any user-visible string is in German; error messages from the
  server stay German.
- **Mobile**: not supported. The app is desktop-only; layouts will
  break under 1024 px. Don't fight it.

### 10.7 Schema versioning + migration

PDF intake manifests and dataset manifests both carry a
`schema_version` field. When a field is added:

- Reader code defaults missing fields to `null` / `[]` (same forward-
  compat idiom as `loadHouseFacts` in W0).
- Writer code writes the new field unconditionally.
- No batch migration scripts — files migrate forward on first save.

This means old localStorage / on-disk state from house-21/22/23
silently gains new fields the next time it's opened post-upgrade.

---

## 11. Self-audit — is this tracker exhaustive?

- ☑ Drops "houses" path in R0 — UI, API, scripts, data, MCP server
  (R0.10), PreviewPage (R0.11), Badge / tier UI (R0.12),
  localStorage migration (R0.13) all explicit.
- ☑ Preserves houses 21 / 22 / 23 as intake-side seed PDFs (§1.2).
- ☑ PDF intake (R1) with multi-file / batch / multi-house
  consolidation.
- ☑ Scene extraction (R2) including draft persistence (R2.8),
  post-extract bbox editing (R2.9), cross-page batch tagging
  (R2.11), crop quality preflight (R2.12), slug uniqueness (R2.13).
  Rotation explicitly deferred to R2 v2 (R2.10).
- ☑ Cross-step navigation (R3) with resume-where-left-off.
- ☑ Per-scene export preview (R4) showing both Set A and Set B —
  preserves `spec/annotation-tool.md §2` verbatim.
- ☑ 3D annotation preview (R5): coordinate convention nailed down
  (§5.2 Y-up, mm units), scene assembly (§5.3), door swing (§5.3
  #10), Schnitt usage as refine-signal-only (§5.3 #11),
  confidence gradient + hover-to-source (§5.5), explicit non-goals
  (§5.6), face-transform math (§8).
- ☑ Bulk export (R6) with explicit folder layout, sanity-check
  rules, versioning.
- ☑ Engineering hygiene (§10): testing strategy, performance
  budgets, auth posture, error states, telemetry, accessibility
  / i18n / mobile stance, schema versioning.
- ☑ Migration plan (§6) with destructive operations gated behind a
  backup branch.
- ☑ Open questions (§7) with defaults so /loop can resolve without
  blocking.

### Gaps deliberately left open

- **Real-time collaboration** on PDF extraction (two users dividing
  bboxes) — single-user only.
- **OCR pre-pass** on PDF pages (auto-detect drawing kind / view from
  page title block) — future R2.x, doesn't change v1 schema.
- **IFC / glTF / STL export** from R5's 3D scene — out of scope; R5
  is a *preview*, not an exchange format.
- **Multi-floor 3D** (OG / DG separate floorplans) — R5 v1 is EG +
  roof only.
- **Stale-fact orange overlay** (carried from W7's deferred work) —
  remains a follow-up.
- **Cross-house "same model" replication** — out of scope for both
  W and R tracker.
