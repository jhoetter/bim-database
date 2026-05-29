import { useEffect, useState, useMemo } from 'react';
import { Link } from 'react-router';
import { fetchDatasets, useResource } from '../api/client';
import { WorkflowPhaseBadge } from '../components/WorkflowPhaseBadge';
import type { DatasetDrawing, DatasetHouse } from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import { getLastVisitedScene } from './AnnotatePage';

// Two views: 'gallery' (flat Pinterest grid of all drawings across houses)
// and 'by-house' (grouped, easier for spot-checking coverage). Filters in
// the sidebar restrict by kind + label status.

type KindFilter = 'all' | 'elevation' | 'floorplan';
type LabelFilter = 'all' | 'unlabeled' | 'labeled' | 'rejected';
type ViewMode = 'gallery' | 'by-house';

export function DatasetPage() {
  const { data, error, loading } = useResource(fetchDatasets, []);
  const [kindFilter, setKindFilter] = useState<KindFilter>('all');
  const [labelFilter, setLabelFilter] = useState<LabelFilter>('all');
  const [viewMode, setViewMode] = useState<ViewMode>('by-house');
  const [houseSweepCount, setHouseSweepCount] = useState<number | null>(null);

  // R0.13 — surface the one-time house-localStorage sweep result. main.tsx
  // ran the sweep before React mounted; we pull the count off `window`.
  useEffect(() => {
    const w = window as unknown as { __bimHouseSweepCount?: number };
    if (typeof w.__bimHouseSweepCount === 'number' && w.__bimHouseSweepCount > 0) {
      setHouseSweepCount(w.__bimHouseSweepCount);
      delete w.__bimHouseSweepCount;
    }
  }, []);

  const houses = useMemo(() => data ?? [], [data]);

  const filtered = useMemo(() => {
    return houses
      .map((h) => ({
        ...h,
        drawings: (h.drawings || []).filter((d) => {
          if (kindFilter !== 'all' && d.kind !== kindFilter) return false;
          if (labelFilter !== 'all' && (d.label_status ?? 'unlabeled') !== labelFilter) return false;
          return true;
        }),
      }))
      .filter((h) => h.drawings.length > 0 || (kindFilter === 'all' && labelFilter === 'all'));
  }, [houses, kindFilter, labelFilter]);

  const totalDrawings = filtered.reduce((acc, h) => acc + h.drawings.length, 0);
  const totalHouses = houses.length;
  const housesWithDrawings = houses.filter((h) => (h.drawings || []).length > 0).length;

  return (
    <Shell
      breadcrumb={<Breadcrumb items={[{ label: 'Datensatz' }]} />}
      leftSidebar={
        <DatasetFilters
          totalHouses={totalHouses}
          housesWithDrawings={housesWithDrawings}
          totalDrawings={totalDrawings}
          kindFilter={kindFilter}
          labelFilter={labelFilter}
          viewMode={viewMode}
          counts={countByKind(houses)}
          labelCounts={countByLabel(houses)}
          onKind={setKindFilter}
          onLabel={setLabelFilter}
          onView={setViewMode}
        />
      }
    >
      <div className="px-6 py-5">
        {houseSweepCount != null && (
          <div className="mb-4 px-3 py-2 rounded-md bg-emerald-50 border border-emerald-300 text-[0.78rem] text-emerald-900">
            ↻ {houseSweepCount} alte House-Einträge aus dem Browser entfernt — Dataset-Daten bleiben unberührt.
            <button
              type="button"
              onClick={() => setHouseSweepCount(null)}
              className="ml-2 underline text-[0.7rem]"
            >schließen</button>
          </div>
        )}
        {loading && <p className="text-muted text-sm">Lade…</p>}
        {error && <p className="text-red-700 text-sm">Fehler: {error.message}</p>}
        {!loading && !error && filtered.length === 0 && <EmptyState />}
        {!loading && !error && filtered.length > 0 && (
          viewMode === 'gallery'
            ? <FlatGallery houses={filtered} />
            : <GroupedByHouse houses={filtered} />
        )}
      </div>
    </Shell>
  );
}

