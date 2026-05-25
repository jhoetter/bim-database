# Scene-image storage strategy

**Status:** implemented (Phase 1 + 2). History rewrite (Phase 3) still deferred.
**Owner:** jhoetter
**Last touched:** 2026-05-25

## What shipped

- **Phase 1** (`d7d6bc2`): `api/scene_render.py` (pdftoppm + Pillow, mtime-keyed cache at `tmp/scene-cache/`), `/scene/{key}/{file}` route, URL routing in `_enrich` (PDF-sourced → `/scene/`, originals → `/static/`), `scripts/render_scene.py` CLI, `make warm-cache`, `source_ref.dpi` (default 200), Vite proxy.
- **Phase 2** (this commit): `.gitignore` glob `data/houses/house-*/house-*.jpg`, `git rm --cached` all 35 committed JPGs (+ removed 2 orphans), `source_ref.rotation_deg` (default 0) for PDFs whose content is drawn rotated relative to page orientation. Verified: renderer reproduces the historical JPGs visually-identically (26/35 byte-identical; 9 rotated h23 scenes visually-identical with byte variance from JPEG encoder nondeterminism).
- **Phase 3** — `git filter-repo` to drop historical JPG blobs from the pack. Still deferred. Needed to actually reclaim disk; without it, the bloat sits in `.git/objects` even though no further JPGs commit.
- **Phase 4** — Optional AVIF q80 output for ~50% cache size reduction. Skipped for now.

## Problem

We commit cropped scene JPGs (e.g. `house-22-elevation-nord.jpg`) directly into git. Every re-crop pass writes new blobs even when only crop coordinates change. The repo is on a trajectory that doesn't scale to all 56 houses, especially with the iteration discipline we just enshrined (2-3 crop revisions per scene is now normal).

## Current state (measured 2026-05-25)

| component | files | size |
|---|---|---|
| `.git/` total | — | **166 MB** |
| working tree `data/houses/` | — | 161 MB |
| source PDFs | 66 | 97 MB (avg 1.5 MB) |
| catalog AVIFs | 228 | 34 MB (avg 151 KB) |
| cropped JPGs | 37 | 14 MB (avg 390 KB) |
| JSON records | 59 | 0.2 MB |

Only **3 of 56 houses** (h21/22/23) have cropped JPGs so far. The catalog houses have AVIFs that came with the source, not crops we generate.

### Per-house crop totals (post-standardization)

| house | JPGs | total | source PDF |
|---|---|---|---|
| house-21 | 13 | 9.5 MB | 50 MB |
| house-22 | 9 | 1.7 MB | 5.7 MB |
| house-23 | 15 | 3.2 MB | 5.5 MB |

### Compression sample (line-drawing crop)

| encoding | size | notes |
|---|---|---|
| JPEG q92 (current) | 198 KB | what the pipeline writes today |
| AVIF q80 | 95 KB | ~50% reduction, lossless-looking for line art |

## Growth projection

