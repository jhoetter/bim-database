import { describe, expect, it } from 'vitest';
import cases from '../../../tests/fixtures/render_geometry/wall_band_cases.json';
import { wallBandPoints } from './renderGeometry';

describe('wallBandPoints', () => {
  for (const c of cases) {
    it(`matches shared fixture: ${c.name}`, () => {
      const actual = wallBandPoints(
        c.start as [number, number],
        c.end as [number, number],
        c.thickness_mm,
        c.px_per_mm ?? 0.05,
      );
      expect(actual).not.toBeNull();
      const rounded = actual!.map((p) => p.map((v) => Number(v.toFixed(3))));
      expect(rounded).toEqual(c.expected);
    });
  }
});
