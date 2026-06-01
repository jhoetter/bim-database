# Code quality + technical debt (CQ) tracker

**Status:** 2026-06-01. Initial audit captured. No CQ items shipped yet.

**Owner:** jhoetter
**Scope:** maintainability, architectural boundaries, test/contract drift,
repo hygiene, and debt that increases the cost/risk of future feature work.

---

## Mission

The annotation pipeline has grown into a working product, but several
surfaces have accumulated too much responsibility. This tracker turns the
audit into concrete work items with severity, evidence, and target outcomes.

North-stars for this tracker:

1. **Green baseline first.** Refactors must start from passing tests, or
   explicitly document which failing contract is being corrected.
2. **One source of truth per domain concept.** Workflow phases, geometry
   requirements, label schema rules, and export readiness must not be
   independently reimplemented in the UI, MCP layer, API, and tests.
3. **Thin adapters, thick domain modules.** FastAPI routes and MCP tools
   should adapt transport to domain functions; they should not own core
   business rules.
4. **Keep corpus data out of ordinary code churn.** Source PDFs, generated
   scene crops, and mutable label outputs need explicit boundaries so code
   reviews remain legible.

---

## Severity scale

| Severity | Meaning |
|---|---|
| Blocker | Current state is unsafe to build on; must be fixed before broad refactors or releases. |
| High | Material maintainability or correctness risk; fix in the next refactor wave. |
| Medium-high | Not immediately blocking, but likely to cause data loss, contract drift, or expensive refactors if left alone. |
| Medium | Compounding debt; schedule after high-severity boundaries are in place. |
| Low-medium | Mostly hygiene, but likely to confuse contributors or automation if it drifts into committed state. |
| Low | Hygiene or polish; fix opportunistically once higher-risk work is controlled. |

---

## 1. Findings

### A. Test baseline is red (F1)

**Severity:** Blocker

`make test` on 2026-06-01 produced:

```
381 passed, 4 failed, 3 skipped
```

All failures are in `tests/test_honest_gate.py` and come from stale workflow
phase expectations:

- tests expect `state["phases"]["W1"]`
- tests expect `state["phases"]["Wgeo"]`
- `mcp_server._derive_workflow_state()` now returns
  `inventory`, `floorplans`, `sections`, `elevations`, `review`

Evidence:

- `tests/test_honest_gate.py` lines 64-99 still describe the old `Wgeo`
  contract.
- `mcp_server.py` lines 507-513 builds the current phase map.

Risk:

The failure is concentrated, but it is a contract drift signal. Any refactor
of export readiness, workflow gates, or agent stop conditions will be hard to
trust while tests and implementation speak different vocabularies.

### B. `AnnotatePage.tsx` is a god component (F2)

**Severity:** High

`ui/src/pages/AnnotatePage.tsx` is approximately 9,587 LOC. It owns:

- route/resource loading
- labels state
- scene metadata propagation
- house facts sync
- localStorage preferences
- autosave
- undo/redo
- snapping and drawing state
- pointer/keyboard handlers
- workflow panel wiring
- canvas rendering
- label inspector callbacks
- several nested UI components

Evidence:

- state cluster begins around `AnnotatePage.tsx:606`
- main pointer-down handler begins around `AnnotatePage.tsx:1301`
- main render tree begins around `AnnotatePage.tsx:2817`
- many helper components live later in the same file, extending the file to
  ~9.6k LOC

Risk:

Small behavior changes require editing a very broad file. Hook dependencies
are difficult to reason about, extracted helper libs cannot fully protect the
app because the orchestration remains centralized, and regression tests tend
to become expensive end-to-end tests instead of cheap unit tests.

### C. `api/main.py` is a god API module (F3)

**Severity:** High

`api/main.py` is approximately 3,887 LOC and mixes route registration with
storage, validation, rendering, PDF extraction, export, cache handling,
recycle/restore, and SPA fallback behavior.

Evidence:

