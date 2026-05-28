# Annotation tool — Workflow (W) tracker

**Status:** Revised 2026-05-28 — gaps from self-audit folded in.
Geometric orientation lookup replaces the cardinal-based table;
Phase 4 split into per-scene sub-steps; provenance-rebound +
stale-fact policies explicit. W0 is implementation-ready.
**Owner:** jhoetter
**Predecessors:**
  - [`spec/annotation-tool.md`](annotation-tool.md) — schema, M0–M6
  - [`spec/annotation-ux.md`](annotation-ux.md) — M7–M13 + X1–X11
  - [`spec/annotation-visualisation.md`](annotation-visualisation.md) — V0–V3
  - N1–N8 (structural snap, house-facts, cross-scene auto-load)

**Goal:** stop treating annotation as "open scene → label it in isolation".
Treat it as **constructing one coherent house model** by progressively
locking in *facts* from whichever scene best yields them. The annotator
gets a step-by-step guide that says *what to capture next, where, and
why* — and the system auto-propagates every captured fact to the scenes
where it's useful.

The current pain in one line: a Schnitt doesn't show building width,
but a Grundriss does. If the user had to figure out themselves that
"go label the EG Grundriss first so the Schnitt's vertical Bezugsmaß
can auto-fill width", we've failed.

---

## 1. The mental shift

| Current | Target |
|---|---|
| Pick a scene → label everything visible | Walk a per-house workflow → label the fact that's most reliably captured in *this* scene → system promotes it everywhere it's useful |
| Each scene is an island | Each scene is a window into one house; data flows *between* scenes |
| User memorizes "what should I label next" | Tool tells the user "next: place Bezugshöhe on Ansicht-Süd" |
| Höhenkoten live per-scene; user re-types them | Höhenkoten are house-global (once anchored); each scene shows the subset that's useful, individually toggleable |
| Wall thickness, building extent, roof pitch — re-derived per scene | Promoted to `house_facts` on first capture; auto-fills downstream |
| Orientation is per-scene-tag scope only | Floorplan has a *north arrow*; every Ansicht/Schnitt's orientation maps to a specific floorplan edge |

---

## 2. The house-first workflow (the 6 phases)

