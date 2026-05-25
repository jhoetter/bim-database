import { useState } from 'react';
import type { House, SceneImage } from '../../api/types';
import { useOntology, ontoLabel } from '../../api/ontology';
import { ImageTile } from './ImageTile';

type ViewMode = 'category' | 'source';

export function HouseImagesSection({ h }: { h: House }) {
  const onto = useOntology();
  const [mode, setMode] = useState<ViewMode>('category');
  if (h.images.length === 0) return null;

  return (
    <section className="mt-5 border-t border-border pt-3.5">
      <div className="flex items-center gap-3 mb-2.5">
        <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">
          Bilder
        </h4>
        <div className="ml-auto inline-flex bg-zinc-100 rounded-md p-0.5">
          <SegBtn active={mode === 'category'} onClick={() => setMode('category')}>
            nach Kategorie
          </SegBtn>
          <SegBtn active={mode === 'source'} onClick={() => setMode('source')}>
            nach Quelle
          </SegBtn>
        </div>
      </div>
      {mode === 'category' ? (
        <ByCategory h={h} />
      ) : (
        <BySource h={h} />
      )}
    </section>
  );

  function SegBtn({
    children,
    active,
    onClick,
  }: {
    children: React.ReactNode;
    active: boolean;
    onClick: () => void;
  }) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`px-2.5 py-0.5 rounded text-[0.7rem] transition ${
          active
            ? 'bg-white text-zinc-900 font-semibold shadow-sm'
            : 'text-muted hover:text-zinc-900'
        }`}
      >
        {children}
      </button>
    );
  }

  function ByCategory({ h }: { h: House }) {
    const groups: Record<string, SceneImage[]> = {};
    for (const i of h.images) (groups[i.category] ??= []).push(i);
    const order = [
      'exterior', 'elevation', 'interior', 'perspective',
      'floorplan', 'section', 'roof_plan', 'detail', 'site_plan',
    ];
    const rank = (c: string) => {
      const i = order.indexOf(c);
      return i < 0 ? 999 : i;
    };
    const cats = Object.keys(groups).sort((a, b) => rank(a) - rank(b));
    const floorOrder = Object.keys(onto.levels ?? {});

    return (
      <div>
        {cats.map((cat) => {
          let list = groups[cat]!;
          if (cat === 'floorplan') {
            list = [...list].sort((a, b) => {
              const ai = a.floor ? floorOrder.indexOf(a.floor) : -1;
              const bi = b.floor ? floorOrder.indexOf(b.floor) : -1;
              return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi);
            });
          }
          return (
            <div key={cat}>
              <h5 className="text-xs text-zinc-900 mt-3 mb-1.5 font-semibold">
                {ontoLabel(onto, 'image_categories', cat)}{' '}
                <span className="text-muted font-normal">({list.length})</span>
              </h5>
              <div className="columns-[240px] gap-3">
                {list.map((i) => (
                  <ImageTile key={i.file} houseKey={h.key} img={i} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  function BySource({ h }: { h: House }) {
    const bySource = new Map<string, SceneImage[]>();
    const originals: SceneImage[] = [];
    for (const i of h.images) {
      const f = i.source_ref?.file;
      if (f) {
        if (!bySource.has(f)) bySource.set(f, []);
        bySource.get(f)!.push(i);
      } else {
        originals.push(i);
      }
    }

    return (
      <div>
        {originals.length > 0 && (
          <SourceCard
            icon="📷"
            name="Originalbilder"
            count={originals.length}
            unit={originals.length === 1 ? 'Bild' : 'Bilder'}
            tone="original"
          >
            <div className="flex gap-2 flex-wrap">
              {originals.map((i) => (
                <ImageTile key={i.file} houseKey={h.key} img={i} />
              ))}
            </div>
          </SourceCard>
        )}
        {[...bySource.entries()].map(([src, list]) => {
          const isPdf = src.toLowerCase().endsWith('.pdf');
          const pdfHref = isPdf ? `/static/${h.key}/${encodeURIComponent(src)}` : null;
          return (
            <SourceCard
              key={src}
              icon={isPdf ? '📄' : '🖼'}
              name={src}
              count={list.length}
              unit={list.length === 1 ? 'Szene' : 'Szenen'}
              pdfHref={pdfHref}
            >
              <div className="columns-[240px] gap-3">
                {list.map((i) => (
                  <ImageTile key={i.file} houseKey={h.key} img={i} />
                ))}
              </div>
            </SourceCard>
          );
        })}
      </div>
    );
  }

  function SourceCard({
    icon,
    name,
    count,
    unit,
    tone = 'normal',
    pdfHref,
    children,
  }: {
    icon: string;
    name: string;
    count: number;
    unit: string;
    tone?: 'normal' | 'original';
    pdfHref?: string | null;
    children: React.ReactNode;
  }) {
    return (
      <div
        className={`rounded-lg px-3.5 py-3 mb-3 border ${
          tone === 'original' ? 'bg-blue-50 border-blue-200' : 'bg-zinc-50 border-border'
        }`}
      >
        <div className="flex items-baseline gap-2 mb-2.5 text-[0.8125rem]">
          <span className="text-base">{icon}</span>
          <span className="font-semibold text-zinc-900 font-mono text-[0.78rem]">{name}</span>
          {pdfHref && (
            <a
              href={pdfHref}
              target="_blank"
              rel="noreferrer"
              className="text-[0.7rem] text-accent hover:underline"
            >
              Quelle öffnen ↗
            </a>
          )}
          <span className="text-muted text-[0.7rem] ml-auto">
            {count} {unit}
          </span>
        </div>
        {children}
      </div>
    );
  }
}
