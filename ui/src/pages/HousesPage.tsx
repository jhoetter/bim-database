import { Link } from 'react-router';
import { fetchHouses, useResource } from '../api/client';

export function HousesPage() {
  const { data, error, loading } = useResource(() => fetchHouses({}), []);

  if (loading) return <Status text="Lade Häuser…" />;
  if (error) return <Status text={`Fehler: ${error.message}`} tone="error" />;
  if (!data || data.length === 0) return <Status text="Keine Häuser gefunden." />;

  return (
    <div className="max-w-7xl mx-auto px-6 py-6">
      <h1 className="text-2xl font-semibold mb-4">{data.length} Häuser</h1>
      <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {data.map((h) => (
          <li
            key={h.key}
            className="border border-border rounded-lg bg-white p-4 hover:shadow-sm transition"
          >
            <Link to={`/house/${h.key}`} className="block">
              <div className="text-sm text-muted">{h.manufacturer ?? '—'}</div>
              <div className="font-medium">{h.model}</div>
              <div className="text-xs text-muted mt-1">
                {h.building_type ?? '—'} · {h.roof_type ?? '—'} ·{' '}
                {h.area_m2 != null ? `${h.area_m2} m²` : '—'}
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Status({ text, tone = 'normal' }: { text: string; tone?: 'normal' | 'error' }) {
  return (
    <div
      className={
        'max-w-7xl mx-auto px-6 py-12 text-sm ' +
        (tone === 'error' ? 'text-red-700' : 'text-muted')
      }
    >
      {text}
    </div>
  );
}