Assume:
- ~12 crops per house (median between h22's 9 and h23's 15)
- Avg crop size ~250 KB JPEG
- 56 houses fully standardized: **~170 MB** of crops, all of it new git blobs
- Every re-iter pass on a house = ~3 MB of fresh blobs that overlay (but don't replace) the previous pass in git history
- The h22 standardization → re-crop loop today already wrote two generations of JPGs for the same scenes

So a "full standardization + one re-crop pass on each" run pushes the repo past **0.5 GB** in git history, mostly redundant since the source PDF already contains all the pixel content.

## Options

### A. Status quo — commit cropped JPGs
- Pros: simple; image served as static file; archive-stable; no compute at request time.
- Cons: bloat compounds; every re-crop is a new blob; drift risk between JPG and JSON `crop_box_pct`; pdftoppm output is not perfectly deterministic across libpoppler versions, so the JPG-in-git is *less* canonical than the source PDF.

### B. Coords-only — render on demand
JSON keeps `source_ref.file` + `page` + `crop_box_pct` + `dpi`. API endpoint `/api/scene-image/<key>/<file>` resolves these and pipes through `pdftoppm | crop` (or `pypdfium2` + PIL).

- Pros: lean git (source PDF + JSON only); re-cropping is a JSON edit, no new blob; single source of truth; deterministic-by-source.
- Cons: first-request latency ~200-500 ms per scene; requires `pdftoppm` (or `pypdfium2`) in deploy env; cold caches show this latency.

### C. Hybrid — coords in git, renders cached out-of-band
Same JSON model as (B). Build/deploy step pre-renders all scenes into `.cache/scenes/<sha>.jpg` (gitignored). API serves from cache; on miss, renders + caches.

- Pros: fast serving (warm cache); lean git; re-cropping = cache invalidation; deterministic-by-source.
- Cons: build step + cache invalidation logic; slight cold-start cost.

### D. Git LFS
Crops stay JPGs, but in LFS so the main repo stays small.

- Pros: minimal code change.
- Cons: LFS infra/cost; GitHub free tier is 1 GB storage / 1 GB bandwidth-per-month — at current trajectory this is consumed within 5-10 re-iter rounds; doesn't fix the underlying duplication.

### E. Re-encode JPG → AVIF
Halves on-disk size of each crop. Orthogonal to A/B/C/D.

- Pros: easy win; ~50% size cut.
- Cons: still bloats over re-iters, just slower; AVIF browser support is fine but tooling chain slightly chunkier.

### F. History rewrite + lean forward
`git filter-repo` to drop historical JPG blobs; combine with (B) or (C) going forward.

- Pros: one-time cleanup recovers current bloat.
- Cons: rewrites history; everyone with a clone has to reset; only valuable if a forward strategy lands too.

## Recommendation

**Adopt (C): hybrid coords-in-git + out-of-band render cache.**

Specifically:
1. JSON keeps `source_ref.file / page / crop_box_pct` + a new `source_ref.dpi` (default 200).
2. Stop committing JPG crops. Add `data/houses/*/*.jpg` (or a tighter glob like `data/houses/*/house-*-elevation-*.jpg`, `*-floorplan-*.jpg`, `*-section-*.jpg`, `*-detail-*.jpg`, `*-doc-*.jpg`) to `.gitignore`.
3. Add a small renderer: `scripts/render_scene.py KEY FILE` — looks up the scene in JSON, opens the PDF, renders the page at `dpi`, crops by `crop_box_pct`, writes to `.cache/scenes/<key>/<file>` (path mirrors logical filename).
4. API endpoint serves from `.cache/scenes/...`; on miss, calls the renderer inline.
5. Cache key includes `(pdf_sha256, page, crop_box_pct, dpi)` so a JSON edit naturally invalidates.
6. Deploy: `make build` invokes the renderer for every scene to warm the cache.
7. One-time cleanup (separately, opt-in): `git filter-repo --path-glob 'data/houses/*/house-*.jpg' --invert-paths` once everyone's prepared. Defer until the new path is stable.

Why this over (B): saves the per-request latency and matches what the API already serves (static-file model). UI doesn't change.

Why not (D): LFS adds infra without solving duplication.

Why also (E) opportunistically: switch the renderer to write AVIF; the UI already handles AVIF for catalog images, so it's a single-line API change.

## Open questions

1. **Determinism.** Do we treat the rendered JPG as canonical, or treat the (PDF + coords) as canonical? Recommendation says the latter. Means small visual drift across libpoppler versions is acceptable — but if any downstream consumer (bim-ai render eval?) compares pixel-by-pixel, that breaks.
2. **Source PDFs in git.** PDFs are 97 MB of the 161 MB. They're the actual archival truth and shouldn't move. But they're also the bulk of `.git/objects`. Keep as-is.
3. **AVIF catalog files.** 228 AVIFs, 34 MB. These came from external sources; they're the *source* artifact for those records. Keep in git.
4. **Cache location.** `.cache/scenes/` at repo root is convenient but mixes deploy state with dev state. Alternative: `tmp/scene-cache/` so it's symmetric with the `tmp/` already used for the logging DB.
5. **Naming.** Should the rendered cache file mirror the current logical filename (`house-22-elevation-nord.jpg`) for URL stability, or be hash-keyed for content addressing? Logical filename is friendlier to debug; hash is friendlier to invalidate. Decision: logical filename, with the cache invalidated on JSON edit by comparing JSON mtime > cache mtime.

## Migration plan (when greenlit)

1. Add `dpi` to `source_ref` schema (default 200, optional).
2. Write `scripts/render_scene.py` + API endpoint.
3. Switch `make build` to warm the cache.
4. Confirm UI works end-to-end against the new endpoint.
5. Add the gitignore entry, `git rm --cached` the existing JPGs (one commit).
6. (Optional) `git filter-repo` to drop them from history.

Each step is reversible up to step 6.

## Related: floor + compass direction backfill (touched 2026-05-25)

Separate but adjacent: the UI now shows `floor` and `view` badges on every scene tile + detail page. Two data gaps:

- **73 catalog floorplans** (h17, h19, h20, h24-46, …) missing `floor`. Single-AVIF `floorplan1.original.avif` / `floorplan2.original.avif` records — need a vision pass to infer EG vs OG/DG from room labels.
- **h21 and h23 elevations** use positional labels (`front/rear/left/right`, `front/side/rear`) because the source PDFs say "Berg/Tal/Linke Giebel/Rechte Giebel" or "Strasse/Eingang/Garten" without an explicit Nordpfeil. **User guidance 2026-05-25: "himmelsrichtung can be derived for most houses with plans"** — site plans / Lageplan PDFs or implicit context (e.g. "Berg-Ansicht" + slope direction in description) usually let us pin down compass. Consistency matters more than absolute correctness; mixing positional + compass within one house is the failure mode to avoid.

This is independent of the storage decision but the two should ship in the same sprint — a backfill pass touches a lot of JSON, and a backfill pass *after* a storage change is much cheaper.
