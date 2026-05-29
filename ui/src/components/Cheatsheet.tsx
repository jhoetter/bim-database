import { useEffect } from 'react';

// L10 — shared keyboard cheatsheet. Lifted out of AnnotatePage so
// ExtractPage can use it too. Sections + bindings come from the caller,
// so each page contributes its own slice; source of truth lives in
// spec/keyboard.md, hardcoded here for now.

export interface CheatsheetSection {
  title: string;
  bindings: Array<[string, string]>; // [keys, description]
}

export interface CheatsheetProps {
  sections: CheatsheetSection[];
  onClose: () => void;
}

export function Cheatsheet({ sections, onClose }: CheatsheetProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === '?') {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="absolute inset-0 z-40 bg-black/40 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-2xl max-w-3xl w-full max-h-[80vh] overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between mb-4">
          <h2 className="text-[1rem] font-semibold">Tastaturkürzel</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-muted hover:text-zinc-900 text-xl leading-none w-7 h-7 flex items-center justify-center"
            aria-label="Schließen"
          >
            ×
          </button>
        </header>
        <div className="grid grid-cols-2 gap-x-6 gap-y-5">
          {sections.map(({ title, bindings }) => (
            <section key={title}>
              <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-2">
                {title}
              </h3>
              <dl className="space-y-1">
                {bindings.map(([keys, desc]) => (
                  <div key={keys + desc} className="flex justify-between gap-3 text-[0.8rem]">
                    <dt className="text-zinc-700">{desc}</dt>
                    <dd className="font-mono text-[0.75rem] text-zinc-900 bg-zinc-100 px-1.5 py-0.5 rounded shrink-0">
                      {keys}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        <p className="mt-5 text-[0.7rem] text-muted text-center">
          Esc oder ? schließt das Fenster.
        </p>
      </div>
    </div>
  );
}

// L10 — section bank shared by both pages. Each page selects which
// sections to surface; AnnotatePage shows both pages' sections plus its
// own, ExtractPage shows just the extract-side ones.

export const CHEATSHEET_SECTIONS_EXTRACT: CheatsheetSection[] = [
  {
    title: 'Seiten-Navigation (Extract)',
    bindings: [
      ['← / →',     'Vorige / nächste Seite'],
      ['↑ / ↓',     'Vorige / nächste Seite (vertikal)'],
      ['Page ↑/↓',  'Vorige / nächste Seite'],
      ['Home / End','Erste / letzte Seite'],
    ],
  },
  {
    title: 'Bbox zeichnen',
    bindings: [
      ['Drag',          'Bbox aufziehen'],
      ['Doppelklick',   'Ganze Seite als Szene'],
      ['Esc',           'Aktive Auswahl aufheben'],
      ['Del / Backspace','Aktiven Entwurf löschen'],
    ],
  },
  {
    title: 'Post-draw Klassifikation',
    bindings: [
      ['G / A / S / D', 'Grundriss / Ansicht / Schnitt / Detail'],
      ['K U E O D S',   'Geschoss (Grundriss)'],
      ['N S O W',       'Himmelsrichtung (Ansicht / Schnitt)'],
      ['Esc',           'Chip schließen, Entwurf bleibt unklassifiziert'],
    ],
  },
  {
    title: 'Szene-Aktionen',
    bindings: [
      ['Klick auf grüne Bbox',      'Aktions-Menü öffnen'],
      ['Doppelklick auf grüne Bbox','Direkt in Annotation springen'],
    ],
  },
  {
    title: 'Verlauf (Extract)',
    bindings: [
      ['Cmd/Ctrl + Z',         'Letzte Szenen-Aktion rückgängig (extract / delete / classify)'],
      ['Cmd/Ctrl + Shift + Z', 'Wiederherstellen'],
      ['Cmd/Ctrl + Y',         'Wiederherstellen (alternativ)'],
    ],
  },
  {
    title: 'Haus-Aktionen',
    bindings: [
      ['⋯ Topbar', 'Haus zurücksetzen — löscht alle Szenen + Annotationen, behält die PDF'],
    ],
  },
  {
    title: 'Hilfe',
    bindings: [
      ['?',   'Dieses Fenster ein/aus'],
      ['Esc', 'Dieses Fenster schließen'],
    ],
  },
];
