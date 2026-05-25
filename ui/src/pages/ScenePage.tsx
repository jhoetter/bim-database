import { Link, useParams } from 'react-router';
import { fetchHouse, useResource } from '../api/client';

export function ScenePage() {
  const { key = '', file = '' } = useParams();
  const decodedFile = decodeURIComponent(file);
  const { data: h, error, loading } = useResource(() => fetchHouse(key), [key]);

  if (loading) return <Status text="Lade…" />;
  if (error) return <Status text={`Fehler: ${error.message}`} tone="error" />;
  if (!h) return <Status text="Haus nicht gefunden." />;

  const img = h.images.find((x) => x.file === decodedFile);
  if (!img) return <Status text={`Szene "${decodedFile}" nicht in ${h.key}.`} />;

  return (
    <div className="max-w-7xl mx-auto px-6 py-6">
      <nav className="text-sm mb-4">
        <Link to="/" className="text-accent hover:underline">
          Alle Häuser
        </Link>
        <span className="text-muted"> / </span>
        <Link to={`/house/${h.key}`} className="text-accent hover:underline">
          {h.model}
        </Link>
      </nav>
      <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6">
        <figure className="bg-white border border-border rounded overflow-hidden">
          <img src={img.url} alt={img.caption ?? img.file} className="block w-full" />
        </figure>
        <aside className="text-sm">
          <h1 className="text-xl font-semibold">{img.caption ?? img.file}</h1>
          <dl className="mt-4 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted">Kategorie</dt>
            <dd>{img.category}</dd>
            <dt className="text-muted">Medium</dt>
            <dd>{img.medium}</dd>
            {img.floor && (
              <>
                <dt className="text-muted">Geschoss</dt>
                <dd>{img.floor}</dd>
              </>
            )}
            {img.view && (
              <>
                <dt className="text-muted">Ansicht</dt>
                <dd>{img.view}</dd>
              </>
            )}
            {img.source_ref && (
              <>
                <dt className="text-muted">Quelle</dt>
                <dd>
                  {img.source_ref.file}
                  {img.source_ref.page != null ? ` · Seite ${img.source_ref.page}` : ''}
                </dd>
              </>
            )}
          </dl>
        </aside>
      </div>
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
