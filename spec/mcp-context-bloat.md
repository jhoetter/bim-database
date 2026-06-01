# MCP context bloat (MCB) tracker

**Status:** 2026-06-01. Initial investigation captured. Implementation
started on branch `mcp-context-bloat-reduction-impl`.

**Owner:** jhoetter
**Scope:** MCP tool schema size, MCP tool result size, visual-inspection
payloads, repeated state reads, agent run structure, and context-window
efficiency for automated house labeling.

---

## Mission

The BIM labeling agent must inspect drawings visually and repeatedly. That
requirement is real; this tracker does not try to remove visual inspection.
It targets the waste around visual inspection:

> The agent should see the pixels needed for the next decision, while old
> pixels, repeated full state, and unused tool schemas do not remain in the
> active model context.

North-stars:

1. **Visual inspection remains first-class.** The agent must still be able
   to request full-scene views, labeled QA views, and tight coordinate crops.
2. **Crop-first by default.** Most label decisions should use bounded crops,
   not full-scene renders.
3. **Summaries cross boundaries.** Scene/phase workers should hand off compact
   durable summaries, not their full visual/tool history.
4. **Compact state is the default.** Full labels, full plan state, and full
   Markdown are debugging tools; normal routing should use summaries.
5. **Measure before and after.** Each mitigation should report prompt-token,
   output-token, image-payload, and tool-call deltas on house-22 or a similarly
   difficult reference house.

---

## 1. External context

The current MCP ecosystem identifies two distinct context-bloat modes:

- **Tool schema bloat:** tool names, descriptions, and input schemas are
  injected into the model context even when many tools are irrelevant.
- **Tool result bloat:** direct tool calls pass intermediate results back
  through the model; large JSON, images, and repeated state reads accumulate
  in the conversation.

The official MCP client best-practices page recommends progressive tool
discovery once tool definitions are a meaningful fraction of the context
window, with a suggested threshold around 1%-5%. It also recommends
programmatic/tool-broker patterns when many intermediate results do not need
to pass through the model.

Atlassian's 2026 MCP compression write-up reports that a single large MCP
server can consume 10k-17k+ tokens of context per request just for tool
descriptions, and describes compression/progressive loading as a way to cut
that cost.

Implication for this repo:

- schema bloat is worth fixing;
- result bloat is likely the larger limiter for visual labeling.

---

## 2. Local investigation summary

Reference run:

- house: `house-22`
- observed transcript:
  `/home/jhoetter/.claude/projects/-home-jhoetter-repos-bim-agent/9b709a81-13c8-45b6-bcfa-6cc03800fa04.jsonl`
- observed MCP server log:
  `tmp/mcp-server.log`
- date of measurement: 2026-06-01

### A. `bim-database` MCP schema size

`mcp_server.py` exposes 73 MCP tools.

Rough static measurement from tool names, signatures, and docstrings:

- docstring chars: ~44,365
- docstring words: ~5,836
- catalog chars rough: ~49,110
- token estimate before JSON Schema overhead: ~12k tokens
- likely loaded schema size with JSON Schema overhead: ~15k-25k tokens

Largest docstrings:

| Tool | Doc chars | Notes |
|---|---:|---|
| `get_scene_view_with_labels` | ~3,550 | image/render QA guidance |
| `get_scene_view` | ~2,815 | image/render guidance |
| `extract_scenes` | ~2,692 | extraction contract |
| `verify_label_placement` | ~2,327 | image QA guidance |
| `upsert_label` | ~2,099 | label schema contract |
| `split_scene` | ~1,939 | extraction/splitting contract |
| `resolve_scene_point` | ~1,922 | coordinate/snap contract |

Assessment:

- This is **moderate schema bloat**.
- It matters for smaller context windows and for clients without deferred
  tool loading.
- It does not by itself explain exhausting a very large context window on
  house-22.

### B. `bim-agent` MCP schema size

`/home/jhoetter/repos/bim-agent/mcp_server.py` dynamically exposes one MCP
tool per FastAPI route under `/api/*`.