- dataset and house facts routes start near `api/main.py:186`
- plan routes start near `api/main.py:326`
- label pathing and validation start near `api/main.py:785`
- grid/render routes start near `api/main.py:1938`
- PDF extraction is a 240-line function at `api/main.py:3102`
- export implementation begins around `api/main.py:3372`
- reset/recycle/restore routes begin around `api/main.py:3695`

Risk:

The API has no clear transport/domain/storage boundary. Feature additions
tend to append more code to `main.py`, and storage behavior is difficult to
test without importing the whole FastAPI app.

### D. `mcp_server.py` is both adapter and domain owner (F4)

**Severity:** High

`mcp_server.py` is approximately 4,628 LOC. It owns transport wrappers,
tool definitions, workflow derivation, export readiness checks, label
mutation helpers, summaries, and prompts.

Evidence:

- tool registration starts at `mcp_server.py:219`
- `_derive_workflow_state()` starts at `mcp_server.py:410`
- label mutation helpers start around `mcp_server.py:2964`
- export readiness starts at `mcp_server.py:3751`
- prompt definitions start around `mcp_server.py:4293`

Risk:

The MCP layer should be a thin adapter over stable API/domain contracts.
Instead, important business logic lives only in MCP. That creates inverted
dependencies and makes non-agent callers second-class.

### E. Workflow and geometry contracts are duplicated/drifting (F5)

**Severity:** High

Workflow readiness and geometry completeness appear in multiple places:

- `mcp_server._REQUIRED_GEOMETRY`
- `mcp_server._derive_workflow_state`
- `api/export_gate.py`
- `api/scene_plan_state.py`
- `ui/src/lib/workflow.ts`
- `tests/test_honest_gate.py`

Evidence:

- `api/export_gate.py:24` lazy-imports `_REQUIRED_GEOMETRY` and
  `_missing_geometry` from `mcp_server.py`.
- `tests/test_honest_gate.py:64` still references `Wgeo`, while current
  runtime phases are named differently.
- README says the MCP server has "22 tools" at `README.md:99`, but the
  current `mcp_server.py` exposes many more tools.

Risk:

Export gates, UI badges, and agent stop conditions can disagree. This is a
correctness risk, not just style debt.

### F. JSON-file persistence is scattered and weakly bounded (F6)

**Severity:** Medium-high

The API directly reads/writes JSON files from many route functions and helper
functions. This is acceptable for a local single-user app only if it is
intentional and bounded; today it is spread across the codebase.

Evidence examples:

- house facts write: `api/main.py:235`
- scene attrs manifest patch: `api/main.py:279`
- labels read/write: `api/main.py:1040`
- extraction manifest write: `api/main.py:3321`
- export file writes: `api/main.py:3421`
- scene plan state writes: `api/scene_plan_state.py:282`
- fact derivation writes: `api/fact_derivation.py:616`

Risk:

There is no central place for atomic writes, file locking, backup/rollback,
JSON validation, or corruption logging. Cross-route behavior is hard to
audit, and concurrent tabs or agent runs can overwrite each other.

### G. UI canonical state is split between localStorage and server files (F7)

**Severity:** Medium-high

`house_facts` is cached in localStorage but also pushed to
`data/dataset/<key>/house_facts.json`.

Evidence:

- `loadHouseFacts()` reads localStorage at `ui/src/lib/house_facts.ts:255`
- `saveHouseFacts()` writes localStorage and schedules server push at
  `ui/src/lib/house_facts.ts:266`
- `syncHouseFactsFromServer()` makes the server copy win when available at
  `ui/src/lib/house_facts.ts:302`
- `AnnotatePage.tsx` also keeps several editor and house-level values in
  localStorage directly.

Risk:

Best-effort sync is pragmatic for a single-user browser, but the current
rules are implicit. It is easy for cross-tab work, agent writes, or manual
JSON edits to produce surprising overwrites.

### H. Tracked mutable corpus data pollutes the code repo (F8)

**Severity:** Medium-high

