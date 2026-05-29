// R1 — PDF intake page.
//
// Drag-and-drop one or many PDFs into a target house key (auto-allocated
// when blank). The server consolidates per-house source files into one
// PDF that the R2 scene extractor renders. Lists every existing intake
// bundle with a "→ Szenen extrahieren" button.

import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import {
  deleteIncomingPdf,
  listIncomingPdfs,
  updateIncomingNotes,
  uploadPdfs,
  useResource,
} from '../api/client';
import type { IncomingPdf } from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import { StepperBar } from '../components/StepperBar';

export function IntakePage() {
  // Refresh hook so list mutations bring new data without a full page
  // reload; bumped after every upload / delete / patch.
  const [rev, setRev] = useState(0);
  const { data, error, loading } = useResource(listIncomingPdfs, [rev]);
  const bumpRev = () => setRev((r) => r + 1);
  return (
    <Shell
      breadcrumb={
        <Breadcrumb items={[{ label: 'Hochladen' }]} />
      }
      leftSidebar={<IntakeSidebar bundles={data ?? []} />}
    >
      <StepperBar
        houseKey=""
        current="intake"
        intakeDone={(data ?? []).some((b) => b.state !== 'pending')}
        extractDone={(data ?? []).some((b) => (b.extracted_scenes?.length ?? 0) > 0)}
        annotateDone={false}
        exportDone={false}
      />
      <div className="px-6 py-5 space-y-6 max-w-5xl">
        <section>
          <h1 className="text-[1.05rem] font-semibold mb-2">PDFs hochladen</h1>
          <p className="text-[0.78rem] text-muted leading-snug mb-3">
            Zieh eine oder mehrere PDFs in das Feld unten. Wenn du einen
            <em> House-Key</em> setzt, landen die Dateien direkt im richtigen
            Bündel; sonst wird der nächste freie Key vergeben. Mehrere Files
            unter demselben Key werden zu <em>einer </em> konsolidierten PDF
            zusammengeführt.
          </p>
          <DropZone onUploaded={bumpRev} />
        </section>

        <section>
          <h2 className="text-[0.95rem] font-semibold mb-2">Eingangsstapel</h2>
          {loading && <p className="text-[0.78rem] text-muted">Lade…</p>}
          {error && <p className="text-[0.78rem] text-red-700">Fehler: {error.message}</p>}
          {!loading && !error && (data ?? []).length === 0 && (
            <p className="text-[0.78rem] text-muted italic">
              Noch nichts hochgeladen. Drop a PDF above.
            </p>
          )}
          {!loading && !error && (data ?? []).length > 0 && (
            <ul className="space-y-2">
              {(data ?? []).map((b) => (
                <li key={b.key}>
                  <BundleRow bundle={b} onChange={bumpRev} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </Shell>
  );
}

function IntakeSidebar({ bundles }: { bundles: IncomingPdf[] }) {
  const counts = {
    pending: 0, partial: 0, extracted: 0, annotated: 0,
  };
  for (const b of bundles) counts[b.state] = (counts[b.state] ?? 0) + 1;
  return (
    <div className="px-4 py-4 space-y-4">
      <header>
        <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">Intake</div>
        <h1 className="text-[1rem] font-semibold leading-snug mt-0.5">Eingangsstapel</h1>
      </header>
      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Status
        </h3>
        <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-[0.78rem]">
          <dt className="text-muted">Gesamt</dt>
          <dd className="font-medium tabular-nums">{bundles.length}</dd>
          {Object.entries(counts).map(([k, n]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{k}</dt>
              <dd className="font-medium tabular-nums">{n}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}

// Drag-and-drop file zone. Tracks isDragOver for the visual hover state +
// the in-flight upload (so a second drop while one is uploading queues
// rather than racing).
function DropZone({ onUploaded }: { onUploaded: () => void }) {
  const [houseKey, setHouseKey] = useState('');
  const [notes, setNotes] = useState('');
  const [isOver, setIsOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; msg: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handle = async (fileList: FileList | File[]) => {
    const files = Array.from(fileList).filter((f) =>
      f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf'),
    );
    if (files.length === 0) {
      setFeedback({ tone: 'error', msg: 'Nur PDF-Dateien werden akzeptiert.' });
      return;
    }
    setBusy(true);
    setFeedback(null);
    try {
      const m = await uploadPdfs(files, houseKey || undefined, notes || undefined);
      setFeedback({
        tone: 'success',
        msg: `✓ ${files.length} Datei(en) in ${m.key} (Seitenanzahl: ${m.page_count ?? '–'})`,
      });
      setHouseKey('');
      setNotes('');
      onUploaded();
    } catch (e) {
      setFeedback({ tone: 'error', msg: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2.5">
      <div className="flex gap-2 items-center text-[0.78rem]">
        <label className="flex items-center gap-1.5">
          House-Key (optional)
          <input
            type="text"
            value={houseKey}
            onChange={(e) => setHouseKey(e.target.value)}
            placeholder={'house-N (auto)'}
            className="px-2 py-1 border border-zinc-300 rounded text-[0.78rem] w-32 font-mono"
            disabled={busy}
          />
        </label>
        <label className="flex items-center gap-1.5 flex-1 min-w-[12rem]">
          Notiz (optional)
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="z. B. „Architekt Müller, 2025-03-12"
            className="px-2 py-1 border border-zinc-300 rounded text-[0.78rem] flex-1"
            disabled={busy}
          />
        </label>
      </div>
      <div
        onDragOver={(e) => { e.preventDefault(); setIsOver(true); }}
        onDragLeave={() => setIsOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsOver(false);
          if (busy) return;
          handle(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={`border-2 border-dashed rounded-lg px-6 py-12 text-center cursor-pointer transition ${
          busy
            ? 'border-zinc-300 bg-zinc-50 cursor-wait'
            : isOver
              ? 'border-accent bg-amber-50'
              : 'border-zinc-300 hover:border-zinc-400 bg-white'
        }`}
      >
        <p className="text-[0.85rem] font-medium text-zinc-800">
          {busy ? 'Lade hoch…' : 'PDFs hierher ziehen oder klicken'}
        </p>
        <p className="text-[0.7rem] text-muted mt-1">
          Eine oder mehrere PDF-Dateien · max. ~50 MB pro Upload
        </p>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          multiple
          className="hidden"
          onChange={(e) => e.target.files && handle(e.target.files)}
          disabled={busy}
        />
      </div>
      {feedback && (
        <p
          className={`text-[0.78rem] ${
            feedback.tone === 'success' ? 'text-emerald-700' : 'text-red-700'
          }`}
        >
          {feedback.msg}
        </p>
      )}
    </div>
  );
}

function BundleRow({ bundle, onChange }: { bundle: IncomingPdf; onChange: () => void }) {
  const [notes, setNotes] = useState(bundle.user_notes);
  const [editing, setEditing] = useState(false);
  // R3 will replace this with a richer state machine; for now we link
  // to the extract page and the annotation editor for whichever scenes
  // already exist (dataset side).
  useEffect(() => setNotes(bundle.user_notes), [bundle.user_notes]);

  const saveNotes = async () => {
    if (notes === bundle.user_notes) {
      setEditing(false);
      return;
    }
    try {
      await updateIncomingNotes(bundle.key, { user_notes: notes });
      onChange();
    } catch (e) {
      window.alert((e as Error).message);
    } finally {
      setEditing(false);
    }
  };
  const remove = async () => {
    if (!window.confirm(`Bündel ${bundle.key} unwiderruflich löschen?`)) return;
    try {
      await deleteIncomingPdf(bundle.key);
      onChange();
    } catch (e) {
      window.alert((e as Error).message);
    }
  };
  return (
    <div className="border border-border rounded-lg bg-white px-3 py-2.5 flex items-start gap-3">
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-1.5">
          <span className="font-mono text-[0.85rem] font-semibold">{bundle.key}</span>
          <span className="text-[0.65rem] text-muted">·</span>
          <span className="text-[0.7rem] text-muted">{bundle.page_count ?? '?'} Seiten</span>
          <span className="text-[0.65rem] text-muted">·</span>
          <StateBadge state={bundle.state} />
        </div>
        <div className="text-[0.72rem] text-muted mt-0.5 truncate">
          {bundle.source_filenames.length} Quelldatei(en) · hochgeladen {bundle.uploaded_at}
        </div>
        {editing ? (
          <div className="mt-2 flex gap-1.5">
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="flex-1 px-2 py-1 border border-zinc-300 rounded text-[0.75rem]"
              autoFocus
            />
            <button type="button" onClick={saveNotes} className="text-[0.72rem] px-2 py-1 bg-emerald-600 text-white rounded">
              Speichern
            </button>
            <button type="button" onClick={() => setEditing(false)} className="text-[0.72rem] px-2 py-1 bg-zinc-200 rounded">
              Abbrechen
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="text-[0.72rem] text-zinc-600 hover:text-zinc-900 mt-1 italic text-left inline-flex items-center gap-1 group"
            title="Notiz bearbeiten"
          >
            <span className="text-zinc-400 group-hover:text-zinc-700">✎</span>
            <span>{notes || 'Notiz hinzufügen'}</span>
          </button>
        )}
      </div>
      <div className="flex flex-col gap-1.5 items-end shrink-0">
        {bundle.consolidated_url && (
          <a
            href={bundle.consolidated_url}
            target="_blank"
            rel="noreferrer"
            className="text-[0.72rem] px-2.5 py-1 rounded bg-zinc-100 hover:bg-zinc-200 text-zinc-800"
          >
            PDF ansehen
          </a>
        )}
        <Link
          to={`/${bundle.key}/extract`}
          className="text-[0.72rem] px-2.5 py-1 rounded bg-accent text-white"
        >
          → Szenen extrahieren
        </Link>
        <button
          type="button"
          onClick={remove}
          className="text-[0.62rem] text-red-700 hover:underline"
        >
          Bündel löschen
        </button>
      </div>
    </div>
  );
}

function StateBadge({ state }: { state: IncomingPdf['state'] }) {
  const map = {
    pending:   { cls: 'bg-zinc-100 text-zinc-700',     label: 'ausstehend' },
    partial:   { cls: 'bg-amber-100 text-amber-900',   label: 'bereit zum Extrahieren' },
    extracted: { cls: 'bg-blue-100 text-blue-900',     label: 'extrahiert' },
    annotated: { cls: 'bg-emerald-100 text-emerald-900', label: 'annotiert' },
  } as const;
  const m = map[state] ?? map.pending;
  return (
    <span className={`text-[0.62rem] px-1.5 py-0.5 rounded-full font-medium ${m.cls}`}>
      {m.label}
    </span>
  );
}
