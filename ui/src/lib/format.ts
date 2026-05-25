import type { House, SceneImage } from '../api/types';

export const fmt = (n: number | null | undefined, suf = ''): string =>
  n != null ? n.toLocaleString('de-DE') + suf : '–';

export function fmtPrice(h: House): string | null {
  if (h.price_on_request) return 'Preis auf Anfrage';
  return h.price_eur != null ? '€ ' + h.price_eur.toLocaleString('de-DE') : null;
}

// Pretty-print a fact's scalar value. Recurses for arrays; objects fall
// through to renderFactValue (which builds a table).
export function formatFactValue(v: unknown, unit?: string | null): string {
  if (v == null) return '–';
  if (Array.isArray(v)) {
    return v.map((x) => formatFactValue(x, '')).join(' × ') + (unit ? ' ' + unit : '');
  }
  if (typeof v === 'number') {
    // mm → m as soon as the magnitude justifies it; rooms and walls stay readable.
    if (unit === 'mm' && v >= 1000) {
      return (v / 1000).toLocaleString('de-DE', { maximumFractionDigits: 3 }) + ' m';
    }
    return v.toLocaleString('de-DE') + (unit ? ' ' + unit : '');
  }
  if (typeof v === 'boolean') return v ? 'ja' : 'nein';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v) + (unit ? ' ' + unit : '');
}

// Card thumbnail: prefer an exterior, then drawings if that's all we have.
export function pickThumbnail(h: House): SceneImage | null {
  const order = ['exterior', 'elevation', 'perspective', 'interior', 'detail', 'floorplan', 'section'];
  const rank = (c: string) => {
    const i = order.indexOf(c);
    return i < 0 ? 999 : i;
  };
  const sorted = [...h.images].sort((a, b) => rank(a.category) - rank(b.category));
  return sorted[0] ?? null;
}
