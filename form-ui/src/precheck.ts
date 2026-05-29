// Client-side quality precheck.
//
// Runs in the browser before upload so customers get instant feedback
// on a blurry or dark photo and can retake without round-tripping to
// the server. Mirrors the server-side gate so the customer doesn't see
// surprise rejects after submitting.
//
// HEIC/HEIF photos cannot be decoded in the browser without a wasm
// polyfill (large). For those we skip the precheck and trust the
// server's response — better than blocking the customer on something
// only the server can see.

export type Precheck = {
  decision: 'pass' | 'warn' | 'reject' | 'skipped';
  reasons: string[];
  metrics?: {
    longSidePx: number;
    blurVar: number;
    exposureMean: number;
    glareFrac: number;
  };
};

const MIN_LONG_SIDE = 1500;
const WARN_LONG_SIDE = 2200;
const BLUR_REJECT_VAR = 60;
const BLUR_WARN_VAR = 120;
const EXPOSURE_MIN = 40;
const EXPOSURE_MAX = 235;
const GLARE_WARN = 0.02;
const GLARE_REJECT = 0.08;

export async function precheckFile(file: File): Promise<Precheck> {
  // Skip PDFs + HEIC — the browser can't reliably decode either to
  // pixel data here. Server-side gate handles them.
  if (file.type.includes('pdf') || /heic|heif/i.test(file.type) || /\.heic$|\.heif$/i.test(file.name)) {
    return { decision: 'skipped', reasons: ['kein Vorab-Check möglich (PDF/HEIC)'] };
  }
  const bitmap = await tryDecode(file);
  if (!bitmap) {
    return { decision: 'skipped', reasons: ['Bild konnte im Browser nicht gelesen werden'] };
  }

  // Downscale to a workable size; we don't need full resolution for
  // the metrics.
  const target = 600;
  const scale = target / Math.max(bitmap.width, bitmap.height);
  const w = Math.max(1, Math.floor(bitmap.width * scale));
  const h = Math.max(1, Math.floor(bitmap.height * scale));
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    return { decision: 'skipped', reasons: ['Browser unterstützt Canvas nicht'] };
  }
  ctx.drawImage(bitmap, 0, 0, w, h);
  const data = ctx.getImageData(0, 0, w, h).data;

  // Pre-pass: gray + cumulative-style stats.
  const gray = new Float32Array(w * h);
  let sum = 0;
  let glare = 0;
  for (let i = 0, j = 0; i < data.length; i += 4, j += 1) {
    // Rec.709 luma.
    const g = 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
    gray[j] = g;
    sum += g;
    if (g >= 245) glare += 1;
  }
  const exposureMean = sum / (w * h);
  const glareFrac = glare / (w * h);

  // Variance of discrete Laplacian.
  let lapSum = 0;
  let lapSumSq = 0;
  let lapN = 0;
  for (let y = 1; y < h - 1; y += 1) {
    for (let x = 1; x < w - 1; x += 1) {
      const i = y * w + x;
      const l =
        -4 * gray[i] +
        gray[i - 1] +
        gray[i + 1] +
        gray[i - w] +
        gray[i + w];
      lapSum += l;
      lapSumSq += l * l;
      lapN += 1;
    }
  }
  const lapMean = lapSum / Math.max(lapN, 1);
  const blurVar = lapSumSq / Math.max(lapN, 1) - lapMean * lapMean;

  const longSidePx = Math.max(bitmap.width, bitmap.height);

  const reasons: string[] = [];
  let decision: Precheck['decision'] = 'pass';
  const worst = (d: Precheck['decision']) => {
    const rank: Record<string, number> = { pass: 0, warn: 1, reject: 2, skipped: 0 };
    if (rank[d] > rank[decision]) decision = d;
  };

  if (longSidePx < MIN_LONG_SIDE) {
    reasons.push(`Auflösung niedrig (${longSidePx}px) — Mindestens ${MIN_LONG_SIDE}px nötig.`);
    decision = 'reject';
  } else if (longSidePx < WARN_LONG_SIDE) {
    reasons.push(`Auflösung knapp (${longSidePx}px) — besser ≥ ${WARN_LONG_SIDE}px.`);
    worst('warn');
  }

  if (blurVar < BLUR_REJECT_VAR) {
    reasons.push('Bild wirkt unscharf — bitte mit ruhiger Hand neu fotografieren.');
    decision = 'reject';
  } else if (blurVar < BLUR_WARN_VAR) {
    reasons.push('Bild ist etwas weich — schärfer geht.');
    worst('warn');
  }

  if (exposureMean < EXPOSURE_MIN) {
    reasons.push('Zu dunkel — bei Tageslicht erneut aufnehmen.');
    decision = 'reject';
  } else if (exposureMean > EXPOSURE_MAX) {
    reasons.push('Zu hell — direktes Sonnenlicht vermeiden.');
    decision = 'reject';
  }

  if (glareFrac >= GLARE_REJECT) {
    reasons.push('Starke Spiegelung erkannt — Winkel ändern, kein Blitz.');
    decision = 'reject';
  } else if (glareFrac >= GLARE_WARN) {
    reasons.push('Etwas Spiegelung — Winkel leicht ändern.');
    worst('warn');
  }

  return {
    decision,
    reasons,
    metrics: { longSidePx, blurVar, exposureMean, glareFrac },
  };
}

async function tryDecode(file: File): Promise<ImageBitmap | null> {
  try {
    return await createImageBitmap(file);
  } catch {
    return null;
  }
}
