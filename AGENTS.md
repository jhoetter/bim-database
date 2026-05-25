# Agent guide — bim-database

This repo is a small house catalogue. It stores **one record per house** in
`data/houses/house-<N>.json`, with all enum vocabulary centralized in
`data/ontology.json` and validated against `schema/house.schema.json`.

When an agent (you) adds a new house, the goal is *fill in as much ontology
as you can* so the data is sliceable by future filters and easy to compare
across records.

---

## Repo layout

```
data/
  ontology.json              # enums (building types, roof types, styles, image categories, …)
  houses/
    house-1.json             # one record per house, validated by schema/house.schema.json
    house-2.json
    ...
schema/
  house.schema.json          # JSON Schema for a record
house-1/                     # image + source-PDF folder, key matches data/houses/house-1.json
  hanse_variant35172_exterior10.original.avif
  hanse_variant35172_floorplan10.original.avif
house-1.pdf                  # optional combined PDF
api/main.py                  # FastAPI; loads data/houses/*.json and serves /houses, /ontology
mcp_server.py                # MCP server with the same data model
ui/index.html                # single-page UI; filters built from /ontology at load
```

The numeric ID in `data/houses/house-<N>.json` **must match** the folder
`house-<N>/` and the PDF `house-<N>.pdf`. The API derives the static URLs from
the ID.

---

## Adding a new house

1. **Scaffold the record:**

   ```
   make new-house ID=24 MODEL="My new house"
   ```

   This writes a stub `data/houses/house-24.json` with all schema fields
   present (most set to null) so you can fill them in.

2. **Add the image folder:** create `house-24/` and drop image files into it.
   Filenames are free-form, but the convention is
   `{slug}-{category}-{view-or-floor}.{ext}` for auto-classification — see
   examples in `house-21/`, `house-22/`, `house-23/`.

3. **Edit the record** — fill out at minimum:
   - `model` — human-readable name (Pflicht)
   - `source` — `catalog` | `documentation` | `survey` | `other`
   - `building_type`, `construction`, `roof_type`, `style` if known
   - `images[]` — one entry per file in `house-24/`, with
     `category` + `medium` and (where applicable) `view` + `floor` + `caption`

4. **Validate** before committing:

   ```
   make validate           # checks every data/houses/*.json against schema + ontology
   ```

5. **Commit.** No further wiring needed — the API picks the new file up on
   reload.

---

## Ontology — enum vocabulary

All enum values **must** come from `data/ontology.json`. Adding a new value:
edit `data/ontology.json`, then use it. UI and filters update automatically.

### Record-level

| Field             | Source                | Notes |
|-------------------|-----------------------|-------|
| `source`          | `ontology.sources`    | Where the data came from. `catalog`=prefab listing, `documentation`=Baupläne, `survey`=own measurements. |
| `building_type`   | `ontology.building_types` | EFH / Doppelhaushälfte / Bungalow / … |
| `construction`    | `ontology.constructions`  | Fertighaus / Massivhaus / … |
| `roof_type`       | `ontology.roof_types`     | Satteldach / Walmdach / Zwerchdach / … |
| `style`           | `ontology.styles`         | modern / historisch / nachkriegsbau / … |
| `energy_standard` | `ontology.energy_standards` | KfW Effizienzhaus 55 / Passivhaus / … |
| `levels`          | list of `ontology.levels` | bottom-up, e.g. `["KG","EG","1. OG","DG"]` |

### Image-level

Each entry in `images[]`:

| Field      | Source                       | Required for                       |
|------------|------------------------------|------------------------------------|
| `file`     | filename relative to folder  | always                             |
| `category` | `ontology.image_categories`  | always (exterior / floorplan / elevation / …) |
| `medium`   | `ontology.image_mediums`     | always (photo / render / drawing / scan / …) |
| `view`     | `ontology.image_views`       | exteriors & elevations (front / rear / north / …) |
| `floor`    | `ontology.levels`            | floorplans (KG / EG / 1. OG / …) |
| `caption`  | free text                    | optional — short German label      |

When in doubt: leave a field `null` rather than guess. The UI hides null
fields cleanly.

---

## What good ontology coverage looks like

Compare `data/houses/house-1.json` (catalog, mostly null on the new fields —
needs filling in) with `data/houses/house-23.json` (documentation,
fully-populated example). The goal is for every record to look more like the
latter over time.

---

## Don't

- Don't invent enum values inline. Add to `data/ontology.json` first.
- Don't put metadata in filenames if the field exists in the schema. The
  filename heuristic is a *fallback*; the JSON is the source of truth.
- Don't break the `data/houses/house-<N>.json` ↔ `house-<N>/` ↔ `house-<N>.pdf`
  naming triple — the API joins them by ID.

---

## Assessing a house against bim-ai's capabilities

Every house carries an optional `bim_ai_blocking_issues` field that drives a
tri-state `modelable_in_bim_ai` badge in the UI:

| Field value                                | Meaning                          | Badge                |
|--------------------------------------------|----------------------------------|----------------------|
| **field absent** (default)                 | not yet assessed                 | _(no badge)_         |
| `[]`                                       | assessed, no blockers            | green **BIM-AI ✓**   |
| `["jhoetter/bim-ai#101", ...]` (all closed)| was blocked, now unblocked       | green **BIM-AI ✓**   |
| `[...]` with ≥1 open                       | currently blocked                | red **BIM-AI ✗**     |
| `[...]` with any uncached ref              | refresh needed                   | grey **BIM-AI ?**    |

