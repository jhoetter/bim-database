// Two bar variants — categorical (makeBar) and bucketed numeric (makeBucketBar).
// Kept in one file because they share the bar/legend chrome.

import type { House } from '../../api/types';
import { useOntology } from '../../api/ontology';
import {
  type Bucket,
  type BucketDef,
  type FilterField,
  type Filters,
  RANGE_PALETTES,
  colorFor,
  matchesActive,
  toFilterValue,
} from '../../lib/filters';

interface CatBarProps {
  label: string;
  counts: [string, number][]; // sorted by descending count
  palette: readonly string[] | Record<string, string>;
  ontologyGroup: string;
  total: number;
  field: FilterField;
  filters: Filters;
  onPick: (field: FilterField, value: string) => void;
  onClear: (field: FilterField) => void;
}

export function CategoricalBar({
  label,
  counts,
  palette,
  ontologyGroup,
  total,
  field,
  filters,
  onPick,
  onClear,
}: CatBarProps) {
  const onto = useOntology();
  const activeVal = filters[field] || '';
  const hasFilter = activeVal !== '';
  const sum = counts.reduce((a, [, n]) => a + n, 0);
  const missing = total - sum;

  type Seg = { key: string | null; n: number; color: string };
  const segs: Seg[] = counts.map(([k, n], i) => ({ key: k, n, color: colorFor(palette, k, i) }));
  if (missing > 0) segs.push({ key: null, n: missing, color: '#e5e7eb' });

  const coverage = total > 0 ? Math.round((total - missing) / total * 100) : 0;

  return (
    <>
      <div
        className={`text-right pr-1 text-xs font-medium tracking-tight ${
          hasFilter ? 'text-accent font-semibold' : 'text-muted'
        }`}
      >
        {label}
        {hasFilter ? (
          <>
            {' · '}
            <button
              type="button"
              onClick={() => onClear(field)}
              className="text-[0.6875rem] text-accent hover:underline"
            >
              ×
            </button>
          </>
        ) : (
          <span
            className="ml-1 text-[0.625rem] text-zinc-400 tabular-nums"
            title={`${total - missing}/${total} Einträge haben dieses Feld`}
          >
            {coverage}%
          </span>
        )}
      </div>
      <div className={`flex h-4 rounded overflow-hidden bg-zinc-100 ${hasFilter ? 'has-filter' : ''}`}>
        {segs.map(({ key, n, color }, idx) => {
          const pct = (n / total * 100).toFixed(1);
          const labelText = key ? onto[ontologyGroup]?.[key] ?? key : 'unbekannt';
          const text = n / total > 0.06 ? n : '';
          const active = key != null && matchesActive(field, key, activeVal);
          const dim = hasFilter && !active && key != null;
          const isUnset = key == null;
          return (
            <button
              key={key ?? `__unset_${idx}`}
              type="button"
              disabled={isUnset}
              onClick={() => key != null && onPick(field, toFilterValue(field, key))}
              title={`${labelText}: ${n} (${pct}%)`}
              className={`flex items-center justify-center text-[0.6rem] font-semibold min-w-0 px-1 whitespace-nowrap overflow-hidden transition hover:brightness-110
                ${active ? 'opacity-100 ring-2 ring-inset ring-black/20' : ''}
                ${dim ? 'opacity-25' : 'opacity-95'}
                ${isUnset ? 'bg-stripes text-zinc-500 cursor-default' : 'cursor-pointer text-white'}`}
              style={{ flex: `${n} 1 0`, background: color }}
            >
              {text}
            </button>
          );
        })}
      </div>
      <Legend
        items={counts.slice(0, 6).map(([k, n], i) => ({
          key: k,
          label: onto[ontologyGroup]?.[k] ?? k,
          n,
          color: colorFor(palette, k, i),
          active: matchesActive(field, k, activeVal),
        }))}
        moreCount={counts.length > 6 ? counts.length - 6 : 0}
        missing={missing}
        hasFilter={hasFilter}
        onPick={(k) => onPick(field, toFilterValue(field, k))}
      />
    </>
  );
}

interface BucketBarProps {
  label: string;
  field: 'area_m2' | 'price_eur' | 'year_built';
  def: BucketDef;
  recs: House[];
  total: number;
  filters: Filters;
  onPickRange: (fmin: FilterField, min: string, fmax: FilterField, max: string) => void;
  onClearRange: (fmin: FilterField, fmax: FilterField) => void;
}

