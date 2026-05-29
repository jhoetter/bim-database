# Layout density + keyboard (L) tracker

**Status:** 2026-05-29. Pre-implementation. User asked for analysis
before code; decisions in §6 still need a call.

**Owner:** jhoetter
**Predecessor:** [`spec/ux-consistency.md`](ux-consistency.md) — U0–U13,
[`spec/auto-persist.md`](auto-persist.md) — A0–A3.

---

## Mission

The pipeline ships and persists invariantly. The remaining friction is
**visual real-estate** — too many rows, popovers in the wrong place,
sidebars that re-ask questions we already know the answer to. Plus a
keyboard surface that's incomplete in obvious ways (no `↑/↓` for page
navigation, no clear discoverability path beyond the cheatsheet).

Three north-stars carried over from `spec/ux-consistency.md`:
1. **Intuition** — actions reachable without learning per-page tricks.
2. **Use of space** — chrome that doesn't earn its pixels gets cut.
3. **Consistency** — same shape = same action everywhere.

Two specific to this tracker:
4. **Anchor popovers at the gesture.** A floating menu that opens
   because of a click should appear near the click, not at a fixed
   geometric centroid of the underlying object.
5. **Don't re-ask what we already know.** A scene whose `kind` is
   set in the manifest should land in the editor with the tool
   palette filtered to that kind. The "Szenen-Tag" selector is
   chrome for an already-answered question.

---

## 1. Findings

### A. The PageNav hint row eats a whole row (L1)

`ExtractPage` PageNav today renders:

```
[←] [4] / 6 [→]  [🗋 Ganze Seite als Szene]  ✓ 2  ● 1   Drag = Bbox · Doppelklick = ganze Seite · ← → Seiten · Esc deselect · Del löschen
```

