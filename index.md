# House Database Index

52 catalog houses (real prefab/solid-construction listings) + 3
testhouses (dev fixtures for the bim-agent convergence loop).
Specs marked `*` confirmed from fertighaus.de; others derived from filename.

- **Catalog** — `house-1` … `house-20`, `house-24` … `house-55`, integer IDs, sourced from fertighaus.de
- **Testhouses** — `testhouse-1`, `testhouse-2`, `testhouse-3`, sourced from
  real-world Baubeschreibungen + Grundrisse, used by `bim-agent` as the
  stress-test corpus for `bim-ai`. See [`testhouse-README.md`](testhouse-README.md)
  for character / origin per testhouse.

---

## Quick Reference — Testhouses

| # | Slug | Character | Levels | PDF |
|---|------|-----------|--------|-----|
| 1 | `testhouse-1` | 1956 Doppelhaushälfte Schalksmühle Weidenstr. (KG/EG/DG, two shed dormers per long facade) | KG, EG, DG | [↓ combined (63p)](testhouse-1.pdf) |
| 2 | `testhouse-2` | Boss SFH 2007 on hillside (daylight basement east, ~3.8m E-W slope) | UG, EG, DG | [↓ (6p)](testhouse-2.pdf) |
| 3 | `testhouse-3` | Historicist Doppelhaus with cross-gables (Zwerchhaus + Schleppgauben + Spitzboden, 5 stacked levels) | KG, EG, OG, DG, Spitzboden | [↓ (10p)](testhouse-3.pdf) |

---

## Quick Reference

