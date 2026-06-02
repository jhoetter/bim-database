# Code-Quality Tracker — bim-database (+ bim-agent MCP consumption)

> **Scope.** Deep code-quality audit of `bim-database`, analyzed primarily through the lens of
> its MCP server and how `bim-agent` consumes it.
> **Date.** 2026-06-02
> **Method.** Full read of `mcp_server.py` (5,995 lines) and `api/main.py` (4,758 lines) in chunks;
> skim of all large `api/` helper modules; cross-reference of `tests/`; trace of every
> bim-agent → bim-database consumption point (filesystem, spawned MCP stdio, direct REST).
> **Repos.** `~/repos/bim-database` (focus), `~/repos/bim-agent` (consumer).

---

## How to read this tracker

Each item has a stable ID (`C#` critical, `H#` high, `M#` medium, `L#` low, `I#` integration).
Status values: `OPEN` / `IN-PROGRESS` / `DONE` / `WONTFIX`. Update inline as work proceeds.

Criticality is **risk-to-the-system**, not effort. An item can be Critical *and* a 1-day fix
(C1/C2 are exactly that).

### Severity summary

| ID | Title | Severity | Effort | Status |
|----|-------|----------|--------|--------|
| C1 | Non-atomic JSON writes → corruption on crash | 🔴 Critical | S | ✅ DONE (80f4a5f) |
| C2 | No locking → lost updates on concurrent writes | 🔴 Critical | M | ✅ DONE (dd9f98e) |
| H1 | TOCTOU in optimistic plan-state version check | 🟠 High | S | ✅ DONE (dd9f98e, via C2) |
| H2 | Full-house fact recompute on every label write | 🟠 High | M | OPEN |
| H3 | MCP transport-error contract honored by only ~half the tools | 🟠 High | M | OPEN |
| H4 | Destructive reset tools have zero test coverage | 🟠 High | S | OPEN |
| H5 | `api/main.py` is a 4,758-line god router | 🟠 High | L | OPEN |
| M1 | Broad `except Exception` swallowing corruption | 🟡 Medium | M | OPEN |
| M2 | Geometry utilities duplicated across 3–4 modules | 🟡 Medium | S | OPEN |
| M3 | Inconsistent magic constants for the same operation | 🟡 Medium | M | OPEN |
| M4 | MCP HTTP plumbing duplicated across 78 tools | 🟡 Medium | M | OPEN |
| M5 | No request models / no return types / non-uniform responses | 🟡 Medium | L | OPEN |
| M6 | `scene_plan_state.py` + `topology_repair.py` lack dedicated unit tests | 🟡 Medium | M | OPEN |
| L1 | CORS wide open (`allow_origins=["*"]`) | 🟢 Low | S | OPEN |
| L2 | ~735 lines of prompt text embedded in `mcp_server.py` | 🟢 Low | S | OPEN |
| L3 | FastMCP private-internal access (`_tool_manager._tools`) | 🟢 Low | S | OPEN |
| L4 | ~40 lazy in-body imports hide dependency graph | 🟢 Low | M | OPEN |
| L5 | Deprecated `asyncio.get_event_loop()` at shutdown | 🟢 Low | S | OPEN |
| L6 | Misc: stray f-string, import-time `mkdir`, 18-param tool | 🟢 Low | S | OPEN |
| I1 | Absolute `/home/jhoetter/...` paths committed in config | 🟠 High | S | OPEN |
| I2 | Three parallel, drifting access paths to bim-database | 🟠 High | M | OPEN |
| I3 | Tight, unvalidated envelope-shape coupling | 🟡 Medium | M | OPEN |
| I4 | Dead `BimDatabaseClient` advertising an unenforced contract | 🟡 Medium | S | OPEN |
| I5 | Duplicated CMD-V3 / envelope schema across repos | 🟡 Medium | M | OPEN |
| I6 | No version/tool-set handshake between agent and server | 🟢 Low | S | OPEN |

Effort key: **S** ≈ < ½ day · **M** ≈ ½–2 days · **L** ≈ multi-day refactor.

---

## God-file inventory

These are the structural root causes that most of the items below hang off. Listed largest × most critical first.

| File | Lines | Bytes | Role | Maint. score | Why it's a god file |
|------|-------|-------|------|--------------|---------------------|
| `mcp_server.py` | 5,995 | 248 KB | 78 MCP tools, all module-level, no classes | 6/10 | One stdio server; uniform but copy-pasted boilerplate per tool; ~735 lines of embedded prompt text |
| `api/main.py` | 4,758 | 195 KB | 88 routes | **3/10** | Fuses routing + JSON persistence + business rules + geometry math + PIL rendering + PDF subprocess into one module |
| `api/scene_plan_state.py` | 2,448 | — | plan-state subsystem | 4/10 | Cohesive but huge; ~600-line `evaluate_gates`; racy writes; no dedicated test file |
| `api/label_render.py` | 945 | — | grid label rendering | 5/10 | `render_grid_with_labels` is L98–434 (337 lines) with a 255-line inline body and 9 flag args |
| `api/topology_repair.py` | 893 | — | repair-candidate heuristics | 5/10 | Heuristic-/magic-number-heavy; duplicated geometry utils; untyped dict contracts; no test file |
| `api/fact_derivation.py` | 642 | — | house-fact derivation | 6/10 | Two ~140-line functions; drives the costly per-write recompute (H2) |
| `api/grid_render.py` | 633 | — | grid overlay rendering | 7/10 | **Best-factored renderer** — small `_draw_*` helpers, one mid-size orchestrator, good tests |
| `api/wall_topology.py` | 405 | — | wall topology QA | 7/10 | Small, well-named helpers; one dispatcher; tested. Only loses points for duplicated `_dist` (see M2) |

