# bim-database

The supervised-learning corpus for **[`bim-ai`](https://github.com/jhoetter/bim-ai)** —
a system that generates BIM models from architectural drawings.

This repo is a single-purpose annotation pipeline: drop architectural PDFs
in, draw bounding boxes around each scene (elevation / floorplan / section
/ detail), annotate the scenes, and export a training corpus with both
raw and rectified ground-truth pairs.

```
   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
   │ Ingestion    │ ─→ │   Scene      │ ─→ │ Annotation   │ ─→ │ Export       │
   │ (batch CLI │      │ extraction   │    │ (W / V / M)  │    │ Set A + B    │
   │  + form)   │      │ (R2)         │    │              │    │              │
   └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
```

Ingestion + scene extraction produce raw-PDF + rectified-PDF pairs in
`data/pdfs/incoming/<house-key>/`; annotation labels in `data/dataset/`
combine to give the corpus its raw↔rectified ground truth.

See [`spec/keyboard.md`](spec/keyboard.md) for the live keyboard and
modifier model used by the annotation editor.

---

## Quick start

```bash
make install     # creates .venv, installs deps
make dev         # starts FastAPI on http://localhost:2500 (with --reload)
make web         # vite dev server on :5173 (proxies API requests to :2500)
```

Open <http://localhost:2500> — single-page UI, dataset list with per-house
workflow phase badges.

> **Security note.** The developer server (`api/main.py`) is
> single-user-on-localhost by default. `POST /pdfs*`, `POST /exports*`
> and `DELETE /pdfs/incoming/*` are un-authenticated. The
> customer-facing submission API (`form_api/main.py`) is a SEPARATE
> process with its own auth + rate-limit — never co-host it with the
> dev server. Do not expose `api/main.py` on a LAN/VPN without fronting
> it with auth.

---

## Ingestion (batch + customer submissions)

A single source-agnostic pipeline (`ingestion/`) feeds both entry points
and emits the canonical R1 intake bundle shape (`manifest.json` +
consolidated PDF + `source/`). Downstream R2 scene extraction is
untouched.

Stages: **normalize** (HEIC/JPEG/PNG/TIFF/PDF → page images, EXIF) →
**quality gate** (resolution / blur / exposure / glare / skew /
document-present, pass / warn / reject) → **rectify** (perspective
contour + deskew fallback; pluggable for a learned dewarp model) →
**restore** (`NoopEnhancer` default; Replicate as the first real
backend, hard-blocked on dimension-text pages so a generative model
never hallucinates digits) → **persist** (consolidated PDF +
`source/` + manifest v2.0 with full pipeline provenance).

**Batch (developer) path:**

```bash
make ingest INPUTS="some/folder/*.pdf some/photos/*.heic"
make ingest INPUTS=scrape/foo.pdf SRC_TYPE=scrape PROFILE=lenient-scrape
```

Failed gates **flag** but do not block — the bundle still lands.

**Customer submission path:**

```bash
export FORM_API_KEY=…           # required — refuses to start without
export FORM_IP_SALT=…
make form-api                   # public FastAPI on :2600
make form-ui-install
make form-ui-dev                # customer SPA on :5174
```

The form ingests into `data/pdfs/submissions/<id>/` (quarantine), never
into `data/pdfs/incoming/`. A developer review queue lives under the
existing `/intake` page (Tab "Kunden-Einreichungen") and can promote
clean submissions into `data/pdfs/incoming/house-NN/` after an optional
title-block redaction.

The intake manifest schema is in
[`schema/intake_manifest.schema.json`](schema/intake_manifest.schema.json)
(v2.0 — backwards-compatible with v1.0).

> **Agentic labeling** (planned, not yet built): the design for an
> MCP server + skill that lets an agent drive the full annotation
> workflow end-to-end lives in
> [bim-agent/spec/trackers/agentic-labeling-tracker.md][lbl]. The MCP
> server will land at `mcp_server.py` in this repo (Phase A of the
> tracker).
>
> [lbl]: https://github.com/jhoetter/bim-agent/blob/main/spec/trackers/agentic-labeling-tracker.md

---

## What's in the box

```
data/
  pdfs/
    incoming/                   # per-house intake bundles (R1 contract)
      house-21/
        manifest.json           # v2.0 — source filenames + state + per-page quality + pipeline
        house-21.pdf            # consolidated, rectified PDF
        source/                 # original uploads (preserved, SHA-dedup'd)
    submissions/                # customer submissions — quarantined
      <submission-id>/          # same bundle shape; promote → incoming/
  dataset/                      # scene crops + label JSONs (R2 output)

ingestion/                      # source-agnostic preprocessing pipeline
  normalize.py / gate.py / rectify.py / restore.py
  bundle.py / manifest.py / pii.py / config.py / cli.py

api/main.py                     # developer FastAPI (:2500) — localhost-only
form_api/main.py                # customer-facing FastAPI (:2600) — API key + rate limit

ui/                             # annotation SPA (Vite, :5173)
form-ui/                        # customer submission SPA (Vite, :5174)

schema/
  scene_labels.schema.json
  intake_manifest.schema.json   # intake bundle shape (v2.0)

tests/                          # ingestion package tests

spec/
  keyboard.md                   # K1–K12 — annotation editor key model
```

---

## Status (2026-05-29)

- Catalog ("houses") path removed in R0.
- PDF intake / scene extraction / annotation / export preview /
  3D preview / bulk export — shipped.
- Ingestion package + customer submission form — shipped; manifest
  upgraded to v2.0 backwards-compatibly.
- Seed PDFs for houses 21 / 22 / 23 preserved at `data/pdfs/incoming/`.
