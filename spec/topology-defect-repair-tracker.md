# Topology Defect Repair Tracker

**Status:** 2026-06-02. Implemented on main working tree. Verified with
`pytest -q`, `npm run typecheck`, and `npm run build`.

**Owner:** jhoetter

**Scope:** Turn floorplan wall/opening defects from a noisy append-only list
into a deterministic, clustered repair system that helps an agent reach
9/10-quality scene labels without burning context on repeated manual defect
inspection.

**Non-goal:** This is not a replacement for vision. The agent must still inspect
image crops before accepting geometry edits. The goal is to precompute strong
candidates, eliminate duplicate stale warnings, and make the agent spend its
vision budget on the few ambiguous decisions that matter.

---

## 1. Why This Tracker Exists

The latest house-22 EG agent run is a major improvement over the earlier
minimal export runs:

- the agent stayed on EG;
- it used the structured scene-plan loop;
- it added 5 opening labels;
- it repaired at least one real near-miss wall corner;
- it closed blocker wall-score defects with visual evidence;
- the EG plan reached terminal `verified` status.

However, the plan still shows a large warning backlog:

- `76` open warning defects;
- `68` of those are `wall_topology`;
- current topology summary has only `18` dangling endpoints, `11` near-miss
  corners, `1` collinear fragment, and `1` short stub.

The warning count is therefore not a true count of independent current
problems. It is partly historical duplication from repeated gate evaluation.
The same review regions appear many times, for example:

- `[1208,712,1368,932]` appears 9 times;
- `[1040,688,1200,904]` appears 9 times;
- `[1272,712,1432,904]` appears 8 times.

The implementation now packages those signals into repairable, deduplicated,
ranked work:

- `api/topology_repair.py` computes current finding fingerprints, spatial
  clusters, deterministic repair candidates, pure simulations, quality reports,
  and topology regression snapshots.
- `api/scene_plan_state.py` stores current findings separately from historical
  defects, marks stale auto-defects as `superseded`, records durable repair
  candidate decisions, and enforces `quality_profile="gold"` for unreviewed
  high-confidence candidates.
- `api/main.py` and `mcp_server.py` expose candidate queue, overlay, apply,
  reject/classify decision, quality-report, and topology-snapshot endpoints/tools.
- `ui/src/pages/AnnotatePage.tsx` defaults the plan panel to current topology
  work, separates history, and shows warning-review completion.

---

## 2. Research Baseline

Floorplan vectorization and CAD-cleanup systems commonly treat walls as a
graph/topology problem rather than as independent line segments. The recurring
themes are:

- detect candidate primitives;
- snap/join endpoints into a graph;
- preserve intentional gaps for openings;
- use geometric constraints such as collinearity, orthogonality, and junction
  consistency;
- validate repairs against image evidence or downstream topology.

Useful reference directions:

- Floor plan vectorization papers and datasets often reconstruct a graph or
  polygonal room/wall representation after raster detection, because raw line
  detections contain gaps, duplicates, and noisy endpoints.
- CAD/vector cleanup workflows distinguish current topology errors from edit
  history, and use snap/extend/trim/merge operations as explicit repair
  candidates.
- Building floorplan reconstruction systems usually need special handling for
  doors/windows, because opening gaps are valid disconnected geometry and should
  not be blindly closed.

Research links captured for implementation context:

- FloorPlanCAD dataset/project: https://floorplancad.github.io/
- CubiCasa5K floorplan parsing benchmark:
  https://github.com/CubiCasa/CubiCasa5k
- A broad floorplan vectorization search baseline:
  https://scholar.google.com/scholar?q=floor+plan+vectorization+wall+topology+graph
- CAD line cleanup concepts: snap, trim, extend, join, and overkill/duplicate
  removal as common operations in CAD editing workflows.

The implication for this repo: `wall_topology_qa` should not only emit
diagnostics. It should produce graph repair proposals that are test-applied,
scored, clustered, and then routed to the agent for visual accept/reject.