**bim-agent side (consumer):** `scripts/testhouse_drive.py` (5,804 lines — the single largest source file in either repo),
`services/semantic_authoring.py` (1,971), `services/source_ingestion.py` (1,940), `services/source_agent_loop.py` (1,411),
`reverse_bim/__init__.py` (1,214). These mix orchestration + IR construction + prompt generation + I/O. Out of primary
scope (the focus is bim-database) but noted because they surround the integration boundary.

> **Note on the two scores.** `mcp_server.py` (6 KB lines) scores *higher* than `main.py` (4.7 KB lines) because the MCP
> server is *uniformly* god-shaped — repetitive but consistent, with no concern-mixing — whereas `main.py` fuses every
> architectural layer into its route handlers. Size ≠ the worst problem; concern-mixing is.

---

# 🔴 CRITICAL

## C1 — Non-atomic JSON writes → corruption on crash
**Severity:** 🔴 Critical · **Effort:** S · **Status:** ✅ DONE (80f4a5f) · **Related:** C2

> **Resolution.** Added `api/persistence.py` with `atomic_write_json` /
> `atomic_write_text` (temp-file + fsync + `os.replace`). Wired through all ~30
> data write sites (manifests, labels, house_facts, plan-state JSON+Markdown,
> exports). Self-healing cache sentinels intentionally left as bare writes.
> Regression test in `tests/test_persistence.py` proves the original file
> survives an interrupted write with no stranded temp., H2

**Locations (representative; ~33 write sites total):**
- `api/main.py:300` — manifest / drawing patch write
- `api/main.py:1422` — labels write (`put_labels`)
- `api/main.py:1457` — house-facts write
- `api/scene_plan_state.py:317` — plan-state write
- `api/scene_plan_state.py:321` — plan-state write
- All other `write_text` / `json.dump` sites in `api/`

**What is going on.** Every persisted artifact — manifests (`data/dataset/<key>/manifest.json`),
per-scene labels (`labels/<stem>.json`), house facts (`house_facts.json`), plan state
(`plans/<stem>.plan.json`) — is written with a bare `path.write_text(json.dumps(...))`. There is **no
write-to-temp-then-`os.replace()`** (which is atomic on the same filesystem), and **no `fsync`**. The
only `tempfile` usage in the codebase is for PDF rasterization (`main.py:3937`), not for data persistence.

`write_text` truncates the target to zero length and then streams the new bytes. If the process dies
between truncation and completion, the file is left **truncated or empty** — i.e. invalid JSON. The next
read throws `json.JSONDecodeError`, which (per M1) is often swallowed and silently re-interpreted as
`labeled=False`, so corruption can manifest as *silent data loss* rather than a loud failure.

**Why it matters / blast radius.** This is the single most dangerous defect in either repo **specifically
because of the host hardware**: the Ryzen 1700X box hard-crashes every few hours from uncorrected CPU MCEs
(see `~/crash-monitor/`). A random hard crash *will* eventually land inside one of these writes. Because
every label write also fans out into a full-house facts rewrite (H2), the window and the number of files in
flight per logical operation are both larger than they look. Corruption of `manifest.json` or
`house_facts.json` can invalidate an entire house's labeling state.

**Evidence.** 33 `write_text`/`json.dump` sites; zero `os.replace`/tempfile-rename in the persistence path;
zero `fsync`.

**Fix.** Add a single `api/persistence.py` helper and route every data write through it:

```python
def atomic_write_json(path: Path, obj, *, indent=2) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=indent))
    os.replace(tmp, path)   # atomic rename on same filesystem
```

Swap all ~33 call sites. ~20 lines of helper + mechanical replacement. Combine with C2 so the temp-write
happens under the lock.

**Acceptance.** A regression test that (a) writes a large doc, (b) simulates an interrupted write (e.g.
patch `write_text` to raise mid-way / kill via truncated temp), and asserts the original file is still
valid JSON afterward.

---

## C2 — No locking → lost updates on concurrent writes
**Severity:** 🔴 Critical · **Effort:** M · **Status:** ✅ DONE (dd9f98e) · **Related:** C1, H1, H2

> **Resolution.** Added `api/persistence.locked_path()` — a process-local mutex
> keyed by absolute path plus an advisory `fcntl.flock` sidecar — held across
> the whole read→mutate→write. Wired around every genuine RMW: house_facts
> (`recompute_facts_after_label_write` now a locked wrapper over
> `_recompute_facts_impl`, `prune_scene_from_facts`, `_persist_scene_calibration`,
> `put_house_facts`), the manifest (`patch_scene_attrs`), and plan/plan-state
> (`write_plan_state`, `save_plan`). No nested same-path acquisition (prune
> releases before the locked recompute). Concurrency tests in
> `tests/test_persistence.py` (30-thread no-lost-update, mutual exclusion).
> **Note:** the full H2 fix (scope/debounce the per-write recompute) is still
> open — C2 serializes facts writers but does not eliminate the per-label
> full-house recompute.

**Locations:** all persistence paths. Most acute:
- `api/main.py:1373–1441` — `put_labels` performs read-modify-write with **no version check at all**
- `api/scene_plan_state.py:311–317` — has a version check but no lock (see H1 for the TOCTOU detail)

