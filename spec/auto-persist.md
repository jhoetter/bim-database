# Auto-persist + propagation (A) tracker

**Status:** 2026-05-29. **A0–A3 + all §8 follow-ups shipped.** Wave
includes classify-undo (popover delta), Adjust-undo (recycle
roundtrip), off-canvas save toast, house-scoped action stack
(ExtractUndoProvider).

**Owner:** jhoetter
**Predecessor:** [`spec/ux-consistency.md`](ux-consistency.md) — U0–U13.

---

## Mission

The user is doing detail work on dense drawings. "Save" buttons and
"extract this batch" buttons are interruptions: they require the user
to remember to confirm the work they just did. They also expose the
user to data loss if they don't.

This tracker removes the manual persistence model end-to-end and makes
the editor invariant:

> **Every classification + every finished geometry is persisted the
> moment it's committed.** The user never sees a "save" button.
> Mistakes are reversible via undo / redo.

Plus a propagation fix that surfaced now: a scene classified at
extract time (`kind=floorplan, floor=EG`) wasn't reaching the
annotation editor's `sceneTag` / `sceneLevel`. The editor treated it
as unclassified and the user had to re-pick the same value.

---

## 1. Findings

### A. Extract → annotate classification doesn't propagate (A0)

When the user picks `kind=floorplan` in the post-draw chip on
ExtractPage, `extractScenes` saves `kind=floorplan, floor=EG` into
`data/dataset/<key>/manifest.json`'s drawing entry. Good.

Annotation editor reads two files:
- `manifest.json` → `drawing.kind/floor/view` (via `houseDataset`)
- `labels/<scene>.json` → `data.scene_tag / scene_level / scene_orientation`

`AnnotatePage` line ~1003:
```ts
const tag = data.scene_tag ?? 'nicht_klassifiziert';
```

The drawing's `kind` is NOT consulted. So a freshly-extracted scene
that has `manifest.drawing.kind === 'floorplan'` STILL opens in the
editor as `sceneTag === 'nicht_klassifiziert'` — the user has to
re-pick the tool-palette tag every time, and labeling tools are
gated on it.

Same bug for `floor` (→ `sceneLevel`) and `view` (→ `sceneOrientation`).

### B. Manual "Extract N scenes" button (A1)

ExtractPage today:
1. User draws a bbox
2. Post-draw classifier chip → pick kind / floor / view
3. **User clicks "→ N Szene extrahieren" in the sidebar to commit.**

Step 3 is friction. Every classified bbox stays orange (draft) until
the user remembers to extract. Drafts also need to be classified
*before* extract, so the "wait to extract" buys nothing — the
classification IS the act of "I want this".

### C. Manual save in annotation (A2)

Annotate editor has:
- `autosave` toggle (default off in early versions; user-toggled to
  on, persists in localStorage)
- `Cmd+S` save shortcut
- `Speichern (Cmd+S)` button in the topbar
- `dirty` flag tracked on every label change
- `beforeunload` guard if dirty

The autosave is a 30-second debounce. The user wants this: zero
delay between "I finished this polygon" and "this polygon is on
disk". They never want to see "● ungespeichert" again.

### D. No undo on the extract / scene-management actions (A3)

AnnotatePage has `pushUndo` / `undo` / `redo` for label edits.
ExtractPage has nothing: extract, delete, adjust are all permanent
with only `window.confirm` as a guard.

Once auto-persist is on (A1 + A2), undo/redo becomes the safety net
that replaces the manual confirm.

---

## 2. Items

### A0 — Derive `sceneTag` / `sceneLevel` / `sceneOrientation` from the dataset manifest when the labels JSON doesn't carry them yet

**Mechanism.** In the label-init effect, fall back from
`data.scene_tag` to a derivation of `currentDrawing.kind`:

| drawing.kind | sceneTag |
|---|---|
| `floorplan` | `grundriss` |
| `elevation` | `ansicht` |
| `section` | `schnitt` |
| `detail` | `sonstiges` |
| null | `nicht_klassifiziert` |

Same for floor → level (lowercase normalised — `EG`/`eg` → `eg`) and
view → orientation (normalised — `north`/`nord`/`N` → `north`).

When the derivation fires (i.e. `data.scene_tag` was absent), mark
the editor as `dirty` so the next save persists the derived
metadata into the labels JSON. After that the labels file is the
source of truth and the dataset manifest stays a hint.

**Acceptance:**
- Open a freshly-extracted floorplan-EG scene → tool palette
  immediately offers Grundriss tools, no "Typ wählen" gate.
- Edit `floor` via the U9/U10 popover → next time the scene is
  opened, the new value is reflected (because save persisted the
  derivation).

### A1 — Auto-extract: the post-draw classifier chip extracts immediately

**Flow change.**

