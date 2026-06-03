// R0 — catalog ("houses") types removed. The dataset (corpus of
// drawings + annotations) is the only data model that survives.

// ── annotation labels ────────────────────────────────────────────────────
// Mirrors schema/scene_labels.schema.json. The discriminator is `type`.

// R0 — narrowed from `'dataset' | 'house'` to a single value. Kept as a
// type alias rather than collapsed to a plain string literal so existing
// call-sites don't have to change shape; future-proofs against re-adding
// a second scope without another mass rename.
export type LabelScope = 'dataset';
export type SceneTag = 'grundriss' | 'ansicht' | 'schnitt' | 'sonstiges' | 'nicht_klassifiziert';
export type LabelStatus = 'readable' | 'not_readable' | 'missing' | 'uncertain';
export type Point = [number, number];
export type Quad = [Point, Point, Point, Point];

export interface LabelRelation {
  other_id: string;
  kind: 'labels' | 'belongs_to' | 'references';
}

interface LabelBase {
  id: string;
  status: LabelStatus;
  source?: string;
  relations?: LabelRelation[];
  notes?: string;
  run_id?: string | null;
  agent_id?: string | null;
  subagent_id?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface WallLabel extends LabelBase {
  type: 'wall';
  geometry: { start: Point; end: Point };
  attributes: {
    thickness_mm?: number | null;
    mass_id?: string;
    mass_kind?: 'main_house' | 'detached_garage' | 'projection' | 'wing' | 'other';
    mass_tool?: string;
    mass_edge_index?: number;
    mass_edge_count?: number;
    edge_confidence?: number;
  };
}

export interface FloorplanOpeningLabel extends LabelBase {
  type: 'floorplan_opening';
  geometry: { quad: Quad };
  attributes: {
    opening_kind?: 'door' | 'window' | 'passage' | 'garage_door' | 'other';
    width_mm?: number | null;
    swing?: 'in' | 'out' | 'sliding' | 'none';
    swing_side?: 'left' | 'right' | 'none';
    transaction_id?: string;
    parent_wall_id?: string;
    parent_wall_quality_status?: 'ink_anchored' | 'centerline_plausible' | 'off_ink' | 'unanchored' | 'uncertain' | null;
    qa_status?: 'passed' | 'failed' | 'needs_review';
    parent_wall_verification?: {
      parent_wall_id?: string;
      quality_status?: 'ink_anchored' | 'centerline_plausible' | 'off_ink' | 'unanchored' | 'uncertain' | null;
      local_qa_ok?: boolean;
    };
  };
}
export type ViewOpeningGeometry =
  | { top_edge: Point[]; bottom_edge: Point[] }                          // rectangle (legacy)
  | { shape: 'circle'; center: Point; radius_px: number }                // round window
  | { shape: 'polygon'; polygon: Point[] };                              // arched/irregular
export interface ViewOpeningLabel extends LabelBase {
  type: 'view_opening';
  geometry: ViewOpeningGeometry;
  attributes: {
    opening_kind?: 'door' | 'window' | 'skylight' | 'dormer' | 'garage_door' | 'other';
    frame_visible?: boolean;
  };
}
export interface ComponentLineLabel extends LabelBase {
  type: 'component_line';
  geometry: { polyline: Point[] };
  attributes: {
    line_kind?:
      | 'first' | 'traufe' | 'gelaende' | 'geschoss'
      | 'ok_ffb' | 'sockel' | 'firstkante' | 'dachschraege'
      | 'kniestock' | 'gebaeudekante' | 'other';
    region_kind?: 'roof' | 'wall_body' | 'gable' | 'ground' | 'unknown';
  };
}
export interface HeightMarkLabel extends LabelBase {
  type: 'height_mark';
  geometry: { anchor: Point };
  attributes: {
    value_mm?: number | null;
    // Named datum this height represents. With this, a height_mark
    // alone is enough — you don't need a separate `first` line.
    datum?:
      | 'first' | 'traufe' | 'gelaende' | 'geschoss' | 'ok_ffb'
      | 'sockel' | 'kniestock' | 'other' | null;
    // (legacy) link to a component_line that this height is measured from.
    reference_line_id?: string | null;
  };
}
export interface DimensionedDistanceLabel extends LabelBase {
  type: 'dimensioned_distance';
  geometry: { start: Point; end: Point };
  attributes: {
    value_mm?: number | null;
    target_orientation: 'horizontal' | 'vertical' | 'unknown' | `angle_deg:${string}`;
    is_reference: boolean;
    dimension_semantic?: 'building' | 'site_setback' | 'elevation_datum' | 'unknown';
    calibration_role?: 'none' | 'building_metric' | 'site_metric' | 'transferred' | 'assumed_isotropic';
    calibration_confidence?: 'low' | 'medium' | 'high';
    transaction_id?: string;
    span_id?: string;
    chain_id?: string;
  };
}
export interface DimensionNumberLabel extends LabelBase {
  type: 'dimension_number';
  geometry: { anchor?: Point; bbox?: Quad };
  attributes: { text?: string; parsed_value_mm?: number | null };
}

export type Label =
  | WallLabel
  | FloorplanOpeningLabel
  | ViewOpeningLabel
  | ComponentLineLabel
  | HeightMarkLabel
  | DimensionedDistanceLabel
  | DimensionNumberLabel;

export type SceneOrientation = 'north' | 'south' | 'east' | 'west';
export type SceneLevel = 'kg' | 'ug' | 'eg' | 'og' | 'dg' | 'spitzboden';

export interface SceneLabels {
  schema_version: '1.0';
  scope?: LabelScope;
  scene_key: string;
  scene_file: string;
  scene_tag: SceneTag;
  /** N6: for Ansicht/Schnitt, which building face this scene shows. Used to
   *  scope cross-scene caches (Nordansicht only pre-fills future
   *  Nordansichten). Null = unset (legacy behavior). */
  scene_orientation?: SceneOrientation | null;
  /** N6: for Grundriss, which floor of the building. Same scoping use. */
  scene_level?: SceneLevel | null;
  image_size_px: [number, number];
  annotated_by?: string;
  annotated_at?: string;
  labels: Label[];
  homography?: {
    matrix?: number[][];
    computed_from?: string[];
    rectified_size_px?: [number, number];
    rms_residual_px?: number;
    status?: 'ok' | 'insufficient_references' | 'degenerate';
  };
  anomalies?: string[];
  /** W7: per-scene display preferences. Labels listed in hidden_label_ids
   *  exist in the JSON but are not rendered on the canvas — useful for
   *  inherited Höhenkoten that pile up but aren't useful in this view.
   *  Stays out of the schema's required set so older saves load forward. */
  display?: {
    hidden_label_ids?: string[];
  };
}

export interface ScenePlan {
  exists: boolean;
  key: string;
  file: string;
  path: string;
  markdown: string;
  version: string | null;
  status: 'draft' | 'active' | 'blocked' | 'needs_repair' | 'blocked_external' | 'review' | 'verified' | 'accepted_incomplete' | 'complete' | string | null;
  template_version: string;
  last_updated: string | null;
  markdown_path?: string;
  state?: ScenePlanState | null;
  legacy_markdown_exists?: boolean;
}

export type ScenePlanTaskStatus =
  | 'todo'
  | 'in_progress'
  | 'blocked'
  | 'needs_repair'
  | 'rejected'
  | 'verified'
  | 'accepted_incomplete';
export type ScenePlanDefectStatus =
  | 'open'
  | 'in_progress'
  | 'fixed'
  | 'rejected'
  | 'rejected_false_positive'
  | 'accepted_uncertain'
  | 'accepted_risk'
  | 'accepted_source_limited'
  | 'superseded';
export type ScenePlanDefectSeverity = 'blocker' | 'warning' | 'info';

export interface ScenePlanGate {
  id: string;
  status: 'pending' | 'passed' | 'failed' | 'waived' | string;
  evidence_ids?: string[];
  waiver_reason?: string | null;
}

export interface ScenePlanTask {
  id: string;
  title: string;
  phase: 'analysis' | 'editing' | 'verification' | string;
  category: string;
  status: ScenePlanTaskStatus | string;
  required: boolean;
  blocked_by?: string[];
  depends_on?: string[];
  invalidates?: string[];
  gates?: ScenePlanGate[];
  evidence_ids?: string[];
  updated_at?: string;
}

export interface ScenePlanDefect {
  id: string;
  title: string;
  status: ScenePlanDefectStatus | string;
  severity: ScenePlanDefectSeverity | string;
  category: string;
  region?: unknown;
  description?: string;
  expected_resolution?: string;
  classification?: string;
  evidence_ids?: string[];
  created_at?: string;
  updated_at?: string;
}

export interface ScenePlanEvidence {
  id: string;
  kind: string;
  mode: 'analysis' | 'editing' | 'verification' | string;
  summary: string;
  tool?: string | null;
  params?: Record<string, unknown>;
  result?: Record<string, unknown>;
  observation_id?: string | null;
  image_url?: string | null;
  run_id?: string | null;
  agent_id?: string | null;
  subagent_id?: string | null;
  created_at?: string;
}

export interface BuildingGlobalFactEntry {
  name?: string;
  value?: unknown;
  unit?: string;
  confidence?: 'low' | 'medium' | 'high' | string;
  provenance_quality?: 'direct_read' | 'derived' | 'transferred' | 'conflicting' | 'review_required' | string;
  review_required?: boolean;
  source?: {
    scene?: string | null;
    label_id?: string | null;
  };
  conflicts?: string[];
  previous_values?: Array<Record<string, unknown>>;
  notes?: string;
}

export interface BuildingGlobalDerivedFactEntry extends BuildingGlobalFactEntry {
  derived?: boolean;
  needs_cross_check?: boolean;
  formula?: string;
  inputs?: string[];
}

export interface BuildingGlobalFactsView {
  schema?: number | string;
  facts?: Record<string, BuildingGlobalFactEntry>;
  derived?: Record<string, BuildingGlobalDerivedFactEntry>;
  fact_ledger?: {
    conflicts?: Array<Record<string, unknown>>;
    review_required?: boolean;
    provenance_counts?: Record<string, number>;
    consumer_note?: string;
  };
}

export interface ScenePlanAction {
  kind: 'defect' | 'task' | string;
  action_id?: string;
  mode?: string;
  task_id?: string | null;
  defect_id?: string | null;
  id: string;
  title: string;
  severity?: string;
  category?: string;
  phase?: string;
  region?: unknown;
  allowed_label_types?: string[];
  forbidden_label_types?: string[];
  allowed_tools?: string[];
  required_evidence?: string[];
  success_gates?: string[];
  rejected_attempts?: Array<Record<string, unknown>>;
  instruction: string;
}

export interface ScenePlanTerminality {
  terminal?: boolean;
  status?: string;
  summary?: string;
  quality_tier?: 'gold' | 'silver' | 'bronze' | 'blocked' | string;
  completion_state?: string;
  review_debt?: number;
  uncertainty_counters?: Record<string, unknown>;
  final_qa_summary?: {
    tier?: string;
    completion_state?: string;
    strengths?: string[];
    uncertainties?: string[];
    uncertainty_reasons?: Record<string, number>;
    missing_or_unreadable?: string[];
    transferred_facts?: Array<Record<string, unknown>>;
    source_unreadable?: DimensionChainReview[];
    human_review_required?: boolean;
    review_debt?: number;
  };
  required_complete?: boolean;
  percent_complete?: number;
  open_blockers?: number;
  open_warnings?: number;
  terminal_warning_decisions?: number;
  current_action_id?: string | null;
  final_qa_allowed?: boolean;
  stale_evidence?: string[];
  next_action_available?: boolean;
  next_action?: ScenePlanAction | null;
  terminality_reasons?: string[];
}

export interface DimensionChainReview {
  evidence_id?: string;
  decision?: 'readable' | 'partially_readable' | 'source_unreadable' | string;
  chain_region?: unknown;
  orientation?: 'horizontal' | 'vertical' | string;
  readable_values?: unknown[];
  unreadable_fragments?: string[];
  reason?: string;
  enhance?: string;
}

export interface ScenePlanState {
  schema_version: 'scene-plan-state-v1' | string;
  key: string;
  file: string;
  scene_tag: SceneTag | string;
  level_or_orientation?: string | null;
  status: string;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
  current_state?: {
    summary?: string;
    label_counts?: Record<string, number>;
    scores?: Record<string, unknown>;
    topology?: Record<string, unknown>;
    findings?: {
      count?: number;
      blockers?: number;
      warnings?: number;
      items?: Array<Record<string, unknown>>;
    };
    finding_clusters?: {
      count?: number;
      items?: Array<{
        cluster_id?: string;
        cluster_fingerprint?: string;
        cluster_type?: string;
        confidence?: string;
        severity?: string;
        summary?: string;
        region?: unknown;
        finding_ids?: string[];
        wall_ids?: string[];
        categories?: string[];
        findings_count?: number;
        decision?: {
          outcome?: string;
          candidate_id?: string;
          updated_at?: string;
          evidence_ids?: string[];
        };
      }>;
    };
    repair_candidate_decisions?: Record<string, {
      candidate_id?: string;
      candidate_op?: string;
      cluster_id?: string;
      cluster_fingerprint?: string;
      finding_ids?: string[];
      outcome?: string;
      evidence_ids?: string[];
      note?: string | null;
      updated_at?: string;
    }>;
    opening_candidate_decisions?: Record<string, {
      candidate_id?: string;
      candidate_fingerprint?: string;
      kind?: string;
      outcome?: string;
      label_id?: string | null;
      parent_wall_id?: string | null;
      region?: unknown;
      evidence_ids?: string[];
      note?: string | null;
      decided_at?: string;
    }>;
    blockers?: string[];
    stale_evidence?: string[];
    source_unreadable?: DimensionChainReview[];
    quality_tier?: string;
    completion_state?: string;
    review_debt?: number;
    final_qa_summary?: ScenePlanTerminality['final_qa_summary'];
    current_action_id?: string | null;
    terminality?: ScenePlanTerminality;
  };
  tasks?: ScenePlanTask[];
  defects?: ScenePlanDefect[];
  evidence?: ScenePlanEvidence[];
  decision_log?: Array<Record<string, unknown>>;
  actions?: Array<Record<string, unknown>>;
}

export interface CandidateQueueItem {
  candidate_id: string;
  candidate_fingerprint?: string;
  kind: string;
  confidence?: string;
  parent_wall_id?: string | null;
  opening_kind?: string;
  region?: unknown;
  span_px?: number;
  instruction?: string;
  suggested_label?: unknown;
}

export interface CandidateQueue {
  candidate_contract: string;
  count: number;
  candidates: CandidateQueueItem[];
  note?: string;
  params?: Record<string, unknown>;
}

export interface SceneWorkbenchState {
  workbench_contract: string;
  key: string;
  file: string;
  plan: {
    exists?: boolean;
    version?: string | null;
    status?: string | null;
    summary?: string;
    quality_tier?: string;
    completion_state?: string;
    review_debt?: number;
    final_qa_summary?: ScenePlanTerminality['final_qa_summary'];
    terminal?: boolean;
    required_complete?: boolean;
    percent_complete?: number;
    open_blockers?: number;
    open_warnings?: number;
    terminal_warning_decisions?: number;
    terminality_reasons?: string[];
  };
  current_task?: string | null;
  phase?: string;
  recommended_view_mode?: string;
  recommended_region?: unknown;
  next_action?: ScenePlanAction | null;
  allowed_tools?: string[];
  forbidden_writes?: string[];
  required_evidence?: string[];
  crop_warnings?: Array<Record<string, unknown>>;
  labels_summary?: {
    total?: number;
    by_type?: Record<string, number>;
    mass_groups?: Array<{
      mass_id: string;
      mass_kind?: string;
      mass_tool?: string;
      wall_count?: number;
      label_ids?: string[];
      edge_confidence_min?: number | null;
    }>;
  };
  blocker_summary?: {
    open_blockers?: number;
    open_warnings?: number;
    terminal_warning_decisions?: number;
    reasons?: string[];
  };
  semantic_exclusions_summary?: {
    available?: boolean;
    count?: number;
    regions?: Array<Record<string, unknown>>;
    note?: string;
  };
  candidate_queue_summary?: {
    included?: boolean;
    reason?: string;
    count?: number;
    by_kind?: Record<string, number>;
    by_confidence?: Record<string, number>;
    candidates?: CandidateQueueItem[];
    truncated?: boolean;
  };
  transaction_history?: Array<{
    transaction_id: string;
    label_types?: Record<string, number>;
    label_ids?: string[];
    label_count?: number;
    tools?: string[];
    qa_statuses?: string[];
    evidence_id?: string;
    summary?: string;
  }>;
  recent_evidence?: ScenePlanEvidence[];
  quality?: Record<string, unknown>;
}

// Dataset (supervised-learning corpus) — drawings come from two sources:
// AI image-models (scripts/generate_drawings) and real scanned plans
// (scripts/include_real_plans.py from houses flagged dataset_starred=true).
// The `source` field on each entry says which. Lives at
// data/dataset/<key>/manifest.json; UI route /dataset.

export interface DatasetDrawing {
  file: string;
  url: string;
  kind: 'elevation' | 'floorplan' | 'section' | 'detail' | string;
  /** 'generated' = AI-produced; 'real' = scanned from a real architect's plan;
   *  'pdf' (R2) = crop from a user-uploaded PDF in data/pdfs/incoming/<key>/. */
  source?: 'generated' | 'real' | 'pdf';
  /** R2 — present when source='pdf'. Lets us replay the crop later (re-extract
   *  at higher DPI, redraw a bbox, etc.) and lets the extract page render
   *  already-committed scenes as overlays on the source page. */
  crop_from?: {
    pdf_file: string;
    page: number;
    bbox_pdf_units: [number, number, number, number];
    dpi: number;
  };
  view?: string | null;        // 'north' | 'south' | 'east' | 'west' for elevations
  floor?: string | null;       // 'EG' | 'OG' | 'DG' | ... for floorplans
  title?: string | null;
  model?: string | null;        // gpt-image-2 etc. (generated only)
  generated_at?: string | null;
  imported_at?: string | null;  // real only
  source_path?: string | null;  // real only — repo-relative source file
  style_refs?: string[];
  content_refs?: string[];
  label_status?: 'unlabeled' | 'labeled' | 'rejected' | string;
  /** M11 coverage badge: true when a labels JSON file exists for this scene. */
  labeled?: boolean;
  label_count?: number;
  scene_replacement?: {
    kind?: string;
    replaced_at?: string;
    old_file?: string;
    new_file?: string;
    old_format?: string;
    new_format?: string;
    reason?: string;
    label_plan_impact?: string;
    review_required?: boolean;
  };
  replacement_history?: Array<Record<string, unknown>>;
}

// R1 — PDF intake bundle. One per house under data/pdfs/incoming/<key>/.
// The consolidated_url points at the merged PDF used by R2's scene extractor.
// schema_version 2.0 adds the ingestion-pipeline provenance fields below; the
// UI keeps reading both 1.0 and 2.0 manifests since the server upgrades on read.
export interface IncomingPdf {
  schema_version: '1.0' | '2.0';
  key: string;
  house_key: string;
  consolidated_pdf: string | null;
  consolidated_url?: string | null;
  source_filenames: string[];
  uploaded_at: string;
  page_count: number | null;
  state: 'pending' | 'partial' | 'extracted' | 'annotated';
  user_notes: string;
  extracted_scenes: Array<{
    page: number;
    bbox_pdf_units: [number, number, number, number];
    scene_file: string;
  }>;
  // v2.0 only — additive.
  source_type?: 'batch' | 'scrape' | 'form';
  pages?: Array<{
    page: number;
    decision: 'pass' | 'warn' | 'reject';
    decision_reasons?: string[];
    pii_flag?: {
      title_block_suspected: boolean;
      title_block_bbox_px: [number, number, number, number] | null;
      redacted: boolean;
    };
    human_qa_required?: boolean;
  }>;
}

// Customer submission queue entry (developer review surface).
export interface IncomingSubmission extends IncomingPdf {
  submission_id: string;
  submitter?: {
    submission_id: string;
    contact_email?: string | null;
    contact_name?: string | null;
  } | null;
  consent?: {
    training_use: boolean;
    license: string;
    consented_at: string;
  } | null;
  summary?: {
    pass: number;
    warn: number;
    reject: number;
    title_blocks_suspected: number;
  };
  promoted_to?: string;
  promoted_at?: string;
}

export interface DatasetHouse {
  key: string;
  linked_house: string;
  model?: string | null;
  manufacturer?: string | null;
  building_type?: string | null;
  drawings: DatasetDrawing[];
  linked_house_meta?: {
    key: string;
    model: string | null;
    manufacturer: string | null;
    building_type: string | null;
  };
  composite?: DatasetComposite;
  // Agentic-labeling: surfaced from house_facts.workflow when an agent
  // ran the labeling. SPA renders an "Agent-gelabelt" chip on the card.
  driven_by?: string | null;
  driven_by_run_id?: string | null;
}

export interface DatasetComposite {
  url: string;
  sheet_size_px: [number, number];
  seed?: number;
  generated_at?: string;
  scenes: Array<{
    file: string;
    kind?: string | null;
    view?: string | null;
    floor?: string | null;
    title?: string | null;
    bbox_px: [number, number, number, number];   // x, y, w, h on the composite
    rotation_deg?: number;
  }>;
  title_block_bbox_px?: [number, number, number, number];
}
