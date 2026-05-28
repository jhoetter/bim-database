# Annotation tool — Workflow (W) tracker

**Status:** Draft (2026-05-28). Pre-implementation.
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
computable per scene.

**Recommended scene:** open each Ansicht/Schnitt in turn. With
Phase 1+2+3 done, every Bezugsmaß has a *suggested value*:
- Horizontal Bezugsmaß → looks up orientation → suggests width_mm
  or depth_mm from house_facts.
- Vertical Bezugsmaß → suggests `first_mm − gelaende_mm` (the
  building's visible height) or `first_mm` (if measured from ±0,00).

**Auto-completion:** for every Ansicht + Schnitt:
`calibration_per_scene[file]` set.

**Facts promoted:**
- Per-scene calibration → downstream cross-scene Y-fill of
  inherited Höhenkoten lands at the *right* y-coordinate (V0.1
  N5 already supports this).

**Why fifth:** until calibration is per-scene, the V0.1
auto-applied Höhenkoten land at viewport centre, not the real
location. Phase 4 finishes the cross-scene pipeline.

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

### 3.3 Show / hide inherited labels per scene

Inherited Höhenkoten can pile up in scenes that don't need them all
(a Schnitt might not show OK FFB DG). Each scene carries a
`display.hidden_label_ids: string[]` so the user can hide noise
without deleting facts.

Hidden ≠ deleted. The label still lives in the scene's JSON; the
canvas just skips rendering it. Toggle via the label list's
visibility icon.

A separate flag `display.collapsed_groups: ['inherited_heights']`
can collapse whole groups at once.

---

## 4. The orientation graph (Phase 3 in depth)

The single missing piece that makes cross-scene auto-fill *correct*
rather than 50/50 right.

### 4.1 What we capture

- One edge of the EG Grundriss outer perimeter is labeled "north".
  By convention the system numbers the other three:
  - North edge → 0° (compass)
  - East edge → +90° (clockwise)
  - South edge → +180°
  - West edge → +270°
- Optionally the user can rotate the whole graph by a `north_angle_deg`
  (useful for tilted house plans).

### 4.2 What it tells us

For any Ansicht / Schnitt:
- The scene's `scene_orientation` (N / S / E / W) names which *face*
  of the building it looks at.
- The face is one of the four floorplan edges.
- The face's length-on-floorplan = that edge's pixel length, which
  via Phase 2's calibration converts to mm.
- So: Ansicht-Nord's horizontal extent in mm = the length of the
  north edge of the EG Grundriss.
- Equivalently for Schnitt: a Schnitt oriented "north" cuts
  perpendicular to the east-west axis, so its horizontal extent =
  the building's depth, not width.

### 4.3 The orientation lookup function

```ts
// Returns the mm extent the scene's horizontal Bezugsmaß should suggest.
horizontalExtentForScene(sceneTag, sceneOrientation, houseFacts):
  case sceneTag of
    'grundriss': null  // Grundriss extents come from direct measurement
    'ansicht' | 'schnitt':
      ifnorthAxis(sceneOrientation): houseFacts.extent.width_mm
      ifeastAxis(sceneOrientation):  houseFacts.extent.depth_mm
      ...
```

Where `ifnorthAxis(o) = o in ['north', 'south']` (those views show
the north-south face which spans east-west, so its length is the
*east-west* extent — `width_mm` by convention).

### 4.4 The orientation indicator on canvas

When Phase 3 is set:
- Every Ansicht/Schnitt shows a small compass widget in the lower-
  right showing the scene's facing direction.
- Every Grundriss shows a north arrow at the same position.

---

## 5. Auto-derivation table (the "what fact fills what" matrix)

The full mapping the WorkflowGuide consults when a scene opens.

| In scene | About to draw / set | Auto-suggest from house_facts | When |
|---|---|---|---|
| any Ansicht | new Höhenkote with `datum='first'` | `heights.first_mm` | Phase 1 done |
| any Schnitt | new Höhenkote with `datum='ok_ffb'` (scene_level=og) | `heights.ok_ffb_og_mm` | Phase 1 done + scene_level set |
| Ansicht-Süd | horizontal `is_reference` dim_distance value | `extent.width_mm` | Phase 2+3 done |
| Ansicht-Ost | horizontal `is_reference` dim_distance value | `extent.depth_mm` | Phase 2+3 done |
| Schnitt-Nord | horizontal `is_reference` dim_distance value | `extent.depth_mm` | Phase 2+3 done |
| any Ansicht/Schnitt | vertical `is_reference` dim_distance value | `extent.height_mm` (or `first_mm`) | Phase 1+2 done |
| any Grundriss | horizontal `is_reference` (long axis aligned to north arrow) | `extent.width_mm` | Phase 3 done |
| any Grundriss | vertical `is_reference` (short axis aligned to east arrow) | `extent.depth_mm` | Phase 3 done |
| any Grundriss | new outer wall | `wall_thickness.outer_mm` | Phase 2 done |
| any Grundriss | new inner wall | `wall_thickness.inner_mm` | Phase 2 done |
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

### Wave W0 — State machine foundation

- **W0.1** Extend `lib/house_facts.ts` schema with `workflow:
  WorkflowState`. Provide migration default = `{phase: 0, all
  phase_completed_at: null, ...}` when reading old caches.
- **W0.2** `lib/workflow.ts`: pure functions for the completion
  predicates + phase advance, given a HouseFacts + the list of
  known scene files. Unit-testable.
- **W0.3** `save()` calls `recomputeWorkflowState(facts, scenes)`
  after `promoteToFacts`, fires phase-completion toast when phase
  pointer advances.

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

- **W4.1** Extend `house_facts.orientation` schema.
- **W4.2** Floorplan canvas: render four edge-pickers on the EG
  outer perimeter. Click one → "this is north". The other three
  follow automatically.
- **W4.3** Compass widget in lower-right of every Ansicht/Schnitt
  showing the current scene's facing direction (driven from
  scene_orientation + house_facts.orientation).

