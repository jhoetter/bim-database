import { Link, useParams } from 'react-router';
import { fetchHouse, useResource } from '../api/client';
import { HouseGallery } from '../components/house/HouseGallery';
import { HouseSpecs } from '../components/house/HouseSpecs';
import { HouseImagesSection } from '../components/house/HouseImagesSection';
import {
  AnomalyPanel,
  DerivedFactsPanel,
  ModelabilityPanel,
  SourcePdfsPanel,
} from '../components/house/HousePanels';

export function HousePage() {
  const { key = '' } = useParams();
  const { data: h, error, loading } = useResource(() => fetchHouse(key), [key]);

  if (loading) return <Status text="Lade…" />;
  if (error) return <Status text={`Fehler: ${error.message}`} tone="error" />;
  if (!h) return <Status text="Nicht gefunden." />;

  return (
    <div className="max-w-5xl mx-auto px-6 py-6">
      <nav className="text-sm mb-4">
        <Link to="/" className="text-accent hover:underline">
          ← Alle Häuser
        </Link>
      </nav>

      <div className="bg-white border border-border rounded-xl overflow-hidden">
        <header className="px-5 py-4 border-b border-border">
          <div className="text-xs text-muted mb-px">
            {h.manufacturer ? `${h.manufacturer} · ${h.key}` : h.key}
          </div>
          <h1 className="text-[1.2rem] font-semibold">{h.model}</h1>
        </header>

        <HouseGallery h={h} />

        <div className="px-5 py-4">
          <HouseSpecs h={h} />

          <div className="flex gap-2 mt-4 flex-wrap">
            {h.source_url && (
              <a
                href={h.source_url}
                target="_blank"
                rel="noreferrer"
                className="px-4 py-1.5 rounded-md text-[0.8125rem] bg-white text-zinc-900 border border-border hover:opacity-90"
              >
                Quelle ↗
              </a>
            )}
            {h.pdf_url && (
              <a
                href={h.pdf_url}
                target="_blank"
                rel="noreferrer"
                className="px-4 py-1.5 rounded-md text-[0.8125rem] bg-accent text-white hover:opacity-90"
              >
                PDF öffnen
              </a>
            )}
          </div>

          <HouseImagesSection h={h} />
          <AnomalyPanel flags={h.anomaly_flags ?? []} />
          <DerivedFactsPanel derived={h.derived_facts} />
          <ModelabilityPanel h={h} />
          <SourcePdfsPanel h={h} />
        </div>
      </div>
    </div>
  );
}

function Status({ text, tone = 'normal' }: { text: string; tone?: 'normal' | 'error' }) {
  return (
    <div
      className={
        'max-w-5xl mx-auto px-6 py-12 text-sm ' +
        (tone === 'error' ? 'text-red-700' : 'text-muted')
      }
    >
      {text}
    </div>
  );
}
