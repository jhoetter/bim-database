// Customer submission form, embedded in the dev SPA.
//
// Mirrors form-ui/ (the standalone, hardened customer surface). The dev
// API exposes POST /submit without an API key — production deploys the
// standalone form_api/ process behind real auth instead.

import { useState } from 'react';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import { precheckFile, type Precheck } from '../lib/precheck';

const ACCEPT =
  '.pdf,.jpg,.jpeg,.png,.tif,.tiff,.heic,.heif,application/pdf,image/jpeg,image/png,image/tiff';

type FileEntry = {
  file: File;
  precheck: Precheck | null;
};

type SubmitResponse = {
  submission_id: string;
  page_count: number;
  pages: Array<{
    page: number;
    decision: 'pass' | 'warn' | 'reject';
    reasons: string[];
    human_qa_required: boolean;
  }>;
  pass: number;
  warn: number;
  reject: number;
};

export function SubmitPage() {
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [contactName, setContactName] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [notes, setNotes] = useState('');
  const [license, setLicense] = useState('permission-granted');
  const [licenseNotes, setLicenseNotes] = useState('');
  const [trainingUse, setTrainingUse] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<SubmitResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const anyReject = files.some((e) => e.precheck?.decision === 'reject');
  const canSubmit = !submitting && files.length > 0 && trainingUse && !anyReject;

  const addFiles = async (incoming: File[]) => {
    const slots = 12 - files.length;
    const accepted = incoming.slice(0, slots);
    for (const file of accepted) {
      const entry: FileEntry = { file, precheck: null };
      setFiles((prev) => [...prev, entry]);
      precheckFile(file)
        .then((p) => {
          setFiles((prev) =>
            prev.map((e) => (e.file === file ? { ...e, precheck: p } : e)),
          );
        })
        .catch(() => {
          setFiles((prev) =>
            prev.map((e) =>
              e.file === file
                ? { ...e, precheck: { decision: 'skipped', reasons: ['Vorab-Check fehlgeschlagen'] } }
                : e,
            ),
          );
        });
    }
  };

  const removeFile = (i: number) => {
    setFiles((prev) => prev.filter((_, idx) => idx !== i));
  };

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const fd = new FormData();
      for (const entry of files) fd.append('files', entry.file, entry.file.name);
      const qs = new URLSearchParams();
      qs.set('contact_name', contactName);
      qs.set('contact_email', contactEmail);
      qs.set('license', license);
      qs.set('license_notes', licenseNotes);
      qs.set('training_use', String(trainingUse));
      qs.set('user_notes', notes);
      const r = await fetch(`/submit?${qs}`, { method: 'POST', body: fd });
      const body = await r.json();
      if (!r.ok) {
        setError(body.detail ?? `${r.status} ${r.statusText}`);
      } else {
        setResult(body as SubmitResponse);
        setFiles([]);
        setNotes('');
        setLicenseNotes('');
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Shell
      breadcrumb={
        <Breadcrumb items={[{ label: 'Datensatz', to: '/' }, { label: 'Kunden-Einreichung' }]} />
      }
      leftSidebar={
        <div className="px-4 py-4 space-y-2">
          <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">
            Kunden-Einreichung
          </div>
          <h1 className="text-[1rem] font-semibold leading-snug">Vorschau</h1>
          <p className="text-[0.72rem] text-muted leading-snug">
            Diese Seite spiegelt das öffentliche Formular wider, das ein Kunde
            sieht. Eingereichtes landet in <code>data/pdfs/submissions/</code>
            und ist anschließend im Tab „Kunden-Einreichungen" sichtbar.
          </p>
        </div>
      }
    >
      <div className="px-6 py-5 max-w-2xl space-y-4">
        <h1 className="text-[1.05rem] font-semibold">Bauunterlagen einreichen</h1>
        <p className="text-[0.78rem] text-muted leading-snug">
          Lade Grundrisse, Ansichten, Schnitte oder Details hoch. Ein scharfes
          Foto bei Tageslicht liefert in der Regel bessere Ergebnisse als eine
          mit einer Scanner-App erzeugte PDF — Apps verkleinern und komprimieren
          das Bild oft so stark, dass Maßangaben nicht mehr lesbar sind.
        </p>

        <section className="border border-border bg-white rounded-lg p-4 space-y-2">
          <h2 className="text-[0.75rem] uppercase tracking-wider text-muted font-medium">
            1. Dateien
          </h2>
          <DropArea onFiles={addFiles} disabled={submitting} />
          {files.length > 0 && (
            <ul className="divide-y divide-border text-[0.85rem]">
              {files.map((e, i) => (
                <li key={i} className="py-2">
                  <div className="flex items-center gap-2">
                    <span className="flex-1 truncate">{e.file.name}</span>
                    <PrecheckBadge p={e.precheck} />
                    <button
                      type="button"
                      onClick={() => removeFile(i)}
                      className="text-muted hover:text-zinc-900 text-lg leading-none px-1"
                      title="Entfernen"
                    >×</button>
                  </div>
                  {e.precheck && e.precheck.decision !== 'pass' && e.precheck.reasons.length > 0 && (
                    <div className="text-[0.72rem] text-muted mt-0.5">
                      {e.precheck.reasons.join(' · ')}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="border border-border bg-white rounded-lg p-4 space-y-2">
          <h2 className="text-[0.75rem] uppercase tracking-wider text-muted font-medium">
            2. Kontakt
          </h2>
          <Field label="Name (optional)">
            <input
              type="text"
              value={contactName}
              onChange={(e) => setContactName(e.target.value)}
              className="w-full px-2 py-1 border border-zinc-300 rounded text-[0.85rem]"
            />
          </Field>
          <Field label="E-Mail (für Rückfragen, optional)">
            <input
              type="email"
              value={contactEmail}
              onChange={(e) => setContactEmail(e.target.value)}
              className="w-full px-2 py-1 border border-zinc-300 rounded text-[0.85rem]"
            />
          </Field>
          <Field label="Notizen">
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder={'z. B. EFH Baujahr 1962, Grundrisse beide Stockwerke'}
              className="w-full px-2 py-1 border border-zinc-300 rounded text-[0.85rem] min-h-[60px]"
            />
          </Field>
        </section>

        <section className="border border-border bg-white rounded-lg p-4 space-y-2">
          <h2 className="text-[0.75rem] uppercase tracking-wider text-muted font-medium">
            3. Nutzungsrechte
          </h2>
          <Field label="Lizenz">
            <select
              value={license}
              onChange={(e) => setLicense(e.target.value)}
              className="w-full px-2 py-1 border border-zinc-300 rounded text-[0.85rem]"
            >
              <option value="permission-granted">Ich habe die Rechte und erlaube die Nutzung</option>
              <option value="cc-by">CC BY (Namensnennung)</option>
              <option value="cc-by-sa">CC BY-SA</option>
              <option value="cc0">CC0 (Public Domain)</option>
              <option value="other">Andere — bitte unten erklären</option>
            </select>
          </Field>
          <Field label="Hinweise (optional)">
            <input
              type="text"
              value={licenseNotes}
              onChange={(e) => setLicenseNotes(e.target.value)}
              className="w-full px-2 py-1 border border-zinc-300 rounded text-[0.85rem]"
            />
          </Field>
          <label className="flex items-start gap-2 text-[0.85rem] mt-1">
            <input
              type="checkbox"
              checked={trainingUse}
              onChange={(e) => setTrainingUse(e.target.checked)}
              className="mt-1"
            />
            <span>
              Ich stimme zu, dass die hochgeladenen Dateien zur Modell-Training-Datenbank
              hinzugefügt werden dürfen.
            </span>
          </label>
        </section>

        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="text-[0.85rem] px-4 py-2 rounded-md bg-accent text-white font-medium disabled:opacity-50"
        >
          {submitting ? 'Lade hoch…' : 'Einreichen'}
        </button>

        {error && (
          <div className="text-[0.78rem] text-red-700 border border-red-300 bg-red-50 rounded p-2">
            {error}
          </div>
        )}
        {result && <Receipt result={result} />}
      </div>
    </Shell>
  );
}

function PrecheckBadge({ p }: { p: Precheck | null }) {
  if (!p) {
    return (
      <span className="text-[0.65rem] px-2 py-0.5 rounded-full bg-zinc-100 text-muted">…</span>
    );
  }
  const cls =
    p.decision === 'pass' ? 'bg-emerald-100 text-emerald-900'
    : p.decision === 'warn' ? 'bg-amber-100 text-amber-900'
    : p.decision === 'reject' ? 'bg-red-100 text-red-900'
    : 'bg-zinc-100 text-zinc-700';
  const label =
    p.decision === 'pass' ? '✓ ok'
    : p.decision === 'warn' ? 'warnung'
    : p.decision === 'reject' ? '✗ zu schlecht'
    : 'prüfen';
  return (
    <span className={`text-[0.65rem] px-2 py-0.5 rounded-full font-medium ${cls}`}>{label}</span>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-[0.85rem]">
      <span className="block text-[0.72rem] text-muted mb-0.5">{label}</span>
      {children}
    </label>
  );
}

function DropArea({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void;
  disabled: boolean;
}) {
  const [over, setOver] = useState(false);
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (disabled) return;
        onFiles(Array.from(e.dataTransfer.files));
      }}
      className={`border-2 border-dashed rounded-lg p-6 text-center text-[0.85rem] cursor-pointer ${
        over ? 'border-accent bg-amber-50' : 'border-zinc-300 bg-zinc-50'
      } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
      onClick={() => {
        if (disabled) return;
        const inp = document.createElement('input');
        inp.type = 'file';
        inp.accept = ACCEPT;
        inp.multiple = true;
        inp.onchange = () => inp.files && onFiles(Array.from(inp.files));
        inp.click();
      }}
    >
      <div>Datei(en) hierher ziehen oder klicken</div>
      <div className="text-[0.7rem] text-muted mt-1">
        PDF, JPEG, PNG, TIFF oder HEIC · bis zu 12 Dateien
      </div>
    </div>
  );
}

function Receipt({ result }: { result: SubmitResponse }) {
  const hasIssues = result.warn > 0 || result.reject > 0;
  return (
    <div className="border border-emerald-300 bg-emerald-50 rounded-lg p-3 text-[0.85rem]">
      <div className="font-medium text-emerald-900">Eingegangen ✓</div>
      <div className="text-[0.72rem] text-muted mt-0.5">
        Referenz: <code>{result.submission_id}</code> · {result.page_count} Seite(n)
      </div>
      {hasIssues && (
        <div className="mt-2 text-[0.78rem] text-emerald-950">
          Einige Seiten brauchen Nacharbeit:
          <ul className="mt-1 space-y-0.5">
            {result.pages
              .filter((p) => p.decision !== 'pass')
              .map((p) => (
                <li key={p.page} className="flex gap-2 items-baseline">
                  <span className="font-mono text-[0.72rem]">S. {p.page}</span>
                  <span
                    className={
                      p.decision === 'reject' ? 'text-red-800' : 'text-amber-800'
                    }
                  >
                    {p.decision}
                  </span>
                  <span className="text-muted text-[0.72rem]">
                    {p.reasons.join(' · ')}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  );
}
