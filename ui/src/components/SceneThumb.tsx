import type { ReactNode } from 'react';
import { Link } from 'react-router';

// Compact chip showing one scene: small thumbnail + short label, with
// optional active-state highlight, a labeled checkmark, and slots for
// trailing decorations and an overlay button (e.g., delete X). Shared
// between the ExtractPage SceneStrip and the AnnotatePage topbar scene
// navigator so the user sees the same visual language in both places.

export interface SceneThumbProps {
  url?: string | null;
  shortLabel: string;
  title?: string;
  active?: boolean;
  labeled?: boolean;
  onClick?: () => void;
  to?: string;
  /** Compact (annotate topbar) vs roomy (extract strip). */
  size?: 'sm' | 'md';
  /** Slot rendered after the label — e.g., readiness dot, count badge. */
  trailing?: ReactNode;
  /** Slot rendered on top-right of the thumbnail — e.g., delete X. */
  overlay?: ReactNode;
}

const THUMB_SIZE = { sm: 'w-7 h-7', md: 'w-10 h-10' };
const LABEL_TEXT = { sm: 'text-[0.65rem]', md: 'text-[0.72rem]' };
const PADDING = { sm: 'pl-0.5 pr-1.5 py-0.5', md: 'pl-1 pr-2 py-1' };

export function SceneThumb({
  url, shortLabel, title, active, labeled, onClick, to,
  size = 'md', trailing, overlay,
}: SceneThumbProps) {
  const inner = (
    <>
      <div className={`relative ${THUMB_SIZE[size]} shrink-0 rounded overflow-hidden bg-zinc-100 border ${active ? 'border-accent' : 'border-zinc-200'}`}>
        {url ? (
          <img
            src={url}
            alt=""
            loading="lazy"
            className="absolute inset-0 w-full h-full object-cover"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-zinc-400 text-[0.55rem] font-semibold">
            ?
          </div>
        )}
        {labeled && (
          <span
            className="absolute bottom-0 right-0 bg-emerald-600 text-white text-[0.5rem] leading-none px-0.5 py-0.5 rounded-tl"
            aria-label="annotiert"
            title="annotiert"
          >
            ✓
          </span>
        )}
      </div>
      <span className={`${LABEL_TEXT[size]} font-medium whitespace-nowrap`}>
        {shortLabel}
      </span>
      {trailing}
    </>
  );

  const cls = `inline-flex items-center gap-1.5 rounded-md ${PADDING[size]} shrink-0 transition border ${
    active
      ? 'bg-accent/10 border-accent text-zinc-900'
      : 'bg-white border-zinc-200 hover:border-zinc-400 text-zinc-700'
  }`;

  if (to) {
    return (
      <span className="relative inline-block shrink-0">
        <Link to={to} className={cls} title={title}>
          {inner}
        </Link>
        {overlay}
      </span>
    );
  }
  return (
    <span className="relative inline-block shrink-0">
      <button type="button" onClick={onClick} className={cls} title={title}>
        {inner}
      </button>
      {overlay}
    </span>
  );
}
