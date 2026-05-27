# Annotation tool — UX redesign

**Status:** decision document — no implementation yet
**Owner:** jhoetter
**Date:** 2026-05-27
**Predecessor:** [`spec/annotation-tool.md`](annotation-tool.md) — schema, data model, M0-M6 build (all shipped).
**Goal:** turn the working-but-clunky M0-M6 editor into a fast, intuitive labeling tool. Same data model, same JSON output — only the interaction layer changes.

---

## 1. The five themes from user feedback (verbatim → mapping)

| user-said | what it really means | sections that fix it |
|---|---|---|
| "wie kann ich z.B. wand-dicke resizen" | wall thickness is a primary attribute and editing it should be obvious + immediate | §6 (direct manipulation) + §7 (wall-specific) |
| "wie kann ich super schnell arbeiten" | every action one key + one click; defaults learn; multi-select for bulk | §9 keyboard map + §10 default-value learning + §11 multi-select |
| "werden walls auto-snappen?" | snap is the default behaviour, Alt to override; visual snap indicator | §4 (snapping system) |
| "platziere ich windows in walls?" | yes — openings belong to walls (parent relation), snap on placement, render as wall-cut | §8 (window-in-wall semantics) |
| "right sidebar pop/shrink annoying" | the layout never jumps when the inspector opens/closes | §3 (right-rail policy) |

These five are the headlines. Everything else in this document supports them.

---

## 2. Principles (this drives every later decision)

1. **No layout shift.** The canvas's pixel dimensions never change because of UI panel state. Selection → inspector slides over canvas as an overlay; canvas keeps its full width.
2. **Snap by default, opt-out with `Alt`.** Snapping is what makes architectural drawing fast. The user should never have to think "I need to enable snapping now."
3. **Direct manipulation > inspector forms.** If you can see the thing on the canvas, you can drag it. Inspector is for typed values, status, and notes — not for the geometry the user just drew.
4. **Defaults learn.** "Last wall was 365 mm thick" → next wall starts at 365 mm. The annotator only types a value once per session per attribute.
5. **Keyboard parity.** Every mouse action has a keyboard equivalent. The user can run the entire tool from the keyboard if the mouse goes missing.
6. **Hierarchy reflects reality.** Openings *belong to* walls. Height marks *reference* component lines. The relations array already supports this — the UI surfaces it.
7. **One tool, one cursor, one mode.** The active tool is always visible (cursor + toolbar highlight + status bar). Tool transitions are fast (single key) and obvious (instant cursor change + 200 ms tool-flash in the toolbar).
8. **Toleranzen, nicht Heuristik.** When the AI/snap system is uncertain (multiple snap candidates, ambiguous tag, etc.), it shows the user the options instead of guessing.
9. **Undo is the safety net.** Any action that touches a label is undoable. N=200 snapshot stack (raised from M2's N=50). Cmd+Shift+Z is redo (new — M2 didn't have redo).
10. **No modal prompts on the hot path.** `window.prompt()` for "what's the value" blocks the keyboard and is jarring. Replace with inline inputs that pop up next to where the user is working.

---

## 3. Right-rail layout policy (the single biggest UX win)

### The current pain

`AnnotatePage` uses the shared `Shell` component. The Shell's right rail is a flex column that takes layout space — when `rightRail` becomes non-null (a label is selected), the canvas shrinks. When the user deselects, the canvas grows back. **The whole canvas reflows on every selection.** Pan/zoom state survives but the image's CSS size jumps, which feels broken.

### Decision: overlay, not reserved

