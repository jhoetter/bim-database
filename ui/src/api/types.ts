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