The repository tracks source PDFs, generated scene crops, label JSONs, and
house facts under `data/`. The current working tree contains many modified
data files plus a deleted tracked PNG and an untracked replacement JPG.

Evidence from audit:

- `git status --short` showed many modified files under
  `data/dataset/house-22/` and `data/pdfs/incoming/`
- `git ls-files data` counted 49 tracked data paths
- tracked binary/data payload was roughly 96 MB, including large PDFs
- `data/dataset/house-22/house-22-floorplan-eg.png` is tracked but missing
  in the current working tree

Risk:

Normal code reviews become noisy. Reverts become risky because they may
discard labeling work. It is hard to distinguish fixtures from live corpus
state.

### I. Broad exception handling hides important failure modes (F9)

**Severity:** Medium

There are many `except Exception` blocks, especially in `api/main.py`, plus
some no-op catches in UI/localStorage code.

Evidence examples:

- `api/main.py` has broad catches around manifest and plan route handling.
- `mcp_server.py` catches broad exceptions in summary/reporting paths.
- UI localStorage code commonly swallows errors.

Risk:

Some broad catches are reasonable at adapter boundaries, but repeated broad
catches without structured logging make corrupt JSON, failed sync, or partial
writes harder to diagnose.

### J. Python dependency/tooling discipline is thin (F10)

**Severity:** Medium

`requirements.txt` is unpinned and contains runtime and test dependencies in
one file. There is no visible Python formatter/linter/typecheck target.

Evidence:

- `requirements.txt` uses unpinned names such as `fastapi`, `pillow`,
  `PyMuPDF`, `opencv-python-headless`, `pytest`.
- `Makefile:test` runs pytest only.
- frontend has `npm run typecheck`; Python has no analogous static gate.

Risk:

Fresh installs can drift. Refactors have fewer automated guardrails against
import cycles, unused code, broad exceptions, or typing regressions.

### K. Frontend package-manager state is locally mixed (F11)

**Severity:** Low-medium

`ui/package-lock.json` is tracked and npm scripts are canonical, but an
untracked `ui/pnpm-lock.yaml` exists locally.

Risk:

Low while untracked, but if committed it would create ambiguous install
instructions and reproducibility confusion.

### L. Documentation is stale in operationally relevant places (F12)

**Severity:** Low-medium

README and comments contain details that no longer match implementation.

Evidence:

- README says `make web` exists in quick start, but the current Makefile has
  `dev`, `dev-api`, and `dev-web`, not `web`.
- README says the MCP server has 22 tools; current `mcp_server.py` registers
  far more.
- `tests/test_honest_gate.py` still documents `Wgeo`.

Risk:

New contributors and agents will follow stale contracts. This is especially
costly in this repo because the agentic workflow reads these docs as
operational truth.

### M. `ExtractPage.tsx` is the second frontend god page (F13)

**Severity:** Medium-high

`ui/src/pages/ExtractPage.tsx` is approximately 2,122 LOC. It is smaller
than `AnnotatePage.tsx`, but still combines PDF page rendering, bbox draft
state, localStorage draft persistence, grid preferences, scene extraction,
delete/restore actions, post-draw classification, scene-strip rendering, and
page controls.

Evidence:

- static scan counted ~242 `const`/`function` declarations in the file.
- existing comments describe localStorage draft persistence, extraction
  auto-persist, click timers, undo-like behavior, delete/reset flows, and
  grid rendering in the same page module.

Risk:

Extraction is the workflow step that creates dataset structure. Keeping the
draft state machine, server mutation calls, and view rendering in one page
makes it harder to guarantee idempotency and undo behavior.

### N. Scene-plan gate evaluator is too large and stateful (F14)

**Severity:** High

`api/scene_plan_state.py` is approximately 1,889 LOC. Most of the module is
cohesive around scene plans, but `evaluate_gates()` is a 378-line function
that mutates state, records evidence, computes label counts, upserts defects,
updates gates, derives task statuses, computes terminality, writes the state,
and renders Markdown.