Measured dynamic catalog:

- tools: 83
- description chars: ~17,791
- description words: ~2,067
- catalog chars rough: ~20,468
- token estimate before JSON Schema overhead: ~5k tokens

Assessment:

- This is **low-to-moderate schema bloat** by itself.
- It becomes material if loaded together with `bim-database` and other MCP
  servers without deferred discovery.
- The dynamic route-proxy approach is broad and should be phase-filtered if
  it is used in production labeling sessions.

### C. Observed client behavior: deferred tool loading helped

The house-22 Claude transcript contains a `deferred_tools_delta` early in
the run.

Observed initial tool delta:

- total added tools: 62
- `bim-database` tools added: 29
- `bim-agent` tools added: 0

Assessment:

- The client was not naively injecting every available `bim-database` tool.
- The schema problem was partially mitigated by the host.
- Remaining schema improvements are still useful, but they are not the
  primary house-22 limiter.

### D. Tool result and visual payload size

The house-22 main transcript plus subagents was roughly 150 MB of JSONL.

Measured usage across the main run and subagent transcripts:

- assistant usage rows: 10,235
- input tokens sum: ~3.69M
- cache creation input tokens sum: ~782.8M
- cache read input tokens sum: ~2.49B
- output tokens sum: ~58.3M
- max prompt-ish total on repeated rows: ~995k tokens
- image-like result lines: ~730
- lines over 50 KB: ~421
- largest individual tool-result lines: ~2.1-2.5 MB, containing base64 images

Mapped main-session + subagent tool result sizes:

| Tool/result family | Calls | Total JSONL MB | Max KB | Avg KB |
|---|---:|---:|---:|---:|
| `Read` | 1,134 | ~15.06 | ~387 | ~13 |
| `mcp__bim-database__get_scene_view` | 72 | ~14.39 | ~2,547 | ~200 |
| `mcp__bim-database__get_scene_view_with_labels` | 37 | ~5.93 | ~2,101 | ~160 |
| `Bash` | 2,210 | ~3.07 | ~34 | ~1 |
| `upsert_label` | 144 | ~0.14 | ~1.4 | ~1 |

Assessment:

- This is **severe result bloat**.
- Image/render tools dominate MCP result bytes.
- The individual label-write tools are not the bloat source; verification
  images and scene views are.

### E. Repeated state-read fan-out

The MCP server log had:

- startups: 36
- `ListToolsRequest`: 36
- `CallToolRequest`: 992
- HTTP requests: 2,012

Most repeated HTTP paths in the sampled log:

| Count | Request |
|---:|---|
| 173 | `GET /labels/dataset/house-22/house-22-floorplan-eg.jpg` |
| 146 | `GET /labels/dataset/house-22/house-22-floorplan-ug.jpg` |
| 144 | `GET /datasets/house-22` |
| 114 | `GET /labels/dataset/house-22/house-22-floorplan-dg.jpg` |
| 90 | `GET /datasets/house-22/house_facts` |
| 65 | `GET /labels/dataset/house-22/house-22-ansicht-nord.jpg` |
| 44 | `GET /datasets/house-22/house-22-floorplan-eg.jpg/score-walls` |
| 37 | `POST /datasets/house-22/house-22-floorplan-eg.jpg/plan-state/evidence` |

There is also a visible export/status fan-out where the agent fetched
house-22, house facts, labels for all 9 scenes, and plan state for all
9 scenes multiple times in under a second.

Assessment:

- This is **medium-to-severe state bloat**.
- The JSON payloads are smaller than images, but repeated full reads inflate
  both model context and latency.
- Plan state grows large on active floorplans:
  - EG plan JSON: ~296 KB
  - UG plan JSON: ~167 KB
  - DG plan JSON: ~134 KB

---

## 3. Overall diagnosis

### Do we have MCP context bloat?

Yes.

### How severe is it?

