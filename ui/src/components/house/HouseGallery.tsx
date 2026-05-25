import type { House } from '../../api/types';
import { useOntology, ontoLabel } from '../../api/ontology';

// Header gallery — top 2 hero images (exterior > elevation > perspective > interior).
export function HouseGallery({ h }: { h: House }) {
  const onto = useOntology();
  const order = ['exterior', 'elevation', 'perspective', 'interior'];
  const rank = (c: string) => {
    const i = order.indexOf(c);
    return i < 0 ? 999 : i;
  };
  const heroes = h.images
    .filter((i) => order.includes(i.category))
    .sort((a, b) => rank(a.category) - rank(b.category))
    .slice(0, 2);

  if (heroes.length === 0) {
    return (
      <div className="bg-zinc-100 flex items-center justify-center text-muted text-xs aspect-[16/7]">
        Kein Bild
      </div>
    );
  }

  const hasScan = heroes.some((i) => i.medium === 'scan' || i.medium === 'drawing');

  return (
    <div
      className={`grid gap-0.5 ${heroes.length === 2 ? 'grid-cols-2' : 'grid-cols-1'} ${
        hasScan ? 'bg-[#fafaf5]' : 'bg-black'
      }`}
    >
      {heroes.map((i) => {
        const fit =
          i.medium === 'scan' || i.medium === 'drawing'
            ? 'object-contain bg-white'
            : 'object-cover';
        return (
          <div key={i.file} className="relative">
            <img
              src={i.url}
              alt={i.caption ?? i.file}
              loading="lazy"
              className={`w-full block ${fit} ${heroes.length === 1 ? 'max-h-[380px]' : 'max-h-[260px]'}`}
            />
            <span className="absolute bottom-1.5 left-1.5 bg-black/55 text-white text-[0.65rem] px-1.5 py-0.5 rounded">
              {ontoLabel(onto, 'image_mediums', i.medium)}
              {i.view ? ' · ' + ontoLabel(onto, 'image_views', i.view) : ''}
            </span>
          </div>
        );
      })}
    </div>
  );
}
