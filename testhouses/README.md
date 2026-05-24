# testhouses/

Dev fixtures for the **bim-agent** convergence loop. Distinct from the
numbered production catalog (`house-1` … `house-20`).

| name | character | source |
|------|-----------|--------|
| `house-alpha` | 1956 Doppelhaus, Schalksmühle Weidenstraße | scan of paper Baubeschreibung + Ansichten/Grundrisse/DG/EG PDFs |
| `house-beta`  | 2007 Boss SFH on hillside lot | one combined Grundrisse/Ansichten/Schnitt PDF |
| `house-gamma` | Historicist Doppelhaus with cross-gables (Zwerchhaus + Schleppgauben), 5 levels including Spitzboden | "Kannenofen" multi-page PDF |

These were originally added to `bim-ai/testhouses/` (2026-05). Moved
here as part of the bim-ai → bim-agent + bim-database split on
2026-05-24 so the BIM software repo no longer ships agentic inputs.

The bim-agent convergence loop reads from this path via an env var
(`BIM_DATABASE_PATH`, default `~/repos/bim-database`). Migration plan:
`bim-agent/spec/trackers/bim-ai-bim-agent-split-tracker-2026-05-24.md`.