// ── sidebar filters ──────────────────────────────────────────────────────────

function DatasetFilters({
  totalHouses,
  housesWithDrawings,
  totalDrawings,
  kindFilter,
  labelFilter,
  viewMode,
  counts,
  labelCounts,
  onKind,
  onLabel,
  onView,
}: {
  totalHouses: number;
  housesWithDrawings: number;
  totalDrawings: number;
  kindFilter: KindFilter;
  labelFilter: LabelFilter;
  viewMode: ViewMode;
  counts: { elevation: number; floorplan: number; section: number; total: number };
  labelCounts: Record<string, number>;
  onKind: (k: KindFilter) => void;
  onLabel: (l: LabelFilter) => void;
  onView: (v: ViewMode) => void;
}) {
  return (
    <div className="px-3 py-3 space-y-5">
      <div>
        <div className="flex items-baseline gap-2">
          <span className="text-[1.5rem] font-semibold tabular-nums">{totalDrawings}</span>
          <span className="text-sm text-muted">Zeichnungen</span>
        </div>
        <p className="text-[0.72rem] text-muted leading-snug mt-1">
          {housesWithDrawings} / {totalHouses} Häuser haben generierte Zeichnungen.
        </p>
      </div>

      <FilterGroup title="Ansicht">
        <FilterRow active={viewMode === 'by-house'} onClick={() => onView('by-house')}>
          nach Haus
        </FilterRow>
        <FilterRow active={viewMode === 'gallery'} onClick={() => onView('gallery')}>
          flache Galerie
        </FilterRow>
      </FilterGroup>

      <FilterGroup title="Typ">
        <FilterRow active={kindFilter === 'all'} onClick={() => onKind('all')} count={counts.total}>
          alle
        </FilterRow>
        <FilterRow
          active={kindFilter === 'elevation'}
          onClick={() => onKind('elevation')}
          count={counts.elevation}
          dotColor="#f59e0b"
        >
          Ansicht
        </FilterRow>
        <FilterRow
          active={kindFilter === 'floorplan'}
          onClick={() => onKind('floorplan')}
          count={counts.floorplan}
          dotColor="#2563eb"
        >
          Grundriss
        </FilterRow>
      </FilterGroup>

      <FilterGroup title="Label-Status">
        <FilterRow
          active={labelFilter === 'all'}
          onClick={() => onLabel('all')}
          count={Object.values(labelCounts).reduce((a, b) => a + b, 0)}
        >
          alle
        </FilterRow>
        <FilterRow
          active={labelFilter === 'unlabeled'}
          onClick={() => onLabel('unlabeled')}
          count={labelCounts.unlabeled ?? 0}
          dotColor="#a1a1aa"
        >
          unlabeled
        </FilterRow>
        <FilterRow
          active={labelFilter === 'labeled'}
          onClick={() => onLabel('labeled')}
          count={labelCounts.labeled ?? 0}
          dotColor="#16a34a"
        >
          labeled
        </FilterRow>
        <FilterRow
          active={labelFilter === 'rejected'}
          onClick={() => onLabel('rejected')}
          count={labelCounts.rejected ?? 0}
          dotColor="#dc2626"
        >
          rejected
        </FilterRow>
      </FilterGroup>

      <div className="text-[0.7rem] text-muted leading-snug border-t border-border pt-3">
        <p>KI-generiert via <code className="font-mono">scripts/generate_synthetic_drawings.py</code>; reale Pläne via <code className="font-mono">scripts/include_real_plans.py</code> (gestarrtes Haus).</p>
        <p className="mt-1">Style-Refs: h21/h22/h23 Pläne.</p>
        <p className="mt-1">Content-Refs: Bilder des jeweiligen Hauses.</p>
      </div>
    </div>
  );
}

function FilterGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
        {title}
      </h3>
      <ul className="space-y-px">{children}</ul>
    </section>
  );
}