| # | Preview | Manufacturer | Model | Area | Rooms | Floors | Price | Energy | Type | PDF | Source |
|---|---------|-------------|-------|-----:|------:|-------:|-------|--------|------|-----|--------|
| 1 | <img src="house-1/hanse_variant35172_exterior10.original.avif" width="100"> | Hanse Haus | Variant 35-172 | 156 m²* | 7* | 1.5* | €448,014* | EH 40 Plus* | EFH | [↓](house-1.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/hanse-variant35172/) |
| 2 | <img src="house-2/hebel_efh-klassik118_exterior1.original.avif" width="100"> | hebelHAUS | EFH Klassik 118 | 118 m²* | 5* | 1.5* | auf Anfrage* | EH 55* | Massivhaus | [↓](house-2.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/hebel-efh-klassik118/) |
| 3 | <img src="house-3/plan-concept_paulik_exterior1.original.avif" width="100"> | Plan-Concept Massivhaus | Paulik | 123 m²* | 5* | 1.5* | auf Anfrage* | EH 55* | Massivhaus | [↓](house-3.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/plan-concept-paulik/) |
| 4 | <img src="house-4/baufritz-mfh_herndl_exterior1.original.avif" width="100"> | Bau-Fritz | Mehrfamilienhaus Herndl | 237 m²* | 3 Einh.* | 2.5* | €1,035,000* | EH 40* | MFH | [↓](house-4.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/baufritzmfh-herndl/) |
| 5 | <img src="house-5/ebh_f88-130holz_exterior1.original.avif" width="100"> | EBH Haus | Bungalow F88-130 Var. Holz | 107 m²* | 3* | 1* | €277,800* | EH 55* | Bungalow | [↓](house-5.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/ebh-bungalowf88-130holz/) |
| 6 | <img src="house-6/schwabenhaus_smartspace-dh04_111e3_exterior.original.avif" width="100"> | Schwabenhaus | SmartSpace DH | 134 m²* | —* | 2.5* | ab €308,171* | EH 40* | Doppelhaus | [↓](house-6.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-smartspacedh/) |
| 7 | <img src="house-7/kopenhagen_exterior_01.original.avif" width="100"> | Danhaus Deutschland | Kopenhagen | 182 m²* | 8* | 1.5* | €523,810* | EH 55* | Zweifamilienhaus | [↓](house-7.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/kopenhagen-mit-einliegerwohnung/) |
| 8 | <img src="house-8/weiott_bungalow353_exterior1.original.avif" width="100"> | WEIOTT-Massiv-Haus | Bunganlow 353 | 127 m²* | 4* | 1* | auf Anfrage* | EH 55* | Bungalow | [↓](house-8.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/weiott-bun353/) |
| 9 | <img src="house-9/invivo-bauhaus115_exterior1.original.avif" width="100"> | invivo haus | EFH Bauhaus 115 | 188 m²* | 4* | 2* | €533,895* | EH 55* | Massivhaus | [↓](house-9.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/invivo-bauhaus115/) |
| 10 | <img src="house-10/baudirekt_dh120_exterior1.original.avif" width="100"> | Baudirekt Architektenhäuser | DH 120 Basis | 119 m²* | 4* | 1.5* | auf Anfrage* | EH 55* | Doppelhaus | [↓](house-10.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/baudirekt-doppelhaus-dh-120-basis/) |
| 11 | <img src="house-11/grombach_exterior_0.original.avif" width="100"> | Rems-Murr-Holzhaus | Grombach | 115 m²* | 5* | 1* | €318,397* | EH 55* | Blockhaus | [↓](house-11.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/grombach/) |
| 12 | <img src="house-12/dammann_isa_exterior1.original.avif" width="100"> | Dammann-Haus | ISA (KfW-EH 40 EE) | 116 m²* | 7* | 1.5* | €297,700* | EH 40 EE* | EFH | [↓](house-12.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/isa-3-kfw-effizienzhaus-55/) |
| 13 | <img src="house-13/fingerhut_mhbadvilbel1_exterior3.original.avif" width="100"> | Fingerhut Haus | Bad Vilbel – Musterhaus NEU | 205 m²* | 6* | 1.5* | €593,766* | EH 55* | EFH | [↓](house-13.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/fingerhuthaus-mhbadvilbel1/) |
| 14 | <img src="house-14/holzbau-rustikal_bh-bodensee_exterior1.original.avif" width="100"> | Holzbau Rustikal | Blockhaus Bodensee | 151 m²* | 6* | 1* | €182,558* | EH 55* | Doppelhaus (Blockhaus) | [↓](house-14.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/holzbau-rustikal-blockhhaus-bodensee/) |
| 15 | <img src="house-15/kbs_illner_exterior1.original.avif" width="100"> | KBS Bau | Illner | 162 m²* | —* | 2* | auf Anfrage* | EH 55* | Massivhaus | [↓](house-15.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/kbs-illner/) |
| 16 | <img src="house-16/kbs_nolte_exterior1.original.avif" width="100"> | KBS Bau | Nolte | 200 m²* | —* | 2* | auf Anfrage* | EH 55* | Massivhaus | [↓](house-16.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/kbs-nolte/) |
| 17 | <img src="house-17/mh_il-6-143_1876_exterior.original.avif" width="100"> | Elbe-Haus BauinformationsZentrum Dresden | IL-6-143 | 135 m²* | 6* | 2* | auf Anfrage* | EH 55* | Massivhaus | [↓](house-17.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/elbe-haus-ost-il-6-143/) |
| 18 | <img src="house-18/mhstyle_exterior2.original.avif" width="100"> | Fertighaus WEISS | MH STYLE | 220 m²* | 4* | 2* | €609,419* | EH 55* | EFH | [↓](house-18.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/style-sonnenenergiehaus/) |
| 19 | <img src="house-19/mymassivhaus_efh-brand_exterior1.original.avif" width="100"> | MYMassivhaus | Einfamilienhaus Brand | 190 m²* | 5* | 2.5* | auf Anfrage* | EH 55* | Massivhaus | [↓](house-19.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/mymassiv-efhbrand/) |
| 20 | <img src="house-20/schneider-mh_sv-topline1_exterior1.original.avif" width="100"> | Schneider Massivhaus | Stadtvilla TOP-Line 1 | 125 m²* | 5* | 2* | auf Anfrage* | EH 55* | Massivhaus | [↓](house-20.pdf) | [fertighaus.de](https://www.fertighaus.de/haeuser/schneider-massivhaus-sv-topline1/) |
| 24 | <img src="house-24/nurda_friesenhaus-fs168_exterior1.original.avif" width="100"> | Nurda | Friesenhaus FS168 | 168 m² | — | — | auf Anfrage | — | Fertighaus | — | [fertighaus.de](https://www.fertighaus.de/haeuser/nurda-friesenhausfs168/) |
| 25 | <img src="house-25/nurda_landhaus-la146_exterior1.original.avif" width="100"> | Nurda | Landhaus LA146 | 146 m² | — | — | auf Anfrage | — | Fertighaus | — | [fertighaus.de](https://www.fertighaus.de/haeuser/nurda-landhausla146/) |
| 26 | <img src="house-26/nurda_stadthaus-s150_exterior1.original.avif" width="100"> | Nurda | Stadtvilla S150 | 150 m² | — | — | auf Anfrage | — | Fertighaus | — | [fertighaus.de](https://www.fertighaus.de/haeuser/nurda-stadtvillas150/) |
| 27 | <img src="house-27/nurda_bauhaus-b180_exterior1.original.avif" width="100"> | Nurda | Bauhaus B180 | 180 m² | — | — | auf Anfrage | — | Fertighaus | — | [fertighaus.de](https://www.fertighaus.de/haeuser/nurda-bauhausb180/) |
| 28 | <img src="house-28/schwabenhaus_solb110e6_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire B110 Entwurf 6 | 110 m² | — | 1 | auf Anfrage | — | Bungalow | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeireb110e6/) |
| 29 | <img src="house-29/schwabenhaus_solitaire-gen01_179e2-exterior.original.avif" width="100"> | Schwabenhaus | Solitaire Generationen | — | — | — | auf Anfrage | — | Fertighaus | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaire-generationen/) |
| 30 | <img src="house-30/schwabenhaus_sene133e1_exterior1.original.avif" width="100"> | Schwabenhaus | Sensation E133 Entwurf 1 | 133 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-sensatione133e1/) |
| 31 | <img src="house-31/schwabenhaus_sene133e4_exterior1.original.avif" width="100"> | Schwabenhaus | Sensation E133 Entwurf 4 | 133 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-sensatione133e4/) |
| 32 | <img src="house-32/schwabenhaus_smartspace140-e3_exterior1.original.avif" width="100"> | Schwabenhaus | SmartSpace E140 Entwurf 3 | 140 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-smartspace-e140e3/) |
| 33 | <img src="house-33/schwabenhaus_solitaeree165-e4_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire E165 Entwurf 4 | 165 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-165-entwurf-4/) |
| 34 | <img src="house-34/schwabenhaus_sene132e1_exterior1.original.avif" width="100"> | Schwabenhaus | Sensation E132 Entwurf 1 | 132 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-sensatione132e1/) |
| 35 | <img src="house-35/schwabenhaus_smartspace140-e1_exterior1.original.avif" width="100"> | Schwabenhaus | SmartSpace E140 Entwurf 1 | 140 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-smartspace-e140e1/) |
| 36 | <img src="house-36/schwabenhaus_selection169-e1_exterior1.original.avif" width="100"> | Schwabenhaus | Selection E169 Entwurf 1 | 169 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/selection-e-169-entwurf-1/) |
| 37 | <img src="house-37/schwabenhaus_solb130e1_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire B130 Entwurf 1 | 130 m² | — | 1 | auf Anfrage | — | Bungalow | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeireb130e1/) |
| 38 | <img src="house-38/schwabenhaus_solb130e4_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire B130 Entwurf 4 | 130 m² | — | 1 | auf Anfrage | — | Bungalow | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeireb130e4/) |
| 39 | <img src="house-39/schwabenhaus_solb150e5_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire B150 Entwurf 5 | 150 m² | — | 1 | auf Anfrage | — | Bungalow | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeireb150e5/) |
| 40 | <img src="house-40/schwabenhaus_solitaeree165-e3_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire E165 Entwurf 3 | 165 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-165-entwurf-3/) |
| 41 | <img src="house-41/schwabenhaus_sel175e4_exterior1.original.avif" width="100"> | Schwabenhaus | Selection E175 Entwurf 4 | 175 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/selection-e-175-entwurf-4/) |
| 42 | <img src="house-42/schwabenhaus_sene133e6_exterior1.original.avif" width="100"> | Schwabenhaus | Sensation E133 Entwurf 6 | 133 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-sensatione133e6/) |
| 43 | <img src="house-43/schwabenhaus_smartspace120-e1_exterior1.original.avif" width="100"> | Schwabenhaus | SmartSpace E120 Entwurf 1 | 120 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-smartspace-e120e1/) |
| 44 | <img src="house-44/schwabenhaus_solitaeree145-e2_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire E145 Entwurf 2 | 145 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-145-entwurf-2/) |
| 45 | <img src="house-45/schwabenhaus_solitaeree165-e7_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire E165 Entwurf 7 | 165 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-165-entwurf-7/) |
| 46 | <img src="house-46/schwabenhaus_solitaeree155-e9_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire E155 Entwurf 9 | 155 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeiree155e9/) |
| 47 | <img src="house-47/schwabenhaus_selection169-e5_exterior1.original.avif" width="100"> | Schwabenhaus | Selection E169 Entwurf 5 | 169 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/selection-e-169-entwurf-5/) |
| 48 | <img src="house-48/schwabenhaus_sel175e5_exterior1.original.avif" width="100"> | Schwabenhaus | Selection E175 Entwurf 5 | 175 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/selection-e-175-entwurf-5/) |
| 49 | <img src="house-49/schwabenhaus_solitaeree155-e7_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire E155 Entwurf 7 | 155 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeiree155e7/) |
| 50 | <img src="house-50/schwabenhaus_solitaeree145-e5_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire E145 Entwurf 5 | 145 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-145-entwurf-5/) |
| 51 | <img src="house-51/schwabenhaus_sene132e4_exterior1.original.avif" width="100"> | Schwabenhaus | Sensation E132 Entwurf 4 | 132 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-sensatione132e4/) |
| 52 | <img src="house-52/schwabenhaus_solb110e3_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire B110 Entwurf 3 | 110 m² | — | 1 | auf Anfrage | — | Bungalow | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeireb110e3/) |
| 53 | <img src="house-53/schwabenhaus_solitaeree145-e7_exterior1.original.avif" width="100"> | Schwabenhaus | Solitaire E145 Entwurf 7 | 145 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-145-entwurf-7/) |
| 54 | <img src="house-54/schwabenhaus_sel175e3_exterior1.original.avif" width="100"> | Schwabenhaus | Selection E175 Entwurf 3 | 175 m² | — | — | auf Anfrage | — | EFH | — | [fertighaus.de](https://www.fertighaus.de/haeuser/selection-e-175-entwurf-3/) |
| 55 | <img src="house-55/schwabenhaus_solitaire-bungalow01_110e3-exterior.original.avif" width="100"> | Schwabenhaus | Solitaire Bungalow B110 Entwurf 3 | 110 m² | — | 1 | auf Anfrage | — | Bungalow | — | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaire-bungalow/) |

