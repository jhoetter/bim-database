import type { ReactNode } from 'react';
import { Link } from 'react-router';
import {
  chipKindLabel, chipReadinessColor, chipReadinessTitle,
  type SceneChipData,
} from './sceneChip';

// U12 — single chip renderer shared by SceneStrip (Extract + Annotate),
// HouseCard scene strip, ExportPage scene table, and the U9 popover
// header. Anything that renders a scene goes through this.

export interface SceneChipProps {
  scene: SceneChipData;
  /** Strong "selected / focused" highlight (the active scene the user is
   *  acting on). */
  active?: boolean;
  /** Softer "this is contextually relevant" highlight (e.g. its source
   *  page matches the current PDF page). */
  here?: boolean;
  onClick?: () => void;
  to?: string;
  /** Slot rendered after the label — page reference, delete button etc. */
  trailing?: ReactNode;
  /** Size variant. md = 40 px thumb (default), sm = 28 px thumb. */
  size?: 'sm' | 'md';
  /** Show the count badge + readiness dot. Default true. */
  showIndicators?: boolean;
  /** Override the "click is a navigation link" decoration — pass the
   *  same JSX the regular button would render but inside a Link with
   *  the given href. */
  className?: string;
  title?: string;
}

export function SceneChip({
  scene, active, here, onClick, to, trailing,
  size = 'md', showIndicators = true, className, title,
}: SceneChipProps) {
  const thumbSize = size === 'sm' ? 'w-7 h-7' : 'w-10 h-10';
  const labelText = chipKindLabel(scene);
  const readinessColor = chipReadinessColor(scene);
  const readinessTitle = chipReadinessTitle(scene);
  const fullTitle = title ?? `${labelText} · ${scene.title}${readinessTitle}`;

  const baseCls = `inline-flex items-center gap-1.5 pl-1 pr-2 py-1 rounded-md border transition ${
    active
      ? 'bg-accent/10 border-accent ring-1 ring-accent/30'
      : 'bg-white border-zinc-200 hover:border-zinc-400'
  }`;
  const wrapperCls = `relative inline-flex shrink-0 rounded-md ${here ? 'bg-accent/5 ring-1 ring-accent/30' : ''} ${className ?? ''}`;

  const inner = (
    <>
      <span className={`relative ${thumbSize} shrink-0 rounded overflow-hidden bg-zinc-100 border ${active ? 'border-accent' : 'border-zinc-200'}`}>
        {scene.url ? (
          <img src={scene.url} alt="" loading="lazy" className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <span className="absolute inset-0 flex items-center justify-center text-zinc-400 text-[0.55rem] font-semibold">?</span>
        )}
        {scene.labeled && (
          <span
            className="absolute bottom-0 right-0 bg-emerald-600 text-white text-[0.5rem] leading-none px-0.5 py-0.5 rounded-tl"
            aria-label="annotiert"
            title="annotiert"
          >✓</span>
        )}
      </span>
      <span className="text-[0.72rem] font-medium whitespace-nowrap">{labelText}</span>
      {showIndicators && (readinessColor || (scene.labelCount > 0)) && (
        <span className="inline-flex items-center gap-0.5">
          {readinessColor && (
            <span
              className="inline-block w-1.5 h-1.5 rounded-full"
              style={{ backgroundColor: readinessColor }}
            />
          )}
          {scene.labelCount > 0 && (
            <span className={`text-[0.6rem] tabular-nums ${active ? 'text-accent font-semibold' : 'text-zinc-400'}`}>
              {scene.labelCount}
            </span>
          )}
        </span>
      )}
      {trailing}
    </>
  );

  if (to) {
    return (
      <span className={wrapperCls}>
        <Link to={to} className={baseCls} title={fullTitle}>{inner}</Link>
      </span>
    );
  }
  return (
    <span className={wrapperCls}>
      <button type="button" onClick={onClick} className={baseCls} title={fullTitle}>{inner}</button>
    </span>
  );
}