**What is going on.** There is **no concurrency control of any kind** — no `fcntl.flock`, no
`threading.Lock`, no `asyncio.Lock`, anywhere in `api/`. The FastAPI route handlers are synchronous `def`
functions, which FastAPI runs in a worker **threadpool**, so two requests genuinely execute in parallel.
Any handler that does read-file → modify-in-memory → write-file can interleave with another:

```
req A: read labels.json (v1)
req B: read labels.json (v1)
req A: write labels.json (v2 = v1 + change A)
req B: write labels.json (v2' = v1 + change B)   # change A is now lost
```

`put_labels` is the worst case: it has no `expected_version` guard whatsoever, so it is unconditionally
**last-writer-wins**.

**Why it matters / blast radius.** The agentic labeling loop and any human/UI activity can issue
overlapping writes to the same house. Lost label updates are silent and only discovered later as "the model
said it placed that label but it's gone." Combined with the per-write full-house recompute (H2), a label
write and a concurrent reset (`reset_house_labeling` `shutil.rmtree`s `labels/` at `main.py:1496`) can race
into a half-deleted, half-recomputed state.

**Evidence.** No lock primitives present in `api/`; `put_labels` has no version field; handlers are sync
`def`.

**Fix.** Per-file advisory lock around every read-modify-write. Cleanest: a small context manager keyed by
absolute path that takes `fcntl.flock` (or a process-local `threading.Lock` registry keyed by path if
single-process is guaranteed). Hold the lock across read → modify → atomic-write (C1). Apply uniformly to
labels, manifest, facts, and plan-state writes.

**Acceptance.** A test spinning N concurrent writers against the same file and asserting all N updates are
present (no lost updates) and the file is always valid JSON.

---

# 🟠 HIGH

## H1 — TOCTOU in the optimistic plan-state version check
**Severity:** 🟠 High · **Effort:** S · **Status:** ✅ DONE (dd9f98e, via C2) · **Related:** C2

> **Resolution.** `write_plan_state` (and `save_plan`) now hold `locked_path`
> across the version comparison *and* the write, so two writers with the same
> `expected_version` can no longer both pass and clobber.

**Location:** `api/scene_plan_state.py:311–317` (`write_plan_state`); version hash at L112–114
(`version_for_state` = sha256 of sorted JSON).

**What is going on.** `write_plan_state` is the *one* place that attempts concurrency safety: it re-reads
the file, compares its `version_for_state` to the caller's `expected_version`, and writes only if they
match. But there is **no lock held between the comparison and the write** — a classic time-of-check /
time-of-use gap. Two writers that both read the same `expected_version` will both pass the check and both
write, so the optimistic-concurrency guarantee is illusory under real concurrency.

**Why it matters.** It gives a *false sense of safety* — the code looks concurrency-aware, so a reviewer may
assume plan-state writes are protected when they are not. Plan-state drives the workflow phase machine;
losing a state transition can desync the agent's notion of "what to do next."

**Evidence.** Re-read + compare + write sequence at L311–317 with no surrounding lock.

**Fix.** Fold into C2: acquire the per-file lock *before* the version comparison and hold it through the
atomic write. Once locked, the version check becomes correct (and can stay as a cheap conflict-detector for
clients).

---

## H2 — Full-house fact recompute on every single-scene label write
**Severity:** 🟠 High · **Effort:** M · **Status:** OPEN · **Related:** C1, C2

**Locations:** `api/main.py:1432` (`recompute_facts_after_label_write` call on every `PUT /labels`) →
`api/fact_derivation.py:481–619`.

**What is going on.** Each time a single scene's labels are written, the API recomputes the *entire house's*
facts: it reads the manifest **plus every scene's `labels/*.json`** for that house and rewrites
`house_facts.json`. So one one-line label edit triggers O(scenes) disk reads + a full facts rewrite.

**Why it matters / blast radius.**
1. **Performance:** write cost scales with house size, not edit size. For a house with many scenes, the
   agentic loop (which writes labels frequently) pays a large, repeated I/O tax.
2. **Correctness/safety:** the recompute reads all label files while another request may be deleting them.
   `reset_house_labeling` does `shutil.rmtree(labels/)` at `main.py:1496`; a recompute racing that gets a
   `FileNotFoundError` mid-loop or computes facts from a partially-deleted set. With no locking (C2), this
   is unguarded.
3. **Amplifies C1:** more files touched per logical operation = larger crash-corruption window.

**Evidence.** `recompute_facts_after_label_write` invoked unconditionally in the label-write path; reads
manifest + all scene label files per call.

**Fix.** (a) Scope/debounce the recompute to the touched scene where derivation allows, or mark facts dirty
and recompute lazily/batched; (b) guard the recompute against concurrent reset via the C2 lock; (c) if full
recompute must stay, at minimum make it read-consistent under a lock and atomic on write.

---

## H3 — MCP transport-error contract honored by only ~half the tools
**Severity:** 🟠 High · **Effort:** M · **Status:** OPEN · **Related:** M4

**Locations:**
- Guard **present** (30 sites): e.g. `mcp_server.py:250` (`list_houses`), `mcp_server.py:293` (`get_house`)
- Guard **absent** (uses bare `_api_*`): `mcp_server.py:2169` (`get_scene_plan_state`),
  `2188` (`get_scene_plan_status`), `2242` (`get_scene_plan_next_actions`),
  `2281` (`start_scene_plan_action`), and others among the 57 bare `_api_*` calls
- Working abstraction already exists: `_cv_get`/`_cv_post` at `mcp_server.py:2752–2778` (used by 14 CV tools)

**What is going on.** The intended contract is that every MCP tool, on a backend transport blip, retries
once via `_wait_for_api()` (polls `/datasets` up to 10s) and, if still down, returns a clean
`api_unreachable` error **envelope**. This guard is hand-rolled inline in ~30 tools:

```python
try:
    status, body = await _api_get("/datasets")
except (httpx.HTTPError, httpx.RequestError):
    if not await _wait_for_api():
        return _api_unreachable_error(started)
    status, body = await _api_get("/datasets")
```

But many tools call `_api_get`/`_api_post` directly with no guard. When the API blips, those tools raise a
**raw `httpx` exception out of the tool** instead of the clean envelope. So the same failure mode produces
two different behaviors depending on which tool the model happened to call.

**Why it matters.** The MCP server's whole value proposition to the LLM is a *uniform, machine-readable
envelope* (`{ok, data, error}`). A raw exception breaks that contract: the model gets an opaque transport
error it can't reason about (vs. an `api_unreachable` with `retry=True` it knows how to handle). It makes the
agent's failure handling nondeterministic across tools. The fix is also a large duplication win (M4).

**Evidence.** `grep -c "if not await _wait_for_api():"` = 30; 57 bare `_api_*` calls; `_cv_get`/`_cv_post`
already fold retry+error+`_ok` into one helper but were only adopted by the CV tools.

**Fix.** Promote `_cv_get`/`_cv_post` (or a single shared `_call(method, path, ...)`) to *the* way every tool
talks to the backend, and migrate all 78 tools onto it. Removes the inline retry blocks and makes the
`api_unreachable` contract universal. Do this alongside M4.

---

## H4 — Destructive reset tools have zero test coverage
**Severity:** 🟠 High · **Effort:** S · **Status:** OPEN

**Locations:** `mcp_server.py:3695` (`reset_scene_labels`), `3730` (`reset_house_labeling`),
`3763` (`reset_house_dataset`). Backing route `reset_house_labeling` does `shutil.rmtree(labels/)` at
`api/main.py:1496`.

**What is going on.** 22 of 78 MCP tools (28%) are never referenced in any test. That gap unfortunately
includes the **three tools that delete data**. There is no test asserting their scope (that
`reset_scene_labels` touches one scene, not the house; that `reset_house_dataset` doesn't escape the
dataset dir), their envelope shape, or their behavior on missing/already-empty targets.

**Why it matters / blast radius.** These are the highest-blast-radius operations in the system — they
`rmtree` real data. An accidental scope bug (wrong key interpolation, path traversal via a crafted
`key`/`file`) deletes the wrong house. They are also the tools most likely to race the recompute path (H2).
Untested destructive operations + no atomic/locked persistence (C1/C2) is the worst combination in the repo.

**Evidence.** Coverage cross-reference: 22/78 tools untested; the three reset tools among them; also untested:
the full repair-candidate flow (`get_scene_repair_candidates`, `get_scene_view_with_repair_candidate`,
`apply_repair_candidate`, `decide_repair_candidate`), most CV tools, and `upsert_wall_anchored`.

**Fix.** Add smoke tests (the suite already patches `_http` to an in-process ASGI transport) that, against a
fixture house: assert each reset's *scope*, assert the returned envelope, assert idempotency on an
already-empty target, and assert a malicious `key`/`file` cannot escape the dataset directory. Do this
**before** any refactor that touches these paths.

---

## H5 — `api/main.py` is a 4,758-line god router
**Severity:** 🟠 High · **Effort:** L · **Status:** OPEN · **Related:** M1, M2, M5, L4

**Location:** `api/main.py` (entire); 88 route decorators, 150 function definitions, one `@app` instance, no
`APIRouter` split.

**What is going on.** A single module fuses every architectural layer:
- **Routing** — 88 endpoints on one `app`
- **Persistence** — 33 raw JSON read/write sites inline in handlers
- **Business rules** — label dependency validation (`_validate_dependent_labels` L1245–1362), scene-tag
  palette gating
- **Geometry math** — `_wall_bbox_region`, `_floorplan_opening_axis`, `_wall_ink_overlap`
- **Rendering** — inline PIL overlay drawing (L941–979, L3472–3589)
- **PDF** — `fitz`/`pdftoppm` subprocess rasterization (L3928–3963)
- **Export bundling** — L4243–4357

Largest functions: `extract_scenes` L3967–4225 (259 lines, does validation + PDF open + per-item crop /
expand / blank-guard + manifest mutation + write, all inline); `upsert_wall_anchored_route` L2602–2738
(137); `export_preview` L4444–4566 (123); `render_scene_grid_with_labels` L3472–3589 (118);
`_validate_dependent_labels` L1245–1362 (118); `_export_one_house` L4243–4357 (115).

**Why it matters.** Every change to any concern means editing this file; merge conflicts and accidental
cross-concern breakage are likely. It defeats type-checking (pervasive `dict[str, Any]`, no return types —
see M5). It's the structural reason C1/C2/M1/M2 are spread everywhere instead of localized. The ~30
`plan-state/*` routes (L326–996) alone are a self-contained subsystem.

**Evidence.** 88 `@app.*` decorators, 150 defs, the function line-ranges above.

**Fix (incremental, not big-bang).** Carve cohesive subsystems into `APIRouter` modules one at a time:
`routes_plan_state.py` (the ~30 plan-state routes), `routes_labels.py`, `routes_pdf.py`, `routes_export.py`,
plus a `persistence.py` (the C1/C2 home). Keep `main.py` as app wiring + startup. Each extraction is
independently shippable and testable.

---

# 🟡 MEDIUM

## M1 — Broad `except Exception` swallowing corruption
**Severity:** 🟡 Medium · **Effort:** M · **Status:** OPEN · **Related:** C1