| Category | Severity | Explanation |
|---|---|---|
| Static `bim-database` tool schema | Medium | ~15k-25k likely tokens; partially mitigated by deferred loading. |
| Static `bim-agent` tool schema | Low-medium | ~5k+ likely tokens; broad dynamic route surface. |
| Visual tool results | High / severe | Hundreds of image results, some 2 MB+ each, repeatedly pushed into model history. |
| Repeated full state reads | Medium-high | Many duplicate labels/manifest/facts/plan-state calls; plan JSON can be hundreds of KB. |
| Run topology | High / severe | Whole-house long conversations carry old scene visual history forward. |

The limiting factor for house-22 was not primarily "too many little label
tools." The limiting factor was the combination of:

1. frequent inline visual payloads;
2. repeated full state reads;
3. long-running whole-house context accumulation;
4. non-trivial but secondary schema overhead.

---

## 3.1 Implementation progress

Current branch: `mcp-context-bloat-reduction-impl`.

Shipped checkpoints:

- **MCB0 measurement harness:** `scripts/mcp_context_report.py` parses MCP
  tool catalogs, Claude JSONL transcripts, and MCP server logs. Focused tests:
  `tests/test_mcp_context_report.py`.
- **MCB1 compact summaries:** added `get_house_context_summary` and
  `get_scene_context_summary` MCP tools so routing/resume turns can avoid
  full labels + full plan-state reads. Focused tests:
  `tests/test_mcp_context_summary.py`.
- **MCB2 image handles/resources:** image tools now support
  `image_delivery="inline"|"handle"|"both"|"auto"` and persist handle-mode
  renders under `tmp/mcp-images/`. Focused tests:
  `tests/test_mcp_image_delivery.py`.
- **MCB3 crop/auto visual policy support:** `image_delivery="auto"` keeps
  small crops inline but switches larger renders to handles above
  `max_inline_bytes` (default 250 KB, configurable with
  `BIM_MCP_IMAGE_MAX_INLINE_BYTES`). A live sample crop in the dirty
  house-22 worktree reduced returned payload from ~58.6k chars to ~1.5k
  chars in handle mode, about **38.6x**.
- **MCB5 toolset split:** `BIM_MCP_TOOL_PROFILE` can expose role-specific
  catalogs (`inventory`, `floorplan`, `view`, `review`, `all`). The
  `floorplan` profile reduced visible tools from 78 to 48 in the focused
  check. Focused tests: `tests/test_mcp_tool_profiles.py`.
- **MCB8 handoff summaries:** added durable compact handoff storage
  (`mcp_handoff.py`) plus MCP tools to write/read/list handoff summaries.
  Focused tests: `tests/test_mcp_handoff.py`.
- **MCB4 output budgets/truncation:** added bounded payload metadata for
  wall/topology/measurement QA tools, compact `list_scene_labels(max_labels=)`,
  and bounded `list_anomalies(max_items=)`. Focused tests:
  `tests/test_mcp_output_bounds.py`.
- **MCB6 schema compression:** startup compaction shortens exposed MCP tool
  descriptions by default (`BIM_MCP_COMPACT_DESCRIPTIONS=1`). Focused
  measurement: description chars dropped from ~48.4k to ~8.1k, saving
  ~40.4k chars before role-profile filtering. Focused tests:
  `tests/test_mcp_description_compaction.py`.
- **MCB7 fan-out cleanup:** `get_house_context_summary`/`get_scene_context_summary`
  provide compact dashboard/state reads, and `list_anomalies` now uses
  `plan-state/status` by default; full plan/Markdown deep checks are opt-in
  with `include_plan_deep_checks=true`.
- **MCB9 prompt policy:** MCP `label-house` prompt now instructs agents to use
  context summaries, `image_delivery="auto"`, bounded QA output, and
  `write_scene_handoff_summary`. Focused tests:
  `tests/test_mcp_prompt_policy.py`.

Focused verification command:

```bash
/home/jhoetter/repos/bim-database/.venv/bin/python -m pytest \
  tests/test_mcp_context_report.py \
  tests/test_mcp_image_delivery.py \
  tests/test_mcp_context_summary.py \
  tests/test_mcp_handoff.py \
  tests/test_mcp_tool_profiles.py \
  tests/test_mcp_description_compaction.py \
  tests/test_mcp_output_bounds.py \
  tests/test_mcp_prompt_policy.py
```

