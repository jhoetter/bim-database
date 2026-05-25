import { type ReactNode } from 'react';
import { formatFactValue } from '../../lib/format';

// Renders a fact value (number, string, array, or nested object) inline.
// Primitive + array values render as a single formatted string. Object values
// render as an expandable details block — each entry is rendered recursively,
// so room-keyed objects with sub-fields per room remain browsable instead of
// being dumped as JSON.

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return v != null && typeof v === 'object' && !Array.isArray(v);
}

export function FactValueRenderer({
  value,
  unit,
  inline = false,
  defaultOpen = false,
}: {
  value: unknown;
  unit?: string | null;
  inline?: boolean;
  defaultOpen?: boolean;
}): ReactNode {
  if (!isPlainObject(value)) {
    return (
      <span className="font-semibold tabular-nums text-zinc-900 break-words">
        {formatFactValue(value, unit)}
      </span>
    );
  }

  const entries = Object.entries(value);
  const n = entries.length;
  // If every entry is a primitive, render compactly without an accordion.
  const allPrimitive = entries.every(([, v]) => !isPlainObject(v));

  if (allPrimitive && inline) {
    return (
      <span className="text-[0.72rem] text-zinc-700 break-words">
        {entries.map(([k, v], i) => (
          <span key={k}>
            {i > 0 && <span className="text-zinc-400"> · </span>}
            <span className="text-muted">{k}</span>{' '}
            <span className="font-semibold tabular-nums">{formatFactValue(v, unit)}</span>
          </span>
        ))}
      </span>
    );
  }

  return (
    <details className="group/fv" open={defaultOpen}>
      <summary className="cursor-pointer text-[0.7rem] text-accent hover:underline list-none flex items-center gap-1 select-none">
        <span className="inline-block w-3 text-center transition-transform group-open/fv:rotate-90">›</span>
        {n} Einträge
      </summary>
      <dl className="mt-1.5 space-y-1.5 pl-3 border-l border-zinc-200">
        {entries.map(([k, v]) => (
          <div key={k} className="min-w-0">
            <dt className="font-mono text-[0.65rem] text-muted leading-tight mb-0.5 break-all">
              {k}
            </dt>
            <dd className="min-w-0 break-words text-[0.72rem] leading-snug">
              <FactValueRenderer value={v} unit={unit} inline />
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