---

## 3. Current Defect Pipeline

### 3.1 Wall Score Defects

`score_walls` compares saved wall labels to detected wall ink.

Current outputs include:

- `missing_regions`: wall-like ink not covered by saved walls;
- `off_ink_segments`: saved wall labels that do not sit on detected wall ink;
- precision, recall, f1, and coverage counts.

Scene-plan gates convert these into blocker defects:

- `wall_missing_region`;
- `wall_off_ink`;
- `score_regression`.

These are valuable, but strict score masks can confuse:

- thick wall face vs structural centerline;
- fixture/furniture ink mistaken for wall ink;
- dimension/site/title-block ink;
- freehand wall thickness variation.

### 3.2 Topology Defects

`wall_topology_qa` currently works from saved wall label geometry only.

It computes:

- endpoint clusters within `endpoint_tol_px`;
- unclustered endpoints as `dangling_endpoints`;
- nearby unconnected endpoints as `near_miss_corners`;
- collinear separated fragments;
- short stubs;
- connected components.

Scene-plan gates convert these into warning defects:

- `wall_topology`;
- `possible_split_wall`;
- `wall_continuity`.

Current problem: each gate evaluation can create fresh warning rows for the
same spatial condition. The plan accumulates historical warnings, while the UI
does not clearly distinguish current findings from stale/superseded findings.

### 3.3 Terminality Mismatch

The current EG state can be:

- terminal `verified`;
- open blockers `0`;
- open warnings `76`;
- stale evidence still listed in `terminality_reasons`.

This is confusing. If stale evidence matters, it should block final QA. If the
latest gate run refreshed the evidence, the stale list should clear.

---

## 4. Target Quality Bar

The target is a reliable 9/10 scene-labeling workflow:

- The agent sees a small ranked queue, not dozens of duplicate warning cards.
- Every current topology finding has a deterministic explanation and candidate
  repair.
- Every proposed edit is simulated before it is offered.
- The agent inspects only the crop and candidate overlay needed for the
  decision.
- The system records whether the candidate was accepted, rejected, or marked
  intentional.
- Rejected/stale warnings do not reappear as new work unless the underlying
  current geometry materially changes.
- Required scene completion does not hide unresolved high-confidence topology
  failures.

The target is not zero warnings. Some dangling endpoints are valid:

- wall ends at a door/opening;
- partial interior partition;
- stair edge or shaft boundary;
- garage/open carport transition;
- ambiguous scan ink.

The target is that warnings are classified, current, grouped, and actionable.

---

## 5. Workstream A - Current Findings, Not Append-Only Defects

### A1. Introduce Current Finding Fingerprints

**Problem:** Defect IDs are not stable enough across repeated gate evaluation.
Sequential titles such as `Dangling wall endpoint 9` cause duplicate warning
cards for the same region.

**Behavior:**

Create a deterministic finding fingerprint for every auto-generated QA result:

- source tool: `score_walls`, `wall_topology_qa`, `wall_continuity_check`;
- category: `dangling_endpoint`, `near_miss_corner`, `collinear_fragment`,
  `short_stub`, `missing_region`, `off_ink_segment`;
- involved label IDs when available;
- normalized geometry:
  - endpoint point rounded to e.g. 8 px grid;
  - review region rounded to e.g. 16 px grid;
  - candidate wall IDs sorted;
- scene file and plan version.

**Acceptance:**

- Re-running `evaluate_scene_plan_gates` without label changes does not create
  new warning defects.
- The same current topology issue maps to the same defect/finding.
- When a wall endpoint moves materially, the old finding becomes `superseded`
  and a new finding may be created.

### A2. Add Superseded Status for Auto Findings

**Problem:** Fixed/rejected defects can remain visible as current work, and old
open warnings remain after the raw QA result disappears.

**Behavior:**

Add an internal or explicit status:

