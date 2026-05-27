import { useState } from 'react';
import { useNavigate, useParams, Link } from 'react-router';
import { fetchHouse, setDatasetStarred, useResource } from '../api/client';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import { HouseSpecs } from '../components/house/HouseSpecs';
import { HouseImagesSection } from '../components/house/HouseImagesSection';
import { SceneDetailPanel } from '../components/house/SceneDetailPanel';
import {
  AnomalyPanel,
  DerivedFactsPanel,
  ModelabilityPanel,
  SourcePdfsPanel,
} from '../components/house/HousePanels';

export function HousePage() {
  const { key = '', file } = useParams();
  const navigate = useNavigate();
  const { data: h, error, loading } = useResource(() => fetchHouse(key), [key]);

  if (loading) return <ShellShim breadcrumb={key}>Lade…</ShellShim>;
  if (error) return <ShellShim breadcrumb={key} tone="error">{`Fehler: ${error.message}`}</ShellShim>;
  if (!h) return <ShellShim breadcrumb={key}>Nicht gefunden.</ShellShim>;

  const decodedFile = file ? decodeURIComponent(file) : null;
  const scene = decodedFile ? h.images.find((x) => x.file === decodedFile) ?? null : null;

  return (
    <Shell
      breadcrumb={
        <Breadcrumb
          items={[
            { label: 'Alle Häuser', to: '/' },
            { label: h.model },
            ...(scene ? [{ label: scene.file }] : []),
          ]}
        />
      }
      leftSidebar={
        <div className="px-4 py-4 space-y-5 min-w-0">
          <header className="min-w-0">
            <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">
              {h.manufacturer ? h.manufacturer : h.key}
              {h.manufacturer && (
                <span className="ml-1.5 text-zinc-400 font-normal">{h.key}</span>
              )}
            </div>
            <h1 className="text-[1rem] font-semibold leading-snug mt-0.5 break-words">
              {h.model}
            </h1>
            {(h.source_url || h.pdf_url) && (
              <div className="flex gap-1.5 mt-2.5">
                {h.source_url && (
                  <a
                    href={h.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="px-2.5 py-1 rounded-md text-[0.7rem] bg-white text-zinc-900 border border-border hover:border-zinc-400"
                  >
                    Quelle ↗
                  </a>
                )}
                {h.pdf_url && (
                  <a
                    href={h.pdf_url}
                    target="_blank"
                    rel="noreferrer"
                    className="px-2.5 py-1 rounded-md text-[0.7rem] bg-accent text-white hover:opacity-90"
                  >
                    PDF
                  </a>
                )}
              </div>
            )}
          </header>

          <DatasetStarPanel houseKey={h.key} initialStarred={h.dataset_starred ?? false} />
          <HouseSpecs h={h} />
          <AnomalyPanel flags={h.anomaly_flags ?? []} />
          <DerivedFactsPanel derived={h.derived_facts} />
          <ModelabilityPanel h={h} />
          <SourcePdfsPanel h={h} />
        </div>
      }
      rightRail={scene ? <SceneDetailPanel img={scene} /> : null}
      rightRailLabel={scene ? 'Szene' : undefined}
      onCloseRightRail={() => navigate(`/house/${h.key}`)}
    >
      <div className="px-6 py-5">
        <HouseImagesSection h={h} />
        {scene == null && decodedFile && (
          <div className="mt-4 px-3 py-2 bg-amber-50 border border-amber-200 rounded-md text-[0.8125rem] text-amber-900">
            Szene <code className="font-mono">{decodedFile}</code> nicht gefunden in {h.key}.
          </div>
        )}
      </div>
    </Shell>
  );
}

// "Add to dataset" star button. When enabled, this house's real architectural
// drawings (elevation/floorplan/section/detail JPGs) are copied into
// data/dataset/<key>/ and become part of the supervised-learning corpus next
// to the AI-generated drawings. Toggle persists to data/houses/<key>/<key>.json
// as `dataset_starred: true`.
function DatasetStarPanel({
  houseKey,
  initialStarred,
}: {
  houseKey: string;
  initialStarred: boolean;
}) {
  const [starred, setStarred] = useState(initialStarred);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [materialized, setMaterialized] = useState<string | null>(null);

  const onClick = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await setDatasetStarred(houseKey, !starred);
      setStarred(result.dataset_starred);
      setMaterialized(result.materialized);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="border border-border rounded-md p-3 bg-white">
      <div className="flex items-center gap-2.5">
        <button
          onClick={onClick}
          disabled={busy}
          className={
            'flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[0.78rem] font-medium border transition ' +
            (starred
              ? 'bg-amber-50 border-amber-300 text-amber-900 hover:bg-amber-100'
              : 'bg-white border-border text-zinc-900 hover:border-zinc-400') +
            (busy ? ' opacity-60 cursor-wait' : '')
          }
          title={
            starred
              ? 'Aus dem Trainings-Datensatz entfernen (Stern abwählen). Bereits kopierte Bilder + Labels bleiben erhalten.'
              : 'Reale Pläne dieses Hauses in den Trainings-Datensatz übernehmen'
          }
        >
          <StarIcon filled={starred} />
          {starred ? 'Im Datensatz' : 'In Datensatz aufnehmen'}
        </button>
        {starred && (
          <Link to={`/dataset/${houseKey}`} className="text-[0.72rem] text-accent hover:underline">
            Anzeigen →
          </Link>
        )}
      </div>
      <p className="mt-2 text-[0.7rem] text-muted leading-snug">
        Markiert dieses Haus als <code className="font-mono">dataset_starred</code> und kopiert dessen reale Zeichnungen (Ansicht/Grundriss/Schnitt/Detail) nach <code className="font-mono">data/dataset/</code>.
      </p>
      {busy && (
        <p className="mt-1.5 text-[0.7rem] text-muted">Übernehme reale Pläne…</p>
      )}
      {materialized && !busy && (
        <p className="mt-1.5 text-[0.7rem] text-green-700">✓ {materialized}</p>
      )}
      {error && (
        <p className="mt-1.5 text-[0.7rem] text-red-700">Fehler: {error}</p>
      )}
    </section>
  );
}

function StarIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M8 1.5l1.8 4.5h3.7l-3.2 2.4 1.2 4.6L8 10.8 4.5 13l1.2-4.6L2.5 6h3.7L8 1.5z" />
    </svg>
  );
}

function ShellShim({
  breadcrumb,
  children,
  tone = 'normal',
}: {
  breadcrumb: string;
  children: React.ReactNode;
  tone?: 'normal' | 'error';
}) {
  return (
    <Shell
      breadcrumb={<Breadcrumb items={[{ label: 'Alle Häuser', to: '/' }, { label: breadcrumb }]} />}
      leftSidebar={<div className="p-4 text-[0.8125rem] text-muted">Lade…</div>}
    >
      <div
        className={
          'max-w-5xl mx-auto px-6 py-12 text-sm ' +
          (tone === 'error' ? 'text-red-700' : 'text-muted')
        }
      >
        {children}
      </div>
    </Shell>
  );
}
