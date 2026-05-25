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
        <div className="p-4 space-y-4">
          <header>
            <div className="text-[0.7rem] uppercase tracking-wider text-muted">
              {h.manufacturer ? `${h.manufacturer} · ${h.key}` : h.key}
            </div>
            <h1 className="text-[1.05rem] font-semibold leading-tight mt-0.5">{h.model}</h1>
          </header>

          <HouseSpecs h={h} />

          <div className="flex gap-2 flex-wrap">
            {h.source_url && (
              <a
                href={h.source_url}
                target="_blank"
                rel="noreferrer"
                className="px-3 py-1 rounded-md text-[0.75rem] bg-white text-zinc-900 border border-border hover:opacity-90"
              >
                Quelle ↗
              </a>
            )}
            {h.pdf_url && (
              <a
                href={h.pdf_url}
                target="_blank"
                rel="noreferrer"
                className="px-3 py-1 rounded-md text-[0.75rem] bg-accent text-white hover:opacity-90"
              >
                PDF
              </a>
            )}
          </div>

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
