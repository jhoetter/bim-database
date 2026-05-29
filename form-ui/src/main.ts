// Customer submission form — tiny vanilla-TS app.
//
// Three sections:
//   1. Drop zone + selected-file list with inline client-side quality verdict
//   2. Contact + consent
//   3. Submit + server response (per-page verdict so customers can retake)

import { precheckFile, type Precheck } from './precheck.ts';

const API_BASE = (import.meta.env.VITE_FORM_API_BASE as string | undefined) ?? 'http://localhost:2600';
const API_KEY = (import.meta.env.VITE_FORM_API_KEY as string | undefined) ?? '';

const ACCEPT = '.pdf,.jpg,.jpeg,.png,.tif,.tiff,.heic,.heif,application/pdf,image/jpeg,image/png,image/tiff';

type FileEntry = {
  file: File;
  precheck: Precheck | null;
};

const state: { files: FileEntry[]; submitting: boolean } = {
  files: [],
  submitting: false,
};

const root = document.getElementById('root')!;
root.innerHTML = `
  <h1>Bauunterlagen einreichen</h1>
  <p class="intro">
    Lade Grundrisse, Ansichten, Schnitte oder Detailzeichnungen hoch.
    <strong>Ein scharfes Foto bei Tageslicht</strong> liefert in der Regel
    bessere Ergebnisse als eine über eine Scanner-App erzeugte PDF —
    Apps verkleinern und komprimieren das Bild oft so stark, dass Maßangaben
    nicht mehr lesbar sind.
  </p>

  <section class="card">
    <h2>1. Dateien</h2>
    <div id="drop" class="dropzone">
      Datei(en) hierher ziehen oder <span style="color: var(--accent); text-decoration: underline;">durchsuchen</span>.
      <div class="hint">PDF, JPEG, PNG, TIFF oder HEIC — bis zu 12 Dateien, gesamt 50 MB.</div>
      <input id="file-input" type="file" accept="${ACCEPT}" multiple style="display:none" />
    </div>
    <ul id="file-list" class="file-list"></ul>
  </section>

  <section class="card">
    <h2>2. Kontakt</h2>
    <label>Name (optional)<input id="contact-name" type="text" autocomplete="name" /></label>
    <label>E-Mail (für Rückfragen, optional)<input id="contact-email" type="email" autocomplete="email" /></label>
    <label>Notizen (optional)
      <textarea id="user-notes" placeholder="z.B. 'EFH Baujahr 1962, Grundrisse beide Stockwerke'"></textarea>
    </label>
  </section>

  <section class="card">
    <h2>3. Nutzungsrechte</h2>
    <label>Lizenz
      <select id="license">
        <option value="permission-granted">Ich habe die Rechte und erlaube die Nutzung</option>
        <option value="cc-by">CC BY (Namensnennung)</option>
        <option value="cc-by-sa">CC BY-SA</option>
        <option value="cc0">CC0 (Public Domain)</option>
        <option value="other">Andere — bitte unten erklären</option>
      </select>
    </label>
    <label>Lizenz-Hinweise (optional)<input id="license-notes" type="text" /></label>
    <label class="consent">
      <input id="training-use" type="checkbox" />
      <span>
        Ich stimme zu, dass die hochgeladenen Dateien zur Modell-Training-Datenbank
        hinzugefügt werden dürfen.
      </span>
    </label>
  </section>

  <button id="submit" class="primary" disabled>Einreichen</button>
  <div id="result"></div>
`;

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const dropEl = $('drop');
const fileInput = $<HTMLInputElement>('file-input');
const fileListEl = $('file-list');
const submitBtn = $<HTMLButtonElement>('submit');
const resultEl = $('result');

dropEl.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => addFiles(Array.from(fileInput.files ?? [])));

dropEl.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropEl.classList.add('dragover');
});
dropEl.addEventListener('dragleave', () => dropEl.classList.remove('dragover'));
dropEl.addEventListener('drop', (e) => {
  e.preventDefault();
  dropEl.classList.remove('dragover');
  const files = Array.from(e.dataTransfer?.files ?? []);
  addFiles(files);
});

async function addFiles(files: File[]) {
  if (files.length === 0) return;
  for (const file of files) {
    if (state.files.length >= 12) break;
    const entry: FileEntry = { file, precheck: null };
    state.files.push(entry);
    renderFiles();
    try {
      entry.precheck = await precheckFile(file);
    } catch {
      entry.precheck = { decision: 'skipped', reasons: ['Vorab-Check fehlgeschlagen'] };
    }
    renderFiles();
  }
  updateSubmitEnabled();
}

