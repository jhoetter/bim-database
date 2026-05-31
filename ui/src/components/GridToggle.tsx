import { useState } from 'react';

// Agenten-Raster toggle — the split button used identically at house level
// (ExtractPage) and scene level (AnnotatePage) so the control behaves the
// same everywhere. Left half toggles the overlay; the ▾ half opens a small
// dropdown to pick which of the 3 pixel-grid tiers (broad/finer/detail) are
// drawn. The image the labeling agents see over the MCP server is the same
// image + this grid, so it's a spot-check tool for when agent labels look off.

export type GridTiers = { broad: boolean; finer: boolean; detail: boolean };
export type GridStyle = 'standard' | 'coordinate_audit' | 'coordinate_pair' | 'coordinate_multicolor';

const MULTICOLOR_SWATCH = [
  '#e63946',
  '#f48c06',
  '#ffca28',
  '#2ecc71',
  '#009688',
  '#00bcd4',
  '#2196f3',
  '#673ab7',
  '#9c27b0',
  '#e91e63',
];

export function GridToggle({
  showGrid,
  setShowGrid,
  gridTiers,
  setGridTiers,
  gridStyle = 'standard',
  setGridStyle,
}: {
  showGrid: boolean;
  setShowGrid: (v: boolean) => void;
  gridTiers: GridTiers;
  setGridTiers: (v: GridTiers) => void;
  gridStyle?: GridStyle;
  setGridStyle?: (v: GridStyle) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="relative flex items-center">
      <button
        type="button"
        onClick={() => setShowGrid(!showGrid)}
        className={`text-[0.7rem] px-2 py-1 rounded-l-md border ${
          showGrid
            ? 'bg-purple-600 text-white border-purple-600'
            : 'bg-white text-zinc-700 border-zinc-300 hover:bg-zinc-50'
        }`}
        title="Agenten-Raster umschalten: das Bild, das ein Labeling-Agent über den MCP-Server sieht (3-stufiges Pixelraster, broad/finer/detail)"
        aria-label="Agenten-Raster umschalten"
        aria-pressed={showGrid}
      >
        {showGrid ? '🤖 Raster' : 'Raster'}
      </button>
      <button
        type="button"
        onClick={() => setMenuOpen((v) => !v)}
        className={`text-[0.7rem] px-1.5 py-1 rounded-r-md border-y border-r ${
          showGrid
            ? 'bg-purple-700 text-white border-purple-700'
            : 'bg-white text-zinc-600 border-zinc-300 hover:bg-zinc-50'
        }`}
        title="Raster-Stufen auswählen"
        aria-label="Raster-Stufen"
        aria-expanded={menuOpen}
      >
        ▾
      </button>
      {menuOpen && (
        <>
          <button
            type="button"
            onClick={() => setMenuOpen(false)}
            className="fixed inset-0 z-30 cursor-default"
            aria-label="Schließen"
          />
          <div className="absolute right-0 top-full mt-1 z-40 bg-white border border-zinc-300 rounded-md shadow-xl min-w-[16rem] p-3 space-y-2 text-[0.78rem]">
            <div className="text-[0.62rem] uppercase tracking-wider text-muted font-semibold">
              Raster-Stufen
            </div>
            {(['broad', 'finer', 'detail'] as const).map((tier) => (
              <label key={tier} className="flex items-center gap-2 text-[0.72rem]">
                <input
                  type="checkbox"
                  checked={gridTiers[tier]}
                  onChange={(e) => setGridTiers({ ...gridTiers, [tier]: e.target.checked })}
                />
                <span className="capitalize font-medium">{tier}</span>
                <span className="text-zinc-400 ml-auto text-[0.65rem]">
                  {tier === 'broad' && '~W/10 px'}
                  {tier === 'finer' && '~W/50 px'}
                  {tier === 'detail' && '~W/200 px'}
                </span>
              </label>
            ))}
            <p className="text-[0.62rem] text-zinc-500 leading-snug">
              Default = broad + finer. Detail nur im Zoom nützlich.
            </p>
            {setGridStyle && (
              <div className="pt-2 border-t border-zinc-200 space-y-1.5">
                <div className="text-[0.62rem] uppercase tracking-wider text-muted font-semibold">
                  Farben
                </div>
                {([
                  ['coordinate_multicolor', 'Mehrfarbiges Suchraster'],
                  ['standard', 'Standard'],
                  ['coordinate_audit', 'Blau / Magenta'],
                  ['coordinate_pair', 'Grün X / Rot Y'],
                ] as const).map(([style, label]) => (
                  <label key={style} className="flex items-center gap-2 text-[0.72rem]">
                    <input
                      type="radio"
                      name="grid-style"
                      checked={gridStyle === style}
                      onChange={() => setGridStyle(style)}
                    />
                    <span>{label}</span>
                    {style === 'coordinate_multicolor' ? (
                      <span
                        className="ml-auto grid h-4 w-16 grid-cols-10 overflow-hidden rounded-sm border border-zinc-200"
                        title="10 wiederkehrende Linienfarben in einem Raster"
                      >
                        {MULTICOLOR_SWATCH.map((color) => (
                          <span key={color} style={{ backgroundColor: color }} />
                        ))}
                      </span>
                    ) : (
                      <span className="ml-auto inline-flex overflow-hidden rounded-sm border border-zinc-200">
                        <span className={`h-3 w-4 ${
                          style === 'coordinate_pair'
                            ? 'bg-emerald-500'
                            : style === 'coordinate_audit'
                              ? 'bg-cyan-500'
                            : 'bg-zinc-700'
                        }`} />
                        <span className={`h-3 w-4 ${
                          style === 'coordinate_pair'
                            ? 'bg-red-500'
                            : style === 'coordinate_audit'
                              ? 'bg-fuchsia-500'
                              : 'bg-zinc-400'
                        }`} />
                      </span>
                    )}
                  </label>
                ))}
                <p className="text-[0.62rem] text-zinc-500 leading-snug">
                  Mehrfarbig färbt jede n-te X/Y-Linie anders, damit man Punkte
                  zur passenden Randkoordinate zurückverfolgen kann.
                </p>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
