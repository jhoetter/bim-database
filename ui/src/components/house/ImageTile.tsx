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
  const isCompassView = (v?: string | null) =>
    v != null && ['north', 'south', 'east', 'west'].includes(v);

  // Persistent orientation chip — floor for floorplans, compass/view for elevations.
  // Shown alongside the caption so it doesn't get buried.
  const orientationLabel =
    img.category === 'floorplan' && img.floor
      ? ontoLabel(onto, 'levels', img.floor) || img.floor
      : img.category === 'elevation' && img.view
      ? ontoLabel(onto, 'image_views', img.view) || img.view
      : null;
  const orientationTone =
    img.category === 'floorplan'
      ? 'bg-blue-600/85'
      : isCompassView(img.view)
      ? 'bg-amber-600/90'
      : 'bg-zinc-700/85';

  const objectFit =
    img.medium === 'scan' || img.medium === 'drawing' ? 'object-contain bg-white' : 'object-cover';

  return (
    <Link
      to={`/house/${houseKey}/scene/${encodeURIComponent(img.file)}`}
      className="relative block mb-3 break-inside-avoid rounded-lg overflow-hidden border border-border bg-white hover:shadow-md hover:border-zinc-300 transition group"
    >
      <img
        src={img.url}
        alt={cap || img.file}
        loading="lazy"
        className={`w-full h-auto block ${objectFit} ${
          nFacts > 0 ? 'ring-2 ring-green-300 ring-inset' : ''
        }`}
      />
      {srcFile && (
        <span
          className="absolute top-1.5 left-1.5 bg-zinc-800/80 text-white text-[0.625rem] font-medium px-1.5 py-0.5 rounded max-w-[calc(100%-3rem)] overflow-hidden whitespace-nowrap text-ellipsis"
          title={`aus ${srcFile}${img.source_ref?.page ? ' p.' + img.source_ref.page : ''}`}
        >
          ←&nbsp;{srcFile}
        </span>
      )}
      {orientationLabel && (
        <span
          className={`absolute ${
            srcFile ? 'top-8' : 'top-1.5'
          } left-1.5 ${orientationTone} text-white text-[0.65rem] font-semibold px-1.5 py-0.5 rounded shadow`}
          title={`${img.category === 'floorplan' ? 'Geschoss' : 'Ansicht'}: ${orientationLabel}`}
        >
          {orientationLabel}
        </span>
      )}
      {nFacts > 0 && (
        <span
          className="absolute top-1.5 right-1.5 bg-green-700 text-white text-[0.65rem] font-bold px-1.5 py-px rounded-full shadow"
          title={`${nFacts} Fakten extrahiert`}
        >
          {nFacts}
        </span>
      )}
      {cap && (
        <span
          className={`absolute bottom-1.5 left-1.5 right-1.5 ${
            isOriginal ? 'bg-blue-600/80' : 'bg-black/65'
          } text-white text-[0.7rem] px-2 py-1 rounded leading-snug line-clamp-2`}
        >
          {cap}
        </span>
      )}
    </Link>
  );
}
