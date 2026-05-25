import type { ReactNode } from 'react';
import type { House } from '../../api/types';
import {
  AXIS_LABELS,
  AXIS_ONTO,
  BUCKETS,
  type FilterField,
  type Filters,
  PALETTES,
  PALETTES_BY_KEY,
  RANGE_GROUPS,
  rangeLabel,
} from '../../lib/filters';
import { useOntology } from '../../api/ontology';
import { BucketBar, CategoricalBar } from './Bar';

// Distinct, sliceable summary of the (filtered) record set. Every axis is a
// click target — clicking a segment toggles that filter, clicking the same
// segment again clears it. Bottom strip lists active filters as chips.

interface Props {
  recs: House[];
  total: number;
  search: string;
  filters: Filters;
  anyActive: boolean;
  onSearch: (q: string) => void;
  onPick: (field: FilterField, value: string) => void;
  onClear: (field: FilterField) => void;
  onPickRange: (fmin: FilterField, min: string, fmax: FilterField, max: string) => void;
  onClearRange: (fmin: FilterField, fmax: FilterField) => void;
  onReset: () => void;
}

export function StatsDashboard({
  recs,
  total,
  search,
  filters,
  anyActive,
  onSearch,
  onPick,
  onClear,
  onPickRange,
  onClearRange,
  onReset,
}: Props) {
  const n = recs.length;
  const hasSearch = search !== '';

  const groupBy = (field: 'source' | 'building_type' | 'construction' | 'roof_type' | 'style' | 'energy_standard'): [string, number][] => {
    const m = new Map<string, number>();
    for (const r of recs) {
      const v = r[field];
      if (v) m.set(v, (m.get(v) ?? 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  };
  const groupTier = (): [string, number][] => {
    const m = new Map<string, number>();
    for (const r of recs) {
      const t = r.reconstructability_tier;
      if (t) m.set(t, (m.get(t) ?? 0) + 1);
    }
    return [...m.entries()].sort(
      (a, b) => parseInt(a[0].replace(/^T/, ''), 10) - parseInt(b[0].replace(/^T/, ''), 10),
    );
  };
  const groupBool = (field: 'modelable_in_bim_ai' | 'has_basement'): [string, number][] => {
    const m = new Map<string, number>();
    for (const r of recs) {
      const v = r[field];
      if (v === true) m.set('true', (m.get('true') ?? 0) + 1);
      if (v === false) m.set('false', (m.get('false') ?? 0) + 1);
    }
    return [...m.entries()].sort(([a]) => (a === 'true' ? -1 : 1));
  };

  return (
    <div className="bg-white border-b border-border px-6 pt-4 pb-4">
      {/* Header: count + search + reset */}
      <div className="flex items-center flex-wrap gap-y-1.5 gap-x-3.5 pb-3 mb-3 border-b border-dashed border-border">
        <span className="text-[1.625rem] font-semibold tracking-tight tabular-nums mr-1.5">
          {n}
          {(anyActive || hasSearch) && (
            <em className="not-italic text-sm text-muted font-normal"> / {total}</em>
          )}
        </span>
        <span className="text-sm text-muted mr-1.5">{n === 1 ? 'Haus' : 'Häuser'}</span>
        <div className="flex-1" />
        <label
          className={`inline-flex items-center gap-1.5 bg-zinc-100 rounded-lg px-3 py-1 border border-transparent transition focus-within:bg-white focus-within:border-accent focus-within:shadow-[0_0_0_3px_rgba(37,99,235,0.12)] min-w-[220px]`}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" className="text-muted shrink-0">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
          <input
            type="search"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Suchen: Hersteller, Modell, ID …"
            className="flex-1 bg-transparent border-none outline-none text-[0.85rem] min-w-0 placeholder:text-zinc-400"
          />
          {hasSearch && (
            <button
              type="button"
              onClick={() => onSearch('')}
              className="inline-flex items-center justify-center w-4 h-4 rounded-full text-[0.7rem] bg-black/10 text-muted hover:bg-black/20 hover:text-zinc-900"
            >
              ×
            </button>
          )}
        </label>
        {anyActive && (
          <button
            type="button"
            onClick={onReset}
            className="text-[0.8125rem] text-muted hover:bg-zinc-100 hover:text-zinc-900 px-3 py-1 rounded-md cursor-pointer select-none"
          >
            Reset
          </button>
        )}
      </div>

      {/* Composition bars */}
      <div className="grid grid-cols-[100px_1fr] gap-y-1.5 gap-x-3.5 text-xs items-center">
        <CategoricalBar label="Quelle"        counts={groupBy('source')}        palette={PALETTES.source}        ontologyGroup="sources"          total={n} field="source"          filters={filters} onPick={onPick} onClear={onClear} />
        <CategoricalBar label="Gebäudetyp"    counts={groupBy('building_type')} palette={PALETTES.building_type} ontologyGroup="building_types"   total={n} field="building_type"   filters={filters} onPick={onPick} onClear={onClear} />
        <CategoricalBar label="Bauweise"      counts={groupBy('construction')}  palette={PALETTES.construction}  ontologyGroup="constructions"    total={n} field="construction"    filters={filters} onPick={onPick} onClear={onClear} />
        <CategoricalBar label="Dachform"      counts={groupBy('roof_type')}     palette={PALETTES.roof_type}     ontologyGroup="roof_types"       total={n} field="roof_type"       filters={filters} onPick={onPick} onClear={onClear} />
        <CategoricalBar label="Stil"          counts={groupBy('style')}         palette={PALETTES.style}         ontologyGroup="styles"           total={n} field="style"           filters={filters} onPick={onPick} onClear={onClear} />
        <CategoricalBar label="Tier"          counts={groupTier()}              palette={PALETTES_BY_KEY.tier!}  ontologyGroup="reconstructability_tiers" total={n} field="min_tier"  filters={filters} onPick={onPick} onClear={onClear} />
        <CategoricalBar label="bim-ai"        counts={groupBool('modelable_in_bim_ai')} palette={PALETTES_BY_KEY.bim_ai!} ontologyGroup="modelable_states" total={n} field="modelable_in_bim_ai" filters={filters} onPick={onPick} onClear={onClear} />
        <CategoricalBar label="Keller"        counts={groupBool('has_basement')} palette={PALETTES_BY_KEY.keller!} ontologyGroup="keller_states"  total={n} field="has_basement"    filters={filters} onPick={onPick} onClear={onClear} />
        <CategoricalBar label="Energiestandard" counts={groupBy('energy_standard')} palette={PALETTES.energy}     ontologyGroup="energy_standards" total={n} field="energy_standard" filters={filters} onPick={onPick} onClear={onClear} />
        <BucketBar label="Fläche"  field="area_m2"    def={BUCKETS.area_m2}    recs={recs} total={n} filters={filters} onPickRange={onPickRange} onClearRange={onClearRange} />
        <BucketBar label="Preis"   field="price_eur"  def={BUCKETS.price_eur}  recs={recs} total={n} filters={filters} onPickRange={onPickRange} onClearRange={onClearRange} />
        <BucketBar label="Baujahr" field="year_built" def={BUCKETS.year_built} recs={recs} total={n} filters={filters} onPickRange={onPickRange} onClearRange={onClearRange} />
      </div>

      {anyActive && (
        <ActiveChips
          filters={filters}
          onClear={onClear}
          onClearRange={onClearRange}
        />
      )}
    </div>
  );
}

function ActiveChips({
  filters,
  onClear,
  onClearRange,
}: {
  filters: Filters;
  onClear: (field: FilterField) => void;
  onClearRange: (fmin: FilterField, fmax: FilterField) => void;
}) {
  const onto = useOntology();
  const rangeFields = new Set<FilterField>();
  const rangeChips: ReactNode[] = [];
  for (const g of RANGE_GROUPS) {
    const lo = filters[g.fmin];
    const hi = filters[g.fmax];
    if (lo === '' && hi === '') continue;
    rangeFields.add(g.fmin);
    rangeFields.add(g.fmax);
    rangeChips.push(
      <Chip
        key={`${g.fmin}/${g.fmax}`}
        axis={g.label}
        value={rangeLabel(g, lo, hi)}
        onClear={() => onClearRange(g.fmin, g.fmax)}
      />,
    );
  }

  const singles = (Object.entries(filters) as [FilterField, string][])
    .filter(([f, v]) => v !== '' && !rangeFields.has(f))
    .map(([f, v]) => (
      <Chip
        key={f}
        axis={AXIS_LABELS[f] ?? f}
        value={chipValueLabel(onto, f, v)}
        onClear={() => onClear(f)}
      />
    ));

  return (
    <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-dashed border-border">
      {rangeChips}
      {singles}
    </div>
  );
}

function chipValueLabel(
  onto: ReturnType<typeof useOntology>,
  field: FilterField,
  value: string,
): string {
  if (field === 'modelable_in_bim_ai') return value === 'true' ? '✓ modellierbar' : '✗ blockiert';
  if (field === 'has_basement') return value === 'true' ? 'mit Keller' : 'ohne Keller';
  if (field === 'min_tier') return `≥ ${value}`;
  const group = AXIS_ONTO[field];
  if (group) return onto[group]?.[value] ?? value;
  return value;
}

function Chip({
  axis,
  value,
  onClear,
}: {
  axis: string;
  value: string;
  onClear: () => void;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 bg-indigo-50 text-indigo-900 pl-2.5 pr-1 py-1 rounded-full text-xs font-medium">
      <span className="text-muted font-normal">{axis}</span>
      {value}
      <button
        type="button"
        onClick={onClear}
        className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-indigo-900/10 text-indigo-900 text-xs hover:bg-indigo-900/30"
      >
        ×
      </button>
    </span>
  );
}