Still open:

- Production-agent prompt mirrors outside this repository, if the operational
  `bim-agent` skill/prompt duplicates the MCP `label-house` policy instead of
  consuming it.
- A fresh long house-labeling replay to produce before/after transcript
  metrics. Focused checks prove payload and schema reductions, but a full
  autonomous drive is still the final integration proof.

## 4. Expected efficiency gain

Best current estimate:

- conservative: **2x-3x** fewer active-context tokens;
- likely after good implementation: **4x-6x** fewer active-context tokens;
- aggressive with handle-based images plus strict scene summaries:
  **8x-10x** fewer active-context tokens.

For planning, use **~5x token efficiency improvement** as the target.

Expected contribution by mitigation class:

| Mitigation | Expected gain |
|---|---:|
| Tool-schema cleanup/deferred loading | 1.05x-1.2x |
| Compact state tools/fewer repeated full reads | 1.1x-1.4x |
| Crop-first visual inspection/lower default image payload | 1.5x-3x |
| Do not carry old visual results across scene/phase workers | 2x-5x |

These gains overlap and should not be multiplied naively. A realistic
combined expectation is 3x-8x, with 5x as the working target.

---

## 5. Assumptions

1. The agent still needs visual inspection for coordinate reading, label
   verification, topology repair, and final QA.
2. The agent does not need every previous image retained in active context
   after it has extracted a coordinate, made a pass/fail decision, or written
   durable evidence.
3. Most geometry writes can be verified with a tight crop around the edited
   feature.
4. Full-scene rendered views remain necessary for orientation and global
   topology, but should be rare.
5. Existing clients may or may not support image handles/resources well. If
   not, payload reduction through crop size, `max_dim`, `png8`, and summaries
   is still valuable.
6. Prompt caching can reduce billing/latency costs but does not solve active
   context pressure when old visual results remain in the transcript.
7. The reference measurements are from one house-22 run/log snapshot; exact
   numbers will shift, but the pattern is strong enough to act on.

---

## 6. Target operating model

### Visual inspection modes

1. **Overview**
   - Purpose: orient the agent, identify region of work.
   - Frequency: once per scene or after major topology uncertainty.
   - Payload: low `max_dim`, no labels unless needed.

2. **Tight crop**
   - Purpose: read coordinates, inspect a label, verify one edit.
   - Frequency: common.
   - Payload: target ~100-250 KB per call where possible.

3. **Full labeled QA**
   - Purpose: global consistency, topology review, final scene QA.
   - Frequency: rare.
   - Payload: allowed to be large, but not repeated casually.

### Context handoff model

Scene workers should end with a compact durable summary:

- scene identity and phase;
- labels added/changed;
- unresolved defects;
- calibration status;
- key measurements;
- final QA status;
- links/IDs to evidence on disk, not embedded images.

The next worker should consume the summary and fetch fresh visual context
only when it needs pixels.

---

## 7. Work items

### MCB0 - Add measurement harness for MCP context pressure

**Severity:** Blocker for optimization work

Create a script that parses Claude JSONL transcripts and MCP server logs and
reports:

- tool schema counts;
- tool-result bytes by tool;
- image/base64 result counts and sizes;
- repeated state-read counts;
- usage token distributions if present;
- largest individual result rows;
- per-scene tool-call counts.

Initial target inputs:

- `.claude/projects/.../*.jsonl`
- `.claude/projects/.../subagents/*.jsonl`
- `tmp/mcp-server.log`

**Acceptance:**

- Running the harness on the house-22 reference transcript produces a short
  Markdown or JSON report.
- Report includes before/after comparable totals.
- The script does not require uploading private drawings or transcripts.

### MCB1 - Add compact state summary tools

**Severity:** High

Add summary endpoints/tools so agents do not need repeated full reads for
routine routing:

