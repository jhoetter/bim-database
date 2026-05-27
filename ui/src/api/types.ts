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
}