Before:
```
draw bbox → orange draft → post-draw chip → pick kind+floor → still orange
  → user clicks "→ N extrahieren" → server extract → green
```

After:
```
draw bbox → post-draw chip → pick kind+floor → server extract → green
```

**Mechanism.** On every `onPostDrawPick` that completes the
classification (kind is set; floor for grundriss is set; view for
ansicht/schnitt is set), call `extractScenes` for that single bbox
and remove it from the draft set. Replace the optimistic UI
("→ N extrahieren") with the chip's "Erledigt" state.

**Edge cases:**
- `kind === 'detail'` (no sub-step): extract immediately after kind
  pick. Today there's no floor/view step for Detail, so a single
  click on the chip extracts.
- Bbox without classification: the user can still draw and leave
  unclassified — but the chip stays open until classified, and the
  bbox stays a draft. No batch button.
- The "Bbox anpassen" action on a green scene already converts back
  to draft. Pair with the auto-extract flow: as soon as the user
  re-classifies / re-shapes and clicks "Übernehmen", re-extract.

**Removes:** the "→ N Szene extrahieren" sidebar button, the
`missingKinds` warning, the `onExtract` aggregate handler. The
`extract-draft` localStorage cache lifetime drops to "until the
user finishes classifying", which is seconds.

**Acceptance:**
- Draw bbox, click `G` then `K` (Grundriss + KG): scene is green
  before the chip closes.
- Toast confirms `✓ Grundriss KG extrahiert`.

### A2 — Auto-save annotations

**Flow change.**

Before:
```
draw label → dirty=true → Cmd+S (or autosave 30s) → server PUT
```

After:
```
draw label → dirty=true → debounced 400 ms → server PUT
```

**Mechanism.** Remove the autosave toggle, the Cmd+S button, the
`Speichern` topbar button, the dirty pill. Every state change that
today calls `setDirty(true)` schedules a 400 ms debounced save via
the existing `saveRef.current?.()`. The save success silently
updates `dirty=false`; failure surfaces as a small persistent error
toast with a manual "Erneut versuchen" action.

**Edge cases:**
- Concurrent saves: each scheduled save replaces a pending one;
  the in-flight save is awaited before the next one starts. A small
  "↻ speichert…" pip can replace the "● ungespeichert" pill so the
  user has *some* signal — but it's auto-fading, not a CTA.
- Offline / 5xx: keep `dirty=true`, retry on next change or on a
  10 s timer; surface as a small persistent toast.
- `beforeunload` guard stays: belt-and-braces if the debounce timer
  hasn't fired yet.

**Removes:** `Speichern (Cmd+S)` button, autosave toggle in the
settings menu, dirty pill, `Cmd+S` shortcut (or keeps it as a
"force-save now" power-user shortcut — leaning toward keep).

**Acceptance:**
- Draw a wall → 400 ms later the scene's `labels.json` on disk
  reflects it.
- Pull the network cable → label edits accumulate as `dirty=true`
  with a "↻ offline — wird gespeichert sobald online" toast → on
  reconnect, single save catches up.

### A3 — Undo / redo for extract-side mutations

**Operations to undo:**
- Extract bbox → undo restores it as a draft with the same
  classification.
- Delete scene → undo restores the dataset entry + the labels file
  (server-side soft delete first).
- Adjust scene (back to draft) → undo restores the original
  extracted scene with its labels.
- Classify scene (kind/floor/view change via the popover) → undo
  restores the previous value.

**Mechanism.** Client-side action log identical to AnnotatePage's
`undoStackRef`:

```ts
type ExtractAction =
  | { kind: 'extract'; file: string; previousDraft: DraftBbox }
  | { kind: 'delete'; manifestEntry: DatasetDrawing; labels: SceneLabels | null }
  | { kind: 'adjust'; originalDrawing: DatasetDrawing; newDraftId: string }
  | { kind: 'classify'; file: string; before: Partial<DatasetDrawing>; after: Partial<DatasetDrawing> };
```

`Cmd+Z` / `Cmd+Shift+Z` work on this stack same as AnnotatePage.

**Server requirement:**
- For undo-of-delete we need the labels JSON back. Either keep a
  ~30 s server-side trash bin (`tmp/recycle/<key>/<file>.json` +
  manifest snapshot) — light to implement — or stop deleting
  outright and just hide; a real delete only ages out after the
  user navigates away.

Recommended: short-TTL recycle bin. New endpoints:

```
POST /datasets/{key}/drawings/{file}/restore -> 200 if recoverable
```

`DELETE` writes to the bin before unlinking; `restore` moves it
back. Auto-prune ≥ 1 h.

**Removes:** the destructive `window.confirm` dialogs at the
ExtractPage level (still keep house-level reset as an explicit
button — destructive at a different scope).