Evidence:

- `evaluate_gates()` starts at `api/scene_plan_state.py:1169`
- AST scan measured it at 378 LOC
- defect generation, stale-evidence handling, task gate mutation, status
  derivation, and write/persist behavior all happen in the same function

Risk:

Gate behavior is correctness-critical for the agentic workflow. A large
stateful evaluator is hard to unit-test in pieces and makes it easy for a
new gate to accidentally change terminality or task reopening behavior.

### O. Route surface is broad and ungrouped (F15)

**Severity:** Medium-high

`api/main.py` registers roughly 70 routes in one module, spanning metadata,
datasets, house facts, plans, labels, submissions, incoming PDFs, rendering,
CV helpers, geometry editing, extraction, exports, reset, recycle, restore,
and SPA fallback.

Evidence:

- route scan found endpoints from `/datasets` through
  `/datasets/{key}/{file}/plan-state/...`, `/labels/...`, `/pdfs/...`,
  `/datasets/{key}/{file}/score-walls`, `/geometry/connect-corners`,
  `/exports/...`, and catch-all SPA fallback.
- route decorators run from `api/main.py:76` through `api/main.py:3881`.

Risk:

Route ordering and catch-all behavior become fragile. It is hard to reason
about which routes are public, destructive, cacheable, or safe for agent use.

### P. Schema/type contract is hand-maintained in multiple languages (F16)

**Severity:** Medium-high

The canonical label schema is `schema/scene_labels.schema.json`, while the
frontend mirrors it manually in `ui/src/api/types.ts`. Backend validation also
contains hand-written semantic guards such as `_LABEL_TYPES_BY_SCENE_TAG`.

Evidence:

- `schema/scene_labels.schema.json` defines 7 label variants under
  `$defs.Label`.
- `ui/src/api/types.ts` manually mirrors the 7-label union.
- schema still allows `scope: "house"` while frontend/backend comments say
  only `dataset` survives post-R0.
- `_LABEL_TYPES_BY_SCENE_TAG` in `api/main.py:840` duplicates palette rules
  also represented in frontend tool gating.

Risk:

Schema, frontend types, and backend semantic validation can diverge. Some
drift is already visible in `scope`. This undermines the value of strict
TypeScript and JSON schema because neither is generated from the other.

### Q. Security boundary depends on deployment discipline (F17)

**Severity:** Medium-high

The developer API explicitly allows all CORS origins and exposes destructive
or mutating routes without authentication. README documents this as
localhost-only, but the app itself does not enforce localhost binding or auth.

Evidence:

- `api/main.py:45` sets `allow_origins=["*"]`, `allow_methods=["*"]`,
  `allow_headers=["*"]`.
- README warns at `README.md:39` that the developer server is
  single-user-localhost and must not be exposed without auth.
- mutating routes include uploads, extract, delete/reset, export, and label
  writes.

Risk:

The risk is acceptable only if deployment discipline is perfect. Since the
same repo contains source PDFs and customer submission flows, accidental
exposure would be high impact.

### R. Test suite has large smoke/integration concentrations (F18)

**Severity:** Medium

The test suite is substantial, but some coverage is concentrated in large
integration/smoke files.

Evidence:

- `tests/test_mcp_smoke.py` is ~1,271 LOC with 49 tests.
- `tests/test_scene_plans.py` is ~883 LOC with 24 tests.
- `tests/test_fact_derivation.py` is ~473 LOC with 32 tests.

Risk:

Large smoke files are valuable, but they are slower to understand and often
encode multiple contracts per test. As refactors proceed, failures may point
at broad behavior instead of the small domain function that changed.

### S. Generated/cache artifacts are inconsistently ignored (F19)

**Severity:** Low-medium

The working tree includes generated/cache-style files such as
`ui/tsconfig.tsbuildinfo`, `ui/test-results/`, `ui/dist/`, and an untracked
`ui/pnpm-lock.yaml`. Some are already ignored, some are local only, and some
are visible in ordinary filesystem scans.

