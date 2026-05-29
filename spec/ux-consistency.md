# UX Consistency (U) tracker

**Status:** Draft 2026-05-29. Implementation-ready for U0–U4 (the three
small immediate fixes shipped 2026-05-29). U5+ requires user direction on
trade-offs before code lands.

**Owner:** jhoetter
**Predecessor:** [`spec/keyboard.md`](keyboard.md) — keyboard / modifier
source of truth.

---

## Mission

The pipeline (intake → extract → annotate → export) ships. The remaining
work is what the user keeps surfacing: tiny but compounding **UX
inconsistencies** that read as "this app is awkward" even when each
individual page is functional. This tracker enumerates every divergence
between pages, weights them by *intuition cost*, and proposes a single
target visual language so future feature work doesn't reopen the gap.

The three north-stars when judging any UI change:
1. **Intuition** — the right action should be reachable without learning
   per-page conventions.
2. **Use of space** — the user is doing detail work on dense drawings;
   chrome that doesn't earn its pixels gets cut.
3. **Consistency** — same shape = same action, same place = same role.
   The user just told us they read identical-looking outlined buttons in
   the topbar as one component; one being blue and inline on the
   dataset page was strictly worse.

---

## 1. Findings (audit, page by page)

Each row is a divergence the user explicitly hit or that is one
refresh-the-page-and-look-again away from being hit.

### A. Topbar

| Page | Breadcrumb slot | Trailing slot | Status |
|---|---|---|---|
| `/` Datasets | `Datensatz` | `+ PDFs hochladen` (primary/blue) | ✓ fixed in `35b486f` |
| `/intake` | `Datensatz › Hochladen` | — | ✓ |
| `/:key` Extract | `Datensatz › <key>` | `Export ▸` / `3D ▸` / `⋯` | ✓ |
| `/:key/scene/:file/annotate` | `Datensatz › <key> › <file>` | `Export ▸` / `Speichern` / `?` | ✓ |
| `/:key/3d` | `Datensatz › <key> › 3D` | — | ✓ |
| `/:key/export` | `Datensatz › <key> › Export` | — | ✓ |
| `/:key/scene/:file/export` | `Datensatz › <key> › <file> › Export` | — | ✓ |

Topbar convention is **stable** now. Don't regress.

### B. Scene navigator

The strip showing the scenes of a house must look identical wherever it
appears — it does the same job in both contexts.

| Page | Where it lives | Chip visual |
|---|---|---|
| Extract | full-width row, `border-b bg-zinc-50`, below topbar | 40 px thumb + label + indicators |
| Annotate | full-width row, `border-b bg-zinc-50`, below topbar | 40 px thumb + label + readiness dot + count |

✓ Aligned in `9b5a61b` after this tracker's audit prompted the move out
of AnnotatePage's topbarTrailing.

**Invariant** — both pages render the strip from the same shell
(`border-b border-border bg-zinc-50`, `px-3 py-1.5`, horizontal scroll,
40 px square thumbs). When a future page (e.g. ExportPage scene picker)
adds a similar strip, copy this same shell.

### C. Active vs. "current page" vs. "selected" state on chips

The strip mixes three orthogonal states; the user already flagged the
collision once when an `isOnPage` chip "looked clicked":

- **selected** — the chip the user just opened (stronger ring,
  background tint, e.g. `bg-accent/10 border-accent ring-1 ring-accent/30`)
- **on the page you're looking at** — softer accent ring only
  (`bg-accent/5 ring-1 ring-accent/30`)
- **labeled** — emerald ✓ on the thumbnail's bottom-right corner

The visual rhythm must keep these three distinct or the user
mis-attributes meaning to whichever one wins.

✓ Aligned in `8881ace` for ExtractPage; AnnotatePage's "current"
chip uses the **selected** style (which is correct — there it really
*is* selected).

### D. Floating canvas-display palette (U5)

`AnnotatePage` line 3643 renders a floating bottom-right palette with
`Farbe / Op slider / − + / FIT`. The user calls this "weird". On audit
it has three sins:

1. **Concept salad** — color, opacity and zoom live in one box but
   address three orthogonal axes. The user has to triangulate which
   button affects what.
2. **Off-grid location** — every other affordance is in the topbar or
   a sidebar; this one floats. Two visual systems in the same view.
3. **Microtype** — `text-[0.6rem]` labels invite a misclick on a
   trackpad.

See U5 below.

### E. Empty / dead corners (U6)

- `IntakePage` topbar `trailing` is empty — fine, but the page also
  has no primary CTA in the topbar despite "Upload" being a heavy
  primary action. The dropzone is the CTA, but a topbar button would
  give the user a top-right anchor (same as Dataset).
- `ExportPage` topbar `trailing` is empty — the bulk-export button
  lives in the sidebar. Pattern is OK, but means *one* page hides
  its primary action in the sidebar while *another* page (Dataset)
  exposes it in the topbar. Pick one or document the rule.

### F. Keyboard surface (U7)

Audited shortcuts:

| Key | Where | What |
|---|---|---|
| `[` | Shell global | Toggle left sidebar |
| `,` `.` | Annotate | Prev / next scene |
| **`←` `→`** | Annotate | **No-op when no label is selected** ← user-reported gap |
| `←` `→` | Extract | Prev / next PDF page |
| `←` `→` | Annotate w/ wall selected | ± 10 mm thickness |
| `Cmd+S` | Annotate | Save |
| `?` | Annotate | Cheatsheet |
| `G/A/S/D` | Extract post-draw + Annotate post-draw | Kind classification |
| `K/U/E/O/D/S` | Extract (floor step) | Floorplan storey |
| `N/S/O/W` | Extract (view step) | Elevation direction |

