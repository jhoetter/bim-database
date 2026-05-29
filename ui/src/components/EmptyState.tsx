import type { ReactNode } from 'react';
import { Link } from 'react-router';

// U14 — one shared empty-state. Used wherever a page or panel has
// nothing to show. Three slots: a heading line, a short body, and an
// optional CTA (Link OR button).

export interface EmptyStateProps {
  /** Main message (e.g. "Noch keine Szenen extrahiert."). */
  title: string;
  /** Optional sub-line explaining what to do next. */
  body?: ReactNode;
  /** Optional primary action: either a router href or a callback. */
  cta?: { label: string; to?: string; onClick?: () => void };
  /** Visual size — default is compact ("inline"); roomy variant for
   *  full-page empties like the dataset overview. */
  size?: 'inline' | 'page';
}

export function EmptyState({ title, body, cta, size = 'inline' }: EmptyStateProps) {
  const isPage = size === 'page';
  return (
    <div
      className={`flex flex-col items-center justify-center text-center ${
        isPage ? 'gap-3 py-16 px-6' : 'gap-1.5 py-4 px-3'
      }`}
    >
      <p className={`${isPage ? 'text-[0.95rem]' : 'text-[0.78rem]'} text-zinc-700 font-medium`}>
        {title}
      </p>
      {body && (
        <p className={`${isPage ? 'text-[0.8rem]' : 'text-[0.72rem]'} text-muted leading-snug max-w-md`}>
          {body}
        </p>
      )}
      {cta && (cta.to
        ? (
          <Link
            to={cta.to}
            className={`${isPage ? 'text-[0.85rem] px-3 py-1.5' : 'text-[0.75rem] px-2.5 py-1'} mt-1 rounded-md bg-accent text-white font-medium hover:opacity-90`}
          >
            {cta.label}
          </Link>
        ) : (
          <button
            type="button"
            onClick={cta.onClick}
            className={`${isPage ? 'text-[0.85rem] px-3 py-1.5' : 'text-[0.75rem] px-2.5 py-1'} mt-1 rounded-md bg-accent text-white font-medium hover:opacity-90`}
          >
            {cta.label}
          </button>
        )
      )}
    </div>
  );
}
