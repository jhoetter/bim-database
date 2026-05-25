// Filter state lifted out of HousesPage. Mirrors the legacy FILTERS object;
// each key matches a query param on /houses. Range-pair fields collapse to
// a single chip via RANGE_GROUPS.

import { useSearchParams } from 'react-router';

export type FilterField =
  | 'source'
  | 'building_type'
  | 'construction'
  | 'roof_type'
  | 'style'
  | 'min_tier'
  | 'modelable_in_bim_ai'
  | 'has_basement'
  | 'energy_standard'
  | 'min_area'
  | 'max_area'
  | 'min_price'
  | 'max_price'
  | 'min_year'
  | 'max_year';

export type Filters = Record<FilterField, string>;

export const FILTER_FIELDS: FilterField[] = [
  'source', 'building_type', 'construction', 'roof_type', 'style', 'min_tier',
  'modelable_in_bim_ai', 'has_basement', 'energy_standard',
  'min_area', 'max_area', 'min_price', 'max_price', 'min_year', 'max_year',
];

export const EMPTY_FILTERS: Filters = Object.fromEntries(
  FILTER_FIELDS.map((f) => [f, ''])
) as Filters;

// Display label for an axis (active-filter chip strip).
export const AXIS_LABELS: Partial<Record<FilterField, string>> = {
  source: 'Quelle',
  building_type: 'Gebäudetyp',
  construction: 'Bauweise',
  roof_type: 'Dachform',
  style: 'Stil',
  min_tier: 'min. Tier',
  modelable_in_bim_ai: 'bim-ai',
  has_basement: 'Keller',
  energy_standard: 'Energie',
  min_area: 'Fläche ≥',
  max_area: 'Fläche ≤',
  max_price: 'Preis ≤',
  min_year: 'Baujahr ≥',
  max_year: 'Baujahr ≤',
};

// Per-axis ontology group, for resolving a filter value to its display label.
export const AXIS_ONTO: Partial<Record<FilterField, string>> = {
  source: 'sources',
  building_type: 'building_types',
  construction: 'constructions',
  roof_type: 'roof_types',
  style: 'styles',
  min_tier: 'reconstructability_tiers',
  energy_standard: 'energy_standards',
};

export interface RangeGroup {
  fmin: FilterField;
  fmax: FilterField;
  label: string;
  unit: string;
  priceFmt?: boolean;
}

export const RANGE_GROUPS: RangeGroup[] = [
  { fmin: 'min_area', fmax: 'max_area', label: 'Fläche', unit: 'm²' },
  { fmin: 'min_price', fmax: 'max_price', label: 'Preis', unit: '€', priceFmt: true },
  { fmin: 'min_year', fmax: 'max_year', label: 'Baujahr', unit: '' },
];

const _fmtPriceShort = (v: number) =>
  v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v);

export function rangeLabel(group: RangeGroup, min: string, max: string): string {
  const fmt = group.priceFmt ? (v: string) => _fmtPriceShort(parseFloat(v)) : (v: string) => v;
  if (min && max) return `${fmt(min)}–${fmt(max)} ${group.unit}`.trim();
  if (min) return `≥ ${fmt(min)} ${group.unit}`.trim();
  if (max) return `< ${fmt(max)} ${group.unit}`.trim();
  return '';
}

// Bucketed numeric axes (Fläche / Preis / Baujahr). Buckets are inclusive
// on both ends, non-overlapping, so click-counts match the API filter.
export interface Bucket {
  key: string;
  label: string;
  min: number | null;
  max: number | null;
}

export interface BucketDef {
  fieldMin: FilterField;
  fieldMax: FilterField;
  buckets: Bucket[];
}

export const BUCKETS: Record<'area_m2' | 'price_eur' | 'year_built', BucketDef> = {
  area_m2: {
    fieldMin: 'min_area',
    fieldMax: 'max_area',
    buckets: [
      { key: '<120', label: '< 120 m²', min: null, max: 119 },
      { key: '120-179', label: '120–179', min: 120, max: 179 },
      { key: '180-239', label: '180–239', min: 180, max: 239 },
      { key: '240+', label: '≥ 240 m²', min: 240, max: null },
    ],
  },
  price_eur: {
    fieldMin: 'min_price',
    fieldMax: 'max_price',
    buckets: [
      { key: '<300k', label: '< 300k €', min: null, max: 299999 },
      { key: '300-499k', label: '300–499k €', min: 300000, max: 499999 },
      { key: '500-799k', label: '500–799k €', min: 500000, max: 799999 },
      { key: '800k+', label: '≥ 800k €', min: 800000, max: null },
    ],
  },
  year_built: {
    fieldMin: 'min_year',
    fieldMax: 'max_year',
    buckets: [
      { key: '<1950', label: '< 1950', min: null, max: 1949 },
      { key: '1950-1979', label: '1950–1979', min: 1950, max: 1979 },
      { key: '1980-2009', label: '1980–2009', min: 1980, max: 2009 },
      { key: '2010+', label: '≥ 2010', min: 2010, max: null },
    ],
  },
};

