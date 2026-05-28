# Annotation tool — Visualisierung (V) tracker

**Status:** Draft (2026-05-28). Pre-implementation.
**Owner:** jhoetter
**Predecessors:**
  - [`spec/annotation-tool.md`](annotation-tool.md) — M0–M6 (data model, MVP)
  - [`spec/annotation-ux.md`](annotation-ux.md) — M7–M13 + X1–X11 (UX redesign, cross-scene)
  - [`spec/keyboard.md`](keyboard.md) — K1–K12 (modifier + keymap source of truth)
  - N1–N8 + Wave 1–3 (structural snap, house-facts, calibration, cross-scene auto-load)

**Goal:** make every drawn label *self-describing on the canvas*. A user
glancing at a Schnitt should instantly see "that orange area is a roof, that
blue rectangle is a window, that ↻-badged height is inherited from
another scene" without reading any text. Visual semantic clarity replaces
clicking-to-inspect for the common cases.

---

## 1. What's wrong today (verbatim feedback + observation)

| Observation | What it actually means |
|---|---|
| "höhenkote wasnt loaded from the other scene" — even though Wave 3 added auto-apply | The N5 auto-apply gates on "scene has zero height_marks". A scene with only ±0,00 already counted as "non-empty" so First/Traufe never came in. Fix: gate on **per-datum** presence, not per-scene. |
| Closed component_line polygons render with only a thin outline + dot vertices — "you didnt draw the area as well" | The "P9 closed-polyline = area" branch exists but the fill color is the line stroke colour at very low alpha, indistinguishable from background on pale paper drawings. Fill needs an *opaque-enough hatched* fill that still doesn't obscure the underlying drawing. |
| "want areas within polygons have slight schraffierungen, slight opacity, and a little icon ideally for what type they are (e.g. roof etc); same for other elements overall" | Each label kind/subtype deserves a *visual signature* — pattern + glyph — so labels are legible without selecting them. |
| Floorplan openings only show a coloured rectangle | Floorplans have rich semantic types (door swing direction, window mullion count, garage rolling vs lift) — current rendering throws all that away. |
| Inherited (cross-scene) labels are flagged only via X5 provenance badges that appear on selection | The label *itself* doesn't visually announce "I came from elsewhere — verify me". |

---

## 2. Principles for visual semantics

1. **Glyph beats text.** A 14 px door-swing arc or a window-mullion symbol communicates faster than a "door" or "window" label.
2. **Pattern beats colour alone.** Colour is already used (per `LEGEND` in `lib/colors.ts`). Layering a *pattern* (hatch / stipple / chevron) onto the fill makes label kinds distinguishable for users with colour-vision differences and on pale-paper scans where mid-saturation hues wash out.
3. **Two opacity tiers.** Fill is light enough to read the underlying drawing (~12–18 % opacity in canvas units). The hatch overlays at ~25–35 % opacity so the pattern still reads.
4. **Glyph is centroid-anchored, screen-pinned size.** Always 16 px regardless of zoom, so labels remain legible at any scale. Hide glyph when the label's bbox is smaller than ~3× glyph width (it would just clutter).
5. **Provenance overrides decoration.** A label promoted from another scene gets a *dashed outline* + a small ↻ corner badge — overriding any per-kind decoration. Cross-scene status is the most important fact about a label.
6. **No new label types.** All visualisation work is rendering-only — no schema changes, no migrations. Everything keys off existing `type` + `attributes.{opening_kind,line_kind,datum}`.

---

## 3. Inventory: label kinds and their visual targets

The visualisation system is a 1:1 mapping from `(type, subtype)` → `{ color, hatch, glyph, decoration }`. Below is the full target table — anything not yet listed will appear in `lib/colors.ts` / `lib/icons.tsx` / `lib/glyphs.tsx`.

### 3.1 Wall (`type='wall'`)

| Attribute | Variant | Color | Pattern | Glyph (small, hidden on narrow walls) |
|---|---|---|---|---|
| `thickness_mm ≥ 300` | exterior / load-bearing | slate-800 | diagonal hatch (existing `bim-wall-hatch`, 45°, 7px) | none (the hatch IS the signature) |
| `thickness_mm 150–300` | interior structural | slate-600 | thinner cross-hatch (60°, 12px) | none |
| `thickness_mm < 150` | partition / drywall | slate-400 | stippled dot pattern | none |
| no thickness yet | unclassified | slate-700 | none | "?" badge at midpoint |

### 3.2 Floorplan opening (`type='floorplan_opening'`)

