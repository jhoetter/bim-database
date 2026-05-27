// Types mirror schema/house.schema.json + the enriched view returned by /houses.

export type Source = 'catalog' | 'documentation' | 'survey' | 'other';

export interface SourceRef {
  file: string;
  page: number | null;
  crop_box_pct: [number, number, number, number] | null;
  page_title: string | null;
  scale: string | null;
}

export interface FactEntry {
  value: unknown;
  evidence?: string | null;
  unit?: string | null;
}

export interface SceneImage {
  file: string;
  url: string;
  category: string;
  medium: string;
  view: string | null;
  floor: string | null;
  caption: string | null;
  source_ref: SourceRef | null;
  facts: Record<string, FactEntry> | null;
  anomaly_flags: string[] | null;
}

export interface DerivedFact {
  value: unknown;
  sources?: string[];
  expected?: unknown;
  ok?: boolean;
  unit?: string;
}

export interface BlockingIssue {
  ref: string;
  url: string;
}

export interface House {
  id: number;
  key: string;
  model: string;
  manufacturer: string | null;
  source: Source;
  source_url: string | null;
  source_origin: string | null;
  building_type: string | null;
  construction: string | null;
  roof_type: string | null;
  style: string | null;
  energy_standard: string | null;
  year_built: number | null;
  has_basement: boolean | null;
  levels: string[] | null;
  area_m2: number | null;
  rooms: number | null;
  floors: number | null;
  price_eur: number | null;
  price_on_request: boolean | null;
  site: string | null;
  character: string | null;
  agent_notes: string | null;
  tags: string[];

  images: SceneImage[];
  pdf_url: string | null;
  source_pdfs: string[];

  bim_ai_blocking_issues: string[] | null;
  modelable_in_bim_ai: boolean | null;
  blocking_open: BlockingIssue[];
  blocking_unknown: BlockingIssue[];
  assessed: boolean;

  data_quality: Record<string, string | null> | null;
  reconstructability_tier: string | null;

  derived_facts: Record<string, DerivedFact> | null;
  anomaly_flags: string[] | null;
}

export type Ontology = Record<string, Record<string, string>>;

// ── annotation labels ────────────────────────────────────────────────────
// Mirrors schema/scene_labels.schema.json. The discriminator is `type`.

export type LabelScope = 'synthetic' | 'house';
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
export interface ViewOpeningLabel extends LabelBase {
  type: 'view_opening';
  geometry: { top_edge: Point[]; bottom_edge: Point[] };
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
      | 'ok_ffb' | 'sockel' | 'firstkante' | 'kniestock' | 'other';
  };
}
export interface HeightMarkLabel extends LabelBase {
  type: 'height_mark';
  geometry: { anchor: Point };
  attributes: { value_mm?: number | null; reference_line_id?: string | null };
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

export interface SceneLabels {
  schema_version: '1.0';
  scope?: LabelScope;
  scene_key: string;
  scene_file: string;
  scene_tag: SceneTag;
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
}

// Synthetic-drawings section — see scripts/generate_synthetic_drawings.py
// and data/synthetic/<key>/manifest.json. Lives in a separate /synthetic
// route since the data is loosely-tied to real houses (intentionally lossy:
// the AI imagines occluded sides) and is staged for manual labeling.

export interface SyntheticDrawing {
  file: string;
  url: string;
  kind: 'elevation' | 'floorplan' | 'section' | string;
  view?: string | null;        // 'north' | 'south' | 'east' | 'west' for elevations
  floor?: string | null;       // 'EG' | 'OG' | 'DG' | ... for floorplans
  title?: string | null;
  model?: string | null;        // gpt-image-2 etc.
  generated_at?: string | null;
  style_refs?: string[];
  content_refs?: string[];
  label_status?: 'unlabeled' | 'labeled' | 'rejected' | string;
}

export interface SyntheticHouse {
  key: string;
  linked_house: string;
  model?: string | null;
  manufacturer?: string | null;
  building_type?: string | null;
  drawings: SyntheticDrawing[];
  linked_house_meta?: {
    key: string;
    model: string | null;
    manufacturer: string | null;
    building_type: string | null;
  };
  composite?: SyntheticComposite;
}

export interface SyntheticComposite {
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
