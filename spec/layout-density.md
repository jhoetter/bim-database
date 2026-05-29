# Layout density + keyboard (L) tracker

**Status:** 2026-05-29. Pre-implementation. All Q1–Q6 ★
recommendations approved by the user; §6 captures them as decisions,
not open questions. §1 extended with findings L8–L16 the user
didn't articulate but the audit surfaced.

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

### H. Cmd+Z runs silently on ExtractPage (L8)

A3 added undo/redo with keyboard bindings. AnnotatePage's undo toasts
`↶ Rückgängig` (and `↷ Wiederherstellen` on redo). ExtractPage's
`runUndo` / `runRedo` paths fire silently — the user can't tell
whether the keystroke was registered, especially when the action
under the cursor (a green scene) just disappears or reappears with
no audio cue.

### I. Auto-extract waiting state is only inside the chip (L9)

When the user presses `G` then `K` for Grundriss KG, the post-draw
chip flips its busy state to `↻ extrahiere…`. But the **orange bbox
on the canvas** does not change — it sits orange for 1–3 s with no
indication that the server is mid-flight. If the chip is dismissed
via Esc before the round-trip returns, the user has zero feedback
that an extract is still in progress.

### J. Cheatsheet content is AnnotatePage-only (L10)

The cheatsheet (opened via `?`) lives inside `AnnotatePage` and only
documents its own keys. ExtractPage has post-draw keys, scene
navigation, page navigation, undo / redo — none are listed
anywhere. After L1 retires the always-visible PageNav hint string,
the cheatsheet becomes the only discoverability channel for those.

### K. Breadcrumb truncation has no tooltip (L11)

The breadcrumb component truncates long filenames via
`overflow-hidden text-ellipsis`. There's no `title` attribute, so
the user has no way to read the full filename. Bites on
`house-22-floorplan-eg-very-long-suffix.jpg`-style names.

### L. SaveStateDot floats between BezugStatus and `?` with no fixed anchor (L12)

The A2 SaveStateDot sits in the topbar between `BezugStatus` and the
`?` cheatsheet button. Its left neighbour changes — when the
modifier-held chip (`Alt · Helfer aus`) appears between them, the dot
shifts right. Reading "where the dot is" requires the user to scan
for it. Better anchor: immediately right of the breadcrumb (left side
of the topbar), where save-state is conventionally placed.

### M. Tool palette tooltips hide the keyboard letter behind hover (L13)

`<button title="Wand (W)">` only reveals the `W` letter on hover.
For a power user trying to internalise the keyboard map, the letter
should be visible inline next to the icon, e.g. as a small
`<kbd>` glyph in the bottom-right of the icon button.

### N. WorkflowGuide phase bodies are long-form (L14)

Each phase body (when expanded) renders paragraphs of guidance text
and inputs. On the new sidebar layout (post L2 + L3), the Workflow
accordion shares vertical space with the SceneDetailsCard + Tools.
The phase bodies may need a "compact mode" toggle or default-collapsed
state to coexist sanely.

### O. HouseMenu (⋯) on ExtractPage hides the only house-level destructive action (L15)

The `⋯` icon in the ExtractPage topbar opens HouseMenu which
contains "Alle Szenen löschen" (full house reset). Discoverability
is by design — destructive lives behind a stable, low-attention
icon. Adequate, but the cheatsheet should at minimum mention that
house-level reset exists via that icon.

### P. Cmd+Z semantics differ by page (L16)

On AnnotatePage `Cmd+Z` undoes label edits. On ExtractPage `Cmd+Z`
undoes extract / delete. The two stacks are independent. A user who
just extracted, then went into a scene and edited labels, then
returned to extract, would expect `Cmd+Z` to undo "the most recent
thing" — but it's scoped to whichever page they're on.

This is **the right behaviour** for principle-of-least-surprise on a
per-page basis (cross-page undo would be wildly more confusing), but
it needs to be documented in the shared cheatsheet (L10) and tested
in §4 acceptance.

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

### L8 — Toast feedback for Extract-side undo / redo

`runUndo` / `runRedo` on ExtractPage emit a small toast (same
pattern as AnnotatePage):

```
↶ Szene zurück in Entwurf  (or)  ↷ Erneut extrahiert
↶ Szene wiederhergestellt        ↷ Erneut gelöscht
↶ Typ zurückgesetzt              ↷ Typ erneut geändert
```

Reuses the existing toast surface if AnnotatePage's `addToast` can
be lifted to a shared context; if not, ExtractPage gets its own
inline toast pip.

### L9 — Pending-extract visual on the bbox itself

The orange draft bbox gets an additional "in flight" decoration
while the server is processing:

```
fill: rgba(245, 158, 11, 0.35)   ← higher opacity than normal draft
stroke: animated dashed pattern  ← strokeDashoffset transition
```

A small `↻` glyph centered on the bbox shows progress. Driven by a
local `extractingDraftIds: Set<string>` state on ExtractPage that's
populated when `extractDraftNow` starts and cleared on completion.

If the chip is dismissed via Esc during the round-trip, the bbox
still carries this visual until the request resolves.

### L10 — Shared cheatsheet (extends L5)

The `Cheatsheet` component in AnnotatePage gets moved into
`ui/src/components/Cheatsheet.tsx` and reads a per-page section
prop. ExtractPage opens it via `?` (new — L5) showing only its own
section; AnnotatePage shows both sections (so a power user can scan
the whole map from inside the editor).