---

## Detail Pages

### House 1 — Hanse Haus · Variant 35-172

![exterior](house-1/hanse_variant35172_exterior10.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Hanse Haus |
| Model | Variant 35-172 |
| Living area | 156 m² |
| Rooms | 7 |
| Floors | 1.5 |
| Price | €448,014 (schlüsselfertig) |
| Energy standard | Effizienzhaus 40 Plus |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Roof | Satteldach |
| PDF | [house-1.pdf](house-1.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/hanse-variant35172/) |

---

### House 2 — hebelHAUS · EFH Klassik 118

![exterior](house-2/hebel_efh-klassik118_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | hebelHAUS |
| Model | EFH Klassik 118 |
| Living area | 118 m² |
| Rooms | 5 |
| Floors | 1.5 |
| Price | auf Anfrage |
| Energy standard | Effizienzhaus 55 (auch 40 möglich) |
| Building type | Einfamilienhaus |
| Construction | Massivhaus |
| PDF | [house-2.pdf](house-2.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/hebel-efh-klassik118/) |

---

### House 3 — Plan-Concept Massivhaus · Paulik

![exterior](house-3/plan-concept_paulik_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Plan-Concept Massivhaus |
| Model | Paulik |
| Living area | 123 m² |
| Rooms | 5 |
| Floors | 1.5 |
| Price | auf Anfrage |
| Energy standard | Effizienzhaus 55 |
| Building type | Einfamilienhaus |
| Construction | Massivhaus |
| Roof | Satteldach |
| Dimensions | 10.12 m × 9.07 m |
| PDF | [house-3.pdf](house-3.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/plan-concept-paulik/) |

---

### House 4 — Bau-Fritz · Mehrfamilienhaus Herndl

![exterior](house-4/baufritz-mfh_herndl_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Bau-Fritz |
| Model | Mehrfamilienhaus Herndl |
| Living area | 237 m² |
| Units | 3 residential units |
| Floors | 2.5 |
| Price | €1,035,000 (schlüsselfertig) |
| Energy standard | Effizienzhaus 40 |
| Building type | Mehrfamilienhaus |
| Construction | Fertighaus (Holzbau) |
| Roof | Satteldach (35°) |
| PDF | [house-4.pdf](house-4.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/baufritzmfh-herndl/) |

---

### House 5 — EBH Haus · Bungalow F88-130 Var. Holz

![exterior](house-5/ebh_f88-130holz_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | EBH Haus |
| Model | Bungalow F88-130 Var. Holz |
| Living area | 107 m² |
| Rooms | 3 |
| Floors | 1 |
| Price | €277,800 (schlüsselfertig) |
| Energy standard | Effizienzhaus 55 (auch 40 möglich) |
| Building type | Einfamilienhaus / Bungalow |
| Construction | Fertighaus |
| Roof | Flachdach |
| PDF | [house-5.pdf](house-5.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/ebh-bungalowf88-130holz/) |

---

### House 6 — Schwabenhaus · SmartSpace DH

![exterior](house-6/schwabenhaus_smartspace-dh04_111e3_exterior.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | SmartSpace Doppelhäuser (111e1 – 111e5) |
| Living area | 111–142 m² (134 m² Ø) |
| Floors | 2.5 |
| Price | ab €308,171 (schlüsselfertig) |
| Energy standard | Effizienzhaus 40 |
| Building type | Doppelhaus |
| Construction | Fertighaus |
| Roof | Satteldach |
| PDF | [house-6.pdf](house-6.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-smartspacedh/) |

---

### House 7 — Danhaus Deutschland · Kopenhagen

![exterior](house-7/kopenhagen_exterior_01.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Danhaus Deutschland |
| Model | Kopenhagen |
| Living area | 182 m² |
| Rooms | 8 |
| Floors | 1.5 |
| Price | €523,810 (schlüsselfertig) |
| Energy standard | Effizienzhaus 55 |
| Building type | Zweifamilienhaus (mit Einliegerwohnung) |
| Construction | Fertighaus |
| Roof | Satteldach (40°) |
| PDF | [house-7.pdf](house-7.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/kopenhagen-mit-einliegerwohnung/) |

---

### House 8 — WEIOTT-Massiv-Haus · Bunganlow 353

![exterior](house-8/weiott_bungalow353_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | WEIOTT-Massiv-Haus |
| Model | Bunganlow 353 |
| Living area | 127 m² |
| Rooms | 4 |
| Floors | 1 |
| Price | auf Anfrage |
| Energy standard | Effizienzhaus 55 |
| Building type | Bungalow |
| Construction | Massivhaus |
| Roof | Walmdach (32°) |
| Dimensions | 20.04 m × 13.86 m |
| PDF | [house-8.pdf](house-8.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/weiott-bun353/) |

---

### House 9 — invivo haus · EFH Bauhaus 115

![exterior](house-9/invivo-bauhaus115_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | invivo haus |
| Model | EFH Bauhaus 115 |
| Living area | 188 m² |
| Rooms | 4 |
| Floors | 2 |
| Price | ab €533,895 |
| Energy standard | Effizienzhaus 55 |
| Building type | Einfamilienhaus |
| Construction | Massivhaus |
| PDF | [house-9.pdf](house-9.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/invivo-bauhaus115/) |

---

### House 10 — Baudirekt Architektenhäuser · DH 120 Basis

![exterior](house-10/baudirekt_dh120_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Baudirekt Architektenhäuser |
| Model | DH 120 Basis |
| Living area | 119 m² |
| Rooms | 4 |
| Floors | 1.5 |
| Price | auf Anfrage |
| Energy standard | Effizienzhaus 55 |
| Building type | Doppelhaus |
| Construction | Massivhaus |
| PDF | [house-10.pdf](house-10.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/baudirekt-doppelhaus-dh-120-basis/) |

---

### House 11 — Rems-Murr-Holzhaus · Grombach

![exterior](house-11/grombach_exterior_0.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Rems-Murr-Holzhaus |
| Model | Grombach |
| Living area | 115 m² |
| Rooms | 5 |
| Floors | 1 |
| Price | €318,397 |
| Energy standard | Effizienzhaus 55 |
| Building type | Einfamilienhaus / Blockhaus |
| Roof | Satteldach (20°) |
| Note | Barrierefrei / rollstuhlgerecht |
| PDF | [house-11.pdf](house-11.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/grombach/) |

---

### House 12 — Dammann-Haus · ISA

![exterior](house-12/dammann_isa_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Dammann-Haus |
| Model | Isa (KfW-Effizienzhaus 40 EE) |
| Living area | 116 m² |
| Rooms | 7 |
| Floors | 1.5 |
| Price | €297,700 (schlüsselfertig) |
| Energy standard | Effizienzhaus 40 EE |
| Building type | Einfamilienhaus |
| Construction | Fertighaus (Holzrahmenbau) |
| Roof | Satteldach (45°) |
| PDF | [house-12.pdf](house-12.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/isa-3-kfw-effizienzhaus-55/) |

---

### House 13 — Fingerhut Haus · Bad Vilbel Musterhaus NEU

![exterior](house-13/fingerhut_mhbadvilbel1_exterior3.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Fingerhut Haus |
| Model | Bad Vilbel – Musterhaus NEU |
| Living area | 205 m² |
| Rooms | 6 |
| Floors | 1.5 |
| Price | €593,766 (schlüsselfertig) |
| Energy standard | Effizienzhaus 55 (bis Plusenergiehaus möglich) |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Dimensions | 13.5 m × 11.7 m |
| PDF | [house-13.pdf](house-13.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/fingerhuthaus-mhbadvilbel1/) |

---

### House 14 — Holzbau Rustikal · Blockhaus Bodensee

![exterior](house-14/holzbau-rustikal_bh-bodensee_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Holzbau Rustikal – Blockhäuser |
| Model | Blockhaus Bodensee |
| Living area | 151 m² |
| Rooms | 6 |
| Floors | 1 |
| Price | €182,558 (Bausatzhaus) |
| Energy standard | Effizienzhaus 55 |
| Building type | Doppelhaus (Blockhaus) |
| Roof | Satteldach |
| Note | Zwei identische Einheiten, Walmdach, geeignet für Ferienvermietung |
| PDF | [house-14.pdf](house-14.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/holzbau-rustikal-blockhhaus-bodensee/) |

---

### House 15 — KBS Bau · Illner

![exterior](house-15/kbs_illner_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | KBS Bau |
| Model | Illner |
| Living area | 162 m² |
| Floors | 2 |
| Price | auf Anfrage |
| Energy standard | Effizienzhaus 55 |
| Building type | Einfamilienhaus |
| Construction | Massivhaus |
| PDF | [house-15.pdf](house-15.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/kbs-illner/) |

---

### House 16 — KBS Bau · Nolte

![exterior](house-16/kbs_nolte_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | KBS Bau |
| Model | Nolte |
| Living area | 200 m² |
| Floors | 2 |
| Price | auf Anfrage |
| Energy standard | Effizienzhaus 55 |
| Building type | Einfamilienhaus |
| Construction | Massivhaus |
| PDF | [house-16.pdf](house-16.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/kbs-nolte/) |

---

### House 17 — Elbe-Haus BauinformationsZentrum Dresden · IL-6-143

![exterior](house-17/mh_il-6-143_1876_exterior.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Elbe-Haus BauinformationsZentrum Dresden |
| Model | IL-6-143 |
| Living area | 135 m² |
| Rooms | 6 |
| Floors | 2 |
| Price | auf Anfrage |
| Energy standard | Effizienzhaus 55 |
| Building type | Massivhaus |
| Roof | Walmdach |
| PDF | [house-17.pdf](house-17.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/elbe-haus-ost-il-6-143/) |

---

### House 18 — Fertighaus WEISS · MH STYLE

![exterior](house-18/mhstyle_exterior2.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Fertighaus WEISS |
| Model | MH STYLE |
| Living area | 220 m² |
| Rooms | 4 |
| Floors | 2 |
| Price | €609,419 (schlüsselfertig) |
| Energy standard | Effizienzhaus 55 / Plusenergiehaus |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Roof | Versetztes Pultdach (25°) |
| PDF | [house-18.pdf](house-18.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/style-sonnenenergiehaus/) |

---

### House 19 — MYMassivhaus · Einfamilienhaus Brand

![exterior](house-19/mymassivhaus_efh-brand_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | MYMassivhaus |
| Model | Einfamilienhaus Brand |
| Living area | 190 m² |
| Rooms | 5 |
| Floors | 2.5 |
| Price | auf Anfrage |
| Energy standard | Effizienzhaus 55 |
| Building type | Einfamilienhaus |
| Construction | Massivhaus |
| Roof | Satteldach |
| PDF | [house-19.pdf](house-19.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/mymassiv-efhbrand/) |

---

### House 20 — Schneider Massivhaus · Stadtvilla TOP-Line 1

![exterior](house-20/schneider-mh_sv-topline1_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schneider Massivhaus |
| Model | Stadtvilla TOP-Line 1 |
| Living area | 125 m² |
| Rooms | 5 |
| Floors | 2 |
| Price | auf Anfrage |
| Energy standard | Effizienzhaus 55 (auch 40 / 40 Plus möglich) |
| Building type | Einfamilienhaus |
| Construction | Massivhaus |
| PDF | [house-20.pdf](house-20.pdf) |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schneider-massivhaus-sv-topline1/) |

---

### House 24 — Nurda · Friesenhaus FS168

![exterior](house-24/nurda_friesenhaus-fs168_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Nurda |
| Model | Friesenhaus FS168 |
| Living area | 168 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Style | Landhausstil |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/nurda-friesenhausfs168/) |

---

### House 25 — Nurda · Landhaus LA146

![exterior](house-25/nurda_landhaus-la146_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Nurda |
| Model | Landhaus LA146 |
| Living area | 146 m² |
| Building type | Landhaus |
| Construction | Fertighaus |
| Style | Landhausstil |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/nurda-landhausla146/) |

---

### House 26 — Nurda · Stadtvilla S150

![exterior](house-26/nurda_stadthaus-s150_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Nurda |
| Model | Stadtvilla S150 |
| Living area | 150 m² |
| Building type | Stadtvilla |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/nurda-stadtvillas150/) |

---

### House 27 — Nurda · Bauhaus B180

![exterior](house-27/nurda_bauhaus-b180_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Nurda |
| Model | Bauhaus B180 |
| Living area | 180 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Style | Bauhaus / Kubus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/nurda-bauhausb180/) |

---

### House 28 — Schwabenhaus · Solitaire B110 Entwurf 6

![exterior](house-28/schwabenhaus_solb110e6_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire B110 Entwurf 6 |
| Living area | 110 m² |
| Floors | 1 |
| Building type | Bungalow |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeireb110e6/) |

---

### House 29 — Schwabenhaus · Solitaire Generationen

![exterior](house-29/schwabenhaus_solitaire-gen01_179e2-exterior.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire Generationen |
| Living area | 179 / 182 / 191 / 200 m² (4 Varianten) |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Note | Mehrgenerationenhaus in vier Größen (je Entwurf 2) |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaire-generationen/) |

---

### House 30 — Schwabenhaus · Sensation E133 Entwurf 1

![exterior](house-30/schwabenhaus_sene133e1_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Sensation E133 Entwurf 1 |
| Living area | 133 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-sensatione133e1/) |

---

### House 31 — Schwabenhaus · Sensation E133 Entwurf 4

![exterior](house-31/schwabenhaus_sene133e4_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Sensation E133 Entwurf 4 |
| Living area | 133 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-sensatione133e4/) |

---

### House 32 — Schwabenhaus · SmartSpace E140 Entwurf 3

![exterior](house-32/schwabenhaus_smartspace140-e3_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | SmartSpace E140 Entwurf 3 |
| Living area | 140 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-smartspace-e140e3/) |

---

### House 33 — Schwabenhaus · Solitaire E165 Entwurf 4

![exterior](house-33/schwabenhaus_solitaeree165-e4_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire E165 Entwurf 4 |
| Living area | 165 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-165-entwurf-4/) |

---

### House 34 — Schwabenhaus · Sensation E132 Entwurf 1

![exterior](house-34/schwabenhaus_sene132e1_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Sensation E132 Entwurf 1 |
| Living area | 132 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-sensatione132e1/) |

---

### House 35 — Schwabenhaus · SmartSpace E140 Entwurf 1

![exterior](house-35/schwabenhaus_smartspace140-e1_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | SmartSpace E140 Entwurf 1 |
| Living area | 140 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-smartspace-e140e1/) |

---

### House 36 — Schwabenhaus · Selection E169 Entwurf 1

![exterior](house-36/schwabenhaus_selection169-e1_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Selection E169 Entwurf 1 |
| Living area | 169 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/selection-e-169-entwurf-1/) |

---

### House 37 — Schwabenhaus · Solitaire B130 Entwurf 1

![exterior](house-37/schwabenhaus_solb130e1_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire B130 Entwurf 1 |
| Living area | 130 m² |
| Floors | 1 |
| Building type | Bungalow |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeireb130e1/) |

---

### House 38 — Schwabenhaus · Solitaire B130 Entwurf 4

![exterior](house-38/schwabenhaus_solb130e4_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire B130 Entwurf 4 |
| Living area | 130 m² |
| Floors | 1 |
| Building type | Bungalow |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeireb130e4/) |

---

### House 39 — Schwabenhaus · Solitaire B150 Entwurf 5

![exterior](house-39/schwabenhaus_solb150e5_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire B150 Entwurf 5 |
| Living area | 150 m² |
| Floors | 1 |
| Building type | Bungalow |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeireb150e5/) |

---

### House 40 — Schwabenhaus · Solitaire E165 Entwurf 3

![exterior](house-40/schwabenhaus_solitaeree165-e3_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire E165 Entwurf 3 |
| Living area | 165 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-165-entwurf-3/) |

---

### House 41 — Schwabenhaus · Selection E175 Entwurf 4

![exterior](house-41/schwabenhaus_sel175e4_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Selection E175 Entwurf 4 |
| Living area | 175 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/selection-e-175-entwurf-4/) |

---

### House 42 — Schwabenhaus · Sensation E133 Entwurf 6

![exterior](house-42/schwabenhaus_sene133e6_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Sensation E133 Entwurf 6 |
| Living area | 133 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-sensatione133e6/) |

---

### House 43 — Schwabenhaus · SmartSpace E120 Entwurf 1

![exterior](house-43/schwabenhaus_smartspace120-e1_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | SmartSpace E120 Entwurf 1 |
| Living area | 120 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-smartspace-e120e1/) |

---

### House 44 — Schwabenhaus · Solitaire E145 Entwurf 2

![exterior](house-44/schwabenhaus_solitaeree145-e2_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire E145 Entwurf 2 |
| Living area | 145 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-145-entwurf-2/) |

---

### House 45 — Schwabenhaus · Solitaire E165 Entwurf 7

![exterior](house-45/schwabenhaus_solitaeree165-e7_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire E165 Entwurf 7 |
| Living area | 165 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-165-entwurf-7/) |

---

### House 46 — Schwabenhaus · Solitaire E155 Entwurf 9

![exterior](house-46/schwabenhaus_solitaeree155-e9_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire E155 Entwurf 9 |
| Living area | 155 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeiree155e9/) |

---

### House 47 — Schwabenhaus · Selection E169 Entwurf 5

![exterior](house-47/schwabenhaus_selection169-e5_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Selection E169 Entwurf 5 |
| Living area | 169 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/selection-e-169-entwurf-5/) |

---

### House 48 — Schwabenhaus · Selection E175 Entwurf 5

![exterior](house-48/schwabenhaus_sel175e5_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Selection E175 Entwurf 5 |
| Living area | 175 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/selection-e-175-entwurf-5/) |

---

### House 49 — Schwabenhaus · Solitaire E155 Entwurf 7

![exterior](house-49/schwabenhaus_solitaeree155-e7_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire E155 Entwurf 7 |
| Living area | 155 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeiree155e7/) |

---

### House 50 — Schwabenhaus · Solitaire E145 Entwurf 5

![exterior](house-50/schwabenhaus_solitaeree145-e5_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire E145 Entwurf 5 |
| Living area | 145 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-145-entwurf-5/) |

---

### House 51 — Schwabenhaus · Sensation E132 Entwurf 4

![exterior](house-51/schwabenhaus_sene132e4_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Sensation E132 Entwurf 4 |
| Living area | 132 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-sensatione132e4/) |

---

### House 52 — Schwabenhaus · Solitaire B110 Entwurf 3

![exterior](house-52/schwabenhaus_solb110e3_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire B110 Entwurf 3 |
| Living area | 110 m² |
| Floors | 1 |
| Building type | Bungalow |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaeireb110e3/) |

---

### House 53 — Schwabenhaus · Solitaire E145 Entwurf 7

![exterior](house-53/schwabenhaus_solitaeree145-e7_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire E145 Entwurf 7 |
| Living area | 145 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/solitaire-e-145-entwurf-7/) |

---

### House 54 — Schwabenhaus · Selection E175 Entwurf 3

![exterior](house-54/schwabenhaus_sel175e3_exterior1.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Selection E175 Entwurf 3 |
| Living area | 175 m² |
| Building type | Einfamilienhaus |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/selection-e-175-entwurf-3/) |

---

### House 55 — Schwabenhaus · Solitaire Bungalow B110 Entwurf 3

![exterior](house-55/schwabenhaus_solitaire-bungalow01_110e3-exterior.original.avif)

| Field | Value |
|-------|-------|
| Manufacturer | Schwabenhaus |
| Model | Solitaire Bungalow B110 Entwurf 3 |
| Living area | 110 m² |
| Floors | 1 |
| Building type | Bungalow |
| Construction | Fertighaus |
| Price | auf Anfrage |
| Source | [fertighaus.de](https://www.fertighaus.de/haeuser/schwabenhaus-solitaire-bungalow/) |