Each phase has a **goal** (what we learn about the house), a
**recommended scene** (where it's easiest to capture), an
**auto-completion rule** (when the system advances), and the
**facts promoted** as a result.

### Phase 0 — Inventory

**Goal:** know every scene this house has, what kind it is, and
what part of the house it shows.

**For each scene:**
- `scene_tag` (Grundriss / Ansicht / Schnitt / Sonstiges)
- `scene_orientation` for Ansicht + Schnitt (N / S / E / W) — for
  Schnitt, the axis along which the cut runs is implicit (Schnitt-Nord
  is the section facing north, i.e. cut along the east-west axis)
- `scene_level` for Grundriss (KG / UG / EG / OG / DG / Spitzboden)

**Auto-completion:** every scene in the house's image set has both
`scene_tag` and (if applicable) `scene_orientation` / `scene_level` set.

**Facts promoted:**
- `house_facts.scene_metadata[file] = { kind, orientation, level }`
- `house_facts.workflow.phase = 0` (or advances to 1 on completion)

**Why first:** every later phase depends on knowing which scene is
which. No floor labeling can promote to `ok_ffb_eg` without knowing
"this scene is EG".

### Phase 1 — Vertical anchor (heights)

**Goal:** know every named height in the house's vertical axis:
Bezugshöhe (±0,00), First, Traufe, Gelände, OK FFB EG/OG/DG…

**Recommended scene:** the **most complete Ansicht** (highest count
of already-labeled Höhenkoten or — failing that — the user's choice).
If no Ansicht is complete, fall back to a Schnitt.

**Actions in that scene:**
1. Place Bezugshöhe (`value_mm=0`, no datum) on the ±0,00 line.
2. Place First (`datum='first'`, `value_mm=<First height>`).
3. Place Traufe (`datum='traufe'`).
4. Place Gelände (`datum='gelaende'`).
5. Place OK FFB EG / OG / DG as visible.

**Auto-completion:** `house_facts.heights.bezug_mm` AND
`house_facts.heights.first_mm` are set. The other datums are
encouraged but not required.

**Facts promoted:**
- `house_facts.heights.{first_mm, traufe_mm, gelaende_mm, ok_ffb_*_mm}`
- Per-scene `calibration_per_scene[file]` — when a Bezugsmaß is
  also drawn, px_per_mm is known and downstream scenes can auto-Y
  inherited Höhenkoten (V0.1 / N5 path).

**Why second:** heights are the most universally shared cross-scene
fact. Locking them in once means every other Ansicht/Schnitt opens
pre-populated.

### Phase 2 — Horizontal anchor (footprint)

**Goal:** know the building's outer extent (width × depth) and the
typical wall thickness.

**Recommended scene:** the **EG Grundriss** (or KG if no EG). It's
the easiest to dimension: outer walls run along orthogonal axes, the
extent is just two Bezugsmaße.

**Actions:**
1. Trace all outer walls (the building's perimeter). Wall thickness
   on at least one outer wall — the tool propagates to neighbors via
   the "last-set thickness" default.
2. Place a horizontal Bezugsmaß across the building's full width.
3. Place a vertical Bezugsmaß across the building's full depth.

**Auto-completion:** `house_facts.extent.width_mm` AND
`extent.depth_mm` set; `wall_thickness.outer_mm` set.

**Facts promoted:**
- `house_facts.extent.{width_mm, depth_mm}` (height_mm comes from Phase 1)
- `house_facts.wall_thickness.outer_mm`
- `calibration_per_scene[<EG-Grundriss>]` (M1-H + M1-V both present)

**Why third:** floorplan extent + height = the box that every
Ansicht/Schnitt's Bezugsmaß can derive from. After Phase 2, the
Schnitt's "wie breit ist das Haus?" answer is computable.

**Fallback when no Grundriss exists.** Some houses in the dataset
have only Ansicht/Schnitt scenes. Phase 2 then degrades to:
1. Pick the Ansicht with the clearest horizontal Bezugsmaß
   labeled. That dim's value goes into `extent.width_mm` *or*
   `depth_mm` depending on orientation (only resolvable once
   Phase 3 also runs — so in Grundriss-less houses, Phase 2 and
   Phase 3 effectively merge).
2. The other extent stays `null`. The refine queue surfaces this
   as a soft warning ("Tiefe unbekannt — Schnitt-Bezugsmaß kann
   nicht abgeleitet werden") rather than blocking the workflow.
3. `wall_thickness.outer_mm` falls back to the German-typical
   default (365 mm) on the first wall draw, user-overridable.

### Phase 3 — Orientation graph

**Goal:** wire up which edge of the floorplan is North, so every
Ansicht/Schnitt's `scene_orientation` ties to a *specific* edge of
the building.

**Recommended scene:** the EG Grundriss again.

**Action:** the user picks one of the floorplan's four outer edges
as North (single click on a small "North arrow" handle). The other
three follow (S, E, W). Optionally: pick a rotation angle if the
plan is not orthogonal to the page.

**Auto-completion:** `house_facts.orientation.north_edge_id` set
(the wall label id) OR `north_angle_deg` set.

**Facts promoted:**
- `house_facts.orientation.{north_edge_id, north_angle_deg}`
- Derived: each Ansicht's orientation → which Grundriss edge it
  faces → which extent dimension (width vs depth) it spans.

**Why fourth:** without orientation, a Schnitt's "I should auto-fill
width=12.5m" is wrong half the time — depth might be 8m. With
orientation: Schnitt-Nord sees the wall along the east-west axis,
so its horizontal span = width.

### Phase 4 — Per-scene Bezugsmaße (calibration)

**Goal:** every Ansicht/Schnitt has at least one horizontal AND one
vertical reference dim (`is_reference=true`), so homography is
computable per scene AND inherited Höhenkoten land at the right Y.

**Sub-step ordering, per scene (required, in this order):**

**4.a — Anchor the Bezugshöhe (±0,00) in this scene.**
The Bezugshöhe is per-scene: First = +12,5 m is a house-global
*value*, but the *pixel-Y at which the ±0,00 line is drawn* is
unique to each scene image. Until the user clicks the ±0,00 line
in this scene, the Y of every inherited height_mark is unknown.
- If the scene already has a Höhenkote with `value_mm === 0`, this
  sub-step is implicitly complete.
- Otherwise the guide preselects the `height_mark` tool, with the
  value input pre-set to `0` and locked. First click on the canvas
  places the Bezugshöhe.
- Immediately after Phase 4.a fires, every inherited
  height_mark's Y is re-computed via
  `bezug_y_px + (datum_value_mm × px_per_mm)` if calibration is
  available, or stays at the V0.1 placement otherwise.

**4.b — Place a horizontal `is_reference` dim.** Value pre-fills
via the lookup in §4.3 (geometry-based, see fix).

**4.c — Place a vertical `is_reference` dim.** Value pre-fills as
`heights.first_mm − heights.gelaende_mm` (visible height) when both
are known, else `heights.first_mm`, else null (user types).

**Auto-completion:** for every Ansicht + Schnitt:
`calibration_per_scene[file]` set AND a Bezugshöhe exists in the
scene's height_marks.

**Facts promoted:**
- Per-scene calibration (px_per_mm, computed from 4.b + 4.c).
- Per-scene Bezugshöhe Y position → unlocks correct Y placement
  for every inherited height_mark in this scene.

**Why fifth:** without 4.a, V0.1's auto-applied Höhenkoten land at
viewport centre — useful as a "facts exist" signal but not at the
real location. Without 4.b/4.c, no homography. Phase 4 finishes
the cross-scene pipeline.

**Opt-out for detail / close-up scenes.** A scene whose `scene_tag`
is `sonstiges` OR whose image obviously shows a partial view (a
window detail, a chimney close-up) cannot carry a whole-building
Bezugsmaß. The guide provides a per-scene "Diese Szene zeigt nur
einen Ausschnitt — Phase 4 überspringen" toggle that excludes it
from the completion predicate.

### Phase 5 — Detail labeling (freeform)

**Goal:** capture everything else — openings, walls per scene,
component lines (First / Traufe / Gelände run as drawn lines),
height_marks beyond the named datums, dimension_distances beyond
the references, etc.

**No recommended order.** The user can go scene-by-scene. The
WorkflowGuide quietly tracks coverage per scene (% of expected
label types present) and flags scenes that look thin.

**Auto-completion:** never — Phase 5 is "good enough" by user
declaration, not by schema.

---

## 3. Cross-scene data flow

### 3.1 What's house-wide, what's per-scene?

| Category | Scope | Where it lives | Promoted via |
|---|---|---|---|
| Heights (First / Traufe / Gelände / OK FFB *) | **house-global** | `house_facts.heights` | save() → promoteToFacts (N4) |
| Extent (width / depth / height) | **house-global** | `house_facts.extent` | save() → promoteToFacts |
| Wall thickness (outer / inner typical) | **house-global** | `house_facts.wall_thickness` | save() → promoteToFacts |
| Calibration (px_per_mm) | **per-scene** | `house_facts.calibration_per_scene[file]` | save() → computeSceneCalibration |
| Orientation graph (N edge / N angle) | **house-global** | `house_facts.orientation` | Phase 3 dedicated UI |
| Scene metadata (kind / orientation / level) | **per-scene** | `house_facts.scene_metadata[file]` | save() — every scene |
| Openings catalog (sizes, types) | **house-global** | `house_facts.openings_catalog` | save() — accumulated counts |
| Rooms (per floor) | **per-floorplan** | `house_facts.rooms_per_level[level]` (V9, future) | Grundriss save |
| The labels themselves | **per-scene** | scene's `labels.json` | save() — primary write path |

### 3.2 Inheritance rules

A new scene opens. The system asks: "which facts from house_facts
should pre-populate?"

Rules:
- **Heights:** all known house heights *that are missing from this
  scene's height_marks* are injected (V0.1). Inherited HMs render
  with the ↻ dashed-purple decoration (V3).
- **Bezugsmaß suggestions:** when the user draws an `is_reference`
  dim_distance with no value, the inline value input pre-fills with:
  - horizontal dim, Ansicht/Schnitt orientation known → width_mm or
    depth_mm (from orientation lookup)
  - vertical dim, Ansicht/Schnitt → height_mm (= first_mm − 0 or
    first_mm − gelaende_mm)
  - horizontal dim, Grundriss → width_mm or depth_mm (whichever
    axis the dim aligns with)
- **Wall thickness:** new wall's `thickness_mm` defaults to
  `house_facts.wall_thickness.outer_mm` when the wall is on the
  outer perimeter, otherwise `inner_mm`.
- **Openings catalog:** when the user starts a new opening, the
  attributes panel shows the catalog as a quick-pick list ("there
  are 6 windows of 1200×1200 in this house — same?").

### 3.3 Provenance-rebound: what happens when the user edits an inherited value

An inherited fact carries provenance (X5 / V3: ↻ badge, "from
scene X"). When the user edits the local value, three behaviors
are possible. The system applies them in this order:

| Local edit shape | Behavior | Rationale |
|---|---|---|
| User changes a *non-named* attribute (e.g. moves the height_mark anchor, changes status) | Local-only. Provenance badge stays. House fact unchanged. | Position/status are scene-local concerns. |
| User changes the *value* (`value_mm`, `datum`) on an inherited label, by ≤1 % vs. house fact | Local-only. Provenance badge stays. No house update. | Rounding / micro-adjust isn't a semantic change. |
| User changes the value by >1 % vs. house fact | **Local override.** The label loses its ↻ inherited badge (it's now a local truth). The house fact stays at the old value. The N8 refine queue fires `height_conflict` to surface the divergence. | The user might be correcting a typo upstream OR adding a scene-specific deviation; we can't tell. Surface for review rather than silently propagating. |
| User explicitly clicks "↻ Hauswert aktualisieren" on the inspector | **Promotion.** New value becomes the house fact. All other scenes' inherited copies re-suggest the new value at their next open. | Explicit promotion is a clear "I'm fixing the house, not just this scene" signal. |

Key principle: **automatic edits never flow backward**. House
facts only update via `promoteToFacts` on save (which writes
*new* labels with `is_reference=true`) OR via the explicit
promotion button. A silently-edited local label cannot rewrite
the house behind the user's back.

### 3.4 Stale-fact invalidation: when does the system re-suggest?

A house fact changes (Phase 1 user revises First from +12,5 m to
+12,8 m). Every scene that already accepted the old value is now
showing a stale figure. Policy:

| Trigger | What re-evaluates |
|---|---|
| `save()` of any scene in this house | Re-run completion predicates; if any phase regresses to incomplete (rare — only on label delete), surface as info toast. |
| `promoteToFacts` writes a *changed* value to `house_facts.heights.*` or `extent.*` | Mark all other scenes' inherited copies of that fact as "stale" in their `display.stale_inherited_ids` set. On next open of one of those scenes, the inherited copy renders with an orange dashed border + "Hauswert hat sich geändert: was XXX, jetzt YYY — übernehmen?" inline action. |
| `extent_mismatch` refine kind (W8.2) | Fired on save when an existing `is_reference` dim's value disagrees with the auto-derivation by >5 %. Doesn't auto-fix; surfaces in the refine queue. |
| User opens a scene with `display.stale_inherited_ids` non-empty | Toast: *"N geerbte Werte sind veraltet — siehe Etikett-Liste"*. |

Re-validation explicitly does NOT run on every keystroke or every
React render — only on save() and on scene-open. Cheap to
compute, costly to re-render. The stale set is the only data the
system carries forward between sessions.

### 3.5 Show / hide inherited labels per scene

Inherited Höhenkoten can pile up in scenes that don't need them all
(a Schnitt might not show OK FFB DG). Each scene carries a
`display.hidden_label_ids: string[]` so the user can hide noise
without deleting facts.

Hidden ≠ deleted. The label still lives in the scene's JSON; the
canvas just skips rendering it. Toggle via the label list's
visibility icon.

A separate flag `display.collapsed_groups: ['inherited_heights']`
can collapse whole groups at once.

The `display.stale_inherited_ids` set (per §3.4) is rendered
separately — stale entries override the hidden state so the user
can't accidentally miss a fact that just changed.

---

## 4. The orientation graph (Phase 3 in depth)

The single missing piece that makes cross-scene auto-fill *correct*
rather than 50/50 right.

### 4.1 What we capture

- One edge of the EG Grundriss outer perimeter is selected as
  "the north-facing edge" by the user — we record its **label id**
  (`north_edge_id`) AND its pixel-length AND its perpendicular
  direction in the floorplan's image space.
- From that single edge we derive everything else:
  - The selected edge's pixel length × Phase 2 calibration
    = `face_north_mm` (length of the north-facing wall).
  - The perpendicular edges' pixel length × calibration
    = `face_east_mm` (length of the east-facing wall).
  - `house_facts.extent.width_mm` and `depth_mm` from Phase 2 stay
    untouched — they are the building's two outer dimensions
    *regardless of orientation*. The orientation graph just says
    *which one is which* when a Ansicht/Schnitt asks for "my
    horizontal extent".
- Optionally the user can rotate by `north_angle_deg` for tilted
  scanned plans (rare, but real).

### 4.2 What it tells us

The single useful question: given an Ansicht/Schnitt and its
`scene_orientation`, what mm extent should its horizontal
`is_reference` dim suggest?

The answer is **geometric, not cardinal**. The cardinal name
(north / south / east / west) of an edge is the user's label; the
*which-dimension-of-the-building-this-face-spans* answer comes from
the picked edge's geometry:

```
1. Phase 3 fixes the north edge — a specific wall id on the EG Grundriss.
2. Convert that wall's image vector to a unit direction n̂.
3. Build the orthogonal basis (n̂, ê) where ê is n̂ rotated +90°.
4. For an Ansicht/Schnitt with scene_orientation = o:
     - 'north' or 'south' face → the face's extent runs along ê
       → suggest `face_east_mm` for horizontal Bezugsmaß
     - 'east' or 'west' face → the face's extent runs along n̂
       → suggest `face_north_mm` for horizontal Bezugsmaß
5. The face_*_mm values come from the actual pixel-length of the
   corresponding Grundriss edges, NOT from a hard "width=12.5m"
   assumption.
```

This works correctly when the north-facing edge is the long axis
(typical row-house) AND when it's the short axis (typical gable
house). The orientation graph carries no assumption about which
axis is "long".

For the special case where `extent.width_mm` and `depth_mm` from
Phase 2 differ from the geometric face lengths (e.g. the Phase 2
Bezugsmaß ran across a porch, not the structural footprint), the
geometric face lengths win — they're the ones the Ansicht's wall
visibly spans.

### 4.3 The orientation lookup function (corrected)

```ts
// Returns the mm extent the scene's horizontal Bezugsmaß should suggest.
horizontalExtentForScene(scene, houseFacts):
  if scene.scene_tag === 'grundriss':
    // Grundriss horizontal extent depends on dim orientation, not on
    // a cardinal name: the dim's image angle vs. the north edge tells
    // us whether it's along n̂ (depth-axis) or ê (width-axis).
    return projectDimToFaceLength(scene, houseFacts.orientation)
  if scene.scene_tag in ['ansicht', 'schnitt']:
    o = scene.scene_orientation
    if o is null: return null  // Phase 0 incomplete
    if houseFacts.orientation == null: return null  // Phase 3 incomplete
    return o in ['north', 'south']
      ? faceLengthAlong('east', houseFacts.orientation)   // ê axis
      : faceLengthAlong('north', houseFacts.orientation)  // n̂ axis
  return null

// Returns the mm length of the building face oriented along axis ('north' or 'east').
faceLengthAlong(axis, orientation):
  edge = axis === 'north'
    ? orientation.north_edge_label  // the wall picked in Phase 3
    : orientation.east_edge_label   // the perpendicular wall, derived
  // The wall's pixel length × the EG-Grundriss px_per_mm.
  return pixelLength(edge.geometry) / orientation.px_per_mm
```

Two helpers stay separate so callers can mock the wall geometry
without spinning up a full HouseFacts.

### 4.4 The orientation indicator on canvas

### 4.5 The orientation indicator on canvas

When Phase 3 is set:
- Every Ansicht/Schnitt shows a small compass widget in the lower-
  right showing the scene's facing direction.
- Every Grundriss shows a north arrow at the same position.
- The compass / north arrow is clickable on the EG Grundriss as a
  shortcut to re-pick the north edge.

---

## 5. Auto-derivation table (the "what fact fills what" matrix)

The full mapping the WorkflowGuide consults when a scene opens.

All formulas below resolve via `horizontalExtentForScene` /
`verticalExtentForScene` (§4.3) — the cardinal name of the scene
is just an input to the geometric lookup, never a direct lookup
key. The "What auto-suggests" column shows the *resolved* value
for the typical "north edge = long axis" case; for gable-facing
orientations the formula swaps width ↔ depth automatically.

| In scene | About to draw / set | What auto-suggests | When |
|---|---|---|---|
| any Ansicht | new Höhenkote with `datum='first'` | `heights.first_mm` | Phase 1 done |
| any Schnitt | new Höhenkote with `datum='ok_ffb'` (scene_level=og) | `heights.ok_ffb_og_mm` | Phase 1 done + scene_level set |
| any Ansicht/Schnitt | new Bezugshöhe (`value_mm=0`) | locked to 0; only Y is user-placed | always |
| any Ansicht ('north' or 'south' facing) | horizontal `is_reference` dim_distance | `faceLengthAlong('east')` — the building's east-west extent | Phase 2+3 done |
| any Ansicht ('east' or 'west' facing) | horizontal `is_reference` dim_distance | `faceLengthAlong('north')` — the building's north-south extent | Phase 2+3 done |
| any Schnitt ('north' or 'south' facing) | horizontal `is_reference` dim_distance | `faceLengthAlong('north')` (Schnitt cuts perpendicular to the face it names) | Phase 2+3 done |
| any Schnitt ('east' or 'west' facing) | horizontal `is_reference` dim_distance | `faceLengthAlong('east')` | Phase 2+3 done |
| any Ansicht/Schnitt | vertical `is_reference` dim_distance | `heights.first_mm − heights.gelaende_mm` (visible building height) else `heights.first_mm` | Phase 1 done |
| any Grundriss | horizontal `is_reference` aligned with the n̂ axis | `faceLengthAlong('east')` | Phase 3 done |
| any Grundriss | vertical `is_reference` aligned with the ê axis | `faceLengthAlong('north')` | Phase 3 done |
| any Grundriss | new outer wall | `wall_thickness.outer_mm` (else 365 mm German default) | Phase 2 done OR fallback |
| any Grundriss | new inner wall | `wall_thickness.inner_mm` (else 175 mm German default) | Phase 2 done OR fallback |
| any Ansicht/Schnitt | new window opening | `openings_catalog` quick-pick | Phase 5 ongoing |
| any Grundriss | new floor-plan opening on outer wall | `openings_catalog` quick-pick + window default size | Phase 5 ongoing |

---

## 6. The WorkflowGuide panel (UI)

### 6.1 Where it lives

A new collapsible panel at the **top of the right rail**, above
the existing scene-tag picker. Stays open by default until the
user has completed Phase 5 in this house, at which point it
auto-collapses (still toggleable from the cheatsheet `?` page).

### 6.2 What it shows

Per phase (current = expanded, others = single-line summary):

```
┌─ Schritt 1 von 6 — Szenen-Inventar ─────────────┐
│ ✓ 4 Szenen klassifiziert                         │
│ ⚠ 1 Szene ohne Orientierung:                     │
│   → [house-21-elevation-rechte-giebel.jpg]       │
│   → Klick: zur Szene wechseln                    │
├─ Schritt 2: Höhenkoten anker — Vorschlag: ──────┤
│   → [house-21-elevation-sued.jpg]                │
│   Schritt 3–6 wartet                             │
└─────────────────────────────────────────────────┘
```

Each unfinished step shows:
- What needs to happen
- A button to jump to the recommended scene
- An "Already done elsewhere — skip" override

Each finished step shows:
- A checkmark + summary of what was captured
- Click to expand (audit / re-edit)

### 6.3 Recommend-a-scene logic

For Phase 1 (height anchor): pick the Ansicht with the most
existing height_marks. Tiebreaker: alphabetical filename.

For Phase 2 (footprint): pick the EG Grundriss; if none, fall back
to the lowest-level Grundriss available; if none, KG.

For Phase 3 (orientation): same scene as Phase 2.

For Phase 4: iterate all Ansicht + Schnitt scenes in order
{North, South, East, West} → {Längsschnitt, Querschnitt} as a
deterministic walk so two annotators on the same house make the
same choices.

For Phase 5: no recommendation; user-driven.

### 6.4 Auto-jump behavior

When the user clicks a step's "go here" button:
- Navigate to the recommended scene.
- Pre-select the tool that the step needs (e.g. `height_mark` for
  Phase 1's "Place Bezugshöhe" sub-step).
- Show an inline call-to-action tooltip at the canvas centre:
  *"Klick auf die ±0,00-Linie"*.

### 6.5 Phase visibility outside the editor

The current phase is house-wide state; users picking a house from
the list shouldn't have to open the editor to know "is this house
ready?". Every house card on the house-list page (`/houses` and
`/dataset`) gets a small phase badge:

```
  Phase 0 ━━━━━━━━━━━ ▢▢▢▢▢▢       (no metadata yet)
  Phase 3 ━━━━━━━━━━━ ▣▣▣▢▢▢       (footprint + heights done)
  Phase 5 ━━━━━━━━━━━ ▣▣▣▣▣▢       (only detail labeling left)
  Fertig ━━━━━━━━━━━━ ▣▣▣▣▣▣       (user marked Phase 5 done)
```

Sorting + filtering options on the list pages: "show only houses
where Phase 3 is incomplete" makes batch progress visible without
opening each. (Cross-house batch *editing* stays out of scope per
§11 — only batch *visibility*.)

---

## 7. State machine + persistence

### 7.1 Workflow state shape

```ts
// extends HouseFacts in lib/house_facts.ts
interface WorkflowState {
  schema_version: '1.0';
  phase: 0 | 1 | 2 | 3 | 4 | 5;
  // Completion record per phase (timestamps; null = not done).
  phase_completed_at: {
    inventory: string | null;
    height_anchor: string | null;
    footprint: string | null;
    orientation: string | null;
    bezugsmasse: string | null;
    detail: string | null;
  };
  // Which scene was used to capture each one-shot phase.
  source_scene: {
    height_anchor: string | null;
    footprint: string | null;
    orientation: string | null;
  };
  // Per-phase override: user can skip a step ("I already have this
  // upstream, don't ask me again"). Stored so the guide stays quiet.
  user_skipped: Partial<Record<keyof WorkflowState['phase_completed_at'], boolean>>;
}
```

Persisted alongside other `house_facts` in
`bim-db:annotate:house-facts:<scope>:<houseKey>` (already in
`lib/house_facts.ts` v1.0 schema — just add the `workflow` field).

### 7.2 Completion predicates

Each phase's "am I done" check, evaluated on every save:

| Phase | Predicate |
|---|---|
| Inventory | every scene in `house.images` has `scene_metadata[file].kind != null` AND (if ansicht/schnitt) `.orientation != null` AND (if grundriss) `.level != null` |
| Height anchor | `house_facts.heights.bezug_mm === 0` (a Bezugshöhe exists somewhere) AND `heights.first_mm != null` |
| Footprint | `extent.width_mm != null` AND `extent.depth_mm != null` AND `wall_thickness.outer_mm != null` |
| Orientation | `orientation.north_edge_id != null` OR `orientation.north_angle_deg != null` |
| Bezugsmaße | every Ansicht + Schnitt scene has `calibration_per_scene[file]` |
| Detail | never auto-completes; user marks done |

Save() re-runs the predicates after `promoteToFacts`. The first
phase whose predicate flips from false → true gets a phase-completion
toast: *"✓ Schritt 2 erledigt — Höhenkoten sind jetzt im ganzen Haus
verfügbar."*

### 7.3 The phase pointer

`workflow.phase` = first phase whose predicate is false. Never
moves backwards (a phase that becomes incomplete via a delete
doesn't reset the workflow — the user can still re-add). Phase 5 is
the terminal state and is "active" forever (until user closes the
guide).

---

## 8. Implementation waves

Same cadence as past trackers: each wave type-checks, builds,
commits, pushes.

### Wave W0 — State machine foundation (the next implementation)

This wave is intentionally pure-data + pure-function: zero UI, zero
behavior changes. It lays the typed scaffolding the later waves all
hang from. Acceptance = HouseFacts loads back the new fields, the
predicate evaluator returns sensible values for an existing house,
and `save()` updates the phase pointer without rendering anything
new yet.

**W0.1 — Extend `lib/house_facts.ts` with the workflow state.**

```ts
// Add to HouseFacts interface:
interface HouseFacts {
  schema_version: '1.0';
  extent: { … };
  heights: { … };
  wall_thickness: { … };
  openings_catalog: [ … ];
  calibration_per_scene: { … };
  scene_metadata: { … };
  // NEW:
  orientation?: OrientationGraph | null;
  workflow?: WorkflowState | null;
  derived_facts?: Record<string, FactEntry>;
}
```

`loadHouseFacts` reads old caches without these fields by defaulting
them to `null` / `{}`. `saveHouseFacts` writes the new shape.
No migration script needed — readers tolerate missing fields.

**W0.2 — `lib/workflow.ts`: pure predicates + phase advance.**

```ts
export const PHASES = ['inventory', 'height_anchor', 'footprint',
                       'orientation', 'bezugsmasse', 'detail'] as const;
export type PhaseId = typeof PHASES[number];

export interface PhaseConfig {
  id: PhaseId;
  order: 0|1|2|3|4|5;
  label_de: string;
  // Pure predicate — facts + every scene file the house has.
  isComplete: (facts: HouseFacts, scenes: SceneSummary[]) => boolean;
  // Pure recommender — returns scene file or null.
  recommends: (facts: HouseFacts, scenes: SceneSummary[]) => string | null;
}

export function currentPhase(facts: HouseFacts, scenes: SceneSummary[]): PhaseId {
  for (const p of PHASE_CONFIGS) {
    if (!p.isComplete(facts, scenes)) return p.id;
  }
  return 'detail';
}

export function advanceWorkflow(
  prev: HouseFacts, next: HouseFacts, scenes: SceneSummary[]
): { newFacts: HouseFacts; advancedTo: PhaseId | null } {
  // Compares currentPhase(prev) vs currentPhase(next). If advanced,
  // stamps phase_completed_at[new-previous-phase] with nowIso(),
  // returns the changed phase id for a toast.
}
```

Predicates per phase mirror §7.2 exactly. **No** behavioral coupling
yet — UI integration is W1+.

**W0.3 — `save()` calls advanceWorkflow + writes back.**

In `AnnotatePage.save()`, after the existing `promoteToFacts(...)`
call, run:

```ts
const scenes = await fetchSceneSummary(scope, key);   // already exists
const facts = loadHouseFacts(scope, key);
const { newFacts, advancedTo } = advanceWorkflow(facts, /*prev*/facts, scenes);
saveHouseFacts(scope, key, newFacts);
if (advancedTo) {
  addToast(`✓ Schritt ${PHASE_LABELS[advancedTo]} erledigt`, 'success', 4000);
}
```

`prev` here is the facts as they were *before* this save's
`promoteToFacts` ran — capture before, compare after. The toast
fires at most once per save.

**W0.4 — DevTools dump.** A tiny `window.__bimWorkflowDebug =
{ phase, predicates: {...} }` getter in dev only, so we can
inspect state without UI. Removed at W6.

### Wave W1 — Phase 0 (Inventory) UX

- **W1.1** Top-of-rail collapsible WorkflowGuide panel.
- **W1.2** Phase 0 step: "X von Y Szenen klassifiziert". Lists
  unclassified scenes; click → navigate.
- **W1.3** Per-scene: when scene_tag is set but
  orientation/level is missing (and applicable), the rail's
  scene-tag block flashes the picker briefly to draw attention.

### Wave W2 — Phase 1 (Heights) UX

- **W2.1** Phase 1 step: "Höhenkoten ankern". Recommend scene by
  height_mark count.
- **W2.2** "Go here" button → navigates + pre-selects `height_mark`
  tool + flashes the canvas with "Klick auf die ±0,00-Linie".
- **W2.3** Auto-detect Bezugshöhe placement → next sub-step "First
  setzen". Walks through {first, traufe, gelaende, ok_ffb_*}.

### Wave W3 — Phase 2 (Footprint) UX

- **W3.1** Phase 2 step: "Hausgrundriss vermessen". Recommend EG
  Grundriss; fall back to lowest available level.
- **W3.2** Sub-steps: trace outer walls, set thickness, place
  horizontal + vertical Bezugsmaß.
- **W3.3** On thickness set → propagate as `wall_thickness.outer_mm`
  default for all subsequent outer-wall draws.

### Wave W4 — Phase 3 (Orientation) UX + data

- **W4.1** Extend `house_facts.orientation` with:
  ```ts
  interface OrientationGraph {
    // Which EG-Grundriss wall the user picked as north-facing.
    north_edge_label_id: string | null;
    // Optional manual rotation when the floorplan isn't orthogonal.
    north_angle_deg?: number | null;
    // Derived (cached) — px_per_mm of the Grundriss the edge was picked on.
    source_grundriss_file: string;
  }
  ```
- **W4.2** Floorplan canvas: render four edge-pickers on the EG
  outer perimeter. Click one → "this is north". The other three
  follow automatically.
- **W4.3** Compass widget in lower-right of every Ansicht/Schnitt
  showing the current scene's facing direction (driven from
  scene_orientation + the picked edge geometry).
- **W4.4** Implement `faceLengthAlong(axis, orientation)` per §4.3,
  consuming the picked edge's actual pixel length. **Critical**:
  the formula is geometric, not cardinal — verify on a house where
  the north edge is the SHORT axis (gable-facing-N case) that the
  Ansicht-Nord horizontal Bezugsmaß suggests `depth`, not `width`.

### Wave W5 — Phase 4 (Calibration) UX

- **W5.1** `lib/auto_extent.ts`: `horizontalExtentForScene(...)` and
  `verticalExtentForScene(...)` per §4.3 (geometric formula, NOT
  cardinal lookup table).
- **W5.2** Dim-distance value-input pre-fills from those helpers
  when the dim is `is_reference=true` and the scene context allows
  derivation.
- **W5.3** Pre-filled values render with the X5 provenance badge
  ("↻ aus Nordkante = 12,5 m").
- **W5.4** WorkflowGuide enforces the sub-step order per scene:
  4.a (Bezugshöhe) before 4.b/4.c (dims). Until 4.a completes,
  inherited Höhenkoten render in their V0.1 fallback positions
  (viewport centre) with the X5 stale badge until calibration is
  established.
- **W5.5** "Detail-Ausschnitt" toggle per scene that excludes it
  from the Phase 4 completion predicate (§Phase 4 opt-out).

### Wave W6 — Phase 5 (Detail) — passive

- **W6.1** Per-scene coverage heuristic: count expected label types
  (walls for Grundriss, openings for Ansicht/Schnitt, height_marks
  for Ansicht/Schnitt) vs. observed. Surface a coverage bar.
- **W6.2** Phase 5 step never auto-completes; user clicks "Fertig
  für dieses Haus" to mark.

### Wave W7 — Show / hide inherited + stale labels

- **W7.1** Extend per-scene metadata with
  `display.hidden_label_ids: string[]`. Stored in the scene's
  labels.json file.
- **W7.2** Label-list rows get a visibility eye icon. Toggling
  hides/shows on canvas; label stays in JSON.
- **W7.3** A single "↻ Geerbte ausblenden" button per scene to
  hide all inherited Höhenkoten at once.
- **W7.4** Per §3.4 implement `display.stale_inherited_ids` —
  written by `promoteToFacts` whenever a house-fact value changes,
  rendered on scene open as an orange dashed border + "Hauswert
  hat sich geändert — übernehmen?" inline action.

### Wave W8 — Refine queue integration

- **W8.1** New refine kind: `workflow_skipped` — surface scenes
  that the workflow recommended but the user navigated away from
  without completing the sub-step.
- **W8.2** New refine kind: `extent_mismatch` — when an `is_reference`
  dim's value disagrees with the derived `extent.*_mm` by > 5 %.

### Wave W9 — House overview + cross-house phase visibility

- **W9.1** A new "House map" page (or sidebar widget) showing the
  EG Grundriss as a small thumbnail with each Ansicht/Schnitt
  pinned to its facing edge. Click a pin → open that scene.
- **W9.2** Coverage badges per scene (% labeled, calibration state)
  visible from the overview.
- **W9.3** Per §6.5, render a phase badge on every house card on
  `/houses` and `/dataset` so cross-house progress is visible
  without opening the editor. Add list-level filters: "Show only
  houses where Phase X is incomplete".

---

## 9. Open design decisions

| Question | Default I'd pick | Why |
|---|---|---|
| Should the WorkflowGuide be modal (block the canvas) or passive (side panel)? | **passive side panel** | Modal interrupts power users who know what they want; passive nudges without blocking. |
| What if the user starts on Phase 5 and skips 0–4? | Allow it. The guide reorders to "what's blocking what" rather than enforcing order. | The user's "I'll just label this" instinct should not be blocked; we just keep telling them what's still missing. |
| Should phase completion trigger automatic navigation to the next phase's scene? | No. Show a "Weiter zu Phase 3" button, but don't auto-navigate. | Auto-navigation is jarring and steals user agency. |
| Multi-user / concurrent editing? | Out of scope (single-user app). | House-facts cache is localStorage; concurrent writes would corrupt it. Address only when the app becomes multi-user. |
| What happens when the user manually edits `house_facts` (e.g. by saving a different value)? | Last-write-wins, with a toast: *"Du hast First von +12,5 m auf +12,3 m geändert — Hauswert aktualisiert"*. | Manual edit is intentional; we shouldn't second-guess. The refine queue's `height_conflict` (N8) already flags cross-scene divergence. |
| Should derived extents be PROMOTED to is_reference dims automatically? | No — only *suggested*. User must explicitly accept (or override) the value. | Auto-creation makes the homography depend on a fact chain the user didn't approve. Suggestions stay polite. |
| What about houses where the floorplan is rotated 30° on the page (not orthogonal)? | Phase 3's `north_angle_deg` handles it. Phase 3's north-arrow picker offers a "rotate manually" mode. | Rare but real; tilted scanned plans are common. |
| Should Phase 1 require ALL named datums, or just Bezug + First? | Just Bezug + First to advance. Others are *recommended* but skippable. | Some houses don't have a labeled Traufe in any view; we shouldn't block the workflow on it. |
| What happens when the picked north edge is later renamed / deleted? | `north_edge_label_id` becomes orphaned → `house_facts.orientation` reverts to null → Phase 3 regresses to "incomplete" → guide re-surfaces step. | Edge-id drift is rare; cleanest semantics are "the edge is gone, the choice is gone". |
| What threshold defines an "inherited-value edit" as a divergence vs a typo? | 1 % (per §3.3). | Round-tripping mm values across scenes can drift by ±5 mm on +12 500 mm; 1 % (= 125 mm) catches genuine disagreement without firing on rounding. |
| Should stale-fact toasts auto-fire on every scene open, or only when the user explicitly looks at the affected label? | Toast once per scene-open when any stale label is present; the individual labels each carry the orange dashed border for inspection. | A toast plus a visual marker is the cheapest "you have updates" signal. |
| Phase 4.a (Bezugshöhe-per-scene) — can it inherit Y from Phase 1's source scene? | No. Pixel coordinates are per-image; cross-scene Y inheritance requires a homography we don't have until 4.b/4.c land. | The user clicks the ±0,00 line each time — fast, one click, removes the entire "where is this label?" guessing game. |

---

## 10. Extensibility — adding new phases / facts later

### 10.1 Phases are config, not hard-coded

`lib/workflow.ts` exports a `PHASES` array. Each phase is:

```ts
{
  id: 'height_anchor',
  order: 1,
  label_de: 'Höhenkoten ankern',
  recommends: (facts, scenes) => /* recommended scene id or null */,
  isComplete: (facts) => boolean,
  promotes: ['heights.first_mm', 'heights.traufe_mm', ...],
  sub_steps: [/* …optional walkthrough */],
}
```

Adding a phase = appending a config object. No JSX changes.

### 10.2 House facts are typed but extensible

`HouseFacts.derived_facts: Record<string, FactEntry>` is a generic
key-value store with provenance:

```ts
interface FactEntry {
  value: unknown;
  sources: string[];        // labelIds that contributed
  computed_at: string;
  algorithm?: string;       // 'min(width_mm,depth_mm)' etc.
}
```

This is where "roof pitch", "gable count", "chimney count" etc.
land later without touching the core schema.

### 10.3 Auto-derivation rules are data, not code

`lib/auto_extent.ts` (Phase 4) exposes the derivation table as a
typed array. New rules append; no switch statements grow.

---

## 11. Self-audit (post-revision)

- ☑ Six phases cover what the user listed. (§2)
- ☑ Cross-scene flow is documented per fact category, *with*
  provenance-rebound semantics (§3.3) and stale-fact
  invalidation policy (§3.4). Edits never silently flow backward
  into house_facts; rebroadcasts only happen on explicit promote.
- ☑ Orientation graph fix: §4.3's formula is **geometric**, not
  cardinal-name-based, so houses where the north edge is the
  short axis behave correctly.
- ☑ Phase 4 is broken into 4.a (Bezugshöhe-per-scene) + 4.b/4.c
  (dims) with an explicit per-scene opt-out for detail/close-up
  scenes that can't carry whole-building Bezugsmaße.
- ☑ Phase 2 has a Grundriss-less fallback path.
- ☑ Phase visibility outside the editor — §6.5, house-list badges.
- ☑ W0 is concretely implementable as the next step: schema diff
  + pure functions + save() integration, zero UI behavior change.
- ☑ Open questions with defaults so /loop can resolve without
  blocking. (§9)

### Gaps deliberately left open

- **Filename auto-classification for Phase 0** — explicitly
  deferred per user request. The pattern is straightforward
  (`*elevation-{cardinal}*` → ansicht + orientation;
  `*floorplan-{kg|eg|og|dg}*` → grundriss + level) and can be
  added as a "Phase 0 booster" after the rest of the workflow is
  shipped without changing any of W0–W9 semantics.
- **Multi-building per house key** — out of scope.
- **Concurrent multi-user editing** — out of scope.
- **Cross-house fact transfer** ("same model as house-22, copy
  its facts") — out of scope; might revisit if the catalog grows
  to >100 houses where exact copies are common.
- **Automatic phase regression on delete** — once a phase
  completes, deleting the source label doesn't reset it. Treated
  as "the user knows what they're doing"; the N8 refine queue
  surfaces the resulting inconsistency.
- **Per-window 3D reconciliation** (matching the same window across
  Ansicht-Süd and Grundriss-EG via reconstructed position) —
  future; depends on this tracker landing first.
- **Sanity range checks on extents/heights** (flag >20 m First on
  an SFH, etc.) — light-touch, can ride on top of W8.