function renderFiles() {
  fileListEl.innerHTML = '';
  state.files.forEach((entry, i) => {
    const li = document.createElement('li');
    const left = document.createElement('div');
    left.style.minWidth = '0';
    left.style.flex = '1';
    left.style.overflow = 'hidden';
    left.style.textOverflow = 'ellipsis';
    left.style.whiteSpace = 'nowrap';
    left.textContent = entry.file.name;

    const right = document.createElement('div');
    right.style.display = 'flex';
    right.style.gap = '8px';
    right.style.alignItems = 'center';

    if (entry.precheck) {
      const tag = document.createElement('span');
      const decision = entry.precheck.decision;
      tag.className = `tag ${decision}`;
      tag.textContent =
        decision === 'pass' ? '✓ ok'
        : decision === 'warn' ? 'warnung'
        : decision === 'reject' ? '✗ zu schlecht'
        : 'prüfen';
      right.appendChild(tag);
    } else {
      const wait = document.createElement('span');
      wait.className = 'tag';
      wait.style.background = '#eee';
      wait.style.color = '#666';
      wait.textContent = '…';
      right.appendChild(wait);
    }

    const rm = document.createElement('button');
    rm.textContent = '×';
    rm.style.border = '0';
    rm.style.background = 'transparent';
    rm.style.fontSize = '1.2rem';
    rm.style.cursor = 'pointer';
    rm.style.color = 'var(--muted)';
    rm.title = 'Entfernen';
    rm.addEventListener('click', () => {
      state.files.splice(i, 1);
      renderFiles();
      updateSubmitEnabled();
    });
    right.appendChild(rm);

    li.appendChild(left);
    li.appendChild(right);
    fileListEl.appendChild(li);

    if (entry.precheck && entry.precheck.reasons.length > 0 && entry.precheck.decision !== 'pass') {
      const hint = document.createElement('div');
      hint.className = 'hint';
      hint.style.flexBasis = '100%';
      hint.textContent = entry.precheck.reasons.join(' · ');
      li.appendChild(hint);
      li.style.flexWrap = 'wrap';
    }
  });
}

function updateSubmitEnabled() {
  const consent = ($('training-use') as HTMLInputElement).checked;
  const hasFiles = state.files.length > 0;
  const anyReject = state.files.some((e) => e.precheck?.decision === 'reject');
  submitBtn.disabled = state.submitting || !hasFiles || !consent || anyReject;
}

['training-use'].forEach((id) =>
  $(id).addEventListener('change', updateSubmitEnabled),
);

submitBtn.addEventListener('click', async () => {
  if (submitBtn.disabled) return;
  state.submitting = true;
  updateSubmitEnabled();
  resultEl.innerHTML = '';

  const form = new FormData();
  for (const entry of state.files) form.append('files', entry.file, entry.file.name);
  form.append('contact_name', ($('contact-name') as HTMLInputElement).value);
  form.append('contact_email', ($('contact-email') as HTMLInputElement).value);
  form.append('license', ($('license') as HTMLSelectElement).value);
  form.append('license_notes', ($('license-notes') as HTMLInputElement).value);
  form.append('training_use', String(($('training-use') as HTMLInputElement).checked));
  form.append('user_notes', ($('user-notes') as HTMLTextAreaElement).value);

  try {
    const res = await fetch(`${API_BASE}/submit`, {
      method: 'POST',
      body: form,
      headers: { 'X-API-Key': API_KEY },
    });
    const body = await res.json();
    if (!res.ok) {
      resultEl.innerHTML = `
        <div class="card" style="border-color: var(--reject)">
          <h2>Fehler</h2>
          <p>${escapeHtml(body.detail ?? res.statusText)}</p>
        </div>`;
    } else {
      resultEl.innerHTML = renderReceipt(body);
      state.files = [];
      renderFiles();
    }
  } catch (e) {
    resultEl.innerHTML = `
      <div class="card" style="border-color: var(--reject)">
        <h2>Netzwerkfehler</h2>
        <p>${escapeHtml(String(e))}</p>
      </div>`;
  } finally {
    state.submitting = false;
    updateSubmitEnabled();
  }
});

function renderReceipt(body: {
  submission_id: string;
  page_count: number;
  pages: { page: number; decision: string; reasons: string[] }[];
  pass: number;
  warn: number;
  reject: number;
  message: string;
}): string {
  const pages = body.pages
    .map(
      (p) => `
      <li>
        <div>Seite ${p.page}</div>
        <div><span class="tag ${p.decision}">${p.decision}</span></div>
        ${p.reasons.length > 0
          ? `<div class="hint" style="flex-basis:100%">${p.reasons.map(escapeHtml).join(' · ')}</div>`
          : ''}
      </li>`,
    )
    .join('');
  return `
    <div class="card">
      <h2>Eingegangen</h2>
      <p>${escapeHtml(body.message)}</p>
      <p class="hint">Referenz: <code>${escapeHtml(body.submission_id)}</code></p>
      <ul class="file-list">${pages}</ul>
    </div>`;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );
}