Risk:

This is mostly hygiene, but generated artifacts make audits noisy and can
accidentally enter commits if ignore rules are incomplete.

---

## 2. Work items

Work item IDs (`CQ*`) are remediation tasks. They intentionally do not map one-to-one to finding IDs (`F*`), because several findings share the same root cause.

### CQ0 — Restore a green test baseline

**Severity:** Blocker
**Status:** Open

Fix the stale `tests/test_honest_gate.py` expectations or intentionally
restore compatibility aliases for the old phase vocabulary.

Acceptance:

- `make test` passes.
- The test names and assertions use the current workflow vocabulary, or a
  compatibility layer is documented and tested.
- The fixed tests still prove the original bug: facts-only scenes cannot pass
  as geometry-complete.

### CQ1 — Extract workflow/geometry contracts from `mcp_server.py`

**Severity:** High
**Status:** Open

Create a pure shared module, likely `api/workflow_state.py` or
`api/workflow_contracts.py`, to own:

- required geometry per scene tag
- missing-geometry helper
- workflow phase derivation
- export readiness phase predicates

Acceptance:

- MCP, API export gate, tests, and UI-facing route helpers import the shared
  backend contract instead of importing from `mcp_server.py`.
- `api/export_gate.py` no longer imports from `mcp_server.py`.
- Tests cover the pure module directly.

### CQ2 — Split `api/main.py` into routers and storage helpers

**Severity:** High
**Status:** Open

Target structure:

- `api/app.py` or slim `api/main.py` for app creation/static mounts
- `api/routes/datasets.py`
- `api/routes/labels.py`
- `api/routes/pdfs.py`
- `api/routes/render.py`
- `api/routes/export.py`
- `api/routes/plans.py`
- `api/storage.py` for JSON pathing, atomic writes, and path validation

Acceptance:

- `api/main.py` becomes mostly app assembly.
- Route files do not duplicate path-safety helpers.
- JSON read/write behavior is centralized.
- Existing HTTP tests still pass.

### CQ3 — Split `mcp_server.py` into thin tool modules

**Severity:** High
**Status:** Open

Target structure:

- `mcp_server.py` only creates/registers the server and shared HTTP client
- `mcp_tools/datasets.py`
- `mcp_tools/scenes.py`
- `mcp_tools/plans.py`
- `mcp_tools/labels.py`
- `mcp_tools/geometry.py`
- `mcp_tools/export.py`
- `mcp_prompts.py`

Acceptance:

- Tool behavior remains unchanged.
- Shared envelope/error handling lives in one helper module.
- Domain logic moves out of MCP tools into API/domain modules where possible.

### CQ4 — Break up `AnnotatePage.tsx`

**Severity:** High
**Status:** Open

Initial extraction targets:

- `useAnnotationData()` for fetch/reset/scene navigation data
- `useAnnotationHistory()` for undo/redo
- `useAnnotationAutosave()` for save scheduling and before-unload behavior
- `useCanvasPointerTools()` for pointer handlers and drawing gestures
- `useGridPreferences()` for grid/localStorage state
- `AnnotationCanvas` for SVG render
- `ToolSidebar` / `LabelInspector` / `PlanPanel` as independent components

Acceptance:

- `AnnotatePage.tsx` drops below 2,000 LOC as a first milestone.
- Pointer-tool logic has unit-testable pure helpers where possible.
- Existing frontend typecheck and renderer parity tests still pass.

### CQ5 — Define canonical persistence rules for house facts

**Severity:** Medium-high
**Status:** Open

Write down and enforce whether server JSON or localStorage is canonical for
each fact category.

Acceptance:

- `house_facts` sync rules are documented in code and spec.
- Cross-tab/server overwrite behavior is explicit.
- Server write failures surface at least a warning state, not only console
  output.
- Tests cover server-wins and local-first migration paths.

### CQ6 — Centralize JSON persistence and atomic writes