export function BucketBar({
  label,
  field,
  def,
  recs,
  total,
  filters,
  onPickRange,
  onClearRange,
}: BucketBarProps) {
  const palette = RANGE_PALETTES[field] ?? {};
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
  const sum = counts.reduce((a, [, n]) => a + n, 0);
  const missing = total - sum;
  const coverage = total > 0 ? Math.round((total - missing) / total * 100) : 0;

  return (
    <>
      <div
        className={`text-right pr-1 text-xs font-medium tracking-tight ${
          hasFilter ? 'text-accent font-semibold' : 'text-muted'
        }`}
      >
        {label}
        {hasFilter ? (
          <>
            {' · '}
            <button
              type="button"
              onClick={() => onClearRange(def.fieldMin, def.fieldMax)}
              className="text-[0.6875rem] text-accent hover:underline"
            >
              ×
            </button>
          </>
        ) : (
          <span
            className="ml-1 text-[0.625rem] text-zinc-400 tabular-nums"
            title={`${total - missing}/${total} Einträge haben dieses Feld`}
          >
            {coverage}%
          </span>
        )}
      </div>
      <div className={`flex h-4 rounded overflow-hidden bg-zinc-100 ${hasFilter ? 'has-filter' : ''}`}>
        {counts.map(([b, n]) => {
          if (n === 0) return null;
          const col = palette[b.key] ?? '#cbd5e1';
          const pct = (n / total * 100).toFixed(1);
          const active = String(b.min ?? '') === fMin && String(b.max ?? '') === fMax;
          const dim = hasFilter && !active;
          const text = n / total > 0.06 ? n : '';
          return (
            <button
              key={b.key}
              type="button"
              onClick={() =>
                onPickRange(def.fieldMin, String(b.min ?? ''), def.fieldMax, String(b.max ?? ''))
              }
              title={`${b.label}: ${n} (${pct}%)`}
              className={`flex items-center justify-center text-[0.6rem] font-semibold text-white px-1 whitespace-nowrap overflow-hidden transition hover:brightness-110 cursor-pointer
                ${active ? 'opacity-100 ring-2 ring-inset ring-black/20' : ''}
                ${dim ? 'opacity-25' : 'opacity-95'}`}
              style={{ flex: `${n} 1 0`, background: col }}
            >
              {text}
            </button>
          );
        })}
        {missing > 0 && (
          <div
            className="flex items-center justify-center text-[0.6rem] font-semibold text-zinc-500 bg-stripes px-1 whitespace-nowrap overflow-hidden"
            style={{ flex: `${missing} 1 0`, background: '#e5e7eb' }}
            title={`unbekannt: ${missing}`}
          >
            {missing / total > 0.06 ? missing : ''}
          </div>
        )}
      </div>
      <Legend
        items={counts.map(([b, n]) => ({
          key: b.key,
          label: b.label,
          n,
          color: palette[b.key] ?? '#cbd5e1',
          active: String(b.min ?? '') === fMin && String(b.max ?? '') === fMax,
        }))}
        missing={missing}
        hasFilter={hasFilter}
        onPick={(k) => {
          const b = def.buckets.find((x) => x.key === k);
          if (b) onPickRange(def.fieldMin, String(b.min ?? ''), def.fieldMax, String(b.max ?? ''));
        }}
      />
    </>
  );
}

interface LegendItem {
  key: string;
  label: string;
  n: number;
  color: string;
  active: boolean;
}

function Legend({
  items,
  moreCount = 0,
  missing,
  hasFilter,
  onPick,
}: {
  items: LegendItem[];
  moreCount?: number;
  missing: number;
  hasFilter: boolean;
  onPick: (key: string) => void;
}) {
  return (
    <div className="col-start-2 flex flex-wrap gap-y-1 text-[0.6875rem] text-muted -mt-px mb-1">
      {items.map((it) => {
        const cls = it.active
          ? 'bg-indigo-50 text-zinc-900 font-semibold'
          : hasFilter
          ? 'opacity-45'
          : '';
        return (
          <button
            key={it.key}
            type="button"
            onClick={() => onPick(it.key)}
            className={`inline-flex items-center py-px pl-1 pr-2 mr-1 rounded-full hover:bg-zinc-100 ${cls}`}
          >
            <span
              className="inline-block w-2 h-2 rounded-sm mr-1.5"
              style={{ background: it.color }}
            />
            {it.label} <strong className="ml-1 text-zinc-900 font-semibold">{it.n}</strong>
          </button>
        );
      })}
      {moreCount > 0 && (
        <span className="text-muted px-1.5 italic">… +{moreCount}</span>
      )}
      {missing > 0 && (
        <span
          className="inline-flex items-center py-px pl-1 pr-2 mr-1 text-muted"
          title={`${missing} Eintrag(e) ohne dieses Feld`}
        >
          <span className="inline-block w-2 h-2 rounded-sm mr-1.5 bg-stripes bg-zinc-200" />
          unbekannt <strong className="ml-1 text-zinc-900 font-semibold">{missing}</strong>
        </span>
      )}
    </div>
  );
}