The trailing hint string (`Drag = Bbox · Doppelklick = …`) is
chrome-as-cheatsheet — five separate keyboard shortcut hints jammed
into one tabular-num text run. It's:
- visually heavy (≈100 chars at `text-[0.7rem]`)
- redundant with `?` cheatsheet (which doesn't exist on this page yet)
- duplicated by the per-button tooltips (`title="Nächste Seite (→)"`)

When drafts exist, this row is replaced by `Auto-gespeichert · 09:50`
which is also low-value chrome.

### B. Scene details strip on AnnotatePage takes the canvas's vertical budget (L2)

The U10 strip renders `SceneDetailsCard variant="compact"` as a
**full-width row above the scene strip**. On a 1280-px screen the
two rows together cost ~88 px of vertical space the canvas would
otherwise own:

```
[topbar 44 px]
[scene-details 48 px]  ← L2 target
[scene-strip   44 px]
[CANVAS — labels go here]
```

The data the strip carries (file + kind + floor/view + status + Bezug
+ `✏ Typ ändern`) is also visible in the breadcrumb (filename) + the
sidebar's Szenen-Tag selector (kind/floor/view) + the topbar's
`BezugStatus` chip. So the strip is **mostly duplicative**.

### C. Sidebar re-asks the kind that the manifest already knows (L3)

`ToolPalette` on AnnotatePage opens with:

```
[Workflow accordion]
[Szenen-Tag picker — Grundriss / Ansicht / Schnitt / Sonstiges]
[SceneOrientation OR SceneLevel picker — gated on the tag]
[Werkzeuge — filtered by tag]
[Selection inspector]
[Settings ▸]
```

After A0 derives `sceneTag` from `drawing.kind`, the Szenen-Tag
picker shows the right preselection — but it's still a visible
selector with four buttons taking ~96 px of sidebar height. The
user has already classified this scene at extract time **and** the
SceneDetailsCard above (L2) restates it. Three places, one fact.

The Werkzeuge section is the one the user actually uses every
session; it should be the first thing in the sidebar. Currently
it's third.

### D. Popover anchoring is centroid-based, not gesture-based (L4)

Three floating overlays:

| Overlay | Anchor today |
|---|---|
| `ExtractedSceneMenu` (bbox click) | bbox top-edge centre, flips below for `topPct < 12` |
| `PostDrawChip` (after a draw) | bbox top-edge centre, flips inside for `topPct < 18` |
| `SceneDetailsCard` popover (popover variant) | bbox top-edge centre |

For a 600×400-px bbox, the popover lands far from where the user
actually clicked. On a large extracted scene the user clicks the
top-left corner; the menu pops up at the top-centre, ~300 px away.
Reads as "where did that come from?".

The PostDrawChip case is justified — it always anchors to the
freshly-drawn bbox so the user knows what they're classifying. But
the click cases (ExtractedSceneMenu, SceneDetailsCard) should
anchor at the click coordinate.

### E. Keyboard surface gaps (L5)

Current map (from `spec/keyboard.md` + audit):

| Where | Key | What |
|---|---|---|
| Shell | `[` | Toggle left sidebar |
| Extract | `←` `→` | Prev / next PDF page |
| Extract | `Esc` | Deselect bbox |
| Extract | `Del` / `Backspace` | Delete selected draft |
| Extract | `G A S D` (post-draw) | Kind |
| Extract | `K U E O D S` (post-draw) | Floor |
| Extract | `N S O W` (post-draw) | View |
| Extract | `Cmd+Z` / `Cmd+Shift+Z` | A3 undo / redo |
| Annotate | `,` `.` | Prev / next scene |
| Annotate | `←` `→` | Prev / next scene (no wall sel) or ±10 mm (wall sel) |
| Annotate | `Cmd+S` | (Was force-save; now no-op after A2 cleanup) |
| Annotate | `?` | Cheatsheet |
| Annotate | letters | Tools (context-aware via K3) |

Gaps:
- **No vertical analog to page nav.** User asked for `↑` `↓` =
  prev/next PDF page on ExtractPage. Both axes feel natural for a
  PDF reader; today only horizontal exists.
- **No "jump to first/last page".** `Home` / `End` is the universal
  way; not implemented.
- **No `?` cheatsheet on ExtractPage.** AnnotatePage has it; the
  same affordance + same component would work on Extract too.
- **No `Cmd+Z` on AnnotatePage's WorkflowGuide popovers.** The
  label undo stack runs alongside ExtractPage's A3 stack but the
  popover-style WorkflowGuide editors are outside both.
- **`Cmd+S` is dead post-A2 but still listens.** Keep as a
  silent flush (per Q3 ★) or stop hijacking the keystroke.

### F. Scene-strip arrows in AnnotatePage's strip wrap the row (L6)

The strip on AnnotatePage today renders `‹ chip chip chip chip ›`
with the `‹›` buttons inside the same flex row. On a narrow window
the chips wrap below the `‹` and the row's vertical rhythm breaks.
Not the worst, but the buttons themselves are redundant with the
`,` `.` and `←` `→` keyboard shortcuts AND the chip clicks. Three
ways to do the same thing in one row.

### G. Tool palette title is "Werkzeuge" — could be the heading of the sidebar (L7)

In the proposed reorganisation (Tools first), the section heading
"Werkzeuge" becomes the *de facto* heading of the sidebar. Instead
of repeating it as a small uppercase label, the section header
could just be the sidebar header — saves a row.

---

## 2. Items

### L1 — Drop the PageNav hint row; rely on tooltips + cheatsheet

**Before:**
```
[buttons] [page counter] [Ganze Seite] [chips] · Drag = Bbox · ...
```

**After:**
```
[buttons] [page counter] [Ganze Seite] [chips]
```

Add a small `?` icon in the topbar that opens the existing cheatsheet
(reused from AnnotatePage). The cheatsheet gets a new section
"ExtractPage" listing the keyboard / pointer affordances.

Saves ~24 px of vertical space (PageNav row shrinks).

### L2 — Move SceneDetailsCard from the full-width row into the sidebar header

The card moves to the left sidebar, just above the (newly first)
Werkzeuge section:

```
[BIM Datensatz brand]
[SceneDetailsCard compact]   ← was full-width row
[Werkzeuge]
[Workflow ▸]
[Selection inspector]
[Settings ▸]
```

The compact variant fits in ~80 px in the sidebar (140 px wide is
generous). Canvas gains back ~48 px vertically + a clearer visual
hierarchy.

The full-width strip block in the children flex column is removed.

### L3 — Reorder the sidebar; drop the Szenen-Tag picker for already-classified scenes

**Before:**

```
[Workflow ▸]
[Szenen-Tag picker]
[Orientation / Level picker]
[Werkzeuge]
[Selection inspector]
[Settings ▸]
```

**After:**

```
[SceneDetailsCard compact]   ← L2
[Werkzeuge]                  ← now first interactive section
[Orientation / Level picker]  ← only when missing
[Workflow ▸]                 ← collapsed by default
[Selection inspector]
[Settings ▸]
```

Removals:
- **Szenen-Tag picker** vanishes when `sceneTag !== 'nicht_klassifiziert'`.
  When unclassified, the picker becomes a single "✏ Typ wählen" CTA
  that opens the SceneDetailsCard's editor in-place.
- "Orientation" / "Level" picker only renders when the corresponding
  field is null AND the tag requires it.

Saves ~120 px of sidebar height for fully-classified scenes (the
common case once A0 + A1 are stable).

### L4 — Anchor popovers at the click coordinate

Each popover gets `anchorX` / `anchorY` props in viewport coords
(captured at the pointerdown/click event). Positioning falls back to
the current centroid model only when `anchorX`/`anchorY` aren't
supplied (e.g. when the popover opens from a chip click in the
strip — the chip is small enough that any of its corners is fine).

**Mechanics:**

```ts
interface PopoverAnchor {
  // Viewport coordinates of the user's click. Used to position the
  // popover so it appears at the gesture, not at a geometric centre.
  clientX: number;
  clientY: number;
}
```

Each call site captures `e.clientX`/`e.clientY` on click and forwards
to the popover. The popover converts to local coords and clamps
inside the viewport bounds (no off-screen flips).

Affected:
- `ExtractedSceneMenu` (page click on bbox → use click coords)
- `SceneDetailsCard` full variant (chip click → keep centroid; bbox
  click → use click coords)
- Future: any new popover.

`PostDrawChip` keeps its current anchor (bbox top edge) because the
chip needs to clearly *belong to the bbox the user just drew*.

### L5 — Keyboard expansion

| Where | New key | Effect | Rationale |
|---|---|---|---|
| Extract | `↑` `↓` | Prev / next PDF page | User-requested. Both axes feel natural for a PDF reader. |
| Extract | `Home` `End` | First / last PDF page | Standard PDF behaviour. |
| Extract | `Page Up` `Page Down` | Same as `↑` `↓` | Standard binding. |
| Extract | `?` | Open cheatsheet | Same as AnnotatePage. Discoverability. |
| Annotate | `?` | (Already exists) | Documentation only — show this and the new Extract bindings. |
| Annotate | `Cmd+S` | Silent flush save | Q3 ★ — keep the muscle memory; chrome stays gone. |

`Cmd+Z` / `Cmd+Shift+Z` already work on both pages.

Conflicts:
- `↑` `↓` for scene navigation on AnnotatePage? Skip — `,` `.` and
  `←` `→` are enough.
- `?` on Extract → `?` is also `Shift+/` which might fire on text
  input. AnnotatePage already gates on `e.target` being non-input;
  copy the same gate.

### L6 — Drop the `‹ ›` buttons in the AnnotatePage scene strip

`,` `.` and `←` `→` handle prev/next. The chip click handles jump.
The `‹›` buttons are redundant + cause the row to wrap on narrow
viewports.

Keep them only if the audit finds touch-screen users (the only
case where the keyboard shortcuts don't help). Survey: zero
identified touch users; remove.

### L7 — Section header → sidebar header

When Tools are the first section, the "Werkzeuge" `<h3>` row can
fold into the section's own structure (the tool icons already speak
for themselves). The "BIM Datensatz" brand cell at the very top
stays. Saves one row + ~8 px of padding.

---

## 3. Order of implementation

| Wave | Items | Risk |
|---|---|---|
| 1 | L1, L5, L6 | low — additive keyboard + small removals |
| 2 | L2, L3 (sidebar restructure) | medium — touches both AnnotatePage layout AND ToolPalette logic |
| 3 | L4 (popover positioning) | medium — three popovers, viewport math |
| 4 | L7 | trivial — falls out of L3 |

L1 + L5 first because they're additive and low-risk. L2 + L3 should
ship together because they're the same redesign. L4 is independent
and can interleave.

---

## 4. Acceptance summary

The user opens a freshly-extracted Grundriss EG scene.

- The topbar shows `Datensatz › house-22 › ...-floorplan-eg.jpg`.
- No scene-details row below the topbar; the canvas starts immediately
  below the scene-strip.
- The left sidebar shows:
  ```
  BIM Datensatz
  --
  [thumb] Grundriss EG · 0 Labels       ✏
  --
  WERKZEUGE
  Select · Wand · Tür · Fenster · Bemaßung · …
  --
  Workflow  Phase 1 / 6 — Szenen-Inventar  ▸
  ```
  No Szenen-Tag picker, no Orientation/Level picker — both are
  derived from the manifest.

The user draws a wall, double-clicks to commit. 400 ms later the
disk file reflects it. Pressing `↑` walks up one page in the PDF
when they switch back to ExtractPage. Pressing `?` on either page
shows the cheatsheet, including the new `↑↓`/`Home`/`End` bindings.

The user clicks on a green bbox in the middle of the canvas. The
action popover appears at their cursor, not at the top of the bbox.

---

## 5. Non-goals

- A wholesale redesign of the editor. L1–L7 are conservative
  swaps + removals.
- New tool affordances. Tool palette content stays; only its
  position changes.
- Mouse-cursor-aware popovers in the WorkflowGuide. Out of scope —
  those are sidebar-anchored already.
- Discoverable keyboard shortcuts via floating hints on hover.
  Cheatsheet via `?` is sufficient.

---

## 6. Open decisions (user input needed)

### Q1 — On L2, does the SceneDetailsCard go in the sidebar OR fold into the topbar?

Options:

| Option | Pro | Con |
|---|---|---|
| **Sidebar header** ★ | Always visible without taking canvas height. | Sidebar narrows the data: long titles truncate. |
| **Topbar trailing chip** | Visible even when sidebar is collapsed. | Even less space than sidebar; clashes with existing topbar trailing. |
| **Fold into the breadcrumb** | Maximally compact. | Filename + kind both fight for space in the breadcrumb. |

**Recommendation:** Sidebar header. The sidebar already houses the
tools; co-locating the "what scene am I on" with "what tools do I
have" makes the per-scene context one place.

### Q2 — On L3, does the Szenen-Tag picker disappear entirely or fall back to an inline edit on the SceneDetailsCard?

| Option | Pro | Con |
|---|---|---|
| **Vanish; edit via popover** ★ | Sidebar truly slim. One place to change classification. | Two clicks to change tag instead of one. |
| **Tiny inline chip in the SceneDetailsCard** | One click. | Adds a chip back to the always-visible header. |
| **Keep as today but smaller** | Lowest blast radius. | Wastes the win of L2. |

**Recommendation:** Vanish + edit via the popover. The user said
"if I labeled it as floorplan in extract, the editor should know"
— so editing the tag from the editor is the exception case.

### Q3 — On L4, should the click-anchored popover follow the mouse as it moves OR stay where it opened?

**Recommendation:** Stay where it opened. Following the mouse fights
with hover semantics on the popover's own buttons.

### Q4 — On L5, does ExtractPage get its own cheatsheet or share AnnotatePage's component with a section per page?

| Option | Pro | Con |
|---|---|---|
| **Shared component, section per page** ★ | One source of truth for the cheatsheet. | Reads must filter to the current page. |
| **Two separate cheatsheets** | Smaller per page. | Duplication risk over time. |

**Recommendation:** Shared component. The cheatsheet should read its
data from `spec/keyboard.md` ideally; for now, hardcode but in one
file.

### Q5 — On L7, do we also drop "BIM Datensatz" brand from the sidebar header?

It's currently the top row and links to `/`. Keeping it is fine;
removing it costs ~24 px more.

**Recommendation:** Keep. It's the only navigation-up affordance
on a narrow viewport where the topbar might scroll.

### Q6 — On L1, do we also drop the `Auto-gespeichert · HH:MM` hint that replaces the keyboard hints when drafts exist?

Auto-extract (A1) means drafts are short-lived. The `Auto-gespeichert`
line refers to the localStorage draft cache, not the dataset-level
auto-save which has its own indicator (A2 SaveStateDot).

**Recommendation:** Drop. A1 makes the localStorage draft a transient
state; the timestamp is uninteresting.

---

## 7. Risks

- **L3 + L4 + A0 sequencing.** A0 derives the tag, L3 hides the
  picker accordingly. If A0 ever returns `nicht_klassifiziert`
  silently (e.g. a manifest with `kind: null`), the user MUST see
  the picker. The empty-state path needs explicit test coverage.
- **L4 clamping** when the user clicks near the viewport edge. The
  popover MUST flip / shift to stay fully visible. Edge case: user
  clicks the bottom-right corner of the canvas — popover opens
  upward-left instead of downward-right.
- **L5 keyboard conflicts** with the WorkflowGuide's inline number
  inputs. `↑/↓` inside a number input should adjust the number, not
  walk PDF pages. Same gate as the existing `e.target instanceof
  HTMLInputElement` check.

---

## 8. After this wave

If everything ships:
- 88 px of vertical chrome reclaimed on AnnotatePage (L1 + L2).
- 120 px of sidebar vertical reclaimed on AnnotatePage for
  classified scenes (L3).
- 24 px of PageNav reclaimed on ExtractPage (L1).
- 4 new keyboard bindings + 1 cheatsheet path on ExtractPage (L5).
- 3 popovers anchor to the user's gesture, not a geometric centre (L4).

Combined with U0–U13 + A0–A3, the editor's chrome-to-content ratio
flips: today the sidebar + topbar + scene strip + details strip
eat ~280 px on a 1280 × 800 viewport (35 %); after this wave they
eat ~160 px (20 %).