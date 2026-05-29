# Keyboard + modifier model

Source of truth for every key and modifier the annotation tool listens to.
The in-app cheatsheet (`?`) renders from this doc. When you change a
binding, change this file too.

## The mental model (3 modifiers + 1 toggle)

| Key | Meaning | Scope |
|---|---|---|
| **`Alt`** *(Option on macOS)* | "Ignore every smart helper for THIS gesture." Bypasses ortho-snap, length-quantize, neighbor-inherit thickness/width, auto-infer kind, post-commit ortho-tidy, the post-draw classifier chip, AND joint-aware drag. | Hold during click/drag/Enter; effect lasts for that gesture only. |
| **`Shift`** | "Force strict assistance." During draw: hard 0°/45°/90°/135° axis lock relative to the detected building axis. During selection: add to / toggle in multi-select. During wall-thickness arrows: 5× step. | Hold during gesture. |
| **`Cmd`/`Ctrl`** | Structural selection + app-level shortcuts. | Hold; `Cmd+letter` for shortcuts. |
| **`Q`** | Persistent global ortho-snap on/off. Scoped to ortho-snap only — does NOT touch length-quantize, neighbor-inherit, etc. (Alt is the broader per-gesture switch; Q is the targeted always-state one.) | Press once to toggle; state in localStorage `bim-db:annotate:adaptive-axis`. |

When `Alt` or `Shift` is held, a small chip appears in the topbar
("Alt · Helfer aus" amber, "Shift · Ortho-Lock" emerald) so the modifier
state is visible at a glance. The chip is driven by document-level
keydown/keyup listeners with a defensive `mousemove` re-sync; window
`blur` and `visibilitychange` reset modifier state so an alt-tab with
Alt held doesn't leave the app stuck.

## Tool selection (single letters, no modifiers)

Letter keys switch tools when no label is selected. When a single label
is selected AND the letter matches a context action for that label type,
the context action fires *instead* of the tool switch (see "Context
reclassify" below).

| Key | Tool |
|---|---|
| `S` | Auswählen (select) |
| `D` | Bemaßte Strecke |
| `N` | Maßzahl |
| `W` | Wand |
| `O` | Öffnung — `floorplan_opening` in Grundriss, `view_opening` in Ansicht/Schnitt |
| `L` | Bauteillinie |
| `H` | Höhenkote |

If the active tool isn't valid for the current scene tag (e.g. Wand in
Ansicht), switching the scene tag falls back to `select` (K11).

## Pending-draw control

| Key | Effect |
|---|---|
| `Esc` | Cancel current pending action (clear pendingStart, pendingPolyline, wall chain anchor, snap, length match, post-draw chip). |
| `Enter` | Commit polyline — fires for `component_line` (≥2 vertices) and `view_opening` shape=polygon (≥3 vertices). |
| `Backspace` | Inside a pending polyline: remove the LAST placed vertex. Otherwise: delete selection. |

## Selection actions (label-aware)

| Key | Effect |
|---|---|
| `Click` | Replace selection. |
| `Shift+Click` | Toggle this label in multi-select. |
| `Cmd/Ctrl+Click` | Select every label in the same connectivity component (M1.4). |
| `Drag empty area` | Rubber-band multi-select. |
| `Double-click wall body` | Split wall at click point (M1.3). |
| `Double-click inside closed region` | Select every wall forming that region. |
| `Cmd/Ctrl + A` | Select all. |
| `Delete` | Delete selection. `Backspace` also deletes — unless a polyline is pending (then it pops the last vertex). |

### Context reclassify (when one label is selected)

Letters fire reclassify-on-selection BEFORE the tool-switch fallback —
so pressing `D` with a `component_line` selected reclassifies to
`dachschraege` without also switching to the dim tool.

**Opening selected** (`floorplan_opening` or `view_opening`):

| Key | Kind |
|---|---|
| `F` | Fenster (window) |
| `T` | Tür (door) |
| `G` | Gaube (dormer) — floorplan: "other" |
| `D` | Dachfenster (skylight) — floorplan: "passage" |
| `A` | Tor (garage_door) |
| `Z` | Sonstige (other) |

**Component line selected:**

| Key | line_kind |
|---|---|
| `W` | Wand (gebaeudekante) |
| `D` | Dach (dachschraege) |
| `Z` | Sonstige (other) |

**Wall selected:**

| Key | Effect |
|---|---|
| `←` / `→` | ±10 mm thickness |
| `Shift + ← / →` | ±50 mm thickness |

### Status (any selection)

| Key | Status |
|---|---|
| `1` | readable |
| `2` | uncertain |
| `3` | not_readable |
| `4` | missing |

## View

| Key | Effect |
|---|---|
| `R` or `0` | Reset view |
| `+` / `=` | Zoom in |
| `-` / `_` | Zoom out |
| Mouse wheel | Pan |
| `Shift + drag` / right-drag | Pan |

## Navigation