- `open`;
- `in_progress`;
- `fixed`;
- `rejected`;
- `accepted_uncertain`;
- `superseded`.

When gate evaluation refreshes current auto findings:

- findings present in latest QA stay open or keep their terminal status;
- findings absent from latest QA become `superseded` unless they were manually
  fixed/rejected;
- fixed/rejected findings remain in history but do not count as current open
  warnings;
- UI defaults to current findings only.

**Acceptance:**

- House-22 EG open warning count drops from the historical `76` to the true
  current finding count.
- The UI can still show history when requested.

### A3. Separate `current_state.findings` From `defects`

**Problem:** `defects` mixes active work queue and audit history.

**Behavior:**

Store current auto-QA state separately:

```json
"current_state": {
  "findings": {
    "wall_topology": [
      {
        "fingerprint": "topology:dangling:...",
        "category": "dangling_endpoint",
        "severity": "warning",
        "region": [1040, 688, 1200, 904],
        "current": true,
        "linked_defect_id": "DEF-111"
      }
    ]
  }
}
```

Defects remain the audit/action log. Findings are the latest machine state.

**Acceptance:**

- `get_scene_plan_status` reports both `open_warning_count` and
  `current_warning_finding_count`.
- Agent routing uses current findings, not raw historical defect count.

---

## 6. Workstream B - Cluster Topology Findings

### B1. Spatially Cluster Related Topology Findings

**Problem:** One physical corner can create many warning cards: dangling start,
dangling end, near-miss corner, possible split wall, continuity candidate.

**Behavior:**

Group current topology findings into clusters by:

- overlapping review regions;
- shared wall IDs;
- endpoint distance;
- collinearity relation;
- same opening/door vicinity.

Cluster shape:

```json
{
  "cluster_id": "TOPO-CL-004",
  "region": [1040, 688, 1200, 904],
  "finding_ids": ["..."],
  "wall_ids": ["lab-a", "lab-b"],
  "categories": ["dangling_endpoint", "near_miss_corner"],
  "confidence": "medium",
  "summary": "Two wall endpoints nearly meet at diele/stair corner.",
  "recommended_next": "review_candidate_repairs"
}
```

**Acceptance:**

- House-22 EG warning UX shows around 10-20 topology clusters, not 68 cards.
- Cluster count is stable under repeated gate evaluation.

### B2. Classify Cluster Type

**Problem:** The agent has to infer what each warning means from low-level text.

**Behavior:**

Assign a deterministic cluster type:

- `near_miss_corner`;
- `unconnected_t_junction`;
- `intentional_opening_gap_candidate`;
- `collinear_split_candidate`;
- `short_stub_candidate`;
- `isolated_component`;
- `likely_fixture_or_annotation`;
- `unknown_topology`.

Use geometry first, then context helpers:

- wall endpoint geometry;
- nearby openings;
- nearby door swings;
- nearby stairs/furniture/dimensions from `ambiguous_line_context`;
- score-wall region overlap.

**Acceptance:**

- Every topology cluster has a clear type and suggested review strategy.
- The UI card starts with the cluster summary, not a generic endpoint sentence.

---

## 7. Workstream C - Deterministic Repair Candidates

### C1. Candidate: Snap Endpoint to Endpoint

**Use when:**

- two endpoints are within `near_miss_px`;
- neither endpoint is protected by an opening relation;
- the joined point is on/near ink;
- snap improves topology without hurting score significantly.

**Candidate output:**

```json
{
  "candidate_id": "CAND-001",
  "op": "snap_endpoint_to_endpoint",
  "edits": [
    {"label_id": "wall-a", "endpoint": "end", "to": [737, 824]},
    {"label_id": "wall-b", "endpoint": "start", "to": [737, 824]}
  ],
  "predicted_delta": {
    "dangling_endpoints": -2,
    "near_miss_corners": -1,
    "components": -1,
    "score_walls_f1": 0.001
  },
  "risk": "low",
  "needs_visual_review": true
}
```

