import type { HouseFacts } from './house_facts';
import type { Point } from '../api/types';

export const FALLBACK_WALL_PX_PER_MM = 0.05;

export function effectiveWallPxPerMm(
  facts: HouseFacts,
  sceneFile: string,
): number {
  const px = facts.calibration_per_scene[sceneFile]?.px_per_mm;
  return typeof px === 'number' && Number.isFinite(px) && px > 0 ? px : FALLBACK_WALL_PX_PER_MM;
}

export function wallBandPoints(
  start: Point,
  end: Point,
  thicknessMm: number,
  pxPerMm: number,
): [Point, Point, Point, Point] | null {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const len = Math.hypot(dx, dy);
  if (len === 0) return null;
  const ux = dx / len;
  const uy = dy / len;
  const px = -uy;
  const py = ux;
  const half = (thicknessMm * pxPerMm) / 2;
  const s: Point = [start[0] - ux * half, start[1] - uy * half];
  const e: Point = [end[0] + ux * half, end[1] + uy * half];
  return [
    [s[0] + px * half, s[1] + py * half],
    [e[0] + px * half, e[1] + py * half],
    [e[0] - px * half, e[1] - py * half],
    [s[0] - px * half, s[1] - py * half],
  ];
}

export function wallBandPath(
  start: Point,
  end: Point,
  thicknessMm: number,
  pxPerMm: number,
): string {
  const pts = wallBandPoints(start, end, thicknessMm, pxPerMm);
  if (!pts) return '';
  const [a, b, c, d] = pts;
  return `M ${a[0]},${a[1]} L ${b[0]},${b[1]} L ${c[0]},${c[1]} L ${d[0]},${d[1]} Z`;
}