| Key | Effect | Scope |
|---|---|---|
| `,` / `<` | Previous scene of the same house | Annotate |
| `.` / `>` | Next scene of the same house | Annotate |
| `←` / `→` | Previous / next scene (when no wall is selected) | Annotate |
| `←` / `→` | Previous / next PDF page | Extract |
| `[` | Toggle left sidebar | Shell global |

## App-wide

| Key | Effect |
|---|---|
| `Cmd/Ctrl + S` | Save |
| `Cmd/Ctrl + Z` | Undo |
| `Cmd/Ctrl + Shift + Z` | Redo |
| `?` | Toggle cheatsheet |

## Cross-platform (K14)

The code uses `e.metaKey || e.ctrlKey` everywhere `Cmd` would apply, so
`Ctrl+S` on Linux/Windows behaves identically to `Cmd+S` on macOS.
`Alt` is called "Option" on macOS but the key event identifier
(`e.altKey`) is the same; this doc uses "Alt" universally.

`Shift` and `Esc` / `Enter` / `Backspace` / `Delete` are identical across
platforms.

## Per-tool gesture FSMs (K13)

Each drawing tool implements an implicit state machine. The reset on
URL change (X1) and on tool switch (X7) clears every state below.

### `select`
```
idle ───click on label────▶ selected (single)
  │  ───Shift+click──────▶ multi-select toggled
  │  ───Cmd+click───────▶ component selected
  │  ───drag on empty───▶ rubber-band ─────▶ multi-select
  │  ───drag on handle──▶ joint-aware drag ─▶ idle
  │  ───double-click body─▶ split (wall/dim/line)
  │  ───double-click region▶ region walls selected
  └──────────────────────▶
```

### `wall`
```
idle ───click 1───▶ pendingStart set, wallChainAnchor set
  │  ───click 2───▶ commit wall (with tidy + neighbor-inherit + length-quantize)
  │                 ↓
  │                 pendingStart = effEnd (auto-chain)
  │                 ↑ loop ─── ───click N near chain anchor ───▶ "Polygon geschlossen" + chain ends
  └─Esc / tool change─▶ idle
```

### `dimensioned_distance`
```
idle ───click 1───▶ pendingStart set
  │  ───click 2───▶ commit dim (with M1 reference recompute + cross-scene
  │                              building-dim prefill from X4 cache)
  │                 ↓
  │                 inline value editor opens at midpoint
  │                 ↓ Enter → commit value + paired dim_number
  │                 ↓ Esc → discard
  │                 ↓ idle (no auto-chain)
  └─Esc / tool change─▶ idle
```

### `floorplan_opening`
```
idle ───click 1 (snap to wall_line)───▶ pendingStart + pendingAttachedWallId
  │  ───click 2───▶ commit quad (rotated to wall axis if attached;
  │                              opening_kind auto-inferred from neighbors;
  │                              width_mm inherited from same-wall siblings)
  │                 ↓
  │                 setSelectedId + setPostDrawChip(kindFamily='floorplan_opening')
  └─Esc / tool change─▶ idle
```

### `view_opening` (3 shapes — switched via inline submenu under the tool)

**rectangle:**
```
idle ───click 1───▶ pendingStart
  │  ───click 2───▶ commit rectangle geometry { top_edge, bottom_edge }
  └─Esc / tool change─▶ idle
```

**circle:**
```
idle ───click 1───▶ pendingStart (= center)
  │  ───click 2───▶ commit { shape: 'circle', center, radius_px }
  └─Esc / tool change─▶ idle
```

**polygon:**
```
idle ───click 1...N───▶ pendingPolyline grows
  │  ───click near pendingPolyline[0] (≥3 pts)───▶ commit closed polygon
  │  ───Enter (≥3 pts)──────▶ commit polygon (without closing edge)
  │  ───Backspace───▶ pop last vertex
  └─Esc / tool change─▶ idle
```

### `component_line` (polyline-stops)
```
idle ───click 1...N───▶ pendingPolyline grows
  │  ───click near pendingPolyline[0] (≥3 pts)───▶ commit closed line + chip (P3, P9 fill)
  │  ───Enter (≥2 pts)──────▶ commit polyline + chip
  │  ───Backspace──────────▶ pop last vertex
  └─Esc / tool change─▶ idle
```

### `height_mark`
```
idle ───click───▶ commit at (lockedX, clickY)
                   • lockedX = X of first existing Höhenkote
                              OR sibling-scene Bezugsachse X (X3/M4.3)
                              OR raw click X
                   • Alt overrides the lock (free X)
```

### `dimension_number`
```
idle ───click───▶ commit dim_number anchor; if cursor was within snap of an
                  existing dim_distance midpoint, links via 'labels' relation
```

## Behaviors NOT bound to keys (gesture only)

- Right-click / right-drag: pan
- Mouse wheel: pan (zoom is ONLY via +/-/FIT buttons or keys)
- Pinch-zoom: not implemented
- Touch / tablet: untested