**Acceptance:**

- The known house-22 EG near-miss around `[737,824]` is proposed with this
  candidate before an agent has to manually inspect it.

### C2. Candidate: Extend to Line Intersection

**Use when:**

- one endpoint stops near another wall segment;
- projection falls on the target segment;
- extension length is below a threshold;
- no opening/door gap is detected between endpoint and target.

**Acceptance:**

- T-junction and L-junction repairs are proposed where geometry supports them.
- Candidate includes the exact new endpoint coordinate and target wall ID.

### C3. Candidate: Merge Collinear Fragments

**Use when:**

- two wall labels are collinear;
- the gap is small or explained by a non-opening artifact;
- merging improves topology and score.

**Guardrails:**

- Do not merge across a validated opening.
- Do not merge when the gap contains a door swing or explicit passage.
- Do not merge if it worsens wall score beyond tolerance.

**Acceptance:**

- `possible_split_wall` warnings become one merge candidate with score delta,
  not standalone vague warnings.

### C4. Candidate: Mark Intentional Opening Gap

**Use when:**

- dangling endpoints face each other across a door/window/opening;
- an opening label already belongs to one of the adjacent walls;
- or `ambiguous_line_context` finds a door swing or stair opening.

**Behavior:**

No geometry edit. Candidate records:

- opening/door evidence;
- why the endpoint should remain free;
- recommended classification.

**Acceptance:**

- Intentional gaps do not repeatedly return as unclassified dangling endpoints.

### C5. Candidate: Delete or Demote Short Stub

**Use when:**

- a short wall stub is not connected;
- it is off ink or overlaps furniture/fixture/dimensions;
- deleting improves precision without hurting recall/topology.

**Acceptance:**

- Short-stub warnings include a tested delete candidate with score delta.

### C6. Candidate: No-Edit Classification

**Use when:**

- score misses wall-face ink but saved label is structural centerline;
- fixture/furniture/dimension/site ink is detected;
- visual source says no structural repair is needed.

**Behavior:**

The system can suggest the likely classification, but the agent must verify
visually before closing.

**Acceptance:**

- Repeated face-vs-centerline defects can be closed once per cluster and do
  not reappear as new blockers on every final gate refresh.

---

## 8. Workstream D - Test-Apply Scoring for Candidates

### D1. Pure Candidate Simulation

**Problem:** The agent currently has to decide a repair, apply it, then see if
it helped.

**Behavior:**

Add a non-persisting simulation path:

- apply candidate edits to an in-memory label copy;
- recompute topology QA;
- recompute wall score;
- recompute opening quality;
- report before/after deltas.

**Acceptance:**

- Every repair candidate includes predicted topology and score deltas.
- Candidate generation never mutates labels.

### D2. Ranked Recommendation

Rank candidates by:

1. blocker severity;
2. topology improvement;
3. wall-score improvement;
4. opening relation safety;
5. edit size;
6. visual ambiguity.

Suggested ranking fields:

```json
{
  "rank": 1,
  "confidence": 0.82,
  "risk": "low",
  "expected_gain": "removes 2 dangling endpoints and 1 near-miss",
  "why": [
    "endpoint distance 21px",
    "shared L-corner ink detected",
    "score_walls f1 +0.001",
    "no nearby opening label"
  ]
}
```

**Acceptance:**

- Agent receives a ranked queue of clusters/candidates.
- Low-confidence candidates are explicitly marked as visual-review-only.

### D3. Candidate Apply Tool

Add a single safe application tool:

`apply_repair_candidate(key, file, candidate_id, expected_version, evidence_ids)`

Behavior:

- refuses stale candidate versions;
- applies only the precomputed edit set;
- re-runs local validation;
- persists labels only if validation passes;
- records attempt/evidence references.

**Acceptance:**

- Agent cannot accidentally apply a candidate generated for older geometry.

---

## 9. Workstream E - Agent Workflow Changes

### E1. Add `get_scene_repair_candidates`

