import { useState, type ReactNode } from 'react';
import type { House } from '../../api/types';
import {
  BUCKETS,
  type Bucket,
  type FilterField,
  type Filters,
  PALETTES,
  PALETTES_BY_KEY,
  RANGE_GROUPS,
  RANGE_PALETTES,
  colorFor,
  matchesActive,
  rangeLabel,
  toFilterValue,
} from '../../lib/filters';
import { useOntology } from '../../api/ontology';

// Compact filter pane for the left sidebar. Each axis is a vertical list of
// clickable value rows showing a color dot + label + count. Range axes
// (Fläche / Preis / Baujahr) render as bucket pills with the same palette.
// Clicking a value toggles it; clicking the active value clears the filter.

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

// Categorical axes — match StatsDashboard's set and palette mapping.
type CatAxisCfg = {
  field: FilterField;
  label: string;
  ontologyGroup: string;
  palette: readonly string[] | Record<string, string>;
  fieldOnRec?: keyof House;  // None for derived axes (tier, bools)
};

const CATEGORICAL_AXES: CatAxisCfg[] = [
  { field: 'source',            label: 'Quelle',          ontologyGroup: 'sources',          palette: PALETTES.source,        fieldOnRec: 'source' },
  { field: 'building_type',     label: 'Gebäudetyp',      ontologyGroup: 'building_types',   palette: PALETTES.building_type, fieldOnRec: 'building_type' },
  { field: 'construction',      label: 'Bauweise',        ontologyGroup: 'constructions',    palette: PALETTES.construction,  fieldOnRec: 'construction' },
  { field: 'roof_type',         label: 'Dachform',        ontologyGroup: 'roof_types',       palette: PALETTES.roof_type,     fieldOnRec: 'roof_type' },
  { field: 'style',             label: 'Stil',            ontologyGroup: 'styles',           palette: PALETTES.style,         fieldOnRec: 'style' },
  { field: 'energy_standard',   label: 'Energiestandard', ontologyGroup: 'energy_standards', palette: PALETTES.energy,        fieldOnRec: 'energy_standard' },
];

