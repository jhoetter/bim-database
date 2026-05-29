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
  created_at?: string;
  updated_at?: string;
}

export interface WallLabel extends LabelBase {
  type: 'wall';
  geometry: { start: Point; end: Point };
  attributes: { thickness_mm?: number | null };
}
export interface FloorplanOpeningLabel extends LabelBase {
  type: 'floorplan_opening';
  geometry: { quad: Quad };
  attributes: {
    opening_kind?: 'door' | 'window' | 'passage' | 'garage_door' | 'other';
    width_mm?: number | null;
    swing?: 'in' | 'out' | 'sliding' | 'none';
    swing_side?: 'left' | 'right' | 'none';
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