| `opening_kind` | Color | Pattern | Glyph | Extra (rendered between quad edges) |
|---|---|---|---|---|
| `window` | sky-600 | horizontal mullion lines (count derived from quad width) | small 4-pane glyph at centroid | thin centre-mullion line splitting the rectangle |
| `door` | teal-600 | none | door-swing-arc (existing concept, see [§3.6 glyph extensions]) | actual quarter-arc rendered from `swing_side` + `swing` |
| `garage_door` | amber-800 | vertical-slat hatch (parallel lines parallel to long edge) | tilt-up garage glyph | none |
| `passage` | zinc-500 | none | open-doorway glyph (`⌷` shape) | dashed jamb lines (no swing arc) |
| `other` | sky-600 | diagonal stripes 30° | `?` glyph | none |

### 3.3 View opening (`type='view_opening'`)

| `opening_kind` | Color | Pattern | Glyph | Extra |
|---|---|---|---|---|
| `window` (rectangle) | sky-600 | horizontal mullions + 1 vertical mullion (4-pane look) | 4-pane glyph at centroid | "↕" mark on left edge when frame_visible=true |
| `window` (circle) | sky-600 | concentric circle hint | circle-cross glyph at centre | — |
| `window` (polygon — arched/irregular) | sky-600 | radial fan lines from the topmost vertex | arched-window glyph | — |
| `door` | teal-600 | vertical panel line down the centre | door-with-handle glyph | "▢" handle dot at lower right |
| `skylight` | cyan-600 | tilted parallel lines (24°) | skylight-tilt glyph | — |
| `dormer` | orange-600 | "house-on-roof" mini outline as pattern | dormer glyph (gable + window pane) | dashed line where dormer meets roof |
| `garage_door` | amber-800 | vertical slats | tilt-up garage glyph | — |
| `other` | sky-600 | diagonal 30° | `?` | — |

### 3.4 Component_line (`type='component_line'`) — OPEN polyline

A polyline whose endpoints are >snap-distance apart. Rendered as a line, no fill.

