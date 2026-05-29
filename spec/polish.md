# UX polish (P) tracker

**Status:** 2026-05-29. Implementation-ready.
**Owner:** jhoetter
**Predecessors:** R0–R6 shipped.

**Goal:** the end-to-end pipeline already works. This tracker closes the
"feels rough" gap — terminology consistency, the dual numbering systems
that confuse new users, the dead Export tile, the missing stepper on
the editor, and a handful of visual hierarchy fixes.

---

## Critical (P0) — must fix; pipeline breaks if not

### P0.1 — Resolve the "Schritt N" namespace collision

On `AnnotatePage` the user sees TWO step systems at once: the StepperBar
("Schritt 3: Annotieren") and the WorkflowGuide ("Schritt 1 / 6 —
Szenen-Inventar"). Same word, different counters.

**Fix:** reserve the word **Schritt** for the four-stage pipeline
(Hochladen / Extrahieren / Annotieren / Export). Rename the
in-editor WorkflowGuide six-phase counter to **Phase** ("Phase 1 / 6 —
Szenen-Inventar").

Touches: every "Schritt N:" usage in `WorkflowGuide*` components inside
`AnnotatePage.tsx`, plus the panel header.

### P0.2 — `/dataset/{key}/export` is a dead route

The stepper's Export tile links to `/{key}/export` — no route exists.
Clicking yields a blank canvas inside the layout.

**Fix:** add a minimal `ExportPage` at `/{key}/export` that:
- Lists every scene in the dataset manifest with a per-scene Set A / Set B health badge
- Links each row to `/{key}/scene/{file}/export-preview`
- Has a "Bulk-Export starten" button that POSTs to `/exports/{key}` and
  surfaces the result (scenes_exported, scenes_skipped, anomalies)

### P0.3 — `AnnotatePage` is missing the stepper

Every other per-house page renders the StepperBar; the editor doesn't.
The user drops out of the pipeline orientation exactly when they're
about to start the longest step.

**Fix:** render the StepperBar above the topbar inside `AnnotatePage`
with `current="annotate"`. ~20 px band, matches existing pages.

### P0.4 — `/{key}/3d` shows the stepper with `current="annotate"`

3D preview lights up the Annotieren tile. Misleading — the user IS on a
distinct page.

**Fix:** the stepper has four steps not five; we don't add a "3D" tile.
Instead `Preview3DPage` renders a small badge in the breadcrumb area
("Annotieren · 3D-Vorschau") and the stepper continues to show
"Annotieren" as current. Resolves the cognitive mismatch by labeling
the sub-context inline.

---

## Important (P1) — smooths real friction

### P1.1 — German terminology in the extract bbox inspector

`ExtractPage`'s per-bbox inspector uses raw English schema values:
`Typ` dropdown shows `floorplan / elevation / section / detail`,
`Himmelsrichtung` shows `north / south / …`, `Geschoss` shows
`kg / ug / eg / og / …`. Open the same scene in `AnnotatePage` and the
metadata reads `Grundriss / Ansicht / Schnitt / Sonstiges` etc.

**Fix:** keep schema values English (no data migration), but render
German labels in the picker:
- floorplan → Grundriss
- elevation → Ansicht
- section → Schnitt
- detail → Detail
- north/south/east/west → Nord/Süd/Ost/West
- kg/ug/eg/og/dg/spitzboden → KG/UG/EG/OG/DG/Spitzboden

`ExtractPage` only — `DatasetDrawing.kind` etc. stay in schema-native.

### P1.2 — Intake card title is unreadable

Houses 21/22/23 currently appear on `/` as cards titled
*"Seed PDF carried over from data/houses/ during R0 cleanup."* Cause:
`_intake_stub_manifest` sets `model = user_notes or key`.

**Fix:** swap the precedence — `model = key`; user_notes shows as a
small subtitle line under the title when present.

### P1.3 — WorkflowGuide collapsed on first open

New users land in `AnnotatePage` with the guide auto-collapsed unless
the workflow phase is past detail. But the FIRST scene any user opens
has no workflow state yet → guide collapses → user doesn't see what
to do.

**Fix:** default-open when phase ∈ {inventory, height_anchor,
footprint, orientation}. Auto-collapse only on phase=detail.

### P1.4 — 3D and Extract buttons compete on the house overview

Both render as same-weight pills at the top of `DatasetHousePage`.
Extract is the next-action 95% of the time; 3D is rarely accessed.

**Fix:** primary CTA `Szenen extrahieren` (accent), secondary `3D
Vorschau` (zinc outline). Place 3D in the right margin (`ml-auto`) so
it's visible but unobtrusive.

### P1.5 — House cards lose their progress signal mid-annotation

Cards show `ausstehend` for intake-only and the WorkflowPhaseBadge dot
strip. But when scenes exist + are partially labeled, the only signal
is the WorkflowPhaseBadge — no per-scene completion fraction.

**Fix:** when `total > 0` and `labeled < total`, render
"N / M annotiert" inline (the existing logic only renders when
`labeled > 0`). Already mostly there; the visibility threshold needs
a tweak to show "0 / N annotiert" too.

---

## Polish (P2) — do-when-bored

### P2.1 — "Datensatz" appears twice across the chrome

Top-left brand says "BIM Datensatz", breadcrumb starts with "Datensatz".

**Fix:** drop the breadcrumb's "Datensatz" root segment — the brand
plus the page name is enough. Pages currently using it: every page.

### P2.2 — Extract sidebar page row is cramped

Each page row in the sidebar packs `S1` + ✓N + ●N + ○ into one tight
flex. Hard to scan at a glance.

**Fix:** two-line layout per page row — name on top, badges below.

### P2.3 — Inline edit on intake notes is invisible

Click the italic note text on a `BundleRow` to edit. No affordance
hints this is interactive.

**Fix:** add a ✎ pencil glyph next to the note. Standard pattern.

### P2.4 — Three different "back" link styles

Pages variously offer "← Zurück", a text link, or the breadcrumb.

**Fix:** the breadcrumb is the only back-link path. Drop the
redundant Link components inside sidebars.

---

## Implementation order

One commit per wave so each is independently revertable.

| Wave | Files touched (approx) | LOC |
|---|---|---|
| P0.1 Phase rename | AnnotatePage.tsx (WorkflowGuide variants) | ~20 |
| P0.2 ExportPage | new pages/ExportPage.tsx + router + StepperBar wiring | ~150 |
| P0.3 Stepper in AnnotatePage | AnnotatePage.tsx + Shell topbar wiring | ~40 |
| P0.4 3D sub-label | Preview3DPage.tsx | ~10 |
| P1.1 German terms | ExtractPage.tsx (KIND_LABELS dict + render) | ~30 |
| P1.2 Card title | api/main.py (_intake_stub_manifest) | ~5 |
| P1.3 Default-open guide | AnnotatePage.tsx (WorkflowGuide useState) | ~5 |
| P1.4 Button hierarchy | DatasetHousePage.tsx | ~10 |
| P1.5 Progress fraction | DatasetPage.tsx HouseCard | ~10 |
| P2.1 Breadcrumb root | 7 pages | ~15 |
| P2.2 Sidebar rows | ExtractPage.tsx | ~15 |
| P2.3 Edit pencil | IntakePage.tsx BundleRow | ~5 |
| P2.4 Back-link cleanup | IntakePage.tsx + ExtractPage.tsx sidebars | ~10 |

Total: ~325 LOC across ~10 files in one go. No schema changes, no
new deps, no migrations.

---

## Self-audit

- ☑ Every score-impacting finding from the audit is captured.
- ☑ The "Schritt vs Phase" namespace split is explicit and applied to
  EVERY collision (not just one place).
- ☑ The dead Export tile gets a real page, not just hidden.
- ☑ Terminology unification is display-only (no data migration).
- ☑ Each item names the files it touches + a LOC estimate so the
  implementer can verify scope before starting.

### Gaps left open
- Telemetry / first-run analytics: out of scope (single-user app).
- Mobile layouts: spec-level decision is "desktop only" (R tracker §10.6).
- Accessibility audit (screen reader / keyboard-only): out of scope
  until requested.
