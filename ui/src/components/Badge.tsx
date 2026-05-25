import type { ReactNode } from 'react';

type Tone =
  | 'type' | 'fertig' | 'massiv' | 'block' | 'roof' | 'style'
  | 'ok' | 'blocked' | 'unknown'
  | 'tier-0' | 'tier-1' | 'tier-2' | 'tier-3' | 'tier-4';

const TONE: Record<Tone, string> = {
  type:    'bg-zinc-100 text-zinc-700',
  fertig:  'bg-blue-100 text-blue-700',
  massiv:  'bg-green-100 text-green-700',
  block:   'bg-amber-100 text-amber-800',
  roof:    'bg-violet-100 text-violet-700',
  style:   'bg-rose-100 text-rose-700',
  ok:      'bg-green-100 text-green-700',
  blocked: 'bg-red-100 text-red-700',
  unknown: 'bg-zinc-100 text-zinc-500',
  'tier-0': 'bg-zinc-100 text-zinc-500',
  'tier-1': 'bg-amber-100 text-amber-800',
  'tier-2': 'bg-blue-100 text-blue-700',
  'tier-3': 'bg-green-100 text-green-700',
  'tier-4': 'bg-violet-200 text-violet-800',
};

export function Badge({
  tone = 'type',
  title,
  children,
  className = '',
}: {
  tone?: Tone;
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={`text-[0.625rem] px-1.5 py-0.5 rounded-full font-medium ${TONE[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

// Construction-type → tone mapping (matches legacy bClass)
export function constructionTone(c: string | null | undefined): Tone {
  if (!c) return 'type';
  const l = c.toLowerCase();
  if (l.includes('fertig')) return 'fertig';
  if (l.includes('massiv')) return 'massiv';
  if (l.includes('block')) return 'block';
  return 'type';
}