- **The inspector becomes a floating overlay on top of the canvas**, not a flex sibling. Default 320 px wide, fixed to the right edge, top-pinned, semi-shadowed.
- The canvas keeps its full width through every selection/deselection cycle.
- The overlay does **not** dim the canvas behind it (it's not a modal).
- A small "pin" icon on the overlay header toggles it into "reserved mode" (Figma-style) for users who prefer the predictable layout. Default = overlay.

### Three visibility states

| state | trigger | visual |
|---|---|---|
| **hidden** | nothing selected | no overlay at all |
| **shown** | label selected | overlay slides in from the right (200 ms transform), no canvas reflow |
| **pinned** | user clicked pin icon | overlay becomes a flex sibling (Shell-style); canvas shrinks; persists in localStorage |

### Cross-page behavior

`AnnotatePage` is the only page where this matters. Browse pages (`SyntheticHousePage`, `HousePage`) keep the existing Shell-reserved layout — they're read-only and selection is rare. We don't generalize this overlay to all pages.

### Implementation hint (just to anchor M7 work)

The `Shell` gets a new optional prop `rightRailMode: 'reserved' | 'overlay' | 'overlay-pinnable'` (default `reserved`). `AnnotatePage` opts into `overlay-pinnable`. Pin state lives in localStorage at `bim-db:annotate:rail-pinned`.

---

## 4. Snapping system

### What snaps to what

| while drawing… | snap targets (priority order) | snap radius (screen px) |
|---|---|---|
| **wall start** | wall endpoints, wall midpoints, dimensioned-distance endpoints, image grid (if grid enabled) | 12 px |
| **wall end** | same as start + the **perpendicular projection** onto an existing wall (so two walls meeting at a T-junction land cleanly) | 12 px |
| **dimensioned_distance** start/end | wall endpoints, other dim-distance endpoints, opening corners | 10 px |
| **dimension_number** anchor | midpoint of a dimensioned_distance (auto-suggests the link) | 16 px |
| **floorplan_opening** placement | **wall line** (snaps along the wall axis — this is the "windows in walls" behaviour, §8) | 14 px |
| **view_opening** placement | bottom of a `traufe` component_line; horizontal alignment with other openings | 10 px |
| **component_line** vertex | other component_line endpoints, wall corners | 8 px |
| **height_mark** anchor | left/right edge of the image, vertical alignment with other height marks | 12 px |

### Snap behaviour

- Snap radius is measured in **screen pixels** (constant feel regardless of zoom level), but the snap position is recorded in image pixels.
- Hold `Alt` (Option on Mac) to disable snap temporarily — useful for nudging the geometry just shy of an existing label.
- Snap candidates are evaluated every `pointermove` event. The closest candidate within the per-tool radius wins. Ties broken by priority (table above).
- A snap indicator is rendered at the snap target while the cursor is within radius:
  - **Solid green circle (r=6)** for an endpoint snap
  - **Green crosshair** for a perpendicular projection onto a wall
  - **Green tick-mark on the wall line** for a wall-line snap (openings)
  - **Grey dashed line** showing the alignment guide (for "snap to same y as another opening")

### Hold-Shift constraints (independent of snap)

- **Shift** while drawing a stroke (wall, dim-distance): snap the line to the nearest of 0°/45°/90°/135° relative to image axes.
- **Shift** while dragging a label's handle: constrain motion to the dominant axis (horizontal or vertical, whichever the drag started with).
- **Shift+Alt** combo: no snap, no axis-lock — free-form.

### Visual snap state machine

```
cursor moves            ┌──────────────────┐
─────────────────────►  │ scan for targets │
                        └─────────┬────────┘
                                  ▼
                       any within radius?
                        ┌─────────┴───────┐
                        YES               NO
                        │                 │
                        ▼                 ▼
              render snap glyph    render plain cursor
              + offer to commit    + no snap action
                  position
```

---

## 5. Drawing constraints + live previews (per tool)

| tool | preview during drawing | Shift behaviour | Alt behaviour | Enter | Esc |
|---|---|---|---|---|---|
| wall | orange dashed segment + length+angle HUD + snap glyph | snap to 0/45/90/135° | disable snap | — | cancel |
| dim_distance | as wall + reference flag indicator if `is_reference_default=true` | snap to 0/90° (most refs are axial) | disable snap | — | cancel |
| dim_number | crosshair cursor + ghost text "1,75…" at cursor | — | disable snap-to-distance-midpoint | — | cancel |
| floorplan_opening | orange dashed rect; if snapped to wall, the rect glides ALONG the wall axis only | snap to 1:2 aspect (typical window) | free placement (no wall snap) | — | cancel |
| view_opening | orange dashed rect | snap to vertical alignment with other openings | — | — | cancel |
| component_line | orange dashed polyline + per-segment length+angle HUD | snap each new segment to 0/45/90° relative to the previous | disable snap | finish polyline | cancel polyline |
| height_mark | orange triangle ghost at cursor | snap to same y as the nearest other height_mark | — | — | cancel |
| link | source label glow + target highlight on hover; dashed connector preview | — | — | — | clear staged source |

### Drawing-state HUD

Already exists for dim_distance + component_line in M4. Expand to cover every drawing tool with the same top-right black overlay pattern:

- Pixel angle (°), formatted to 1 decimal place
- Length in pixels + (if a horizontal/vertical snap is active) the implied mm value pre-derived from the nearest reference stroke's pixel-per-mm ratio
- "≈ horizontal/vertical/45°" badge when within 5° of an axis
- "Snap: ‹target type›" line when a snap candidate is captured

---

## 6. Direct manipulation — drag handles per label type

When a label is **selected**, transform handles appear on the canvas. Each handle is a 10 px screen-circle that scales inversely with zoom (stays visually constant). Hover → cursor changes appropriately (`nesw-resize`, `ns-resize`, etc.).

| label type | handles | drag behaviour | hover cursor |
|---|---|---|---|
| **wall** | 2 endpoint handles + 1 thickness handle perpendicular to the wall axis at its midpoint + 1 body region for translate | endpoint = move that end; thickness = grow/shrink wall band; body drag = translate both endpoints together | `move` body, `crosshair` endpoints, `col-resize` rotated to wall normal for the thickness handle |
| **dim_distance** | 2 endpoint handles + body translate | endpoints reshape; body moves the whole stroke (both endpoints) | same |
| **dim_number** | 1 anchor handle + click-to-edit text inline (no inspector trip) | drag anchor moves it; click text → inline `<input>` over the label on the canvas | `move` |
| **floorplan_opening** | 4 corner handles + 1 swing-flip toggle handle (for doors only) | resize the rect; flip handle (click, not drag) toggles `swing_side`; body drag translates along the parent wall axis only (see §8) | corner cursors per quadrant |
| **view_opening** | top-left, top-right, bottom-left, bottom-right (4) + body translate | resize the rect (independent corners since the schema supports polyline edges, but in M3 it's a rect) | as floorplan |
| **component_line** | one handle per vertex + a "+" insertion handle at the midpoint of each segment | drag vertex moves it; click "+" inserts a new vertex mid-segment | `move` per vertex |
| **height_mark** | 1 anchor handle | drag = move | `move` |

### Multi-select handles

When ≥2 labels are selected, a single rotation-locked **bounding-box handle group** appears around the convex hull. 4 corners + 4 edges + body. Drag = translate the whole selection. Edge drags scale uniformly (no per-label distortion).

### Hover affordances

- Hover over a label (any tool) → outline thickens by 1 px + cursor becomes `pointer`. Subtle, no jump.
- Hover over a handle → cursor changes (`move`, `nesw-resize`, etc.).
- Hover over an empty area in `select` tool → cursor stays default arrow.
- Hover over an empty area in a drawing tool → `crosshair`.

---

## 7. Wall thickness — the specific UX

The user called this out explicitly. Three ways to edit, all coexisting:

### A. Drag the thickness handle on the canvas
- A small handle at the midpoint of the selected wall, offset perpendicular by `current_thickness_mm / scale_factor` (so it's drawn at the actual wall edge in image-pixel space).
- Drag perpendicular to the wall axis → live updates `thickness_mm`. The wall band visibly grows/shrinks as you drag.
- Snaps to **standard residential thicknesses** (115, 175, 240, 300, 365, 490 mm) within 5 px of the snap value. Visual: snap tick + value label.

### B. Inspector slider + number input
- The inspector's "thickness_mm" field gets a slider (50-500 mm range, step 5) alongside the number input. Both stay in sync.
- Quick-set buttons for the standard values: `[115] [175] [240] [300] [365]`. One click = set + save the new "last used" default.

### C. Arrow keys when wall is selected
- `←` / `→` decrement / increment by 10 mm. `Shift+←/→` by 50 mm.
- (`↑`/`↓` are reserved for tab-to-prev/next-label.)

### Visualisation

- A wall is rendered as a **band** of half-thickness on each side of the axis line, not as a single thick line. The band is semi-transparent in normal state, fully opaque when the wall is selected.
- At very low zoom, the band collapses to a stroke wider than 1 px so the wall remains visible.

### Default learning

After the first wall the user gives a thickness, every subsequent new wall in the same scene starts at that thickness. Different scene-tag = different last-used. Stored at `bim-db:annotate:defaults` in localStorage.

---

## 8. Windows in walls — the "openings belong to walls" semantic

### What the user expects (and we should deliver)

When the annotator drops a `floorplan_opening` near a wall, the opening should *attach* to that wall. It should glide along the wall axis when dragged, can be resized only along that axis (width), and is conceptually a child of the wall.

### Data model — no schema bump needed

The schema already has `relations: [{other_id, kind}]` with `kind ∈ {labels, belongs_to, references}`. We use `belongs_to`:

```jsonc
{
  "type": "floorplan_opening",
  "id": "...opening-id...",
  "geometry": {"quad": [...]},
  "attributes": {...},
  "relations": [
    {"other_id": "...wall-id...", "kind": "belongs_to"}
  ]
}
```

`belongs_to` is the "parent wall" link. Each opening has at most one. On the wall side, no relation is stored — the parent-side relation suffices, and the UI scans all openings for `belongs_to → wall_id` when it needs the inverse.

### Placement flow

1. User picks the `floorplan_opening` tool.
2. Drag a rectangle on the canvas.
3. As the rectangle's center moves near a wall (within snap radius), the rectangle:
   - Rotates to align with the wall axis
   - Snaps its center to the wall line (perpendicular to wall)
   - Sets `belongs_to` → that wall's id (held as a UI hint, written on commit)
   - Visual: the rectangle gets a magenta border, indicating "attached to wall"
4. If the user drags away from any wall before releasing, the rectangle becomes unattached (no relation), magenta border drops, free placement applies.
5. On release, the opening is created with `belongs_to` if attached.

### Edit constraints when attached

- **Dragging the body**: translates only along the wall axis. Perpendicular drag is rejected.
- **Resizing corners**: the two corner pairs perpendicular to the wall stay aligned to the wall edges; only the two pairs parallel to the wall axis are draggable (resize width).
- **Right-click → "Detach from wall"**: drops the relation, restores free placement + arbitrary rotation.
- **Deleting the wall**: the user is prompted "X attached opening(s) will lose their parent — delete them too?" with `[Just unlink]` (default) and `[Delete openings]` and `[Cancel]`.

### Visualisation

- An attached opening renders as a **cut-out** of the wall band: the wall is rendered minus the opening's footprint, and the opening's rectangle is drawn in its dedicated color.
- A small "↑" arrow on door openings indicates the swing direction (in/out) + a small arc indicates which side.
- An unattached opening renders as a free rectangle (current behaviour).

### Doesn't apply to view_opening (yet)

View openings (windows on an elevation/section) could similarly attach to component_lines like `traufe` (eave) and `ok_ffb` (storey level), so they slide along the facade. **Deferred** to M11 — first ship the floorplan-side semantics that matter most.

---

## 9. Keyboard map (exhaustive)

### Tool switching (always available)

| key | tool |
|---|---|
| `S` | Select |
| `D` | Bemaßte Strecke |
| `N` | Maßzahl |
| `W` | Wand |
| `O` | Öffnung (Grundriss or Ansicht, tag-aware) |
| `L` | Bauteillinie |
| `H` | Höhenkote |
| `K` | Verknüpfen |
| `M` | Messen (read-only — shows pixel length+angle, doesn't save) — **new tool in M7** |

### Drawing-mode keys

| key | effect |
|---|---|
| `Shift` (held) | axis-/angle-lock per tool (§5 table) |
| `Alt` (held) | disable snap, disable axis-lock |
| `Esc` | cancel current draw / clear staged link source / deselect |
| `Enter` | finish polyline (component_line) |
| `Backspace` (during polyline) | remove last vertex |

### Selection / canvas

| key | effect |
|---|---|
| `Tab` | select next label (creation order); wraps |
| `Shift+Tab` | previous label |
| `Cmd/Ctrl+A` | select all |
| `←/→/↑/↓` | nudge selected geometry by 1 px |
| `Shift+←/→/↑/↓` | nudge by 10 px |
| `Cmd/Ctrl+D` | duplicate selected (placed +20 px offset) |
| `Cmd/Ctrl+G` | group selected — **deferred post-M13** |
| `Del` / `Backspace` | delete selected |
| `Cmd/Ctrl+Z` | undo |
| `Cmd/Ctrl+Shift+Z` | redo — **new in M9** |
| `Cmd/Ctrl+S` | save |
| `Cmd/Ctrl+E` | export (open preview / zip) — **new in M7** |

### View

| key | effect |
|---|---|
| `R` | reset view (fit image) |
| `+` / `-` | zoom in / out by 1.25× |
| `Space` (held) + drag | pan (alternative to Shift/right-drag) |
| `1` | zoom to fit |
| `2` | zoom to 100% (1:1 pixel) |
| `]` | toggle left sidebar |
| `[` | toggle right inspector overlay |

### Status changes (selected label)

| key | sets status to |
|---|---|
| `Cmd/Ctrl+1` | readable |
| `Cmd/Ctrl+2` | not_readable |
| `Cmd/Ctrl+3` | missing |
| `Cmd/Ctrl+4` | uncertain |

---

## 10. Default-value learning

### What gets remembered

Stored as a tree in localStorage at `bim-db:annotate:defaults`:

```
synthetic-or-house :  // by scope
  grundriss :         // by scene_tag
    wall:
      thickness_mm: 365
    floorplan_opening:
      opening_kind: "window"
      width_mm: 1000
      swing: "none"
      swing_side: "none"
    dimensioned_distance:
      target_orientation: "horizontal"
      is_reference: true
  ansicht :
    view_opening:
      opening_kind: "window"
      frame_visible: true
    component_line:
      line_kind: "traufe"
    ...
```

### Rules

- Every committed label updates the corresponding leaf with its attributes.
- A newly drawn label of type X reads its initial attributes from this tree (per scope + scene_tag).
- Status is **not** remembered — defaults to `readable` always (the honesty principle from `annotation-tool.md` §4.1).
- A "Defaults zurücksetzen" button in the editor's left sidebar wipes the tree for the current scope+tag.

### Why scope+tag

A `wall` in a `grundriss` scene is a floor-plan wall (typical 240-365 mm). A wall in `ansicht` is a section wall view. Different defaults make sense.

---

## 11. Multi-select + bulk operations

### Activation

- **Rubber-band**: drag on empty canvas in `select` tool. All labels whose center falls inside the rectangle are selected.
- **Shift+click**: toggle individual label in/out of the selection.
- **Cmd/Ctrl+A**: select all labels in the current scene.

### Inspector behaviour with N > 1 selected

- Title shows "N labels selected" with a per-type breakdown (e.g. "3 walls, 2 floorplan_openings").
- Common-attribute editor: any attribute that ALL selected labels carry is editable; setting it updates all of them. Attributes that differ across the selection show as `(multiple)` and clicking reveals a per-label list.
- `status` is always editable (universal).
- Bulk delete works.

### Bulk actions surfaced as buttons

| button | effect | available when |
|---|---|---|
| **Status →** dropdown | set status on all selected | any |
| **Delete** | delete all selected | any |
| **Group** (post-M13) | wrap in a relation `belongs_to` group | any |
| **Linearize** | for multiple dim_distances forming a chain: snap them collinear | ≥2 dim_distances |
| **Same width** | for multiple openings: set width_mm = average | ≥2 same-type openings |
| **Same line_kind** | for multiple component_lines: set all to one kind | ≥2 component_lines |

### Multi-select drag

Drag any label in the selection → translate ALL selected labels by the same delta. Snap behaviour applies to the whole group (snap to the closest target across all moved labels).

---

## 12. Inline editing (no more `window.prompt()`)

The M2/M3 code uses `window.prompt()` for entering dim_number text and height_mark value. Both are jarring (steals keyboard focus, blocks until dismissed, looks like a 1995 browser dialog).

Replacement: **inline floating input** positioned at the label's anchor on the canvas. Appears the instant the label is placed, focused, ready for typing. Tab/Enter commits, Esc cancels and removes the just-created label.

Same pattern for inline editing existing labels (click the text portion of a dim_number → input opens at that location).

---

## 13. Toast / status surface

A bottom-center toast strip (~bottom 24 px of the canvas) shows non-blocking status:

| event | toast |
|---|---|
| label created | `Wand 4,15 m · 365 mm — gespeichert in Cmd+S` (5 s) |
| snap fired | `Snap → Wandende` (1.5 s) |
| link created | `🔗 "1,75" ↔ horizontale Strecke (1750 mm)` (3 s) |
| save success | `✓ Gespeichert (12 Labels)` (2 s) |
| save error | `✗ Speichern fehlgeschlagen: ‹reason›` (sticky until dismissed) |
| undo | `↶ Wand gelöscht widerrufen` (2 s) |
| autosave (when enabled) | `Auto-gespeichert` (1 s, ultra-subtle) |

The toast surface does **not** push content. It overlays on the canvas, fades in/out.

---

## 14. Auto-save (optional, off by default)

Setting in the editor's left sidebar: **"Automatisch speichern alle 30 s wenn ungespeichert"** with a checkbox. Default: off (explicit save remains the contract). When on:

- Every 30 s, if `dirty`, fire a save in the background.
- Toast: subtle "Auto-gespeichert" with 1 s fade.
- Errors fail loudly (red toast, sticky).
- Manual Cmd+S still works and resets the auto-save timer.

---

## 15. Visual language

| element | color/shape | rationale |
|---|---|---|
| selected | red outline `#dc2626` (M3 already) | universal "this is what you're editing" |
| link source (staged) | magenta outline `#a21caf` (M5 already) | distinguishes from selected — link mode is a different intent |
| reference dim_distance | orange + thick + larger ticks (M4 already) | the homography anchors are 1st-class |
| wall | violet `#7c3aed` band (new — band, not line) | distinct from dim_distance which uses green |
| floorplan_opening | orange `#ea580c` rectangle (M3 already), magenta border when attached to wall | attached state is visually distinct |
| view_opening | same orange (M3) | same kind, different scene |
| component_line | cyan `#0891b2` polyline (M3) | distinct from walls |
| height_mark | pink `#be185d` triangle (M3) | rare; small; needs distinct color |
| dim_number | sky `#0ea5e9` circle (M3) | mid-saturation |
| dim_distance | green `#16a34a` (M3) | the workhorse |
| snap target | green circle radius 6 + 1 px halo | matches dim_distance — green = "ground truth alignment" |
| snap alignment guide | grey dashed `#94a3b8` | quieter than the active label |
| hover (not selected) | +1 stroke-width + 30% lighter | feedback without commitment |

### Cursors

| tool | cursor |
|---|---|
| select (empty canvas) | default arrow |
| select (over label) | pointer |
| select (over handle) | per-handle directional (`move`, `nesw-resize`, etc.) |
| any draw tool | crosshair |
| link tool (no source) | cell |
| link tool (source staged) | pointer + tooltip |
| pan (space held or right-drag) | grab / grabbing |
| zoom (wheel) | (no special cursor) |

---

## 16. Performance budget

The canvas should remain interactive at:

- 200 labels per scene (Pinterest-grade noise upper bound)
- 5000×5000 px image (a composite sheet from M0)
- 60 fps pan/zoom on a 2018-era laptop

Strategy:

- SVG stays the rendering layer (current). Native pan/zoom via viewBox. No per-label React state churn during drag — use refs + raw DOM manipulation on the dragged glyph, then commit state on pointer-up.
- Snap-target search is O(N) per pointermove. With 200 labels, that's 200 distance checks per move — fine (~1 ms). If it ever bites, bin-spatial-hash by image-pixel grid.
- Multi-select rubber-band: only AABB tests against label centers — O(N).
- Undo stack stores `Label[]` snapshots, not deltas — `N=200` × `200 labels` × `~200 bytes per label` ≈ 8 MB max. Acceptable for an in-memory tab.

---

## 17. Milestones

Each milestone is a stop-and-show point as before. Tracker (this doc) gets a ✅ + commit SHA when each ships.

### M7 — Right-rail overlay + drag-handles on basic labels (the layout fix) ✅ shipped 2026-05-27

The headline experience win. Two intertwined deliverables:

1. **`Shell.rightRailMode = 'overlay-pinnable'`**: AnnotatePage's right inspector becomes a floating overlay; canvas never reflows. Pin icon toggles reserved mode, persisted in localStorage.
2. **Drag handles** on every existing label type (wall endpoints, dim_distance endpoints, dim_number anchor, opening corners, polyline vertices, height_mark anchor). Body drag for translate. Hover cursor shapes.

Scope cap: M7 does NOT include snap (M8) or wall thickness handle (M9). Just position handles + the overlay.

### M8 — Snap system (the headline interaction win) ✅ shipped 2026-05-27

Implements §4 in full:
- Snap target enumeration per tool
- Screen-pixel-aware snap radius
- Snap indicator rendering (green circles, alignment guides)
- `Alt` to disable snap, `Shift` for axis-lock
- Snap on draw + snap on drag (existing labels snap when moved)

### M9 — Wall thickness handle + slider + arrow keys + redo ✅ shipped 2026-05-27

The wall-thickness UX (§7) in full + redo functionality.

- Perpendicular thickness handle on selected walls
- Inspector slider + standard-thickness quick buttons
- Arrow-key nudges for thickness when wall selected
- Wall renders as a band (current line + perpendicular fill)
- Redo with `Cmd+Shift+Z`; expand undo stack to N=200

### M10 — Windows-in-walls semantics ✅ shipped 2026-05-27

Implements §8 in full:
- `floorplan_opening` snaps to nearest wall on placement
- Opening rotates to wall axis when attached
- `belongs_to` relation added to opening
- Movement constrained to wall axis when attached
- Detach via right-click
- Wall-delete cascade prompt
- Cut-out rendering (wall band minus opening)

### M11 — Multi-select + bulk operations ✅ shipped 2026-05-27

Implements §11:
- Rubber-band selection
- Shift+click toggle
- Cmd+A select all
- Multi-label inspector
- Bulk delete / status / type-specific actions
- Group-drag with shared snap

### M12 — Inline editing + toast surface + auto-save option

- Replace `window.prompt` with inline canvas input for dim_number text + height_mark value (§12)
- Toast surface (§13) for all event types listed
- Auto-save option in sidebar (§14), off by default

### M13 — Defaults-learning + keyboard parity audit

- localStorage `bim-db:annotate:defaults` tree (§10)
- Every existing keyboard shortcut from M2/M3 still works; new keys from §9 added
- "Defaults zurücksetzen" button per scope+tag
- Visible keyboard-shortcut help: `?` opens an in-canvas cheatsheet overlay

---

## 18. Decisions (locked in this tracker)

1. **Overlay is the default for the inspector.** Reserved-space mode is opt-in via pin icon.
2. **Snap is on by default; Alt is the universal "off".** No "snap mode" toggle.
3. **Openings attach to walls via `relations[{kind:"belongs_to"}]`.** No schema bump. Cascade prompt on wall delete.
4. **Wall thickness gets BOTH a handle AND a slider.** Redundancy on this single most-frequent attribute is fine.
5. **No global "draw vs edit" mode.** Tools are always "draw" tools; click on existing label always selects (and switches the active tool to `select` afterwards for the next click).
6. **Defaults learn per scope + scene_tag**, not globally. Walls in a Grundriss differ from walls in an Ansicht.
7. **Auto-save stays opt-in.** Single-user means data loss risk is low; explicit save remains the contract.
8. **Performance target: 200 labels, 5000² px, 60 fps**. No SVG → canvas migration planned; commit on pointer-up.
9. **Undo stack jumps from N=50 → N=200.** Memory budget ≈ 8 MB, acceptable.
10. **`?` key opens a keyboard cheatsheet.** Discoverability for power users.

---

## 19. Open questions

These are real forks I want jhoetter's call on before M7 begins:

- **Touch/tablet support.** Currently mouse-only. Stylus drawing on an iPad is plausibly the future workflow. Defer to post-M13 or design now? Recommendation: **defer**, but keep all event handlers using `PointerEvent` (already true) so touch works degraded but functional.
- **Voice annotations.** Holding a key + speaking would let the user dictate notes/values while keeping eyes on the drawing. Cool but speculative. Recommendation: **defer** until there's a real ask.
- **Symmetry tools.** When labeling a symmetric facade, mirror-paste a label set across an axis. Recommendation: **defer post-M13** — useful but not pain-point-fixing.
- **Templates per house-type.** Catalog houses share standardised dimensions; a "Standard EFH-Grundriss" template that pre-places typical openings might cut labeling time by half. Recommendation: **defer**, design after seeing real-use friction.
- **Visual coverage badge per scene.** Show in the gallery: which scenes are "fully labeled" vs "partial". Trivial to compute (label count + manual mark). Worth doing in M11 or post-M13? Recommendation: **piggyback on M11** since multi-select work touches the labels list anyway.

---

## 20. What's intentionally NOT in this redesign

To prevent scope creep arguments later:

- **CAD-grade wall connectivity** (T-joints, network solving) — too complex; we draw walls independently
- **Real homography (8-DOF)** beyond the affine in M6 — defer until a real consumer needs it
- **In-canvas grid + snap-to-grid** — over-engineering; reference strokes already calibrate everything
- **Layer system** — labels are flat; tag determines visibility filters if we ever need them
- **Versioning of labels** — git is the version system; no in-app diff
- **Auto-detection / pre-labeling from a model** — separate project; this is the *training* tool, not yet the *inference* tool
- **Collaborative editing / locking** — single user (locked decision from `annotation-tool.md` §11)
- **Mobile-first responsive layout** — desktop is the target; the canvas needs real estate

---

## 21. Self-audit (every user concern → section)

| concern | section |
|---|---|
| wall thickness resize is unclear | §7 (handle + slider + arrow keys + standard quick-buttons) |
| how do I work super fast | §9 (keyboard map exhaustive), §10 (defaults learn), §11 (multi-select), §12 (inline edit) |
| do walls auto-snap | §4 (snap system) + §5 (drawing constraints + Shift behaviour) |
| do I place windows IN walls | §8 (window-in-wall semantics, full UX flow + cascade) |
| right sidebar pop/shrink is annoying | §3 (right-rail policy — overlay default, no layout shift) |
| general intuition | §6 (direct manipulation), §15 (visual language), §13 (toasts) |

Items I went beyond the user's prompt for (and why):
- §10 default-value learning — biggest speed win the user implied with "super fast"
- §11 multi-select — natural extension of "fast"
- §14 auto-save — risk mitigation, opt-in so the explicit-save contract holds
- §16 performance budget — must be quantified before snap math goes in
- §18 decisions — keeps the M7-M13 work from re-litigating these
- §20 not-in-scope — preempts "did you forget X?"
- §21 this self-audit — the user explicitly asked the previous tracker to be self-audited

If you read this and something is missing, that's a tracker-completeness bug — flag it and I'll patch the right section before M7 starts.