| `line_kind` | Color | Stroke style | Inline glyph (centred on longest segment) |
|---|---|---|---|
| `gebaeudekante` | slate-800 | solid 3 px | none |
| `dachschraege` | orange-600 | solid 2.5 px | roof-slope glyph (`◭`) |
| `first` | orange-700 | solid 2 px | ridge glyph (single peak, "/\") |
| `traufe` | orange-500 | solid 2 px | eave glyph (horizontal + drip) |
| `gelaende` | green-700 | dashed (3,2) | ground-hatch glyph (3 short slashes) |
| `geschoss` / `ok_ffb` / `sockel` / `kniestock` | green-700 dim | dotted | level-tick glyph (▬) |
| `dachschraege` (when both endpoints share a joint with a wall) | inherit | solid | roof glyph (no change) |
| `other` | gray-500 | dashed (4,3) | `?` |

### 3.5 Component_line — CLOSED polyline (the "P9 area" case)

When `first ≈ last` within snap radius, the polyline is conceptually a *region*. Inferred regions get full area decoration:

| Inferred area kind | Detection rule | Color (15% fill / 30% hatch) | Hatch | Centroid glyph |
|---|---|---|---|---|
| **roof** | ≥1 edge is `dachschraege` OR ≥1 vertex is the highest point of the building and two edges descend symmetrically | orange-600 | diagonal 45° + 75° crossed (architectural roof hatch) | roof glyph (◭) |
| **wall body** | ≥75 % of perimeter edges classify as `gebaeudekante` | slate-700 | dense diagonal 45° (matching wall hatch) | wall glyph (▦) |
| **gable** | ≥1 dachschraege edge AND ≥1 gebaeudekante edge, ≥3 vertices, region NOT containing the highest point | orange-400 | sparse diagonal 60° | gable glyph (triangle with horizontal base) |
| **ground / earth** | polygon includes the bottom edge of the image AND uses `gelaende` line_kind | green-800 | earth-symbol hatch (alternating tick rows) | earth-tick glyph |
| **inferred-area unknown** | doesn't match above | gray-500 | sparse diagonal 45° | "?" glyph |

### 3.6 Height_mark (`type='height_mark'`)

Already renders the triangle + label. Adds:

| Datum | Existing glyph | New decoration |
|---|---|---|
| Bezugshöhe (value=0) | `±0,00` | small horizontal-bezug-glyph next to the value (already implicit, formalize) |
| `first` | text | tiny ridge-glyph (▲) next to the value |
| `traufe` | text | tiny eave-glyph (▬⌐) next to the value |
| `gelaende` | text | tiny ground-glyph (≈) next to the value |
| `ok_ffb` | text | floor-glyph (▭) |
| inherited (cross-scene) | + ↻ corner badge (already in X5) | dashed border around the triangle |

### 3.7 Dimensioned_distance (`type='dimensioned_distance'`)

| Variant | Color | Stroke style | Endpoint glyph |
|---|---|---|---|
| Standard dim | purple-600 | solid + arrow ticks (existing) | small short tick marks |
| `is_reference=true` (M1 calibration source) | pink-600 (existing) | solid + filled arrow heads | **gold star glyph** at midpoint (NEW — signals "this drives the homography") |

### 3.8 Dimension_number (`type='dimension_number'`)

| Variant | Color | Glyph |
|---|---|---|
| `parsed_value_mm` set | purple-600 | small `#` glyph |
| `parsed_value_mm` null | purple-300 | grey `?` glyph |

---

## 4. Cross-cutting decorations (override all per-kind visuals)

| Condition | Visual override | Source signal |
|---|---|---|
| Label inherited from another scene of the same house | dashed outline (`4,3`) + ↻ corner badge | `crossSceneProvenance: Map<labelId, string>` (already in AnnotatePage state) |
| Label in `refine` queue with `kind='height_conflict'` | red ring + flashing badge | `collectRefineIssues` output (N8) |
| Label in `refine` queue with `kind='off_axis'` | yellow dashed extension showing target angle | existing refine analysis |
| Label is `selectedId` or in `selectedIds` | accent ring (existing) | selection state |
| Label has unresolved attribute (e.g. opening_kind='other', dim value_mm=null) | small `?` overlay in upper-left corner | refine queue scan |
| Label has status `not_readable` / `missing` / `uncertain` | greyscale tint + ⓘ corner badge | label.status |

---

## 5. Floorplan-specific layer (Grundriss only)

A Grundriss is a *plan view*: room-level pictograms become useful. This is its own micro-tracker (V9) because it depends on detecting *closed wall loops*.

### 5.1 Detect rooms

A room is a closed region bounded by wall labels. Run `detectClosedRegions(walls)` against the connectivity graph (M1.1 — walls share joints). Each closed loop = a candidate room.

### 5.2 Room labelling

For each detected room, **once** the user opens its inspector or auto-classifies it, store the room kind in a per-house cache (`bim-db:annotate:rooms:<scope>:<houseKey>:<sceneFile>`). Render the cached kind even before re-detection runs.

Room kinds + pictograms (16 px glyph at the room centroid, with light hatch):

| Room kind | Glyph | Hatch (5 % opacity) |
|---|---|---|
| Wohnzimmer | sofa | gentle vertical |
| Küche | stove | dot-grid |
| Esszimmer | table-chairs | gentle horizontal |
| Schlafzimmer | bed | gentle 30° |
| Badezimmer | wash-basin | tile-grid (small squares) |
| WC | toilet | smaller tile-grid |
| Flur | corridor-arrow | none |
| Treppe | stair-glyph | parallel lines |
| HWR / Technik | gear | speckle |
| Garage | car | vertical slats |
| Keller | brick | brick-pattern |
| (other) | `?` | none |

### 5.3 Persist & infer

Same as N4 house_facts — promoted room kinds become house-wide knowledge keyed by `(orientation, level)`. Floor "EG" has *one* Wohnzimmer; if the user labels it on Grundriss-EG-A, the same room shape on Grundriss-EG-B (a copy of the same plan, different rendering) auto-fills.

---

## 6. BIM-icon library — gaps to close

Current `lib/icons.tsx` is a 13-icon subset of `jhoetter/bim-icons` (tools + scene tags + question mark + link). The V tracker needs *semantic* glyphs that live alongside the tool icons but render at canvas-glyph size (16 px screen-pinned).

### 6.1 New icons needed (commit to `lib/glyphs.tsx`)

Group A — opening + wall semantics:
- `WindowPaneIcon` (4-pane grid)
- `DoorSwingIcon` (door + quarter arc) — note: the *actual* swing arc on the canvas is geometry-driven, this is just the centroid glyph
- `DoorHandleIcon` (small dot/oval) — for view_opening door
- `GarageDoorIcon` (tilt-up panel hint)
- `PassageIcon` (open doorway)
- `SkylightIcon` (slanted-rectangle)
- `DormerIcon` (gable on roof slope)
- `WindowCircleIcon` (circle + cross)
- `WindowArchedIcon` (arch on rectangle)

Group B — line + area semantics:
- `RoofSlopeIcon` (◭)
- `RidgeIcon` (peak `/\`)
- `EaveIcon` (horizontal + drip)
- `GroundIcon` (3 ground-ticks)
- `WallBodyIcon` (▦)
- `GableIcon` (gable triangle)

Group C — height + dim semantics:
- `BezugIcon` (formalize the ▽ triangle already drawn for ±0,00)
- `DimRefStarIcon` (gold star — flags is_reference dims)
- `LevelTickIcon` (small ▬ stroke)

Group D — room pictograms (V9, lower priority):
- `RoomSofaIcon` / `RoomBedIcon` / `RoomStoveIcon` / `RoomBathIcon` / `RoomToiletIcon` / `RoomStairIcon` / `RoomCarIcon` / `RoomBrickIcon` / `RoomGearIcon` / `RoomTableIcon` / `RoomCorridorIcon`

### 6.2 Strategy

Each Group A/B/C glyph is a 24×24 SVG path component matching the existing `icon()` factory in `lib/icons.tsx`. Group D lives in `lib/room-glyphs.tsx` so the bundler can tree-shake it for non-Grundriss views.

**Don't reach for the npm `jhoetter/bim-icons` package yet** — adding a new dep just to access a subset is heavier than maintaining ~20 inline SVGs. Once Group D lands and the icon list exceeds ~35, revisit.

---

## 7. SVG hatch pattern library

All hatches defined once in the `<defs>` block of the canvas SVG, then referenced via `fill="url(#hatch-roof)"`.

| Pattern id | Visual | Used by |
|---|---|---|
| `bim-wall-hatch` (existing) | diagonal 45° lines | wall fill |
| `bim-wall-cross` | crossed 60° + 120° | interior structural wall |
| `bim-wall-stipple` | dot grid | partition wall |
| `bim-roof-hatch` | crossed 45° + 75° (architectural roof shading) | roof region fill |
| `bim-gable-sparse` | sparse diagonal 60° | gable region fill |
| `bim-ground-hatch` | alternating tick rows (earth symbol) | ground region fill |
| `bim-mullion-h` | horizontal lines (window panes) | view/floorplan window |
| `bim-mullion-v` | vertical-slat lines | garage door |
| `bim-skylight-tilt` | diagonal 24° (close-spaced) | skylight |
| `bim-dormer-house` | tiny outlines | dormer (rare; may scrap) |
| `bim-stripe-30` | diagonal 30° (unclassified marker) | opening_kind='other' |
| `bim-stipple-light` | dots 18 % opacity | room pictograms hatch base |
| `bim-tile-grid` | square grid | bathroom |
| `bim-tile-grid-fine` | smaller squares | WC |
| `bim-stair-lines` | parallel | staircase |
| `bim-brick` | running-bond brick | basement |
| `bim-speckle` | irregular dots | utility |

Each pattern is opacity-gated at the `<pattern>` level (≤35 %) so layering on top of the user's drawing stays subordinate.

---

## 8. Implementation waves

Three waves, mirroring N1–N8 cadence. Each wave: type-check + build + commit + push, one commit per wave.

### Wave V0 — Bugfixes blocking visual clarity

- **V0.1** N5 auto-apply must trigger when **any** known house-height datum is missing from the scene — not gate on "scene has zero height_marks". Fix the condition in the post-hydration effect (currently `!initialLabels.some((l) => l.type === 'height_mark')`).
- **V0.2** Closed component_line fill is invisible in practice. Increase fill alpha from current value, layer hatched pattern over it.

### Wave V1 — Per-kind glyphs + hatches for openings + lines

- **V1.1** Add Group A + B icons to `lib/glyphs.tsx`.
- **V1.2** Add `<defs>` patterns (`bim-roof-hatch` … `bim-stripe-30`) to the canvas SVG.
- **V1.3** Floorplan opening rendering: dispatch on `opening_kind` for fill pattern + centroid glyph. Door swing arc derives from `swing_side`+`swing`.
- **V1.4** View opening rendering: dispatch by `opening_kind` AND `shape` (circle/polygon/rectangle).
- **V1.5** Component_line OPEN polyline rendering: per-kind glyph at centre of longest segment.
- **V1.6** Height_mark rendering: per-datum small glyph next to the value.

### Wave V2 — Closed-polygon area decoration (the user's "roof needs an area" ask)

- **V2.1** Add closed-region kind inference (`lib/region_kind.ts`): roof / wall-body / gable / ground / unknown, using edge `line_kind`s + topology.
- **V2.2** Render closed polylines with: opaque-enough fill (15 % alpha) + hatch overlay (region kind → pattern from §7) + centroid glyph (region kind → icon).
- **V2.3** Inferred-area provenance: when the auto-classifier ran, surface that with a small "↻ aus Kanten erkannt" badge for 4 s after commit. User can override via the post-draw chip.

### Wave V3 — Cross-cutting decorations + room pictograms (Grundriss)

- **V3.1** Inherited-label visual override: dashed border + ↻ corner badge for any label whose id is in `crossSceneProvenance`.
- **V3.2** Refine-queue visual hooks: height_conflict labels get a red ring; off_axis get a yellow dashed target-angle hint; missing-attribute labels get a `?` corner.
- **V3.3** Room detection on Grundriss: `detectRooms(walls)` over connectivity, render Group D pictogram + light hatch when room kind is known.
- **V3.4** Per-house room kind cache (`bim-db:annotate:rooms:<scope>:<houseKey>:<sceneFile>`) — populated when user picks from a post-detect chip.

---

## 9. Open design decisions (resolve before/during Wave V1)

| Question | Default I'd pick | Why |
|---|---|---|
| Should hatches scale with zoom or stay screen-pinned? | **scale with zoom** | Hatches should feel like part of the drawing, not the UI. Glyphs stay screen-pinned because they need readable text/symbol. |
| What happens if a closed polyline is *very* small (<24 px screen)? | hide both hatch + glyph; keep stroke | Below that scale they just look noisy. |
| Should we render decoration when the label is **being drawn** (preview)? | only stroke + centroid glyph; no hatch | Hatching a moving region is jittery. |
| Show *door swing arc* even when door is in a view (not floorplan)? | no, only floorplan | A door in elevation doesn't have a swing direction. |
| Do partition walls (`thickness_mm < 150`) get a *visible* fill? | yes, but very faint | Otherwise they're invisible at small thicknesses. |
| Should the closed-polyline region kind inference fire automatically, or only on user request? | automatic, but show "↻ aus Kanten erkannt" toast so user can dismiss | Mirrors N2 inferred-area auto-fill cadence — user always overrides via post-draw chip. |
| Are pictograms localized? | German labels for now, glyph is universal | Matches existing UI language. |

---

## 10. Self-audit — is this tracker exhaustive?

- ☑ Bugfix from current state (V0.1, V0.2) — surfaced explicitly so it doesn't get lost.
- ☑ Every label type in `api/types.ts` has an entry in §3 — wall, floorplan_opening, view_opening (all 3 shapes), component_line (open + closed), height_mark, dimensioned_distance, dimension_number.
- ☑ Cross-cutting decorations (provenance, refine, selection, attribute gaps, status) — §4.
- ☑ Grundriss-specific layer (rooms) — §5.
- ☑ Required icon assets enumerated — §6, with explicit "don't add the npm dep yet" decision.
- ☑ Required SVG patterns enumerated — §7.
- ☑ Implementation broken into waves matching past tracker cadence (V0/V1/V2/V3) — §8.
- ☑ Open design questions with defaults — §9.

### Gaps deliberately left open

- **Dark-mode / high-contrast rendering** — not in scope; the editor is currently light-mode only. Revisit when/if the app gets a dark theme.
- **Print/export of annotated scene** — out of scope; the underlying JSON schema doesn't change, so any future print path can re-render from labels.
- **Animations** — no animated decorations beyond the existing toast / refine-queue blink. Keep canvas calm; movement should mean "this just changed".

---

## 11. Implementation checklist (for the /loop driver)

After sign-off, the work fans out as:

1. Apply Wave V0 bugfixes — single commit.
2. Add `lib/glyphs.tsx` (Groups A–C only) — single commit.
3. Add `<defs>` patterns block (§7) — single commit.
4. Wave V1.3–V1.6 — opening + line + height rendering, one commit per type.
5. Wave V2.1 — region inference module + tests.
6. Wave V2.2–V2.3 — closed-polyline rendering + provenance toast.
7. Wave V3.1–V3.2 — cross-cutting decorations.
8. Wave V3.3–V3.4 — Grundriss rooms (gated on V9 readiness).

Per past trackers: in case of doubt, choose the recommended default from §9 and proceed. No backwards-compat shims; all behaviour is rendering-only so old saves render fresh.