export function SidebarFilters({
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
  const onto = useOntology();
  const n = recs.length;
  const hasSearch = search !== '';

  return (
    <div className="px-3 py-3 space-y-4">
      {/* Header: count + search + reset */}
      <div>
        <div className="flex items-baseline gap-2">
          <span className="text-[1.5rem] font-semibold tabular-nums">
            {n}
            {(anyActive || hasSearch) && (
              <em className="not-italic text-sm text-muted font-normal"> / {total}</em>
            )}
          </span>
          <span className="text-sm text-muted">{n === 1 ? 'Haus' : 'Häuser'}</span>
        </div>
        <SearchInput value={search} onChange={onSearch} hasSearch={hasSearch} />
        {anyActive && (
          <button
            type="button"
            onClick={onReset}
            className="mt-2 text-[0.75rem] text-accent hover:underline"
          >
            ← Alle Filter zurücksetzen
          </button>
        )}
      </div>

      {/* Categorical axes */}
      {CATEGORICAL_AXES.map((axis) => {
        const counts = countCategorical(recs, axis.fieldOnRec!);
        if (counts.length === 0) return null;
        return (
          <CategoricalSection
            key={axis.field}
            label={axis.label}
            field={axis.field}
            counts={counts}
            palette={axis.palette}
            ontologyGroup={axis.ontologyGroup}
            filters={filters}
            onPick={onPick}
            onClear={onClear}
            onto={onto}
          />
        );
      })}

      {/* Tier (derived) */}
      <CategoricalSection
        label="Datenqualität (Tier)"
        field="min_tier"
        counts={countTier(recs)}
        palette={PALETTES_BY_KEY.tier!}
        ontologyGroup="reconstructability_tiers"
        filters={filters}
        onPick={onPick}
        onClear={onClear}
        onto={onto}
        renderLabel={(k) => `≥ ${k}`}
      />

      {/* bim-ai boolean */}
      <CategoricalSection
        label="bim-ai modellierbar"
        field="modelable_in_bim_ai"
        counts={countBool(recs, 'modelable_in_bim_ai')}
        palette={PALETTES_BY_KEY.bim_ai!}
        ontologyGroup="modelable_states"
        filters={filters}
        onPick={onPick}
        onClear={onClear}
        onto={onto}
      />

      {/* basement boolean */}
      <CategoricalSection
        label="Keller"
        field="has_basement"
        counts={countBool(recs, 'has_basement')}
        palette={PALETTES_BY_KEY.keller!}
        ontologyGroup="keller_states"
        filters={filters}
        onPick={onPick}
        onClear={onClear}
        onto={onto}
      />

      {/* Range axes */}
      {(['area_m2', 'price_eur', 'year_built'] as const).map((field) => (
        <BucketSection
          key={field}
          field={field}
          recs={recs}
          filters={filters}
          onPickRange={onPickRange}
          onClearRange={onClearRange}
        />
      ))}
    </div>
  );
}

// ── helpers ──────────────────────────────────────────────────────────────────

function countCategorical(recs: House[], field: keyof House): [string, number][] {
  const m = new Map<string, number>();
  for (const r of recs) {
    const v = r[field];
    if (typeof v === 'string' && v) m.set(v, (m.get(v) ?? 0) + 1);
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function countTier(recs: House[]): [string, number][] {
  const m = new Map<string, number>();
  for (const r of recs) {
    const t = r.reconstructability_tier;
    if (t) m.set(t, (m.get(t) ?? 0) + 1);
  }
  return [...m.entries()].sort(
    (a, b) => parseInt(a[0].replace(/^T/, ''), 10) - parseInt(b[0].replace(/^T/, ''), 10),
  );
}

function countBool(recs: House[], field: 'modelable_in_bim_ai' | 'has_basement'): [string, number][] {
  const m = new Map<string, number>();
  for (const r of recs) {
    const v = r[field];
    if (v === true) m.set('true', (m.get('true') ?? 0) + 1);
    if (v === false) m.set('false', (m.get('false') ?? 0) + 1);
  }
  return [...m.entries()].sort(([a]) => (a === 'true' ? -1 : 1));
}

// ── search input ─────────────────────────────────────────────────────────────

function SearchInput({
  value,
  onChange,
  hasSearch,
}: {
  value: string;
  onChange: (v: string) => void;
  hasSearch: boolean;
}) {
  return (
    <label className="mt-2 flex items-center gap-1.5 bg-white rounded-md px-2 py-1 border border-border focus-within:border-accent focus-within:shadow-[0_0_0_3px_rgba(37,99,235,0.12)]">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" className="text-muted shrink-0">
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Suchen…"
        className="flex-1 bg-transparent border-none outline-none text-[0.8rem] min-w-0 placeholder:text-zinc-400"
      />
      {hasSearch && (
        <button
          type="button"
          onClick={() => onChange('')}
          className="inline-flex items-center justify-center w-4 h-4 rounded-full text-[0.7rem] bg-black/10 text-muted hover:bg-black/20 hover:text-zinc-900"
          aria-label="Suche leeren"
        >
          ×
        </button>
      )}
    </label>
  );
}

// ── categorical section ─────────────────────────────────────────────────────

const VISIBLE_LIMIT = 6;

function CategoricalSection({
  label,
  field,
  counts,
  palette,
  ontologyGroup,
  filters,
  onPick,
  onClear,
  onto,
  renderLabel,
}: {
  label: string;
  field: FilterField;
  counts: [string, number][];
  palette: readonly string[] | Record<string, string>;
  ontologyGroup: string;
  filters: Filters;
  onPick: (field: FilterField, value: string) => void;
  onClear: (field: FilterField) => void;
  onto: Record<string, Record<string, string>>;
  renderLabel?: (key: string) => string;
}) {
  const [expanded, setExpanded] = useState(false);
  const activeVal = filters[field] || '';
  const hasFilter = activeVal !== '';
  const shown = expanded ? counts : counts.slice(0, VISIBLE_LIMIT);
  const hiddenCount = counts.length - shown.length;

  if (counts.length === 0) return null;

  return (
    <section>
      <div className="flex items-baseline gap-2 mb-1.5">
        <h3 className={`text-[0.7rem] uppercase tracking-wider font-semibold ${hasFilter ? 'text-accent' : 'text-muted'}`}>
          {label}
        </h3>
        {hasFilter && (
          <button
            type="button"
            onClick={() => onClear(field)}
            className="text-[0.65rem] text-accent hover:underline"
          >
            ×
          </button>
        )}
      </div>
      <ul className="space-y-px">
        {shown.map(([k, n], i) => {
          const active = matchesActive(field, k, activeVal);
          const fullLabel =
            renderLabel
              ? renderLabel(k)
              : ontologyLabel(onto, ontologyGroup, k, field);
          const color = colorFor(palette, k, i);
          return (
            <li key={k}>
              <button
                type="button"
                onClick={() => onPick(field, toFilterValue(field, k))}
                className={`group w-full flex items-center gap-2 px-2 py-1 rounded text-[0.78rem] text-left transition ${
                  active
                    ? 'font-semibold ring-1 ring-inset'
                    : 'hover:bg-zinc-100'
                }`}
                style={
                  active
                    ? { background: hexWithAlpha(color, 0.15), color: '#18181b' }
                    : undefined
                }
                title={fullLabel}
              >
                <span
                  className="inline-block w-2.5 h-2.5 rounded-[3px] shrink-0"
                  style={{ background: color }}
                />
                <span className="flex-1 truncate">{fullLabel}</span>
                <span className="text-muted tabular-nums text-[0.72rem] shrink-0">{n}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {hiddenCount > 0 && !expanded && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-1 text-[0.7rem] text-accent hover:underline pl-2"
        >
          + {hiddenCount} weitere
        </button>
      )}
      {expanded && counts.length > VISIBLE_LIMIT && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="mt-1 text-[0.7rem] text-muted hover:text-accent hover:underline pl-2"
        >
          weniger anzeigen
        </button>
      )}
    </section>
  );
}

// ── bucket / range section ──────────────────────────────────────────────────

function BucketSection({
  field,
  recs,
  filters,
  onPickRange,
  onClearRange,
}: {
  field: 'area_m2' | 'price_eur' | 'year_built';
  recs: House[];
  filters: Filters;
  onPickRange: (fmin: FilterField, min: string, fmax: FilterField, max: string) => void;
  onClearRange: (fmin: FilterField, fmax: FilterField) => void;
}) {
  const def = BUCKETS[field];
  const palette = RANGE_PALETTES[field] ?? {};
  const label =
    field === 'area_m2' ? 'Fläche' : field === 'price_eur' ? 'Preis' : 'Baujahr';
  const fMin = filters[def.fieldMin] || '';
  const fMax = filters[def.fieldMax] || '';
  const hasFilter = fMin !== '' || fMax !== '';

  const counts: [Bucket, number][] = def.buckets.map((b) => {
    let n = 0;
    for (const r of recs) {
      const v = r[field];
      if (v == null) continue;
      if (b.min != null && v < b.min) continue;
      if (b.max != null && v > b.max) continue;
      n++;
    }
    return [b, n];
  });

  // Find which bucket (if any) is currently picked.
  const activeKey =
    hasFilter
      ? def.buckets.find(
          (b) => String(b.min ?? '') === fMin && String(b.max ?? '') === fMax,
        )?.key ?? null
      : null;

  if (counts.every(([, n]) => n === 0)) return null;

  return (
    <section>
      <div className="flex items-baseline gap-2 mb-1.5">
        <h3 className={`text-[0.7rem] uppercase tracking-wider font-semibold ${hasFilter ? 'text-accent' : 'text-muted'}`}>
          {label}
        </h3>
        {hasFilter && (
          <button
            type="button"
            onClick={() => onClearRange(def.fieldMin, def.fieldMax)}
            className="text-[0.65rem] text-accent hover:underline"
          >
            ×
          </button>
        )}
      </div>
      <ul className="space-y-px">
        {counts.map(([b, n]) => {
          if (n === 0) return null;
          const color = palette[b.key] ?? '#cbd5e1';
          const active = b.key === activeKey;
          return (
            <li key={b.key}>
              <button
                type="button"
                onClick={() =>
                  onPickRange(def.fieldMin, String(b.min ?? ''), def.fieldMax, String(b.max ?? ''))
                }
                className={`group w-full flex items-center gap-2 px-2 py-1 rounded text-[0.78rem] text-left transition ${
                  active ? 'font-semibold ring-1 ring-inset' : 'hover:bg-zinc-100'
                }`}
                style={active ? { background: hexWithAlpha(color, 0.15), color: '#18181b' } : undefined}
              >
                <span
                  className="inline-block w-2.5 h-2.5 rounded-[3px] shrink-0"
                  style={{ background: color }}
                />
                <span className="flex-1 truncate">{b.label}</span>
                <span className="text-muted tabular-nums text-[0.72rem] shrink-0">{n}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {hasFilter && (
        <div className="mt-1 text-[0.65rem] text-muted pl-2">
          {rangeLabel(
            RANGE_GROUPS.find((g) => g.fmin === def.fieldMin)!,
            fMin,
            fMax,
          )}
        </div>
      )}
    </section>
  );
}

function ontologyLabel(
  onto: Record<string, Record<string, string>>,
  group: string,
  key: string,
  field: FilterField,
): string {
  if (field === 'has_basement') return key === 'true' ? 'mit Keller' : 'ohne Keller';
  if (field === 'modelable_in_bim_ai') return key === 'true' ? '✓ modellierbar' : '✗ blockiert';
  return onto[group]?.[key] ?? key;
}

// Build a "color w/ alpha" string. Supports #rgb and #rrggbb hex inputs from
// the existing PALETTES; falls back to the original color on parse failure.
function hexWithAlpha(hex: string, alpha: number): string {
  const m = hex.match(/^#([0-9a-f]{3,8})$/i);
  if (!m) return hex;
  const c = m[1];
  let r: number, g: number, b: number;
  if (c.length === 3) {
    r = parseInt(c[0]! + c[0]!, 16);
    g = parseInt(c[1]! + c[1]!, 16);
    b = parseInt(c[2]! + c[2]!, 16);
  } else if (c.length >= 6) {
    r = parseInt(c.slice(0, 2), 16);
    g = parseInt(c.slice(2, 4), 16);
    b = parseInt(c.slice(4, 6), 16);
  } else {
    return hex;
  }
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// re-export for the ActiveChips section below
export type { ReactNode };