State comes from `data/.issue_state.json`, refreshed by `make refresh-issue-state`
(uses the `gh` CLI to read live issue state). Commit the refreshed cache.

### Walkthrough — assessing a single house

1. **Convert the photos.** Catalog houses ship AVIFs; for visual inspection
   render them to JPG:

   ```bash
   .venv/bin/python -c "
   import pillow_avif; from PIL import Image; from pathlib import Path
   for p in Path('house-N').glob('*.avif'):
       Image.open(p).convert('RGB').save(f'/tmp/h{p.stem}.jpg', quality=85)"
   ```

2. **Read the primary exteriors + floorplans.** Use the Read tool on each
   `/tmp/h*.jpg` to actually see the building. Pattern-match against the
   bim-ai capability matrix below.

3. **Identify the distinctive features.** Roof type, dormer types, footprint
   shape (rectangle / L / U / T), site (flat / hillside / split-level),
   special elements (Erker, Galerie, Wintergarten, Anbau, curved walls),
   facade pattern.

4. **Cross-reference bim-ai capabilities.** Quick map (re-verify against
   `~/repos/bim-ai/app/bim_ai/roof_geometry.py` and `app/bim_ai/elements/`
   if unsure — capabilities evolve):

   | Capability                       | Status today     |
   |----------------------------------|------------------|
   | Satteldach, Walmdach, Pultdach, Flachdach | ✓ supported |
   | Hillside / toposolid             | ✓ supported     |
   | Shed / gable / hipped dormers    | ✓ supported     |
   | Cantilevered roof overhangs      | ✓ supported     |
   | Balcony                          | ✓ supported     |
   | Slab voids (Galerie, stair shaft)| ✓ supported     |
   | Krüppelwalmdach                  | ✗ missing (EA-1)|
   | Mansarddach, Tonnendach, Zeltdach| ✗ missing       |
   | Versetztes Pultdach              | ✗ missing (#101)|
   | Zwerchdach / Zwerchhaus          | ⚠ partial topology |
   | Erker / Facade bay               | ✗ missing (#102)|
   | Eyebrow / Fledermaus dormer      | ✗ missing       |
   | Curved walls                     | ✗ missing       |
   | Wintergarten                     | ✗ missing       |

5. **Decide the verdict** for this house:

   - If every feature is supported → set `"bim_ai_blocking_issues": []` and
     fill out `roof_type`, `style`, `has_basement`, `levels`, `character`
     while you're in the file.
   - If a feature hits an *existing* open issue → add its ref to the list:
     `["jhoetter/bim-ai#101"]`.
   - If a feature hits a gap with *no existing* issue → file one in
     `jhoetter/bim-ai` (see next section), then link.

6. **Refresh + validate + commit.**

   ```bash
   make refresh-issue-state    # picks up any new issue refs
   make validate               # JSON schema + ontology + image-file existence
   git add data/ houses-N/ ... && git commit -m "house-N: ..." && git push
   ```

### Filing a new bim-ai capability-gap issue

Convention (matches existing issues like #31, #53, #56):

1. **Title** — `Engine: <feature> — blocks house-N (<manufacturer> <model>)`.
2. **Labels** — `enhancement`, `area: modeling`, `area: rendering` (whichever
   apply), `capability-gap`, `from: bim-agent`.
3. **Body sections**:
   - **Symptom** — link to the house in bim-database; describe the feature
     and why it matters visually.
   - **Classification** — *modeling* (data model can't represent it),
     *rendering* (data model can but renderer doesn't draw it), *authoring*
     (the AI can't recognise it). Be specific.
   - **Suspected fix** — concrete: which file, which Pydantic class, which
     enum to extend.
   - **Acceptance** — name the houses that should render correctly when this
     closes.
   - **Houses affected** — initial list.
4. **Screenshots** — upload to the `assets-v1` release on bim-ai (an asset
   bucket, not a code release). Rename the file locally first to
   `issue-NNN-<slug>.jpg`, then:

   ```bash
   gh release upload assets-v1 issue-NNN-<slug>.jpg --repo jhoetter/bim-ai
   ```

   Reference the result via:
   `https://github.com/jhoetter/bim-ai/releases/download/assets-v1/issue-NNN-<slug>.jpg`

   These render inline in GitHub markdown and don't pollute the repo with
   binary commits. (GitHub's `/user-attachments/assets/<uuid>` URLs only
   come from web-UI drag-drop — no public CLI API; release assets are the
   closest CLI-uploadable equivalent.)

Worked example: **#101 (Versetztes Pultdach)** and **#102 (Erker)** —
both filed from `house-18`. Read those before writing your first.

### Reusing issues across houses

Multiple houses can list the same issue. When that issue closes, every one
of them flips to `BIM-AI ✓` on the next `make refresh-issue-state`. If you
notice a pattern recurring across catalog houses, prefer one good issue with
all affected houses listed in the *Houses affected* section over many
sibling issues.
