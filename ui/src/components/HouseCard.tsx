import { Link } from 'react-router';
import type { House } from '../api/types';
import { useOntology, ontoLabel } from '../api/ontology';
import { fmt, fmtPrice, pickThumbnail } from '../lib/format';
import { Badge, constructionTone } from './Badge';

function modelableTitle(h: House): string {
  if (h.modelable_in_bim_ai === true) return 'bim-ai can model this house today';
  if (h.modelable_in_bim_ai === false)
    return 'Blocked: ' + (h.blocking_open ?? []).map((b) => b.ref).join(', ');
  return 'Unknown — refresh issue-state cache (`make refresh-issue-state`)';
}

export function HouseCard({ h }: { h: House }) {
  const onto = useOntology();
  const thumb = pickThumbnail(h);
  const specs = [
    h.area_m2 != null ? fmt(h.area_m2, ' m²') : null,
    h.rooms != null ? `${h.rooms} Zi.` : null,
    h.floors != null ? `${h.floors} Gesch.` : h.levels ? `${h.levels.length} Gesch.` : null,
    h.year_built != null ? `Bj. ${h.year_built}` : null,
  ].filter(Boolean) as string[];
  const price = fmtPrice(h);
  const m = h.modelable_in_bim_ai;
  const showModelBadge = h.assessed === true;
  const modelTone = m === true ? 'ok' : m === false ? 'blocked' : 'unknown';
  const modelLabel = m === true ? 'BIM-AI ✓' : m === false ? 'BIM-AI ✗' : 'BIM-AI ?';

  const tierN = h.reconstructability_tier
    ? parseInt(h.reconstructability_tier.replace(/^T/, ''), 10)
    : null;

  // Drawings/scans need contain — they're portrait pages, not photos.
  const imgFit =
    thumb && (thumb.medium === 'scan' || thumb.medium === 'drawing')
      ? 'object-contain bg-[#fafaf5] p-1.5'
      : 'object-cover';

  return (
    <Link
      to={`/house/${h.key}`}
      className="relative block bg-white border border-border rounded-lg overflow-hidden transition hover:shadow-md hover:-translate-y-px"
    >
      {tierN != null && (
        <span className="absolute top-2 left-2 z-10 shadow-sm">
          <Badge
            tone={`tier-${tierN}` as `tier-${0 | 1 | 2 | 3 | 4}`}
            title={ontoLabel(onto, 'reconstructability_tiers', h.reconstructability_tier)}
            className="!font-bold"
          >
            T{tierN}
          </Badge>
        </span>
      )}
      {showModelBadge && (
        <span className="absolute top-2 right-2 z-10 shadow-sm">
          <Badge tone={modelTone} title={modelableTitle(h)} className="!font-bold !tracking-wider">
            {modelLabel}
          </Badge>
        </span>
      )}
      {thumb ? (
        <img
          src={thumb.url}
          loading="lazy"
          alt={thumb.caption ?? h.model}
          className={`block w-full aspect-[4/3] ${imgFit}`}
        />
      ) : (
        <div className="w-full aspect-[4/3] bg-zinc-100 flex items-center justify-center text-xs text-muted">
          Kein Bild
        </div>
      )}
      <div className="px-3 py-2.5">
        <div className="text-[0.6875rem] text-muted mb-px">{h.manufacturer ?? '–'}</div>
        <div className="font-semibold text-[0.9375rem] mb-1.5">{h.model}</div>
        {specs.length > 0 && (
          <div className="flex gap-3 flex-wrap text-xs text-muted mb-1.5">
            {specs.map((s) => (
              <span key={s}>{s}</span>
            ))}
          </div>
        )}
        {price && <div className="font-semibold text-[0.9rem]">{price}</div>}
        <div className="flex gap-1.5 flex-wrap mt-2">
          {h.building_type && <Badge tone="type">{h.building_type}</Badge>}
          {h.construction && (
            <Badge tone={constructionTone(h.construction)}>{h.construction}</Badge>
          )}
          {h.roof_type && <Badge tone="roof">{h.roof_type}</Badge>}
          {h.style && <Badge tone="style">{ontoLabel(onto, 'styles', h.style)}</Badge>}
        </div>
      </div>
    </Link>
  );
}
