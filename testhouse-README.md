# Testhouses

Dev fixtures for the [`bim-agent`](https://github.com/jhoetter/bim-agent)
convergence loop. Distinct from the numbered production catalog
(`house-1` … `house-20`).

These are sourced from real-world Baubeschreibungen + Grundrisse and
chosen to stress-test specific bim-ai capability surfaces:

| slug | character | levels | what it stresses |
|------|-----------|--------|------------------|
| `testhouse-1` | 1956 Doppelhaushälfte, Schalksmühle Weidenstraße — gable roof + two shed dormers per long facade + exposed cellar on Tal side | KG, EG, DG | clean baseline: 3-level stack, dormer rendering, two-front-door Doppelhaus topology |
| `testhouse-2` | 2007 Boss SFH on steep hillside lot, L-shape with attached garage, daylight basement on east | UG, EG, DG | sloped toposolid (`heightSamples`), L-shape massing, basement opening cuts |
| `testhouse-3` | Historicist Doppelhaushälfte with cross-gables (Zwerchhaus / Zwerchgiebel) + arched Zwerchgauben + Schleppgauben + carport with spiral stair + 5 stacked levels including Spitzboden | KG, EG, OG, DG, Spitzboden | the demanding one: ornament, cross-gable read, 5-level stack — bellwether for capability-gap GH issues |

## Layout

```
testhouse-1.pdf          ← combined PDF (15 source PDFs merged, 63 pages)
testhouse-1/             ← original source PDFs preserved for archeology
  Ansichten.pdf
  Grundrisse, Schnitt.pdf
  EG.pdf
  DG.pdf
  …
testhouse-2.pdf          ← combined PDF (6 pages — was already single-source)
testhouse-2/
  Grundrisse, Ansichten, Schnitt (1).pdf
testhouse-3.pdf          ← combined PDF (10 pages — was already single-source)
testhouse-3/
  Kannenofen.pdf
```

`testhouse-N.pdf` is the file the agent should pass to its LLM reader
subagent — it contains everything (architectural drawings + admin
metadata) in a single artifact. The `testhouse-N/` folder preserves
the individual source PDFs for forensic / detail lookup. (Note: one
of testhouse-1's source PDFs — `NW-2025-005835290.pdf` — is encrypted
and was excluded from the merge; still available individually in the
folder.)

`testhouses.json` holds metadata (character, levels, building_type,
agent_notes) and is served by the REST API on port :2500 as
`/testhouses` and `/testhouses/{id}`. The UI's "Testhouses" tab
reads from that endpoint.

## History

Originally added to `bim-ai/testhouses/house-{alpha,beta,gamma}/`
(2026-05). Moved here as part of the bim-ai → bim-agent +
bim-database split on 2026-05-24, then flattened to `testhouse-N`
naming + per-testhouse combined PDFs on the same day so they match
the catalog's `house-N` + `house-N.pdf` pattern.

The bim-agent convergence loop reads from this path via an env var:

```
$BIM_DATABASE_PATH/testhouse-1.pdf
$BIM_DATABASE_PATH/testhouse-1/                  (optional drill-down)
```

`BIM_DATABASE_PATH` defaults to `~/repos/bim-database`. Migration
plan: `bim-agent/spec/trackers/bim-ai-bim-agent-split-tracker-2026-05-24.md`.