Section schema:

```ts
type Section = { title: string; bindings: Array<{ keys: string[]; effect: string }> };
type CheatsheetProps = { sections: Section[]; onClose: () => void };
```

The new ExtractPage section covers: bbox draw, `← → ↑ ↓ Home End`
page nav, `Esc Del` selection, `G/A/S/D` post-draw kind, `K/U/E/O/D/S`
floor, `N/S/O/W` view, `Cmd+Z` extract-side undo, `?` cheatsheet
itself, **+ note about the ⋯ HouseMenu (L15)**.

### L11 — Breadcrumb tooltip on truncation

`Breadcrumb` component checks each crumb's text length and adds a
`title={c.label}` attribute when the label could plausibly truncate
(simplest: always add `title`). One-line fix.

### L12 — SaveStateDot anchored next to the breadcrumb

The dot lives **immediately right of the breadcrumb** instead of
mid-topbar. New layout:

```
[sidebar toggle] [Breadcrumb] [SaveStateDot] [flex-1 spacer] [BezugStatus] [modifiers] [?] 
```

This pins it to a fixed position (left of the spacer) so the user
always knows where to look. The save state is conventionally
associated with the document name in modern editors; this matches.

### L13 — Inline keyboard letters on tool icons

Tool palette buttons render a small `<kbd>` glyph in the bottom-
right corner of the icon:

```
┌─────────┐
│ [icon]  │
│      W  │ ← bottom-right kbd
└─────────┘
```

Style: `text-[0.55rem] tabular-nums font-mono text-zinc-400
position: absolute bottom-0.5 right-1`. Same pattern as the
emerald `✓` overlay on SceneChip thumbnails (consistent
"corner-badge" rhythm).

Hovers still show the full title for the screen-reader case.

### L14 — WorkflowGuide compact mode

Each phase body opt-in to a `compact` rendering that shows the
phase title, completion %, and a single "Eintragen" link that opens
the full body as a popover modal. The full inline body is reserved
for a "Workflow ◐" page (`/:key/workflow`) — a separate full-page
view for users who want to deeply edit the workflow.

Sidebar default = compact. Modal = full editor.

### L15 — Cheatsheet documents `⋯` HouseMenu

Added as a row in the cheatsheet's "Haus-Aktionen" section, with
the visual `⋯` glyph and a one-line description "Haus zurücksetzen
— löscht alle Szenen + Annotationen, behält die PDF".

### L16 — Cmd+Z scope documented per page

In the cheatsheet, the "Rückgängig" rows make the page-scope
explicit:

```
Cmd+Z (auf Extract)     Letzten Szenen-Vorgang rückgängig
Cmd+Z (im Editor)       Letzten Label-Vorgang rückgängig
Cmd+Shift+Z             Erneut (vorigen Cmd+Z)
```

No code change — just documentation that closes the principle-of-
least-surprise gap.

---

## 3. Order of implementation

| Wave | Items | Risk |
|---|---|---|
| 1 | L1, L5, L6, L8, L11, L12, L16 | low — additive keyboard + small removals + new toasts + tooltip |
| 2 | L2, L3, L7 (sidebar restructure) | medium — touches AnnotatePage layout + ToolPalette logic |
| 3 | L4 (popover positioning) + L9 (in-flight bbox decoration) | medium — viewport math + new SVG state |
| 4 | L10, L13, L15 (cheatsheet upgrade + tool kbd glyphs) | medium — shared component lift |
| 5 | L14 (WorkflowGuide compact + modal) | medium — biggest refactor; ship last |

L1 + L5 + L8 + L11 + L12 + L16 are all "small wins" that batch
cleanly. L2 + L3 + L7 are the sidebar redesign. L4 + L9 share the
overlay-positioning theme. L10 + L13 + L15 are the cheatsheet
upgrade. L14 is the biggest scope and ships last.

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

## 6. Decisions (user-approved 2026-05-29)

All Q1–Q6 ★ recommendations are approved. They are now decisions, not
options. Captured here as the rule each item ships against.

- **D1 (L2 anchor):** SceneDetailsCard goes in the **sidebar header**.
- **D2 (L3 tag picker):** Szenen-Tag picker **vanishes entirely**;
  editing the classification happens via the SceneDetailsCard popover.
- **D3 (L4 click anchor):** Click-anchored popover **stays where it
  opened**; does not follow the mouse.
- **D4 (L5 cheatsheet):** **Shared `Cheatsheet` component**, one
  section per page.
- **D5 (L7 brand row):** **Keep** "BIM Datensatz" as the sidebar
  top row.
- **D6 (L1 timestamp):** **Drop** the `Auto-gespeichert · HH:MM`
  fallback line on the PageNav row.

Locked-in non-goals carry forward from §5.

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

## 8. Out-of-scope for this tracker (already captured elsewhere)

These came up during the audit but belong to existing trackers; not
re-litigating here:

- **Empty-state copy unification** — already U14 in
  `spec/ux-consistency.md`.
- **Semantic colour tokens** — already U16.
- **Mobile / narrow-viewport breakpoints** — already U17.
- **Server-side undo state** — explicit non-goal in
  `spec/auto-persist.md` §4.
- **Auto-classify (filename inference)** — non-goal in
  `spec/auto-persist.md` §4.

---

## 9. After this wave

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