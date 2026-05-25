import { Link } from 'react-router';
import type { SceneImage } from '../../api/types';
import { useOntology, ontoLabel } from '../../api/ontology';

export function ImageTile({ houseKey, img }: { houseKey: string; img: SceneImage }) {
  const onto = useOntology();
  const cap =
    img.caption ||
    [
      img.floor && ontoLabel(onto, 'levels', img.floor),
      img.view && ontoLabel(onto, 'image_views', img.view),
    ]
      .filter(Boolean)
      .join(' · ');
  const nFacts = Object.keys(img.facts ?? {}).length;
  const isOriginal = !img.source_ref;
  const srcFile = img.source_ref?.file ?? null;

  const objectFit =
    img.medium === 'scan' || img.medium === 'drawing' ? 'object-contain bg-white' : 'object-cover';

  return (
    <Link
      to={`/house/${houseKey}/scene/${encodeURIComponent(img.file)}`}
      className="relative block group"
      title="Klick öffnet Szenen-Detail"
    >
      <img
        src={img.url}
        alt={cap || img.file}
        loading="lazy"
        className={`h-32 rounded border border-border bg-white block ${objectFit} ${
          nFacts > 0 ? 'ring-2 ring-green-300 ring-inset' : ''
        }`}
      />
      {srcFile && (
        <span
          className="absolute top-1 left-1 bg-zinc-800/80 text-white text-[0.575rem] font-medium px-1.5 py-0.5 rounded max-w-[calc(100%-3rem)] overflow-hidden whitespace-nowrap text-ellipsis"
          title={`aus ${srcFile}${img.source_ref?.page ? ' p.' + img.source_ref.page : ''}`}
        >
          ←&nbsp;{srcFile}
        </span>
      )}
      {nFacts > 0 && (
        <span
          className="absolute top-1 right-1 bg-green-700 text-white text-[0.6rem] font-bold px-1.5 py-px rounded-full shadow"
          title={`${nFacts} Fakten extrahiert`}
        >
          {nFacts}
        </span>
      )}
      {cap && (
        <span
          className={`absolute bottom-1 left-1 right-1 ${
            isOriginal ? 'bg-blue-600/75' : 'bg-black/55'
          } text-white text-[0.6rem] px-1.5 py-px rounded whitespace-nowrap overflow-hidden text-ellipsis`}
        >
          {cap}
        </span>
      )}
    </Link>
  );
}