function FilterRow({
  children,
  active,
  count,
  dotColor,
  onClick,
}: {
  children: React.ReactNode;
  active: boolean;
  count?: number;
  dotColor?: string;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={`w-full flex items-center gap-2 px-2 py-1 rounded text-[0.78rem] text-left transition ${
          active ? 'bg-accent/10 text-accent font-semibold' : 'hover:bg-zinc-100'
        }`}
      >
        {dotColor && (
          <span
            className="inline-block w-2.5 h-2.5 rounded-[3px] shrink-0"
            style={{ background: dotColor }}
          />
        )}
        <span className="flex-1 truncate">{children}</span>
        {count != null && (
          <span className="text-muted tabular-nums text-[0.72rem] shrink-0">{count}</span>
        )}
      </button>
    </li>
  );
}

// ── content views ────────────────────────────────────────────────────────────

function GroupedByHouse({ houses }: { houses: DatasetHouse[] }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
      {houses.map((h) => (
        <HouseCard key={h.key} house={h} />
      ))}
    </div>
  );
}

// One card per house with a representative image + stats. Clicking the card
// goes directly to annotation view — RESUMING on the last-visited scene if
// the user has been here before (per-house localStorage), else the hero
// scene (preferring floorplan-EG).
function HouseCard({ house }: { house: DatasetHouse }) {
  const labeled = house.drawings.filter((d) => d.labeled).length;
  const total = house.drawings.length;
  const pct = total === 0 ? 0 : Math.round((labeled / total) * 100);
  const hero =
    house.drawings.find((d) => d.kind === 'floorplan' && d.floor === 'EG') ??
    house.drawings.find((d) => d.kind === 'floorplan') ??
    house.drawings[0];
  if (!hero) return null;
  // Resume on last-visited if it still exists in this house's drawings.
  const last = getLastVisitedScene('dataset', house.key);
  const targetFile =
    last && house.drawings.some((d) => d.file === last) ? last : hero.file;
  return (
    <Link
      to={`/dataset/${house.key}/scene/${encodeURIComponent(targetFile)}/annotate`}
      className="block rounded-lg overflow-hidden border border-border bg-white hover:shadow-md hover:border-zinc-300 transition"
      title={
        targetFile === hero.file
          ? `${total} Zeichnung(en) — Klick öffnet Annotation`
          : `${total} Zeichnung(en) — fortsetzen bei zuletzt besuchter Szene`
      }
    >
      <div className="aspect-square bg-zinc-50 flex items-center justify-center overflow-hidden">
        <img
          src={hero.url}
          alt={house.model ?? house.key}
          loading="lazy"
          className="max-w-full max-h-full object-contain"
        />
      </div>
      <div className="p-2.5 space-y-1">
        <div className="flex items-baseline gap-1.5">
          <h3 className="text-[0.85rem] font-semibold truncate">{house.model ?? house.key}</h3>
          <span className="text-[0.65rem] text-muted font-mono">{house.key}</span>
        </div>
        <div className="flex items-center gap-1.5 text-[0.7rem] text-muted">
          <span>{total} {total === 1 ? 'Zeichnung' : 'Zeichnungen'}</span>
          {labeled > 0 && (
            <span
              className={`text-[0.62rem] px-1.5 py-0.5 rounded-full font-semibold ${
                pct === 100 ? 'bg-emerald-600 text-white'
                : pct >= 50  ? 'bg-emerald-100 text-emerald-900'
                            : 'bg-amber-100 text-amber-900'
              }`}
              title={`${labeled} / ${total} Szenen annotiert`}
            >
              ✓ {labeled}/{total} ({pct}%)
            </span>
          )}
        </div>
        <WorkflowPhaseBadge
          scope="dataset"
          houseKey={house.key}
          sceneFiles={house.drawings.map((d) => d.file)}
        />
      </div>
    </Link>
  );
}

