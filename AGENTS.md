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