**Locations:** 35 occurrences in `api/main.py` (44 across `api/`); 4 in `mcp_server.py`.
- `api/main.py:156–161`, `181–182`, `1006–1007` — label/manifest enrichment swallow-all (`# noqa: BLE001`)
- `mcp_server.py:3827` (`_current_action_write_warning`), `3950` (`upsert_label` wall-anchoring check),
  `4533`, `4970` — silently swallow all errors; 3950 marks anchoring "unchecked" on *any* failure

**What is going on.** Bare `except Exception:` blocks (many `pass` or set a default) catch *everything*,
including `json.JSONDecodeError` from a corrupt file and `OSError` from a real I/O fault. The
enrichment paths then mark a house `labeled=False` and move on, so a corrupt or unreadable file looks
identical to an unlabeled one. In the MCP server, a genuine backend bug in the anchoring check becomes an
invisible "unchecked."

**Why it matters.** This is the mechanism that turns C1 corruption into *silent* data loss instead of a loud
error. It also masks bugs during development — failures that should surface as 500s or test failures are
swallowed.

**Fix.** Narrow each `except` to the specific expected exceptions (`json.JSONDecodeError`, `OSError`,
`httpx.HTTPError`), and on the corruption-relevant paths surface a warning field (e.g. `{"corrupt": true}`)
rather than collapsing to `labeled=False`. Log the swallowed exception at minimum.

---

## M2 — Geometry utilities duplicated across 3–4 modules
**Severity:** 🟡 Medium · **Effort:** S · **Status:** OPEN · **Related:** H5

**Locations:**
- `_as_point` — `api/main.py:1130`, `api/scene_plan_state.py:1097`, `api/topology_repair.py:88`
  (three different signatures/bodies; the last also accepts tuples)
- `_wall_segment` — `api/scene_plan_state.py` (after its `_as_point`) and `api/topology_repair.py:95`
- `_point_seg_distance` — `api/wall_topology.py:45` and `api/geometry_checks.py:85` (different arg shapes)
- `_dist` — `api/wall_topology.py:25` and `api/topology_repair.py:103`

**What is going on.** Core point/segment geometry primitives are reimplemented in multiple modules, with
subtly divergent signatures. This is classic copy-paste drift: a fix or precision change to one copy doesn't
propagate, and the divergent signatures make it easy to call the "wrong" variant.

**Why it matters.** Geometry correctness is load-bearing for wall topology, repair candidates, and label
placement. Divergent duplicates are a latent source of "works in one route, subtly wrong in another" bugs.

**Fix.** Create `api/geometry_util.py` with one canonical `Point`/`as_point`, `segment`, `dist`,
`point_seg_distance`. Replace all duplicates; reconcile the signature differences explicitly.

---

## M3 — Inconsistent magic constants for the same operation
**Severity:** 🟡 Medium · **Effort:** M · **Status:** OPEN

**Locations:**
- Wall-scoring `tol_px`: `18` (`main.py:1215`), `9` (`2782`), `8` (`2825`)
- `min_wall_px`: `8` / `16` (`3221`) / `12` (`3259`)
- `close_px`: `82` / `0`
- `dpi` bound: `<=0 or >600` (`main.py:2098`) vs `<=0 or >1200` (`4048`)
- Ink-overlap threshold `0.6` (`main.py:1239`); pads `pad=40` (`922`), `pad_px=96` (`1193`), `-20/+20`
  (`topology_repair.py:138–141`), `pad=24` (`147`)

**What is going on.** The *same conceptual operation* uses different constants depending on which route or
helper invokes it. Wall scoring with `tol_px=18` in one route and `tol_px=8` in another will produce
different F1 scores for identical geometry, and there's no documented reason for the divergence.

**Why it matters.** (1) Correctness: a caller can't reason about results without knowing which constant fired.
(2) The bim-agent reference driver keys off `score-walls` `f1` to decide whether to keep a pass (see I3) — if
the threshold a tool uses differs from what the driver expects, passes get silently kept/reverted wrongly.
(3) Tuning is impossible when the knob has three values.

**Fix.** A single named profile table (e.g. `SCORE_PROFILES = {"strict": {...}, "default": {...}}`) shared by
all callers; pass a profile name, not raw constants. Document each value. Centralize the `dpi` bound in one
validator.

---

## M4 — MCP HTTP plumbing duplicated across 78 tools
**Severity:** 🟡 Medium · **Effort:** M · **Status:** OPEN · **Related:** H3

**Locations:** `mcp_server.py` — 30 inline retry blocks; 41 `_http_status_to_error` call sites; the four
verb helpers `_api_get/_api_post/_api_put/_api_delete` (L114–156) each carry an identical 4-line
json-decode block (L119–123, 134–138, 143–147, 152–156); `started = time.time()` opens all 78 tools.

**What is going on.** Each tool re-implements the same plumbing: stamp `started`, call the backend, handle
transport errors, map status to an error envelope, decode JSON. The `_cv_get`/`_cv_post` helpers
(L2752–2778) already prove this can be one function — they're just only used by the 14 CV tools.