// Palettes that index by position — fine for categorical enums where colour
// has no semantic meaning.
export const PALETTES = {
  source: ['#1d4ed8', '#92400e'],
  building_type: ['#1d4ed8', '#15803d', '#6d28d9', '#b45309', '#be123c', '#0891b2', '#65a30d', '#7c2d12', '#0e7490', '#7e22ce', '#a16207', '#9d174d', '#374151'],
  construction: ['#1d4ed8', '#15803d', '#b45309', '#6d28d9', '#be123c', '#0891b2'],
  roof_type: ['#6d28d9', '#0891b2', '#7c3aed', '#9333ea', '#a855f7', '#7e22ce', '#5b21b6', '#4c1d95', '#312e81', '#1e1b4b'],
  style: ['#be123c', '#dc2626', '#9d174d', '#7c2d12', '#9a3412', '#831843', '#0e7490', '#a16207', '#374151'],
  energy: ['#0891b2', '#0e7490', '#155e75', '#164e63', '#1e3a8a', '#1d4ed8', '#3b82f6'],
} as const;

// Palettes keyed by value — colour stays attached to the value through
// filtering, so semantic colour (✓ / ✗ / tier) doesn't shift.
export const PALETTES_BY_KEY: Record<string, Record<string, string>> = {
  tier: {
    T0_visual_only: '#9ca3af',
    T1_schematic: '#fbbf24',
    T2_dimensioned_plans: '#3b82f6',
    T3_architectural_set: '#22c55e',
    T4_construction_grade: '#a855f7',
  },
  bim_ai: { true: '#15803d', false: '#b91c1c', unknown: '#9ca3af' },
  keller: { true: '#0891b2', false: '#94a3b8' },
};

export const RANGE_PALETTES: Record<string, Record<string, string>> = {
  area_m2: { '<120': '#93c5fd', '120-179': '#3b82f6', '180-239': '#1d4ed8', '240+': '#1e3a8a' },
  price_eur: { '<300k': '#fcd34d', '300-499k': '#f59e0b', '500-799k': '#d97706', '800k+': '#92400e' },
  year_built: { '<1950': '#a78bfa', '1950-1979': '#7c3aed', '1980-2009': '#5b21b6', '2010+': '#312e81' },
};

export const colorFor = (
  palette: readonly string[] | Record<string, string>,
  key: string,
  idx: number,
): string => {
  if (Array.isArray(palette)) return palette[idx % palette.length] ?? '#9ca3af';
  return (palette as Record<string, string>)[key] ?? '#9ca3af';
};

// Tier semantics: filter is a *minimum*, so "T2" means "T2+".
export const tierNum = (s: string | null | undefined): number | null =>
  s ? parseInt(String(s).replace(/^T/, '').split('_')[0], 10) : null;

export const tierFilterValue = (k: string): string => `T${tierNum(k)}`;

export function matchesActive(field: FilterField, key: string | null, activeVal: string): boolean {
  if (!activeVal || key == null) return false;
  if (field === 'min_tier') {
    const a = tierNum(activeVal);
    const b = tierNum(key);
    return a != null && b != null && b >= a;
  }
  return key === activeVal;
}

export const toFilterValue = (field: FilterField, key: string): string =>
  field === 'min_tier' ? tierFilterValue(key) : key;

// URL is the source of truth — Filters + search both live in the query
// string so deep links and back/forward work for free.
export function useFiltersFromUrl(): {
  filters: Filters;
  search: string;
  setFilter: (field: FilterField, value: string) => void;
  setRange: (fmin: FilterField, min: string, fmax: FilterField, max: string) => void;
  setSearch: (q: string) => void;
  reset: () => void;
  anyActive: boolean;
} {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(
    FILTER_FIELDS.map((f) => [f, params.get(f) ?? '']),
  ) as Filters;
  const search = params.get('q') ?? '';
  const anyActive = FILTER_FIELDS.some((f) => filters[f] !== '');

  const update = (mutate: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(params);
    mutate(next);
    setParams(next, { replace: true });
  };

  const setFilter = (field: FilterField, value: string) =>
    update((p) => {
      // Toggle off when re-clicking the same value
      if (p.get(field) === value) p.delete(field);
      else if (value === '') p.delete(field);
      else p.set(field, value);
    });

  const setRange = (fmin: FilterField, min: string, fmax: FilterField, max: string) =>
    update((p) => {
      const sameMin = (p.get(fmin) ?? '') === min;
      const sameMax = (p.get(fmax) ?? '') === max;
      if (sameMin && sameMax) {
        p.delete(fmin);
        p.delete(fmax);
        return;
      }
      if (min === '') p.delete(fmin);
      else p.set(fmin, min);
      if (max === '') p.delete(fmax);
      else p.set(fmax, max);
    });

  const setSearch = (q: string) =>
    update((p) => {
      if (q) p.set('q', q);
      else p.delete('q');
    });

  const reset = () =>
    update((p) => {
      for (const f of FILTER_FIELDS) p.delete(f);
      p.delete('q');
    });

  return { filters, search, setFilter, setRange, setSearch, reset, anyActive };
}

export function matchesSearch(r: { id: number; key: string; manufacturer: string | null; model: string }, q: string): boolean {
  if (!q) return true;
  const ql = q.toLowerCase();
  const hay = [r.id, r.key, r.manufacturer, r.model]
    .filter(Boolean)
    .map((x) => String(x).toLowerCase())
    .join(' ');
  return hay.includes(ql);
}