- `get_house_summary(key)`
- `get_scene_summary(key, file)`
- `get_plan_summary(key, file)`
- `get_label_summary(key, file)`
- optional `get_export_readiness_summary(key)`

These should return counts, statuses, blocker IDs, recent action IDs, and
small label summaries, not full geometry arrays or full plan JSON.

**Acceptance:**

- A scene-routing step can determine next action without calling full labels,
  full plan state, and full house facts.
- Summary payloads are typically under 5 KB.
- Full existing tools remain available for debug/deep work.

### MCB2 - Add image handle/resource mode

**Severity:** High

For visual tools, support a mode that stores the rendered image on disk and
returns:

- image ID/path/resource URI;
- dimensions;
- byte size;
- crop region;
- render parameters;
- optional tiny thumbnail or no inline image.

Candidate tools:

- `get_scene_view`
- `get_scene_view_with_labels`
- `verify_label_placement`
- `get_pdf_page_view`
- `dimension_chain_context`

The agent/client can explicitly request inline image payloads when it truly
needs the pixels in the current model turn.

**Acceptance:**

- Default or opt-in handle mode reduces tool-result line size by at least 80%
  for full-scene renders.
- Visual inspection remains possible through explicit inline requests or
  client resource viewing.
- Stored render files are garbage-collectable by run ID or age.

### MCB3 - Make crop-first verification the default

**Severity:** High

Change agent guidance and tool ergonomics so normal geometry verification
uses tight crops:

- prefer `verify_label_placement` over broad
  `get_scene_view_with_labels`;
- compute tight auto-crops around labels with padding;
- set conservative default `max_dim` for verification;
- reserve full labeled views for topology/global QA.

**Acceptance:**

- A normal `upsert_label` verification result is under 100-250 KB in the
  transcript for typical labels.
- Full-scene image calls are visible in metrics and intentionally rare.
- House-22 replay/re-run shows fewer MB-scale image result rows.

### MCB4 - Add output budgets and truncation metadata

**Severity:** High
**Status:** Shipped in `mcp-context-bloat-reduction-impl`.

Tools that can return large lists should accept explicit limits and report
truncation:

- `max_items`
- `max_regions`
- `max_defects`
- `summary_only`
- `include_geometry`
- `include_markdown`
- `include_evidence`

Affected areas:

- plan state;
- wall scoring/missing regions;
- topology QA;
- measurement QA;
- labels;
- anomalies.

**Acceptance:**

- Large payload tools include `truncated: true/false` and omitted counts.
- Agent can ask for full detail on one defect/region by ID.
- Default payloads stay bounded even on noisy scenes.

### MCB5 - Split MCP toolsets by phase

**Severity:** Medium-high
**Status:** Shipped in `mcp-context-bloat-reduction-impl`.

Expose smaller tool groups for common worker roles:

- inventory/extraction;
- floorplan geometry;
- elevations/sections;
- scene-plan QA;
- export/review;
- admin/reset/debug.

This can be implemented by separate MCP servers, environment-variable tool
filters, or a gateway/proxy that exposes only phase-relevant tools.

**Acceptance:**

- A floorplan labeling worker does not see extraction/admin/debug tools by
  default.
- Schema size for a normal scene worker is under 5k-10k tokens before JSON
  Schema overhead.
- Full tool access remains available for a maintainer/debug profile.

### MCB6 - Compress and shorten tool descriptions

**Severity:** Medium
**Status:** Shipped in `mcp-context-bloat-reduction-impl`.

Move long procedural guidance out of tool docstrings and into:

- skill docs;
- compact workflow prompt snippets;
- per-tool `help` details loaded on demand;
- tests/contracts.

Keep tool descriptions focused on:

- one-line purpose;
- when to use;
- required arguments;
- dangerous caveats.

**Acceptance:**

- `bim-database` MCP docstring chars drop by at least 50%.
- No loss of critical schema constraints in the actual input schema.
- Tool-selection quality does not regress in smoke tests.

### MCB7 - Avoid repeated full-state fan-out

