import { useNavigate, useParams } from 'react-router';
import { fetchHouse, useResource } from '../api/client';
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