Issues:
- `←` `→` does **two different things** on Extract (page) and Annotate
  (thickness or, with no selection, nothing). The user expects the
  scene-strip to obey arrow keys consistently with the strip in front
  of them.
- ✓ Fixed for Annotate in U2: ArrowLeft / ArrowRight walk scenes when
  no wall is selected.
- Open question: should Extract's `← →` (page-nav) also surface as
  scene-nav when the cursor is over the scene strip? Probably not —
  page-nav is the higher-frequency action there.

### G. Contextual menu on extracted scenes (U3)

The `Annotieren` link in `ExtractedSceneMenu` didn't navigate.

Root cause: the menu renders **inside** PageCanvas's page `<div>` which
has an `onPointerDown` that calls `setPointerCapture` to start the
"drag to create a new bbox" gesture. The pointer capture re-routed all
subsequent events to the page div, so the link never saw its own
`pointerup` and `click` never fired. The page's handler skips elements
with `data-bbox-handle` set; the menu just didn't carry that marker.

✓ Fixed in U3 by tagging the backdrop + menu container with
`data-bbox-handle="menu"` and stopping `pointerdown` propagation.

### H. Visible noise the user removed (U4)

The "8 EXTRAHIERT · 1 ENTWURF · 2 AUF S1" strip header was extra label
text that re-stated what the row already showed visually. The user
removed it. The lesson — chrome text near the data is a tax unless it
unlocks an action.

✓ Removed in U4.

---

## 2. Immediate fixes (U0–U4, shipped 2026-05-29)

| ID | Title | State |
|---|---|---|
| U0 | Topbar trailing slot is right-justified everywhere (Dataset's primary button moved off the breadcrumb) | shipped in `35b486f` |
| U1 | Scene strip lives below the topbar, not inside `topbarTrailing` | shipped in `9b5a61b` |
| U2 | ArrowLeft / ArrowRight walk scenes on AnnotatePage when no wall is selected | this commit |
| U3 | `Annotieren` link in `ExtractedSceneMenu` actually navigates (escape page-canvas pointer-capture) | this commit |
| U4 | Drop the strip-summary text ("N extrahiert · M Entwurf") — the chips speak for themselves | this commit |

---

## 3. Implementation-ready next batch (U5–U8)

### U5 — Canvas-display palette (Farbe / Op / − + / FIT)

**Problem:** floating bottom-right palette is visually inconsistent
with the rest of the chrome and mixes three different concerns.

**Proposal:** consolidate into the AnnotatePage topbar `topbarTrailing`
as three buttons mirroring the `[` sidebar toggle pattern:

- `🌗` opacity (popover with slider, default 100 %)
- `⛶` fit-to-view (single click, no popover; replaces `FIT`)
- `−` `+` zoom (icon buttons; keep `0`/`R` keyboard reset)
- Grayscale and opacity bundle into one popover (display options)

Alternative if topbar is too crowded: same buttons, but pinned to the
**left sidebar bottom** so canvas controls live with tool palette
(also in the left sidebar). Either landing is fine; the floating
position is the one to retire.

**Open question for user:** topbar or left-sidebar-bottom?

### U6 — Topbar primary action policy

**Decide one rule and document it.** Either: "every page where the
primary action exists puts it in `topbarTrailing` as the rightmost
button (primary/blue)" — which means IntakePage gets a `+ Hochladen`
button and ExportPage gets a `→ Bulk-Export` button — or: "primary
actions live in the sidebar; topbar is for navigation only" — which
means Dataset's `+ PDFs hochladen` moves into the sidebar.

User has been responsive to "primary in topbar" so far. Recommend
ratifying that — minor work: add the two missing trailing buttons.

### U7 — Keyboard contract

Update `spec/keyboard.md` with the new ArrowLeft/Right semantics so
the in-app cheatsheet (rendered from that doc) shows them.

Open: should `Q`/`W` walk scenes too? `Page Up` / `Page Down`?
Probably not — keep `,` `.` and `←` `→` as the two pairs. Mention
both in the cheatsheet under "Szenen-Navigation".

### U8 — Modal/menu pointer-capture interaction

`ExtractedSceneMenu` revealed a class of bug: anything that renders
inside a canvas (PageCanvas, the annotation SVG) needs to escape the
canvas's pointer-capture logic explicitly. Document the rule in
`AGENTS.md`: any in-canvas overlay carries `data-bbox-handle="<role>"`
and stops `pointerdown` propagation.

---

## 4. Future / open questions (U9+)

- **U9 — Empty-state copy** is page-specific German prose written ad
  hoc. Promote three common empty states (no scenes / no labels / no
  exports) to a shared `EmptyState` component with consistent typography
  and a primary CTA.
- **U10 — Toast policy.** AnnotatePage has rich toasts; ExtractPage uses
  `window.alert` for errors. Lift toasts into a Shell-level provider
  and use them everywhere.
- **U11 — Color tokens.** `bg-emerald-{50,100,600,700,900}` is used
  ad-hoc to mean "labeled / extracted / ready". Define one semantic
  scale (`status-ready` / `status-warn` / `status-flag`) in
  `tailwind.config` and audit existing call sites.
- **U12 — Mobile-ish breakpoints.** The pages assume ≥ 1280 px wide.
  Not a priority for the labeling rig, but worth a single MQ pass so
  the homepage at least lists houses on a laptop screen.

---

## 5. Non-goals

- **Not** a redesign — every fix in U0–U8 is conservative, swap-this-for-that
  scope. The pipeline ships; only its rough edges go away.
- **Not** a theme refresh — colors stay, typography stays, only the
  *placement* and *grouping* of existing affordances changes.
- **Not** adding new functionality — `Adjust extracted` (R2.9) shipped
  separately; the Adjust action surfaces via the same menu that this
  tracker fixed in U3.
