# bim-database

The supervised-learning corpus for **[`bim-ai`](https://github.com/jhoetter/bim-ai)** —
a system that generates BIM models from architectural drawings.

This repo is a single-purpose annotation pipeline: drop architectural PDFs
in, draw bounding boxes around each scene (elevation / floorplan / section
/ detail), annotate the scenes, and export a training corpus with both
raw and rectified ground-truth pairs.

```
   ┌──────────────┐       ┌────────────────┐       ┌────────────────┐
   │  PDF intake  │ ─→    │   Scene        │ ─→    │   Annotation   │
   │   (R1)       │       │   extraction   │       │   (W0-W9 +     │
   │              │       │   (R2)         │       │    V0-V3 +     │
   └──────────────┘       └────────────────┘       │    M / K / N)  │
                                                   └────────┬───────┘
                          ┌────────────────┐                │
                          │  Export        │ ◀──────────────┘
                          │  preview (R4)  │
                          └────────────────┘
                                  │
                                  ▼
                          ┌────────────────┐
                          │  3D preview    │
                          │  (R5)          │
                          └────────────────┘
```

See [`spec/end-to-end-readiness.md`](spec/end-to-end-readiness.md) for the
pipeline design and [`spec/annotation-workflow.md`](spec/annotation-workflow.md)
for the in-editor labeling workflow.

---

## Quick start

```bash
make install     # creates .venv, installs deps
make dev         # starts FastAPI on http://localhost:2500 (with --reload)
make web         # vite dev server on :5173 (proxies API requests to :2500)
```

Open <http://localhost:2500> — single-page UI, dataset list with per-house
workflow phase badges.

> **Security note.** The server is single-user-on-localhost by default.
> `POST /pdfs*`, `POST /exports*` and `DELETE /pdfs/incoming/*` are
> un-authenticated. Do not expose this server on a LAN or VPN without
> fronting it with auth.

---

## What's in the box

```
data/
  pdfs/incoming/                # R1: per-house PDF intake bundles
    house-21/
      manifest.json             # source filenames + state + notes
      house-21.pdf              # consolidated PDF (per R1.3)
      source/                   # original uploads (preserved)
  dataset/                      # R2 output: scene crops + label JSONs
    house-21/
      manifest.json
      house-21-floorplan-eg.jpg
      labels/
        house-21-floorplan-eg.json

api/
  main.py                       # FastAPI app

ui/src/
  pages/                        # DatasetPage / DatasetHousePage / AnnotatePage
  lib/                          # workflow, house_facts, region_kind, …

schema/
  scene_labels.schema.json      # source of truth for label JSON shape

scripts/
  cleanup_houses_legacy.py      # R0.6: drops obsolete data/houses/

spec/
  annotation-tool.md            # data model, M0–M6, two-stage training
  annotation-ux.md              # M7–M13 + X1–X11
  annotation-visualisation.md   # V0–V3
  annotation-workflow.md        # W0–W9
  end-to-end-readiness.md       # R0–R6 (this tracker)
  keyboard.md                   # K1–K12
```

---

## Status (2026-05-29)

- Catalog ("houses") path removed in R0.
- PDF intake (R1) + scene extraction (R2) + cross-step navigation
  (R3) + export preview (R4) + 3D preview (R5) + bulk export (R6)
  in progress per the R tracker.
- Seed PDFs for houses 21 / 22 / 23 preserved at `data/pdfs/incoming/`.
