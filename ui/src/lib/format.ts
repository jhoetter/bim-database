// R0 — catalog-specific formatters (fmtPrice, pickThumbnail) deleted with
// the houses path. Only the generic numeric / fact-value formatters
// survive; they're shared across the dataset side.

export const fmt = (n: number | null | undefined, suf = ''): string =>
  n != null ? n.toLocaleString('de-DE') + suf : '–';

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