**Acceptance:**
- Extract a bbox → Cmd+Z → bbox is back as a draft.
- Delete a scene → Cmd+Z within 30 s → scene + labels restored.
- After a chain of 3 classifications → Cmd+Z three times walks back
  through them in order.

---

## 3. Order of implementation

A0 first (smallest, fixes the user's reported "bothering" bug).

A1 + A2 share the "auto-persist invariant" mental model; ship them
together so the user doesn't see one save button gone and another
still there.

A3 needs A1 + A2 to be in place (no point undoing a batch you can
still cancel). Ships last in the same wave.

| Wave | Items | Risk |
|---|---|---|
| 1 | A0 | low — single effect-level fallback |
| 2 | A1 + A2 | medium — removes affordances the user is used to seeing |
| 3 | A3 + recycle bin endpoint | medium — new server route + state |

---

## 4. Non-goals

- Server-side undo/redo via DB transactions. Client-side action log
  is sufficient for single-user offline-tolerant work.
- Auto-classify (e.g. guess "EG" from filename). Out of scope; A0 is
  only about propagating what the user *already* picked.
- Replacing `Cmd+Z` semantics on AnnotatePage. The existing stack
  stays; A3 just adds a sibling stack for ExtractPage.
- Real-time multi-user sync. Single user, single device per session.

---

## 5. Open decisions (user input needed before coding)

These are the calls I want the user to make rather than guess on. The
implementation forks for each.

### Q1 — On A0, does the *manifest* or the *labels file* win when both have a value?

When the user later edits `kind` in the U9 popover, that writes the
manifest. When they pick a tool palette tag inside the editor, that
writes the labels file. The two can diverge.

| Option | Pros | Cons |
|---|---|---|
| **Manifest wins** | Single canonical place; popover edits are authoritative. | Editor-side tag selector becomes second-class — changes there get overwritten on next mount unless also synced upstream. |
| **Labels wins (default after first save)** ★ | Editor-side edits feel local + immediate; manifest only seeds the editor on first open. | A later popover edit on a previously-saved scene gets silently shadowed. |
| **Two-way sync via PATCH on every change** | No divergence. | More moving parts; the popover and the tool palette both need to write to both files. |

**Recommendation:** Labels wins after first save; the popover edit
ALSO updates the labels file via a small extension to the existing
PATCH endpoint (it already touches the manifest; teach it to also
update the matching labels file if it exists).

### Q2 — On A1, what happens to bboxes the user draws but never classifies?

Possible policies:

| Option | Behaviour |
|---|---|
| **Forget on tab close** | Drafts live in localStorage until the user classifies them or explicitly discards. (Current behaviour minus the manual extract button.) |
| **Auto-classify as Detail after N seconds** | A 10 s idle timer extracts unclassified bboxes as Detail. The user can re-classify later via the popover. |
| **Block scene-page navigation while drafts exist** | The user must finish before leaving. Heavy-handed. |

**Recommendation:** First option. The "Entwurf verwerfen" link in
the sidebar stays as the only way out for unclassified bboxes.

### Q3 — On A2, is `Cmd+S` deleted or kept as "force flush"?

Auto-save handles ~99% of the cases. Power users sometimes want to
force a flush before doing something risky (closing the laptop,
running a script that reads the labels file).

**Recommendation:** Keep `Cmd+S` but only as a no-op-feedback
shortcut (cancels the pending debounce and saves *now*). No
on-screen button. The shortcut keeps muscle memory; the chrome goes.

### Q4 — On A2, what shows up when a save is in flight or has failed?

The current `dirty` pill is a CTA. With auto-save it becomes status
display. Options:

| Option | Behaviour |
|---|---|
| **No indicator** | Silent. User only sees the failure toast. |
| **Tiny dot** ★ | A 6 × 6 px dot near the breadcrumb: amber while saving, red on failure, hidden when clean. No CTA, just state. |
| **Persistent "↻ speichert" pill** | Loud. Distracting on a busy canvas. |

**Recommendation:** Tiny dot. Hover tooltip with last save time.

### Q5 — On A3, how long does the recycle bin keep deleted scenes?

| Option | Note |
|---|---|
| **Until next house switch** | Recycle bin lives per-house, cleared on navigation. |
| **30 s** | Maps to the "I changed my mind" window. Aggressive auto-purge. |
| **1 h** ★ | Survives the user walking away for coffee. Cron purges after. |
| **Forever (no purge)** | Bin grows. Need a UI to manage. |

**Recommendation:** 1 h. Implementation: write to
`tmp/recycle/<key>/<scene>.tar` (the JPG + the labels JSON + the
manifest-entry snapshot). Purge in a background sweep on app boot
+ a server-side cron.

### Q6 — On A3, does undo work across scenes?

If the user extracts a bbox in scene-A, then opens scene-B, can
`Cmd+Z` from scene-B still undo the extract from scene-A?

| Option | Note |
|---|---|
| **House-scoped global stack** ★ | One stack per house. Undo can reach back into other scenes; UI focus follows the action. |
| **Per-scene stack** | Cleaner mental model; loses cross-scene chains. |
| **No undo on page navigation** | Simplest; loses the "I extracted then went to label, oh wait" recoverability. |

**Recommendation:** House-scoped global stack. The stack lives in a
React context above the SceneStrip. On undo, the action's `before`
state determines which scene the UI navigates to first.

### Q7 — On A2, do we save partial gestures (in-flight polyline) or only completed ones?

A polyline is multiple clicks. The user might be in the middle of
drawing wall #3 when the debounce fires. Three options:

| Option | Behaviour |
|---|---|
| **Save on every commit (Enter / closed shape)** ★ | Persists only complete geometries. Matches the user's mental model. |
| **Save the in-flight state too** | More granular but pollutes the labels file with transient state. |
| **Save on completion + on idle (no input for 2 s)** | Hybrid; survives a crash mid-polyline but the file has half-drawn things. |

**Recommendation:** First option. The user's request was "once
elements are finished (e.g. a polygon)". This matches.

---

## 6. Risks

- **A1 + a slow server.** If the server is slow to extract, the user
  sees their classification "stick" with no green feedback for 1–3 s.
  Today the manual extract button gives them an "Extrahiere…" state.
  Mitigation: the post-draw chip stays open with a small spinner
  until the server returns. Also need a clear retry path on 5xx.
- **A2 + the user navigating mid-debounce.** Save is scheduled but
  the user clicks a scene chip before the 400 ms timer fires. The
  router navigation `await`s the pending save (we already added a
  `saveRef`-based hook for this on scene-switch; extend it to all
  navigations).
- **A3 + the recycle bin trust window.** If the user deletes scene X,
  closes their laptop, opens it 2 h later, the bin is empty. The
  user expects undo to still work. Mitigation: in the toast that
  fires on delete, show the 1 h window explicitly and offer "✕
  Endgültig löschen" to opt out of the bin (instant hard-delete).
- **A0 + a stale manifest cache.** The editor reads from
  `houseDataset` which is `useResource`-cached. If the user edits
  the manifest in another tab, the cache is stale. Acceptable for
  single-user-single-device; flag for the multi-device future.

---

## 7. Acceptance summary (when the wave is done)

The user opens a freshly-extracted Grundriss EG scene. They see:
- The breadcrumb says `Datensatz › house-22 › house-22-floorplan-eg.jpg`.
- The SceneDetailsCard at the top reads `Grundriss EG`, `unannotiert`.
- The tool palette is in Grundriss mode (Wand / Tür / Fenster), no
  "Typ wählen" gate.

They draw a wall. 400 ms later the disk file reflects it. No
"Speichern" button anywhere. A tiny amber dot near the breadcrumb
flashed for half a second; gone now.

They press Cmd+Z. The wall disappears. Cmd+Shift+Z brings it back.

They go back to the house overview. They draw a new bbox on page 4,
press G then K (Grundriss + Keller). The bbox turns green inside a
second; no extract button needed. They press Cmd+Z. The green
overlay disappears; the bbox is a draft again. They press Cmd+Z
again — the bbox is gone.

They delete `house-22-detail-detail-2.jpg`. Toast says "Szene
gelöscht — 1 h Rückgängigkeits-Fenster". They press Cmd+Z. The
scene is back in the strip and in the manifest, with its old labels.

Nothing in this scenario asked them to confirm or save.

---

## 8. Follow-ups (deferred for now)

These trade scope for the wave shipping today. Each is a future
iteration if the user surfaces the pain.

- **Classify undo via the popover.** The U9 `SceneDetailsCard`
  PATCH path isn't pushed onto the A3 action log yet — the user can
  re-edit the popover for the same effect. Adding it needs the
  card to expose `onBeforeChange(prev, next)` to the host page.
- **House-scoped stack survives navigation.** The current stack
  lives in `ExtractPage` state and dies on a house switch.
  Persisting via a top-level React context would let the user undo
  after walking away and back. Low priority; ExtractPage is the
  only producer.
- **`Adjust extracted`** (extracted → draft) isn't yet wrapped as
  an `extract` undo action. The existing flow still has its own
  confirm dialog. Folding it into the action log lets Cmd+Z reverse
  it cleanly.
- **In-flight save indicator off-canvas.** When the user is in the
  WorkflowGuide popover (Phase editing), the SaveStateDot in the
  topbar may be off-screen. Could echo the dot near the popover or
  surface as a tiny banner if a save fails while the user is heads-
  down editing.