Tool response:

```json
{
  "scene": "house-22-floorplan-eg.jpg",
  "status": "needs_review",
  "clusters": [
    {
      "cluster_id": "TOPO-CL-004",
      "severity": "warning",
      "region": [632, 726, 822, 904],
      "summary": "Near-miss L-corner between west exterior and kitchen wall.",
      "candidates": [
        {
          "candidate_id": "CAND-001",
          "op": "snap_endpoint_to_endpoint",
          "confidence": 0.82,
          "risk": "low",
          "predicted_delta": {"dangling_endpoints": -2, "f1": 0.001}
        }
      ]
    }
  ]
}
```

**Acceptance:**

- Agent can fetch a compact candidate queue in one call.
- Response is bounded: top N clusters, top M candidates per cluster.

### E2. Add Candidate Overlay Render

Add render mode:

`get_scene_view_with_repair_candidate(key, file, candidate_id)`

Overlay should show:

- current labels;
- proposed edits in a distinct color;
- affected endpoints/corners;
- before/after endpoint coordinates;
- nearby opening labels;
- candidate risk and expected delta in metadata, not on image.

**Acceptance:**

- Agent can visually approve/reject candidate from one crop instead of manually
  deriving the edit.

### E3. Add Agent Decision Outcomes

Allowed outcomes:

- `accepted_applied`;
- `rejected_false_positive`;
- `rejected_intentional_opening`;
- `rejected_would_hurt_score`;
- `accepted_uncertain`;
- `needs_manual_geometry`.

**Acceptance:**

- Every current cluster ends with a meaningful classification.
- Rejected clusters are remembered by fingerprint and not recreated as fresh
  warnings unless geometry changes.

---

## 10. Workstream F - UI Defect Panel Redesign

### F1. Current vs History Tabs

Default panel:

- current blockers;
- current warning clusters;
- current candidate queue.

History panel:

- fixed/rejected/superseded defect rows;
- evidence links;
- previous classifications.

**Acceptance:**

- The screenshot case no longer shows dozens of repeated dangling endpoint
  cards by default.

### F2. Cluster Cards Instead of Endpoint Cards

Cluster card content:

- title: `Near-miss corner near Küche/Diele`;
- severity/status;
- current finding count;
- recommended candidate;
- expected delta;
- buttons: inspect, accept candidate, reject/classify.

**Acceptance:**

- One physical issue is represented once.

### F3. Warning Completion Bar

Show:

- current blockers;
- current warning clusters;
- classified warnings;
- unresolved high-confidence warnings.

**Acceptance:**

- A scene can be `verified` with warnings, but the UI makes clear whether
  warnings are reviewed/classified or merely ignored.

---

## 11. Workstream G - Terminality and Gate Semantics

### G1. Fix Stale Evidence Semantics

**Problem:** A terminal verified scene currently can still list stale evidence
in `terminality_reasons`.

**Behavior options:**

Option A:

- stale evidence blocks `FINAL_QA`;
- final gate run clears stale tasks when it refreshes all required evidence.

Option B:

- stale evidence is only advisory;
- do not include it in `terminality_reasons` for terminal verified scenes.

Recommended: Option A. Stale evidence should either be real or absent.

**Acceptance:**

- `terminal=true` and `status=verified` never includes stale evidence reasons.

### G2. Escalate High-Confidence Topology Candidates

Warnings should stay optional only when low/medium confidence or classified.

If a topology cluster has:

- low-risk deterministic repair;
- topology improvement >= configured threshold;
- no opening risk;
- no wall-score regression;
- candidate confidence >= e.g. 0.8;

then it should become a blocker or at least prevent final QA until classified.

**Acceptance:**

- A clear near-miss corner candidate cannot be left as an unreviewed warning
  while the scene reaches 100%.

### G3. Require Warning Review Summary for 9/10 Mode

Add optional strict profile:

`quality_profile="gold"`

Gold mode requires:

- no current blocker findings;
- no unclassified high-confidence warning clusters;
- all repair candidates above confidence threshold accepted/rejected;
- final visual evidence after candidate review.

**Acceptance:**

- Default mode remains exportable.
- Gold mode drives the agent toward 9/10 quality.

---

## 12. Workstream H - Metrics and Regression Tests

### H1. Candidate Generation Tests

Unit fixtures:

- near-miss L-corner -> snap candidate;
- T-junction endpoint -> extend candidate;
- door opening gap -> intentional gap candidate;
- collinear fragments -> merge candidate;
- isolated fixture line -> no-edit/reject candidate.

### H2. Defect Deduplication Tests

Tests:

- repeated `evaluate_scene_plan_gates` does not increase current open warning
  count;
- moved geometry supersedes stale findings;
- rejected fingerprint is not reopened without material geometry change.

### H3. House-22 EG Regression Snapshot

Create a non-binary regression snapshot from current labels:

- current topology summary;
- current cluster count;
- candidate count;
- candidate types;
- accepted/rejected classifications.

Do not require exact pixel-perfect score metrics, but assert stable topology
semantics.

### H4. Agent Transcript Quality Metrics

For a completed scene run, compute:

- number of current findings generated;
- number of duplicate/superseded historical defects;
- number of candidate repairs accepted;
- number rejected as false positive;
- number of visual crop inspections per accepted repair;
- final current blocker count;
- final unclassified high-confidence warning count.

**Acceptance:**

- A successful EG run produces a short quality report, not just
  `ready=true`.

---

## 13. Proposed Implementation Order

1. Add finding fingerprints and superseded handling.
2. Add current-finding summary fields to plan status.
3. Cluster topology findings by region/wall IDs.
4. Generate simple candidates:
   - snap endpoint;
   - extend to intersection;
   - mark intentional opening gap.
5. Add simulation scoring for candidates.
6. Add `get_scene_repair_candidates`.
7. Add candidate overlay render.
8. Add gold-profile terminality.
9. Update UI defect panel to show clusters/current findings.
10. Add regression tests and transcript metrics.

This order fixes the warning-noise problem first, then adds deterministic
repair intelligence.

---

## 14. Expected Impact

For house-22 EG, expected near-term improvement:

- visible warning rows: from `76` historical rows to roughly `10-20` current
  clusters;
- manual defect-inspection steps: reduced by 50-80%;
- repeated gate refresh noise: eliminated;
- obvious near-miss repairs: proposed automatically;
- final verified state: clearer because current warnings are either reviewed
  or explicitly accepted as advisory.

Quality expectation:

- Current strict plan loop: about 7.5-8.5/10 depending on scene.
- With deduped current findings and candidate repair: plausible 9/10 for
  EG-style floorplans.
- 10/10 still requires visual judgment and likely one or more scene-specific
  hard cases; deterministic repair should make those hard cases visible rather
  than hidden in noise.

---

## 15. Resolved Design Decisions

1. `superseded` is a public defect status. It keeps audit history intact while
   removing stale auto-findings from the current work queue.
2. High-confidence topology warnings block only in `quality_profile="gold"`.
   Default export readiness remains permissive; gold mode drives 9/10 review.
3. Intentional openings and false positives are remembered as durable repair
   candidate decisions keyed by cluster fingerprint and finding IDs. Reviewed
   clusters do not reappear as fresh gold blockers unless the underlying
   geometry changes.
4. Candidate overlays stay visual: current labels plus proposed edits. Candidate
   risk, expected delta, simulation, and decision status stay in metadata and
   reports to avoid reintroducing context bloat.
5. The quality report and topology snapshot are first-class machine-readable
   outputs. A successful agent handoff should cite them instead of relying on
   `ready=true` alone.

Future scorer-profile work remains separate: `score_walls` may still need
distinct early-recall, final structural-centerline, and wall-face-preview
profiles, but that is outside this topology-defect repair tracker.