**Severity:** Medium-high
**Status:** Open

Introduce helpers for:

- safe path construction
- JSON load with typed error context
- atomic JSON write via temp file + replace
- optional backup or corrupt-file quarantine

Acceptance:

- labels, manifests, house facts, and plan states use the shared helpers.
- Broad JSON corruption behavior is tested once at the storage layer and
  lightly at route level.

### CQ7 — Separate fixtures from live corpus data

**Severity:** Medium-high
**Status:** Open

Choose a policy:

- keep small deterministic fixtures tracked under `tests/fixtures/`
- move source PDFs and mutable corpus state to external storage or Git LFS
- ignore generated scene crops/labels unless explicitly promoted as fixtures

Acceptance:

- `.gitignore` reflects the policy.
- README explains how to obtain/restore corpus data.
- `git status` after normal app usage does not show unrelated corpus churn.

### CQ8 — Add Python quality gates

**Severity:** Medium
**Status:** Open

Add minimal, low-friction tooling after CQ0:

- formatter/import sorter
- lint rule for broad exception catches, unused imports, and complexity
- optional typecheck for pure modules first

Acceptance:

- `make verify` runs tests plus agreed quality checks.
- Existing intentional exceptions are explicitly waived with reason.

### CQ9 — Reduce broad exception catches at non-boundary layers

**Severity:** Medium
**Status:** Open

Audit `except Exception` uses and classify:

- adapter-boundary: allowed, must log/map error
- domain-layer: replace with precise exceptions
- best-effort UI/localStorage: allowed, but important sync failures must
  surface to the user or telemetry/logs

Acceptance:

- Broad catches in domain code are removed or justified.
- Route adapters log enough context for corrupt files and failed writes.

### CQ10 — Pin or lock Python dependencies

**Severity:** Medium
**Status:** Open

Adopt a reproducible dependency story:

- pinned `requirements.txt`, or
- `requirements.in` + compiled lock, or
- `pyproject.toml` with lock tooling

Acceptance:

- Fresh installs are reproducible.
- Runtime and test/dev dependencies are separated or clearly documented.

### CQ11 — Clean package-manager and generated artifacts

**Severity:** Low-medium
**Status:** Open

Keep npm as the canonical frontend package manager unless intentionally
switching.

Acceptance:

- `ui/pnpm-lock.yaml` is removed or documented/committed as canonical.
- generated files such as `tsconfig.tsbuildinfo`, test-results, and dist
  outputs are ignored or intentionally tracked.

### CQ12 — Refresh README and tracker references

**Severity:** Low-medium
**Status:** Open

Update docs after CQ0/CQ1 so operational instructions match the code.

Acceptance:

- README quick start uses existing Makefile targets.
- MCP tool count wording avoids stale numbers or is generated.
- Workflow vocabulary in docs/tests is consistent.

### CQ13 — Split `ExtractPage.tsx`

**Severity:** Medium-high
**Status:** Open

Initial extraction targets:

- `useExtractionDrafts()` for bbox drafts and localStorage persistence
- `usePdfPageView()` for page rendering/page navigation
- `useExtractionMutations()` for extract/delete/restore/reset server calls
- `ExtractCanvas` for page image, grid, bbox drawing, and selection
- `ExtractSceneStrip` shared with annotation where possible

Acceptance:

- `ExtractPage.tsx` drops below 1,000 LOC as a first milestone.
- Draft persistence and extraction idempotency have focused tests.
- Existing extraction route tests and frontend typecheck still pass.

### CQ14 — Decompose `scene_plan_state.evaluate_gates()`

**Severity:** High
**Status:** Open

Split the evaluator into pure or mostly pure pieces:

- evidence ingestion
- label summary computation
- defect synthesis
- task gate evaluation
- terminality derivation
- persistence/render wrapper

Acceptance:

- `evaluate_gates()` becomes orchestration under ~100 LOC.
- Pure pieces have direct tests for defect and terminality behavior.
- Existing scene-plan route tests still pass.

