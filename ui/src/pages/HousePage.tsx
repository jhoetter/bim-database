import { Link, useParams } from 'react-router';
import { fetchHouse, useResource } from '../api/client';

export function HousePage() {
  const { key = '' } = useParams();
  const { data: h, error, loading } = useResource(() => fetchHouse(key), [key]);

  if (loading) return <Status text="Lade…" />;
  if (error) return <Status text={`Fehler: ${error.message}`} tone="error" />;
  if (!h) return <Status text="Nicht gefunden." />;

  return (
    <div className="max-w-7xl mx-auto px-6 py-6">
      <nav className="text-sm mb-4">
        <Link to="/" className="text-accent hover:underline">
          ← Alle Häuser
        </Link>
      </nav>
      <header className="mb-6">
        <div className="text-sm text-muted">{h.manufacturer ?? '—'}</div>
        <h1 className="text-3xl font-semibold">{h.model}</h1>
        <div className="text-sm text-muted mt-1">
          {h.building_type ?? '—'} · {h.roof_type ?? '—'} ·{' '}
          {h.area_m2 != null ? `${h.area_m2} m²` : '—'}
        </div>
      </header>
      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted mb-3">
          Szenen ({h.images.length})
        </h2>
        <ul className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {h.images.map((img) => (
            <li
              key={img.file}
              className="border border-border rounded bg-white overflow-hidden"
            >
              <Link to={`/house/${h.key}/scene/${encodeURIComponent(img.file)}`}>
                <img
                  src={img.url}
                  alt={img.caption ?? img.file}
                  className="block w-full aspect-[4/3] object-cover bg-bg"
                  loading="lazy"
                />
                <div className="p-2 text-xs">
                  <div className="truncate font-medium">
                    {img.caption ?? img.file}
                  </div>
                  <div className="text-muted truncate">
                    {img.category}
                    {img.floor ? ` · ${img.floor}` : ''}
                    {img.view ? ` · ${img.view}` : ''}
                  </div>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </section>
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