function FlatGallery({ houses }: { houses: DatasetHouse[] }) {
  const all = houses.flatMap((h) => h.drawings.map((d) => ({ houseKey: h.key, d })));
  return (
    <div className="columns-[260px] gap-3">
      {all.map(({ houseKey, d }) => (
        <DrawingTile key={`${houseKey}-${d.file}`} houseKey={houseKey} d={d} showHouseKey />
      ))}
    </div>
  );
}

function DrawingTile({
  houseKey,
  d,
  showHouseKey = false,
}: {
  houseKey: string;
  d: DatasetDrawing;
  showHouseKey?: boolean;
}) {
  const kindTone =
    d.kind === 'floorplan'
      ? 'bg-blue-600/85'
      : d.kind === 'elevation'
      ? 'bg-amber-600/90'
      : 'bg-zinc-700/85';
  const label =
    d.kind === 'floorplan' && d.floor
      ? d.floor
      : d.kind === 'elevation' && d.view
      ? d.view.charAt(0).toUpperCase() + d.view.slice(1)
      : d.kind;

  return (
    <Link
      to={`/dataset/${houseKey}/scene/${encodeURIComponent(d.file)}/annotate`}
      className="relative block mb-3 break-inside-avoid rounded-lg overflow-hidden border border-border bg-white hover:shadow-md hover:border-zinc-300 transition"
    >
      <img
        src={d.url}
        alt={d.title ?? d.file}
        loading="lazy"
        className="w-full h-auto block bg-white"
      />
      <span
        className={`absolute top-1.5 left-1.5 ${kindTone} text-white text-[0.65rem] font-semibold px-1.5 py-0.5 rounded shadow`}
      >
        {label}
      </span>
      {showHouseKey && (
        <span className="absolute top-1.5 right-1.5 bg-zinc-800/80 text-white text-[0.625rem] font-medium px-1.5 py-0.5 rounded">
          {houseKey}
        </span>
      )}
      {d.label_status && d.label_status !== 'unlabeled' && (
        <span
          className={`absolute bottom-1.5 right-1.5 text-white text-[0.625rem] font-semibold px-1.5 py-0.5 rounded ${
            d.label_status === 'labeled'
              ? 'bg-green-700'
              : d.label_status === 'rejected'
              ? 'bg-red-700'
              : 'bg-zinc-600'
          }`}
        >
          {d.label_status}
        </span>
      )}
      {d.title && (
        <span className="absolute bottom-1.5 left-1.5 right-12 bg-black/65 text-white text-[0.7rem] px-2 py-0.5 rounded line-clamp-1">
          {d.title}
        </span>
      )}
    </Link>
  );
}

function EmptyState() {
  return (
    <div className="max-w-xl mx-auto py-12 text-center text-sm text-muted">
      <p className="font-semibold text-zinc-900 mb-2">Datensatz noch leer</p>
      <p>
        KI-generieren mit{' '}
        <code className="font-mono bg-zinc-100 px-1.5 py-0.5 rounded">
          python scripts/generate_synthetic_drawings.py
        </code>
        {' '}(erfordert <code className="font-mono">OPENAI_API_KEY</code>) oder reale Pläne aus einem gestarrten Haus mit{' '}
        <code className="font-mono bg-zinc-100 px-1.5 py-0.5 rounded">
          python scripts/include_real_plans.py
        </code>{' '}übernehmen.
      </p>
    </div>
  );
}

// ── counting helpers ────────────────────────────────────────────────────────

function countByKind(houses: DatasetHouse[]) {
  let elevation = 0,
    floorplan = 0,
    section = 0,
    total = 0;
  for (const h of houses) {
    for (const d of h.drawings || []) {
      total++;
      if (d.kind === 'elevation') elevation++;
      else if (d.kind === 'floorplan') floorplan++;
      else if (d.kind === 'section') section++;
    }
  }
  return { elevation, floorplan, section, total };
}

function countByLabel(houses: DatasetHouse[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const h of houses) {
    for (const d of h.drawings || []) {
      const k = d.label_status ?? 'unlabeled';
      out[k] = (out[k] ?? 0) + 1;
    }
  }
  return out;
}