### CQ15 — Inventory and classify API routes

**Severity:** Medium-high
**Status:** Open

Before splitting routers, create a route inventory with categories:

- read-only
- mutating
- destructive
- rendering/cache
- agent/CV helper
- public submission
- SPA/static fallback

Acceptance:

- The inventory lives in this tracker or a generated doc.
- Destructive routes have explicit auth/deployment assumptions.
- Router split in CQ2 follows this classification.

### CQ16 — Generate or validate TS types from JSON schema

**Severity:** Medium-high
**Status:** Open

Choose one canonical source for label contracts.

Options:

- generate `ui/src/api/types.ts` from JSON schema
- generate JSON schema from TypeScript
- add schema/type drift tests if generation is too heavy

Acceptance:

- `scope` vocabulary is reconciled (`dataset` only, or documented legacy).
- Label variants and enum values cannot drift silently.
- Palette rules are shared or tested across frontend/backend.

### CQ17 — Make developer API exposure fail-safe

**Severity:** Medium-high
**Status:** Open

Keep local development easy, but add guardrails for accidental exposure.

Acceptance:

- CORS and host assumptions are configurable.
- Destructive routes can require an opt-in token outside localhost/dev.
- README and `.env.example` document the safe defaults.

### CQ18 — Split large smoke tests into contract-focused suites

**Severity:** Medium
**Status:** Open

Refactor large tests only after CQ0/CQ1 so the baseline is stable.

Acceptance:

- MCP transport smoke tests stay smoke-level.
- Workflow, scene-plan, and fact-derivation contracts move into focused pure
  unit tests where possible.
- Test names map to one behavioral contract each.

### CQ19 — Normalize generated/cache artifact policy

**Severity:** Low-medium
**Status:** Open

Acceptance:

- `.gitignore` explicitly covers generated TypeScript build info, local
  Vite/test artifacts, and non-canonical lockfiles.
- The repo documents which build outputs, if any, are intentionally tracked.

---

## 3. Suggested order of work

1. **CQ0** — green test baseline.
2. **CQ1** — shared workflow/geometry contract.
3. **CQ14** — decompose scene-plan gate evaluation while the contract is fresh.
4. **CQ15 + CQ2 + CQ6** — API route inventory, router split, storage boundary.
5. **CQ3** — MCP tool modularization after the contract is no longer MCP-owned.
6. **CQ4 + CQ13** — frontend editor/extraction page breakup.
7. **CQ5 + CQ7 + CQ16 + CQ17** — persistence, corpus, schema, and exposure boundaries.
8. **CQ8-CQ12 + CQ18-CQ19** — tooling, tests, docs, and artifact hygiene.

---

## 4. Audit notes

Commands run during the initial audit:

- `find ... wc -l` excluding `.venv`, `node_modules`, `.claude`, `data`,
  `tmp`, and caches
- Python AST scan for longest functions/classes
- `grep`/`rg` scans for broad catches, TODO/legacy/fallback markers, JSON
  read/write sites, and localStorage use
- `make test`
- `cd ui && npm run typecheck`
- `git status --short`
- `git ls-files data`
- API route decorator inventory
- schema/TypeScript type comparison
- test-file size/count scan

Baseline from 2026-06-01:

- Frontend typecheck passed.
- Backend pytest failed only in `tests/test_honest_gate.py`.
- Largest source files excluding dependencies/data:
  - `ui/src/pages/AnnotatePage.tsx` ~9,587 LOC
  - `mcp_server.py` ~4,628 LOC
  - `api/main.py` ~3,887 LOC
  - `ui/src/pages/ExtractPage.tsx` ~2,122 LOC
  - `api/scene_plan_state.py` ~1,889 LOC
- `api/main.py` exposes roughly 70 routes.
- `api/scene_plan_state.evaluate_gates()` is ~378 LOC.
- `tests/test_mcp_smoke.py` is ~1,271 LOC.
