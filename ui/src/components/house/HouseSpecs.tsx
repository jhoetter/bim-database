import type { House } from '../../api/types';
import { useOntology, ontoLabel } from '../../api/ontology';
import { fmt, fmtPrice } from '../../lib/format';

// Spec table — only rows with values render.
export function HouseSpecs({ h }: { h: House }) {
  const onto = useOntology();
  const dq = h.data_quality;
  const rows: [string, string | number | null | undefined][] = [
    ['Hersteller', h.manufacturer],
    ['Modell', h.model],
    ['Quelle', h.source && ontoLabel(onto, 'sources', h.source)],
    ['Gebäudetyp', h.building_type],
    ['Bauweise', h.construction],
    ['Dachform', h.roof_type],
    ['Stil', h.style && ontoLabel(onto, 'styles', h.style)],
    ['Baujahr', h.year_built],
    ['Keller', h.has_basement == null ? null : h.has_basement ? 'ja' : 'nein'],
    ['Geschosse', h.levels?.join(' → ') ?? h.floors],
    ['Wohnfläche', h.area_m2 != null ? fmt(h.area_m2, ' m²') : null],
    ['Zimmer', h.rooms],
    ['Energiestandard', h.energy_standard],
    ['Preis', fmtPrice(h)],
    ['Lage / Standort', h.site],
    ['Charakter', h.character],
    ['Agent notes', h.agent_notes],
    [
      'Rekonstruierbarkeit',
      h.reconstructability_tier &&
        ontoLabel(onto, 'reconstructability_tiers', h.reconstructability_tier),
    ],
    ['Grundriss-Qualität', dq && ontoLabel(onto, 'floorplan_grades', dq.floorplan_grade)],
    [
      'Außenansicht-Abdeckung',
      dq && ontoLabel(onto, 'exterior_coverages', dq.exterior_coverage),
    ],
    ['Ansichten', dq && ontoLabel(onto, 'elevation_sets', dq.elevation_set)],
    ['Schnitt', dq && ontoLabel(onto, 'section_drawings', dq.section_drawing)],
    [
      'Bau-Spezifikation',
      dq && ontoLabel(onto, 'construction_specs_grades', dq.construction_specs),
    ],
  ];
  const filled = rows.filter(([, v]) => v != null && v !== '');

  return (
    <table className="w-full border-collapse text-[0.85rem]">
      <tbody>
        {filled.map(([k, v]) => (
          <tr key={k} className="border-b border-border last:border-b-0">
            <td className="py-1.5 text-muted w-1/3 align-top">{k}</td>
            <td className="py-1.5 font-medium align-top">{String(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
