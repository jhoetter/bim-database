import { useEffect, useState } from 'react';
import { fetchHouses } from '../api/client';
import type { House } from '../api/types';
import { matchesSearch, useFiltersFromUrl } from '../lib/filters';
import { HouseCard } from '../components/HouseCard';
import { SidebarFilters } from '../components/stats/SidebarFilters';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';

// TOTAL is the unfiltered count — survives across renders so the header can
// show "9 / 57 Häuser" once a filter is active.
let TOTAL_CACHED = 0;

export function HousesPage() {
  const { filters, search, setFilter, setRange, setSearch, reset, anyActive } =
    useFiltersFromUrl();
  const [recs, setRecs] = useState<House[]>([]);
  const [total, setTotal] = useState(TOTAL_CACHED);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchHouses(filters)
      .then((data) => {
        if (cancelled) return;
        const filtered = search ? data.filter((r) => matchesSearch(r, search)) : data;
        setRecs(filtered);
        if (!anyActive && !search) {
          TOTAL_CACHED = data.length;
          setTotal(data.length);
        }
      })
      .catch((e: unknown) => !cancelled && setError(e as Error))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(filters), search]);

  return (
    <Shell
      breadcrumb={<Breadcrumb items={[{ label: 'Alle Häuser' }]} />}
      leftSidebar={
        <SidebarFilters
          recs={recs}
          total={total || recs.length}
          search={search}
          filters={filters}
          anyActive={anyActive}
          onSearch={setSearch}
          onPick={setFilter}
          onClear={(f) => setFilter(f, '')}
          onPickRange={setRange}
          onClearRange={(fmin, fmax) => {
            setRange(fmin, '', fmax, '');
          }}
          onReset={reset}
        />
      }
    >
      <div className="px-6 pt-3 pb-2 text-[0.8125rem] text-muted">
        {loading
          ? 'Lade…'
          : error
          ? `Fehler: ${error.message}`
          : `${recs.length} ${recs.length === 1 ? 'Eintrag' : 'Einträge'} gefunden`}
      </div>
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3.5 px-6 pt-2 pb-6 list-none">
        {recs.map((h) => (
          <li key={h.key}>
            <HouseCard h={h} />
          </li>
        ))}
      </ul>
    </Shell>
  );
}
