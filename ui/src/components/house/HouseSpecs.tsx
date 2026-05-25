import type { House } from '../../api/types';
import { useOntology, ontoLabel } from '../../api/ontology';
import { fmt, fmtPrice } from '../../lib/format';

// Two-column dl for short specs, one-row dl for long text. Long values
// (Charakter, Agent notes, Lage) break to a stacked label-above-value layout
// so they wrap cleanly inside the sidebar.

export function HouseSpecs({ h }: { h: House }) {
  const onto = useOntology();
  const dq = h.data_quality;

  type Row = { key: string; value: string; long?: boolean };
  const rows: Row[] = [];

  const push = (k: string, v: string | number | null | undefined, long = false) => {
    if (v == null || v === '') return;
    rows.push({ key: k, value: String(v), long });
  };

  push('Hersteller', h.manufacturer);
  push('Quelle', h.source && ontoLabel(onto, 'sources', h.source));
  push('Gebäudetyp', h.building_type);
  push('Bauweise', h.construction);
  push('Dachform', h.roof_type);
  push('Stil', h.style && ontoLabel(onto, 'styles', h.style));
  push('Baujahr', h.year_built);
  push('Keller', h.has_basement == null ? null : h.has_basement ? 'ja' : 'nein');
  push('Geschosse', h.levels?.join(' → ') ?? h.floors);
  push('Wohnfläche', h.area_m2 != null ? fmt(h.area_m2, ' m²') : null);
  push('Zimmer', h.rooms);
  push('Energiestandard', h.energy_standard);
  push('Preis', fmtPrice(h));
  push(
    'Tier',
    h.reconstructability_tier &&
      ontoLabel(onto, 'reconstructability_tiers', h.reconstructability_tier),
  );
  if (dq) {
    push('Grundriss', ontoLabel(onto, 'floorplan_grades', dq.floorplan_grade));
    push('Außenansicht', ontoLabel(onto, 'exterior_coverages', dq.exterior_coverage));
    push('Ansichten', ontoLabel(onto, 'elevation_sets', dq.elevation_set));
    push('Schnitt', ontoLabel(onto, 'section_drawings', dq.section_drawing));
    push('Bau-Spec', ontoLabel(onto, 'construction_specs_grades', dq.construction_specs));
  }
  // Long-text rows go to the bottom and render stacked.
  push('Lage', h.site, true);
  push('Charakter', h.character, true);

  if (rows.length === 0) return null;

  const compact = rows.filter((r) => !r.long);
  const long = rows.filter((r) => r.long);

  return (
    <div className="text-[0.78rem]">
      {compact.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 mb-0">
          {compact.map((r) => (
            <div key={r.key} className="contents">
              <dt className="text-muted truncate">{r.key}</dt>
              <dd className="font-medium break-words min-w-0">{r.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {long.length > 0 && (
        <div className="mt-3 space-y-2">
          {long.map((r) => (
            <div key={r.key}>
              <div className="text-[0.65rem] uppercase tracking-wider text-muted font-semibold mb-0.5">
                {r.key}
              </div>
              <p className="text-[0.78rem] leading-relaxed break-words">{r.value}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