**Severity:** Medium-high
**Status:** Partially shipped in `mcp-context-bloat-reduction-impl`; remaining
proof requires a fresh house-22-style replay and MCP log comparison.

Audit orchestration paths that repeatedly fetch:

- `/datasets/{key}`;
- `/datasets/{key}/house_facts`;
- `/labels/dataset/{key}/{file}`;
- `/plan-state` for every scene.

Add batch summary endpoints or cache-friendly combined endpoints where the
agent needs a dashboard view.

**Acceptance:**

- Export/status checks do not fetch all labels and all plan states multiple
  times in one turn.
- House-level status can be fetched in one compact call.
- MCP log repeated-read counts drop materially on house-22.

### MCB8 - Scene/phase worker handoff summaries

**Severity:** High

Define a durable handoff summary format written after each scene/phase:

```json
{
  "key": "house-22",
  "file": "house-22-floorplan-eg.jpg",
  "phase": "floorplan",
  "status": "verified",
  "labels_added": 18,
  "labels_changed": 3,
  "open_defects": [],
  "uncertain_labels": [],
  "calibration": {"status": "ok"},
  "evidence_refs": ["..."],
  "notes": "short human-readable summary"
}
```

The next worker should read summaries first and fetch pixels only for open
questions.

**Acceptance:**

- A full house run can be resumed from summaries without replaying old visual
  context.
- Subagent results passed to the parent are concise.
- Parent agent does not accumulate every scene's image history.

### MCB9 - Add visual payload policy to tool docs and agent prompts

**Severity:** Medium

Document the intended policy:

- use one overview per scene;
- use tight crops for label decisions;
- use full labeled views only for topology/global QA;
- after reading a coordinate, write the coordinate and discard the image from
  working memory in the summary;
- never call full views in a loop without a narrowed hypothesis.

**Acceptance:**

- House-labeling skill/prompt includes this policy.
- MCP tool descriptions point to compact/handle modes.
- Metrics can flag policy violations, e.g. more than N full-scene renders in
  a scene worker.

---

## 8. Success metrics

Use house-22 as the first benchmark.

Target after MCB1-MCB4 + MCB8:

| Metric | Current observed | Target |
|---|---:|---:|
| Prompt-ish max rows | ~995k | <300k typical |
| MB-scale image result rows | many | rare/intentional |
| Normal verification payload | often large | 100-250 KB target |
| Full-scene render calls | frequent enough to dominate bytes | overview/QA only |
| Repeated full label/plan reads | hundreds | reduced by 50%+ |
| Full-house run restarts | 2-3 reported | 0-1 target |
| Effective token efficiency | baseline | 3x-8x improvement |

Do not treat token efficiency as the only success criterion. Label quality
must not regress; visual inspection is retained and made more deliberate.

---

## 9. Open questions

1. Which client/runtime will execute the production labeling agent, and does
   it support MCP resources/image handles without injecting base64 into every
   model turn?
2. Should image handle mode become the default, or should it be an explicit
   `delivery="handle"` option while inline remains default for compatibility?
3. Where should generated visual artifacts live, and what retention policy is
   acceptable?
4. Can the parent agent delegate visual work to scene workers and receive only
   summaries, or does the current harness force full subagent transcript
   retention?
5. Which tool descriptions are truly needed for tool selection, and which
   belong in a skill/workflow document?

---

## 10. Initial priority order

1. MCB0 - measurement harness.
2. MCB3 - crop-first verification defaults.
3. MCB1 - compact state summary tools.
4. MCB8 - scene/phase handoff summaries.
5. MCB2 - image handle/resource mode.
6. MCB7 - repeated full-state fan-out cleanup.
7. MCB5/MCB6 - schema/toolset cleanup.
8. MCB4/MCB9 - output budgets and policy enforcement.

Rationale:

- Measurement is needed to prove gains.
- Crop-first and compact summaries attack the largest observed bloat source.
- Handle mode can produce the largest gain, but may depend on client support.
- Schema cleanup is valuable, but secondary for house-22 based on current
  evidence.
