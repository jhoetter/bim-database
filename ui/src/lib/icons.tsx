// Local subset of the bim-icons library (jhoetter/bim-icons). The package
// isn't on npm and ships TSX source only, so for now we inline the specific
// icons the annotation editor uses. If we ever need more or want to share
// across pages, swap this for a real dep.
//
// All icons are 24x24 viewBox, stroke-based with currentColor. Default
// strokeWidth = 1.5; pass size= as a number for any other size.

import type { ReactNode, SVGAttributes } from 'react';

interface IconProps extends SVGAttributes<SVGSVGElement> {
  size?: number | string;
  strokeWidth?: number | string;
}

function icon(displayName: string, paths: ReactNode) {
  function Icon({ size = 16, strokeWidth = 1.5, ...rest }: IconProps) {
    return (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false"
        {...rest}
      >
        {paths}
      </svg>
    );
  }
  Icon.displayName = displayName;
  return Icon;
}

// ── tools ───────────────────────────────────────────────────────────────────
export const SelectIcon = icon(
  'SelectIcon',
  <path d="M5 3L18 13L12 14L15 21L12 22L9 15L5 19z" />,
);

export const WallIcon = icon('WallIcon', <path d="M2 9h20 M2 15h20" />);

export const DoorIcon = icon(
  'DoorIcon',
  <>
    <path d="M2 9h7 M15 9h7 M2 15h7 M15 15h7 M9 9v6" />
    <path d="M9 15A6 6 0 0 1 15 9" />
  </>,
);

export const OpeningIcon = icon(
  'OpeningIcon',
  <path d="M4 4H20V20H4z M8 8H16V16H8z M8 8L16 16 M16 8L8 16" />,
);

export const CentreLineIcon = icon(
  'CentreLineIcon',
  <path d="M12 2V22 M9 6H15 M9 18H15 M9 12H15" />,
);

export const SpotElevationIcon = icon(
  'SpotElevationIcon',
  <path d="M12 9L8 19H16z M12 3V9 M9 3H15" />,
);

export const DimensionIcon = icon(
  'DimensionIcon',
  <path d="M4 7V17 M20 7V17 M4 12h16 M4 9L7 12L4 15 M20 9L17 12L20 15" />,
);

export const KeynoteIcon = icon(
  'KeynoteIcon',
  <>
    <path d="M12 3L4 7.5V16.5L12 21L20 16.5V7.5z" />
    <path d="M12 8V16 M9 8H12" />
  </>,
);

// ── scene tags ──────────────────────────────────────────────────────────────
export const PlanViewIcon = icon(
  'PlanViewIcon',
  <path d="M2 4h20v16H2z M2 13h20 M13 4V13" />,
);

export const ElevationViewIcon = icon(
  'ElevationViewIcon',
  <path d="M2 20H22 M5 20V6H19V20 M8 14h4V20 M14 10h4V14H14z" />,
);

export const SectionViewIcon = icon(
  'SectionViewIcon',
  <path d="M4 12h16 M4 8v8 M20 8v8 M4 16v4 M20 16v4" />,
);

export const LayersIcon = icon(
  'LayersIcon',
  <path d="M12 2L2 7L12 12L22 7L12 2z M2 12L12 17L22 12 M2 17L12 22L22 17" />,
);

export const QuestionIcon = icon(
  'QuestionIcon',
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.5 9A2.5 2.5 0 0 1 12 7.5A2.5 2.5 0 0 1 14.5 10C14.5 12 12 12 12 14 M12 17.5V17.51" />
  </>,
);