### Wave W5 — Phase 4 (Calibration) UX

- **W5.1** `lib/auto_extent.ts`: `horizontalExtentForScene(...)` and
  `verticalExtentForScene(...)` — the auto-derivation table from §5.
- **W5.2** Dim-distance value-input pre-fills from those helpers
  when the dim is `is_reference=true` and the scene context allows
  derivation.
- **W5.3** Pre-filled values render with the X5 provenance badge
  ("↻ aus extent.width_mm").

### Wave W6 — Phase 5 (Detail) — passive

- **W6.1** Per-scene coverage heuristic: count expected label types
  (walls for Grundriss, openings for Ansicht/Schnitt, height_marks
  for Ansicht/Schnitt) vs. observed. Surface a coverage bar.
- **W6.2** Phase 5 step never auto-completes; user clicks "Fertig
  für dieses Haus" to mark.

### Wave W7 — Show / hide inherited labels

- **W7.1** Extend per-scene metadata with
  `display.hidden_label_ids: string[]`. Stored in the scene's
  labels.json file.
- **W7.2** Label-list rows get a visibility eye icon. Toggling
  hides/shows on canvas; label stays in JSON.
- **W7.3** A single "↻ Geerbte ausblenden" button per scene to
  hide all inherited Höhenkoten at once.

### Wave W8 — Refine queue integration

- **W8.1** New refine kind: `workflow_skipped` — surface scenes
  that the workflow recommended but the user navigated away from
  without completing the sub-step.
- **W8.2** New refine kind: `extent_mismatch` — when an `is_reference`
  dim's value disagrees with the derived `extent.*_mm` by > 5 %.

### Wave W9 — House overview map

- **W9.1** A new "House map" page (or sidebar widget) showing the
  EG Grundriss as a small thumbnail with each Ansicht/Schnitt
  pinned to its facing edge. Click a pin → open that scene.
- **W9.2** Coverage badges per scene (% labeled, calibration state)
  visible from the overview.

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

## 11. Self-audit

- ☑ Six phases cover what the user listed: scene category, heights
  in Ansicht/Schnitt, walls + Bezug in Grundriss, Bezug in
  Schnitt/Ansicht, rest. (§2)
- ☑ Cross-scene flow is documented per fact category. (§3)
- ☑ Orientation graph is the missing piece, explicitly carved
  out as Phase 3 with its own deep section. (§4)
- ☑ The "wie breit ist das Haus?" question from the prompt has a
  worked answer: §5 row "Schnitt-Nord horizontal Bezug =
  extent.depth_mm".
- ☑ Visibility per scene (show/hide Höhenkoten) is in W7.
- ☑ "Must be extendable" is §10.
- ☑ Implementation waves match past tracker cadence. (§8)
- ☑ Open questions with defaults so /loop can resolve without
  blocking. (§9)

### Gaps deliberately left open

- **Multi-building per house key** — out of scope. Each house key
  is assumed to be one physical building.
- **Concurrent multi-user editing** — out of scope. localStorage
  + single-user app constraints.
- **Cross-house fact transfer** — e.g. "this is the same model as
  house-22, copy its facts" — out of scope; might revisit if the
  catalog grows to >100 houses where copies are common.
- **Automatic phase regression on delete** — once a phase
  completes, deleting the source label doesn't reset it. Treated
  as "the user knows what they're doing" rather than fighting
  them. The N8 refine queue surfaces the resulting inconsistency.