**Why it matters.** 78× duplication means every new tool copies ~8–10 lines of boilerplate, the file keeps
growing, and inconsistencies creep in (H3 is exactly this — the duplication wasn't applied uniformly).

**Fix.** Collapse the four verb helpers into one parametrized `_api(method, path, ...)`; have every tool call
a single `_call`-style wrapper (the `_cv_*` pattern) that does started-stamp + retry + status-mapping +
decode. Strongly coupled with H3 — do them together. Expect the file to shrink by several hundred lines.

---

## M5 — No request models / no return types / non-uniform responses
**Severity:** 🟡 Medium · **Effort:** L · **Status:** OPEN · **Related:** H5

**Locations:** `api/main.py` — all 88 routes. Bodies are mostly `dict = Body(...)` with ad-hoc `.get()` +
manual `isinstance`; **no route has a `-> ReturnType` annotation**; return types vary (raw list from
`list_datasets`, bare on-disk dict from `get_labels`, `{"ok": True, "data": ...}` from plan-state routes,
`Response`/`FileResponse` from renders).

**What is going on.** Without Pydantic request models there's no schema validation and no generated OpenAPI
body docs; without return types and a uniform envelope, clients (including bim-agent — see I3) must
special-case each route's response shape.

**Why it matters.** Validation gaps let malformed input reach business logic; the non-uniform responses are
the server-side half of the tight, fragile coupling bim-agent has to specific response shapes (I3).
Pervasive `dict[str, Any]` defeats static analysis on the data layer.

**Fix.** Introduce Pydantic body models per route family and a consistent response convention (either the
`{ok, data}` envelope everywhere data is returned, or a documented exception list for binary/file routes).
Best done route-family-by-family during the H5 router extraction.

---

## M6 — Largest stateful modules lack dedicated unit tests
**Severity:** 🟡 Medium · **Effort:** M · **Status:** OPEN · **Related:** H4

**Locations:** `api/scene_plan_state.py` (2,448 LOC, incl. ~600-line `evaluate_gates`) and
`api/topology_repair.py` (893 LOC) — neither has a dedicated `tests/test_*.py` file.

**What is going on.** These two modules carry the most state-machine and heuristic logic in the backend, yet
they're only exercised *indirectly* through MCP/scene-plan smoke tests, which cover happy paths — not the
branch matrix of `evaluate_gates` or the clustering/candidate-generation edge cases in `topology_repair`.
(For contrast, the small geometry helpers — `wall_score`, `wall_topology`, `corner_detect`, `snap`, etc. —
*do* have dedicated unit tests.)

**Why it matters.** `evaluate_gates` decides workflow progression; an untested branch means the agent can be
advanced/blocked incorrectly with no test to catch it. `topology_repair` produces the candidates that
`apply_repair_candidate` mutates real data with.

**Fix.** Add `tests/test_scene_plan_state.py` covering the gate-evaluation branch matrix, and
`tests/test_topology_repair.py` covering finding-extraction, clustering, and candidate generation with
hand-built fixtures. Pair with H4 (the destructive-tool tests).

---

# 🟢 LOW

## L1 — CORS wide open
**Severity:** 🟢 Low · **Effort:** S · **Status:** OPEN
**Location:** `api/main.py:46–50` — `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`.
**What/why.** Acceptable for localhost dev, but it's committed as the default. If this server is ever exposed
beyond `127.0.0.1`, any origin can drive it. **Fix:** restrict origins via env/config; default to localhost.

## L2 — ~735 lines of prompt text embedded in `mcp_server.py`
**Severity:** 🟢 Low · **Effort:** S · **Status:** OPEN
**Location:** `mcp_server.py:5229–5959` — `prompt_*` / `resource_*` functions are mostly hardcoded
multi-page prompt strings (`prompt_label_house` L5229–5403 alone is ~175 lines).
**What/why.** This is *content*, not logic, inflating the god file and making prompt edits a code change.
**Fix.** Move prompt bodies to resource files (e.g. `prompts/*.md`) loaded at startup; keep the tool/resource
registration thin.

## L3 — FastMCP private-internal access
**Severity:** 🟢 Low · **Effort:** S · **Status:** OPEN
**Location:** `mcp_server.py:5979` (`_apply_tool_profile`) — `getattr(mcp._tool_manager, "_tools", {})` then
`mcp.remove_tool(...)`.
**What/why.** Reaches into FastMCP private internals. A FastMCP upgrade that renames `_tool_manager`/`_tools`
makes tool-profile filtering **silently no-op** (the `getattr` default `{}` swallows the breakage). **Fix.**
Use a public FastMCP API if available; otherwise assert the attribute exists and fail loudly if it doesn't.

## L4 — ~40 lazy in-body imports hide the dependency graph
**Severity:** 🟢 Low · **Effort:** M · **Status:** OPEN · **Related:** H5
**Location:** `api/main.py` — ~40 `from .xxx import yyy` statements inside route bodies (e.g. L264, 330, 339,
363, 422, 429, …), used to dodge import cycles.
**What/why.** Hides the real module dependency graph and defers import errors to *request time* instead of
boot. **Fix.** Resolve the underlying import cycle (the H5 extraction helps) and hoist imports to module top.

## L5 — Deprecated `asyncio.get_event_loop()` at shutdown
**Severity:** 🟢 Low · **Effort:** S · **Status:** OPEN
**Location:** `mcp_server.py` `main()` finally block (L5984+) —
`asyncio.get_event_loop().run_until_complete(_http.aclose())`.
**What/why.** `get_event_loop()` is deprecated/fragile post-3.10 and can raise at shutdown, leaving the HTTP
client unclosed. **Fix.** Use `asyncio.run(...)` / `anyio` for the client close, or close within the existing
loop context.

## L6 — Misc small smells
**Severity:** 🟢 Low · **Effort:** S · **Status:** OPEN
- Stray `f`-string with no placeholder: `mcp_server.py:4158`
  (`f"orientation must be 'horizontal' or 'vertical'"`).
- Import-time side effects: `api/main.py:61,69` `mkdir` (`tmp/exports-cache`, `ui/dist/assets`) run on
  `import api.main`. **Fix.** Move to a startup hook.
- 18-parameter tool `get_scene_view_with_labels` (`mcp_server.py:785–803`) and 12-param `get_scene_view`.
  **Fix.** Group rendering options into a typed options object.

---

# 🔗 INTEGRATION (bim-agent → bim-database)

> **Consumption mechanism (3 paths, not 1).**
> **Path A — Filesystem (dominant runtime path).** Reverse-BIM/ingestion reads bim-database as a directory
> tree via `BIM_DATABASE_PATH` (`bim-agent/app/bim_agent/config.py:16`, default `~/repos/bim-database`);
> `scripts/testhouse_drive.py:71` stages `house-N.pdf` + `house-N/` renders. No protocol — `pathlib`/`shutil`.
> **Path B — Spawned MCP over stdio (live agentic path).** `scripts/label_drive.py` spawns
> `bim-database/.venv/bin/python mcp_server.py` (`mcp_session()` L413), lists tools, converts them to
> Anthropic tool defs (L584–592), and **lets the Claude model emit the tool calls**; the driver routes
> `tool_use` blocks back through `session.call_tool(...)` (L629). bim-agent's own code does **not** call the
> tools by name in the hot loop.
> **Path C — Direct REST to `:12500`** (`scripts/label_drive_ref.py`, `BASE` L25) for the deterministic
> reference path; `label_drive.py:730` also probes `:12500/datasets` as a health check.
> **Operational requirement:** Paths B/C need *two* bim-database processes live — the FastAPI on `:12500`
> **and** the spawned stdio MCP shim (the MCP server only proxies to `:12500`).
> **Integration health score: 6/10.**

## I1 — Absolute `/home/jhoetter/...` paths committed in config
**Severity:** 🟠 High · **Effort:** S · **Status:** OPEN
**Locations:** `bim-agent/.mcp.json:4–5` (`command`/`args` hardcode the absolute venv + script path);
`bim-agent/scripts/label_drive.py:755–756` (default MCP cmd); `app/bim_agent/mcp_client/bim_database.py:29`.
**What is going on.** The committed MCP registration hardwires `/home/jhoetter/repos/bim-database/.venv/bin/python`
and the absolute `mcp_server.py` path. A fresh checkout, a relocated venv, or another machine silently fails
to launch the server (Path B gives an opaque stdio spawn failure — see I2 failure modes).
**Why it matters.** Non-portable; ties the integration to one machine's home dir. Given the multi-machine
setup (remote Linux box + Mac), this is an active footgun.
**Fix.** Make `.mcp.json` resolve via `${BIM_DATABASE_PATH}` / a relative launcher (the env var already
overrides it — just stop committing the literal home path). Centralize per I2.

## I2 — Three parallel, drifting access paths with three env vars
**Severity:** 🟠 High · **Effort:** M · **Status:** OPEN · **Related:** I1
**Locations:** `BIM_DATABASE_PATH` (`config.py:16`), `BIM_DATABASE_MCP_CMD` (`label_drive.py:753–761`),
`BIM_DATABASE_API_BASE` (`.mcp.json:7`).
**What is going on.** Three independent ways to reach the same dependency (filesystem root, spawned-MCP
command, REST base URL), each with its own default and its own env var, maintained separately.
**Why it matters.** They can drift out of sync (e.g. `BIM_DATABASE_PATH` points at one checkout while the MCP
cmd launches another), producing confusing "the agent sees stale/wrong data" failures.
**Fix.** One `config` helper resolves the bim-database location; derive the MCP launch command and the API
base from it so all three paths agree by construction.

## I3 — Tight, unvalidated envelope-shape coupling
**Severity:** 🟡 Medium · **Effort:** M · **Status:** OPEN · **Related:** M3, M5
**Locations:** `label_drive.py:459–463` (`_extract_text`/`_fetch_workflow_state` assume a single
`TextContent` carrying JSON `{ok, data, error}`), `:484` (`envelope["data"]["houses"][*]["key"]` /
`["has_labels"]`), `label_drive_ref.py:133/141` (assumes `wall-outline` → list or `{"masses":[{"polygon":...}]}`,
`score-walls` → `{"f1": float}`).
**What is going on.** The bulk of the tool surface is loosely coupled (discovered dynamically, handed to the
LLM). But a handful of reach-ins parse specific nested response shapes with **no schema validation**.
**Why it matters / what breaks on drift.**
- Rename/remove `get_workflow_state` or `list_houses` → `next`-house resolution and the phase loop hard-fail
  (KeyError/RuntimeError).
- Drop the `{ok, data, error}` envelope → every `_extract_text` / `["data"]` access throws.
- Rename `has_labels`/`key` → **silent wrong-house selection** (no error).
- Change `score-walls` from `f1` → the reference driver always reports "not improved" and reverts every pass
  — a **silent regression** with no error.
**Fix.** Validate responses against a shared Pydantic envelope model and fail loud on shape drift; add a
contract test pinning the `list_houses` row schema and the `score-walls` `f1` key.

## I4 — Dead `BimDatabaseClient` advertising an unenforced contract
**Severity:** 🟡 Medium · **Effort:** S · **Status:** OPEN
**Location:** `bim-agent/app/bim_agent/mcp_client/bim_database.py` (the `BimDatabaseClient` /
`bim_database_mcp()` wrapper with typed `get_ontology`/`list_houses`/`get_house` helpers).
**What is going on.** This typed client has **zero non-test callers** in the entire repo (the live driver uses
the spawned-MCP path instead). Its docstring claims "~6 tools" while the server now exposes 78. It advertises
a contract nobody enforces and that silently rots.
**Why it matters.** A future developer may wire against this stale wrapper believing it reflects the server.
Dead code that *looks* authoritative is worse than no code.
**Fix.** Either delete it, or wire `label_drive.py` to use it (so it's exercised and kept honest). If kept,
fix the docstring and regenerate the typed helpers from the live tool list.

## I5 — Duplicated CMD-V3 / envelope schema across repos
**Severity:** 🟡 Medium · **Effort:** M · **Status:** OPEN
**Locations:** `bim-agent/app/bim_agent/cmd_builders.py:49` (re-implements bim-ai's `schemaVersion="cmd-v3.0"`
bundle format); `mcp_client/bim_ai.py:64–71` (re-declares the `revision_conflict` body shape); the
`{ok, error}` envelope re-declared ~15× across `app/bim_agent/`.
**What is going on.** Wire-contract definitions (CMD-V3 bundle, envelope) are copy-pasted rather than imported
from a shared source. Note the cross-repo lineage: bim-agent originated from bim-ai and shares live
references — so these duplicates can drift against *two* upstreams.
**Why it matters.** Schema drift across repos is silent until a payload fails to parse at runtime.
**Fix.** Extract a shared `bim-wire` contract package (or at minimum a single envelope model in
`app/bim_agent/models/`) imported everywhere. Cross-check bim-ai before changing the CMD-V3 shape.

## I6 — No version/tool-set handshake between agent and server
**Severity:** 🟢 Low · **Effort:** S · **Status:** OPEN
**Location:** `bim_database.py:4` (docstring drift "~6 tools" vs 78); no assertion at
`session.initialize()`.
**What is going on.** The agent spawns the server and lists tools but never asserts a minimum server version
or required tool set. Drift is only discovered when a specific call fails mid-run.
**Fix.** At `session.initialize()`, assert a min tool set / version and fail fast with a clear message if the
server is older/incompatible than the driver expects.

---

# What's good (do not regress)

- **LLM-context engineering in `mcp_server.py` is the standout.** Uniform `_ok`/`_err`/`_meta` envelope
  (L82–111); `_truncate_lists` (L2781–2800) with explicit `truncation:{returned,total,omitted}` metadata;
  `_compact_plan_mutation_response` (L2803–2877, `plan-mutation-summary/v1`) so a one-line write doesn't echo
  hundreds of lines into context; three-mode image delivery (`inline`/`handle`/`auto`, L1315–1369) with
  sha256 file handles + a `cleanup_image_handles` GC tool and `png8` default to save tokens.
- **Centralized config & error mapping in the MCP server.** Single `API_BASE` (L47), shared keep-alive
  `httpx.AsyncClient` (L70–74), clean status→error mapping (`_http_status_to_error` L212–224:
  404→`not_found`, 409→`conflict`, 400/422→`schema_invalid`, 5xx→`retry`).
- **Real integration tests, not mocks.** Smoke tests patch `_http` to an in-process `httpx.ASGITransport`
  pointed at the FastAPI app (`tests/test_mcp_smoke.py` L37–48) — genuine end-to-end MCP→API coverage with no
  network. Dedicated tests for the context machinery (`test_mcp_bounded_results`, `test_mcp_context_*`,
  `test_mcp_image_delivery`, `test_mcp_tool_profiles`, `test_mcp_handoff_summary`).
- **Well-factored geometry modules.** `grid_render.py` (7/10) and `wall_topology.py` (7/10) are small,
  single-purpose, and unit-tested. The sprawl is concentrated in `main.py` and the render orchestrators.
- **Mature MCP consumer on the bim-agent side.** `label_drive.py` discovers tools dynamically, converts
  transport errors into model-readable envelopes (`{"ok": false, "error": {"code": "transport_error",
  "retry_advisable": true}}`, L631–634), and orders health checks correctly (SDK/key → `:12500` exit-3 with a
  "run make dev-forwarded" hint → logging API → spawn MCP).
- **No dead/commented-out code or TODO/FIXME rot** in `mcp_server.py`; strong type-hint discipline on params
  (`from __future__ import annotations` set).

---

# Recommended sequencing

1. **C1 + C2 (atomic writes + per-file locking).** Smallest fix with the largest risk reduction; urgent given
   the host's hard-crash history. Land the `persistence.py` helper, migrate all ~33 write sites, add the
   corruption + lost-update regression tests. *(Closes C1, C2; fixes H1 as a side effect.)*
2. **H4 + M6 (test the destructive + stateful paths)** *before* refactoring near them — reset tools,
   `evaluate_gates` branch matrix, `topology_repair` candidates.
3. **H3 + M4 (unify MCP backend calls).** Promote `_cv_get`/`_cv_post` to the single way every tool talks to
   the backend; makes the `api_unreachable` contract universal and removes the bulk of the duplication.
4. **H2 (scope/guard the per-write recompute).**
5. **H5 + M5 + L4 (carve `main.py` into routers; add Pydantic models + return types as you go).** Incremental,
   one router family at a time.
6. **Integration cleanup: I1 + I2 (de-hardcode paths, centralize resolution), then I3/I4/I5/I6** (validated
   envelope, drop/exercise the dead client, shared wire contract, version handshake).
7. **Polish: M1, M2, M3, L1, L2, L3, L5, L6.**

---

*Generated from a deep multi-agent audit on 2026-06-02. IDs are stable; update Status inline as items land.*
