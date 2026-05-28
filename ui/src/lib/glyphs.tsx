// Semantic glyphs for canvas-rendered labels. These differ from lib/icons.tsx
// (which holds tool-palette + scene-tag icons): glyphs render *on the canvas*
// at the centroid of a label, are screen-pinned to 16 px regardless of zoom,
// and convey semantic subtype ("this is a door, that's a roof").
//
// All glyphs are 24×24 viewBox, stroke-based with currentColor so they pick
// up the per-label colour. Render via <SemanticGlyph x={..} y={..} size={..} />
// — which transforms into screen-space.

import type { ReactNode, SVGAttributes } from 'react';

interface GlyphProps extends SVGAttributes<SVGGElement> {
  cx: number;
  cy: number;
  /** Glyph size in canvas units (callers pass `16 / zoom` to keep it
   *  screen-pinned). Defaults to 16 (no scaling). */
  size?: number;
  strokeWidth?: number;
}

function makeGlyph(displayName: string, paths: ReactNode) {
  function Glyph({ cx, cy, size = 16, strokeWidth = 1.6, ...rest }: GlyphProps) {
    // Each glyph's paths live in a 24×24 box; we translate to (cx-size/2,cy-size/2)
    // and scale to `size`, so the centroid lands at (cx,cy).
    return (
      <g
        transform={`translate(${cx - size / 2} ${cy - size / 2}) scale(${size / 24})`}
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        {...rest}
      >
        {paths}
      </g>
    );
  }
  Glyph.displayName = displayName;
  return Glyph;
}

// ── Group A: opening + wall semantics ─────────────────────────────────────

export const WindowPaneGlyph = makeGlyph(
  'WindowPaneGlyph',
  <>
    <rect x="4" y="4" width="16" height="16" />
    <line x1="12" y1="4" x2="12" y2="20" />
    <line x1="4" y1="12" x2="20" y2="12" />
  </>,
);

export const DoorSwingGlyph = makeGlyph(
  'DoorSwingGlyph',
  <>
    <path d="M6 20 V6 H14" />
    <path d="M6 6 A14 14 0 0 1 20 20" />
  </>,
);

export const DoorHandleGlyph = makeGlyph(
  'DoorHandleGlyph',
  <>
    <rect x="6" y="4" width="12" height="16" />
    <circle cx="15" cy="13" r="1" fill="currentColor" />
  </>,
);

export const GarageDoorGlyph = makeGlyph(
  'GarageDoorGlyph',
  <>
    <rect x="4" y="6" width="16" height="14" />
    <line x1="4" y1="10" x2="20" y2="10" />
    <line x1="4" y1="14" x2="20" y2="14" />
    <line x1="4" y1="18" x2="20" y2="18" />
  </>,
);

export const PassageGlyph = makeGlyph(
  'PassageGlyph',
  <>
    <path d="M5 20 V8 H19 V20" />
    <line x1="5" y1="20" x2="19" y2="20" strokeDasharray="2,2" />
  </>,
);

export const SkylightGlyph = makeGlyph(
  'SkylightGlyph',
  <>
    <path d="M6 18 L9 6 L19 6 L16 18 Z" />
    <line x1="9" y1="6" x2="16" y2="18" />
  </>,
);

export const DormerGlyph = makeGlyph(
  'DormerGlyph',
  <>
    <path d="M4 18 L12 8 L20 18" />
    <rect x="10" y="14" width="4" height="4" />
  </>,
);

export const WindowCircleGlyph = makeGlyph(
  'WindowCircleGlyph',
  <>
    <circle cx="12" cy="12" r="8" />
    <line x1="4" y1="12" x2="20" y2="12" />
    <line x1="12" y1="4" x2="12" y2="20" />
  </>,
);

export const WindowArchedGlyph = makeGlyph(
  'WindowArchedGlyph',
  <>
    <path d="M5 20 V12 A7 7 0 0 1 19 12 V20 Z" />
    <line x1="5" y1="12" x2="19" y2="12" />
    <line x1="12" y1="6" x2="12" y2="20" />
  </>,
);

// ── Group B: line + area semantics ────────────────────────────────────────

export const RoofSlopeGlyph = makeGlyph(
  'RoofSlopeGlyph',
  <>
    <path d="M4 18 L12 6 L20 18" />
    <line x1="4" y1="18" x2="20" y2="18" />
  </>,
);

export const RidgeGlyph = makeGlyph(
  'RidgeGlyph',
  <>
    <path d="M3 18 L12 6 L21 18" />
  </>,
);

export const EaveGlyph = makeGlyph(
  'EaveGlyph',
  <>
    <line x1="3" y1="10" x2="21" y2="10" />
    <line x1="6" y1="10" x2="6" y2="16" />
    <line x1="12" y1="10" x2="12" y2="18" />
    <line x1="18" y1="10" x2="18" y2="16" />
  </>,
);

export const GroundGlyph = makeGlyph(
  'GroundGlyph',
  <>
    <line x1="3" y1="9" x2="21" y2="9" />
    <line x1="6" y1="12" x2="9" y2="15" />
    <line x1="12" y1="12" x2="15" y2="15" />
    <line x1="18" y1="12" x2="21" y2="15" />
  </>,
);

export const WallBodyGlyph = makeGlyph(
  'WallBodyGlyph',
  <>
    <rect x="4" y="4" width="16" height="16" />
    <line x1="4" y1="10" x2="20" y2="10" />
    <line x1="4" y1="16" x2="20" y2="16" />
    <line x1="12" y1="4" x2="12" y2="10" />
    <line x1="8" y1="10" x2="8" y2="16" />
    <line x1="16" y1="10" x2="16" y2="16" />
    <line x1="12" y1="16" x2="12" y2="20" />
  </>,
);

export const GableGlyph = makeGlyph(
  'GableGlyph',
  <>
    <path d="M4 20 L12 6 L20 20 Z" />
  </>,
);

// ── Group C: height + dim semantics ───────────────────────────────────────

export const BezugGlyph = makeGlyph(
  'BezugGlyph',
  <>
    <path d="M6 8 L18 8 L12 18 Z" />
    <line x1="3" y1="20" x2="21" y2="20" />
  </>,
);

export const DimRefStarGlyph = makeGlyph(
  'DimRefStarGlyph',
  <path
    d="M12 3 L14 10 L21 10 L15.5 14 L17.5 21 L12 17 L6.5 21 L8.5 14 L3 10 L10 10 Z"
    fill="currentColor"
    stroke="none"
  />,
);

export const LevelTickGlyph = makeGlyph(
  'LevelTickGlyph',
  <>
    <line x1="4" y1="12" x2="20" y2="12" strokeWidth="2.2" />
  </>,
);

export const QuestionGlyph = makeGlyph(
  'QuestionGlyph',
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.5 9.5 A2.5 2.5 0 0 1 14.5 10 C14.5 12 12 12 12 14 M12 17 L12 17.5" />
  </>,
);
