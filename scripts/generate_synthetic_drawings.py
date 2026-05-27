#!/usr/bin/env python3
"""Generate synthetic architectural drawings for every house in bim-database.

Why: bootstrap a labeled training corpus for a supervised vision model that
should recognize architectural elements in technical-paper drawings. Each
synthetic drawing is loosely tied to a real house (so the AI has a sane
content prior) but is not a faithful reproduction — the model imagines
occluded sides, and the artifacts are explicitly meant to be reviewed +
manually labeled afterwards.

Generation order, per house, is *sequential and cumulative*: each new
drawing is generated with the previously-produced drawings of the same
house included as additional reference images. This anchors cross-view
consistency — the south elevation can see the north, the east can see
both, and floor plans see every elevation so window/door openings line up
with facade rhythm.

For floorplans, after generation we ask a vision model to extract the
dimension annotations along each axis and verify they sum consistently.
If not, we regenerate (up to 3 attempts) with feedback about which axis
didn't add up.

Style references: real scanned drawings from h21, h22, h23. A pair is
chosen *once per house* (not per drawing) so every drawing of a given
fake house shares the same style anchor; the level-of-detail preset is
likewise fixed per-house. Between houses, both vary.

Content references: each target house's own images (AVIFs / catalog
photos / floorplan scans).

Output: data/synthetic/<key>/<file>.png + manifest.json per house.

Resumable: skips targets that already exist on disk; on rate limits,
sleeps and continues; on API errors, logs and continues with the next
target. Floorplan verification calls are also resumable — once a
floorplan is on disk we don't re-verify on a subsequent run.

Run:
    python scripts/generate_synthetic_drawings.py              # all houses
    python scripts/generate_synthetic_drawings.py house-1      # one house
    python scripts/generate_synthetic_drawings.py --dry-run    # plan only
    python scripts/generate_synthetic_drawings.py --kind elevation  # only elevations
    python scripts/generate_synthetic_drawings.py --kind floorplan  # only floorplans
    python scripts/generate_synthetic_drawings.py --no-verify  # skip the
                                  # floorplan-consistency verification pass

Requires OPENAI_API_KEY in .env at the repo root.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from api.scene_render import HOUSES_DIR, render_scene  # noqa: E402

DATASET_DIR = REPO / "data" / "dataset"
# Back-compat alias for any external code that imports the old name.
SYNTHETIC_DIR = DATASET_DIR
PREPARED_DIR = REPO / "tmp" / "synthetic-prepared"
PROMPT_INDEX = DATASET_DIR / "prompt_index.jsonl"
DIFFICULT_HOUSES_LOG = DATASET_DIR / "difficult_houses.jsonl"

# A house is flagged "difficult" if at least this fraction of its drawings
# fail the dimension-sum verification even after all retry attempts. Flagged
# houses are NOT skipped — they're recorded for downstream filtering of the
# training corpus.
DIFFICULT_HOUSE_FAIL_RATIO = 0.5

# Houses that DON'T get synthetic drawings — they're the style references.
SKIP_KEYS = {"house-21", "house-22", "house-23"}

# Style-ref pools: (house_key, scene_file). A pair is chosen ONCE per fake
# house — not per drawing — so a fake house's full set shares one style anchor.
STYLE_REFS_ELEVATION: list[tuple[str, str]] = [
    ("house-21", "house-21-elevation-tal.jpg"),
    ("house-21", "house-21-elevation-berg.jpg"),
    ("house-21", "house-21-elevation-linke-giebel.jpg"),
    ("house-22", "house-22-elevation-sued.jpg"),
    ("house-22", "house-22-elevation-nord.jpg"),
    ("house-22", "house-22-elevation-west.jpg"),
    ("house-23", "house-23-elevation-strasse.jpg"),
    ("house-23", "house-23-elevation-garten.jpg"),
]
STYLE_REFS_FLOORPLAN: list[tuple[str, str]] = [
    ("house-21", "house-21-floorplan-eg-detail.jpg"),
    ("house-21", "house-21-floorplan-dg-detail.jpg"),
    ("house-22", "house-22-floorplan-eg.jpg"),
    ("house-22", "house-22-floorplan-dg.jpg"),
    ("house-23", "house-23-floorplan-eg.jpg"),
    ("house-23", "house-23-floorplan-og.jpg"),
]

# Level-of-detail presets. Drawn once at house start, fixed across that house's
# drawings (so a "very-detailed" h-5 stays detailed across all six outputs).
PRESETS = ["standard", "detailed", "very-detailed"]
# Bias toward detailed — better training data, and the model's "minimal" tends
# to look impoverished anyway.
PRESET_WEIGHTS = [0.15, 0.45, 0.40]

DETAIL_INSTRUCTIONS = {
    "standard": "",
    "detailed": (
        "Add restrained technical annotations: a Bezugshöhe ground line, "
        "First-höhe (ridge) and Traufhöhe (eave) labels with numeric heights, "
        "two or three dimension chains along the facade, a German title block "
        "at the bottom. Hand-lettered numerals."
    ),
    "very-detailed": (
        "Include rich technical annotations as a 1950s/60s Bauplan would: "
        "Bezugshöhe ground line, First (ridge), Traufe (eave), OK FFB "
        "(Oberkante Fertigfußboden) per storey, multiple dimension chains "
        "with German millimetre notations like '1,75' or '2.625', visible "
        "Maßstab 1:100 label, hatching for materials (mauerwerk, putz), "
        "tiny architect's signature block at the corner. All hand-lettered."
    ),
}

# ── per-house "nuance traits" ────────────────────────────────────────────────
# Each house draws ONE option from each pool with the per-house RNG, so the
# six drawings of a house feel like one architect's work, while two
# different houses look like two different architects working in two
# different decades on two different desks.
#
# We deliberately spread the trait-space wide (lettering, paper, capture
# method, line weight, persona) plus a per-drawing "twist" so the dataset
# doesn't collapse into one visual mode. The chosen profile is logged into
# data/synthetic/prompt_index.jsonl so we can scan for accidental repetition.

LETTERING_STYLES = [
    "neat upright architect's print, all uppercase, evenly spaced",
    "slanted draftsman's cursive, slightly inconsistent x-height",
    "blocky upright stencil-style lettering with thick uniform strokes",
    "scrappy engineer's print, a little hurried, slightly uneven baseline",
    "fine italic technical hand, light pressure, narrow letters",
    "rounded hand-printed letters, slightly childlike but legible",
    "tall narrow capitals, faded ink, occasional double-stroked letter",
    "mixed case hand-printing, lowercase for notes and uppercase for titles",
]
LETTERING_WEIGHTS = [0.16, 0.16, 0.12, 0.16, 0.12, 0.10, 0.10, 0.08]

PAPER_CONDITIONS = [
    "clean archive paper, faintly yellowed by age",
    "ivory-toned drafting paper with a soft uniform tone",
    "cream tracing paper with visible weave and slight translucency",
    "slightly water-stained at one edge, otherwise crisp",
    "tea-toned where it sat in light, white in shadow",
    "fresh white drafting vellum, almost no aging",
    "heavily creased paper with two perpendicular fold lines visible",
    "blueprint-tinged cyanotype-like paper, very faint blue cast",
]
PAPER_WEIGHTS = [0.18, 0.18, 0.14, 0.10, 0.10, 0.14, 0.10, 0.06]

# Capture methods — about 50% give a noticeable photographed-paper feel
# (slight / strong tilt / overhead-phone), so the dataset isn't all
# perfectly-square scans. Compare to data/houses/house-23/*.jpg which were
# all phone-photographed from a folded paper plan and show this look.
CAPTURE_METHODS = [
    ("flatbed_scan",
     "flatbed-scanned at high resolution, perfectly square to the page, "
     "even lighting, no perspective"),
    ("photo_slight_tilt",
     "photographed on a desk at a slight angle (about 1-2 degrees rotation), "
     "warm desk-lamp lighting from upper-left, one faint horizontal "
     "paper-fold shadow band crossing the image, white desk visible at the "
     "page edges"),
    ("photo_strong_tilt",
     "photographed handheld, paper visibly tilted by 2-3 degrees clockwise, "
     "one diagonal fold-crease shadow running across the sheet, slight "
     "perspective so the far edge of the page is fractionally smaller than "
     "the near edge, a corner of the page slightly curled up"),
    ("photo_overhead_phone",
     "photographed overhead with a phone camera, very slight tilt (about 1 "
     "degree), one prominent horizontal fold shadow across the middle of "
     "the page, faint diffuse shadow at one corner where the photographer's "
     "hand or phone hovered, slight chromatic warmth"),
    ("aged_scan",
     "old institutional scan, slight skew under 1 degree, some dust speckle, "
     "slightly washed-out contrast, faint scanner-bar streaks"),
    ("photo_folded_plan",
     "photograph of a plan that has been folded into eighths and unfolded "
     "again — clear cross-shaped fold creases divide the page into a grid "
     "of rectangles, with darker shadow bands along each crease line and "
     "the sheet rotated 1-2 degrees off horizontal in the photo"),
]
CAPTURE_WEIGHTS = [0.30, 0.18, 0.12, 0.18, 0.10, 0.12]

LINE_WEIGHT_OPTIONS = [
    "delicate fine 0.3 mm linework throughout, almost spidery",
    "medium-weight inked lines, occasionally a heavier outline at building edges",
    "heavy-pressed pencil lines, occasionally slightly smudged",
    "mixed weights — bold outlines for the building envelope, fine internal hatching",
    "uniform 0.5 mm rapidograph ink, mechanically consistent",
]
LINE_WEIGHT_WEIGHTS = [0.18, 0.32, 0.18, 0.22, 0.10]

ARCHITECT_PERSONAS = [
    ("sparse-modernist",
     "Sparse minimal annotations; only essential dimension chains and labels. "
     "The drawing speaks for itself; lots of clean paper around the building."),
    ("verbose-classicist",
     "Densely annotated with multiple dimension chains, material callouts on "
     "every wall surface, small explanatory side-notes around the building."),
    ("nordic-clean",
     "Restrained Nordic-school annotations; clean tightly-spaced numerals; "
     "very little redundant chain; a thin north arrow if a plan view."),
    ("postwar-german",
     "Postwar-German Bauplan rigor — material hatchings labeled, OK-FFB lines "
     "per storey, small German abbreviations everywhere (OK, UK, FFB, RFB)."),
    ("hurried-engineer",
     "Slightly hurried draftsmanship — one or two dimension values look "
     "hand-corrected (small overstrike or arrow correction). Still fully legible."),
    ("1970s-precision",
     "1970s technical-school precision — exacting linework, sober lettering, "
     "small project-number stamp in the lower corner."),
]
ARCHITECT_WEIGHTS = [0.18, 0.20, 0.16, 0.22, 0.12, 0.12]

# Per-drawing "twists" — picked per drawing (not per house) so each sheet has
# one small unique detail. Across many sheets these should mostly not repeat,
# which is the whole point of the index file: we can grep for over-used twists.
TWISTS = [
    "a small red 'GEPRÜFT' stamp in one corner",
    "a blue ballpoint margin note in the right margin in German cursive",
    "two parallel hand-erased pencil marks faintly visible under the inked linework",
    "a coffee-cup ring stain on one corner of the page",
    "a paperclip indentation along the top edge",
    "a thumbprint smudge near the title block",
    "a tiny architect's stamp/seal in the title block",
    "a numbered file-folder sticker on one corner",
    "punched binder holes along the left edge",
    "subtle bluish print-through from a sheet underneath",
    "yellowed adhesive-tape repair across one fold",
    "a folded-over dog-eared corner",
    "a tiny pencil-corrected dimension figure in one segment, original lightly erased",
    "a marginal pencil scale-bar drawn at the bottom of the sheet",
    "a fine red revision-cloud around one window or door",
    "a small archivist's catalog number stamped in the lower corner",
    "a graphite smudge from the draftsman's wrist along the bottom edge",
    "a small north-arrow with 'N' marked even if slightly out of place on an elevation",
    "a wax-pencil revision tick mark in the margin",
    "a tiny faded date '12.03.1968' written in pencil in the corner",
    "a small drafting-tape residue square at one corner",
    "a few scattered pencil-compass pin-holes",
    "a faint typewritten correction strip taped over one note",
    "a hand-drawn key/legend box in the lower-right corner",
    "a tiny rubber-stamp page-number in the corner like 'Bl. 3/12'",
    "a faint orange highlighter sweep across one room label",
]

def pick_nuance_profile(seed: int) -> dict:
    """Deterministically choose one option from each trait pool for this house."""
    rng = random.Random(seed + 13)
    return {
        "lettering": rng.choices(LETTERING_STYLES, weights=LETTERING_WEIGHTS, k=1)[0],
        "paper": rng.choices(PAPER_CONDITIONS, weights=PAPER_WEIGHTS, k=1)[0],
        "capture": rng.choices(CAPTURE_METHODS, weights=CAPTURE_WEIGHTS, k=1)[0],
        "line_weight": rng.choices(LINE_WEIGHT_OPTIONS, weights=LINE_WEIGHT_WEIGHTS, k=1)[0],
        "persona": rng.choices(ARCHITECT_PERSONAS, weights=ARCHITECT_WEIGHTS, k=1)[0],
    }


def pick_twist(seed: int, target_filename: str) -> str:
    """One twist per drawing — deterministic from house seed + filename."""
    rng = random.Random(f"{seed}-{target_filename}")
    return rng.choice(TWISTS)


def render_nuance_clause(profile: dict, twist: str) -> str:
    """Inject the nuance profile + twist into a text block the prompt can include."""
    capture_id, capture_desc = profile["capture"]
    persona_id, persona_desc = profile["persona"]
    return (
        "Per-sheet handcraft and capture (this house's signature look — keep CONSISTENT across all this house's drawings):\n"
        f"- Lettering: {profile['lettering']}. Do NOT default to a clean uniform CAD-style font — the lettering must look hand-made.\n"
        f"- Linework: {profile['line_weight']}.\n"
        f"- Paper: {profile['paper']}.\n"
        f"- Capture: {capture_desc}.\n"
        f"- Draftsman personality: {persona_desc}\n"
        f"Per-sheet twist (unique to THIS sheet): {twist}.\n"
        "Important: avoid the 'AI-generated technical drawing' aesthetic — no perfectly even letterforms, no synthetic paper texture. The image should look like a photograph or scan of a physical plan drawn by hand."
    )


VERIFY_MODEL = os.getenv("BIM_SYNTHETIC_VERIFY_MODEL", "gpt-4o")
MODEL = os.getenv("BIM_SYNTHETIC_MODEL", "gpt-image-2")
MAX_STYLE = 2
MAX_CONTENT = 6
MAX_PRIOR = 4
MAX_TOTAL_IMAGES = 16  # OpenAI per-request cap

PROMPT_ELEVATION = """\
You are creating a fake but highly convincing architectural technical paper drawing.

Task:
Create a single orthographic architectural elevation drawing of the house shown
in the house reference images. The drawing must be a straight-on elevation view
— not isometric, not perspective.

View: {view_label}

House reference images:
- Preserve the architectural character, roof geometry, massing, window
  proportions, facade materials, chimney, dormers, balconies, terraces,
  and visible details from the photos and floorplan references.
- If the requested elevation is not fully visible in the photos, infer a
  plausible elevation that is architecturally consistent with what IS visible.
- Do not invent a completely different house.

{prior_context}

Use the technical-drawing reference images only as STYLE reference (not content):
- monochrome pencil / graphite / ink linework on paper
- hand-drawn but technically precise
- old-school architectural elevation drawing
- realistic photographed/scanned-paper look (NOT a digital "old paper" filter)

{nuance_clause}

{detail_instructions}

CRITICAL — dimension chains must reflect actual features:
- Every dimension SEGMENT (the spacing between two tick marks) must
  correspond to a real feature on the facade you're drawing: a wall
  section, the WIDTH of a specific door, the WIDTH of a specific window,
  the gap BETWEEN two features.
- Tick marks belong at feature EDGES — door jambs, window reveals, wall
  corners — never in the middle of a window or door.
- Value plausibility: front door 0.90-1.10 m, standard window 0.80-1.50 m,
  picture window 1.50-3.00 m. Two visually-identical windows must carry
  the same dimension label.
- Sums must check: segments along the bottom must equal the overall
  building width. Storey heights along the side must equal the overall
  height.

{feedback_hint}

Output requirements:
- one single sheet of paper, centered building elevation
- no isometric angle, no dramatic perspective
- no photorealistic colored render
- no blue sky, no trees, no garden photo background
- no people unless tiny scale figures are part of the drawing style
- include a hand-lettered title near the bottom: "{title_text}"
- looks like it came from an architect's physical paper drawing archive

The result should look like a real paper drawing scanned from an old plan set,
NOT a modern CAD export.
"""

PROMPT_FLOORPLAN = """\
You are creating a fake but highly convincing architectural floorplan drawing.

Task:
Create a single top-down orthographic floor plan of the house shown in the
house reference images, for the floor labeled "{floor_label}".

House reference images:
- Match the building's overall massing / footprint as best you can infer.
- Reasonable room layout given visible windows and doors.
- Walls, doors, dimension chains, room labels, stair runs.

{prior_context}

Use the technical-drawing reference images only as STYLE reference (not content):
- monochrome pencil / graphite / ink linework on paper
- hand-drawn but technically precise
- old-school architectural floor plan style
- thick outer walls, thin partitions
- room name + area annotations in German (Wohnzimmer, Schlafzimmer, Küche,
  Bad, Flur, Diele, Eltern, Kind, …) where layout warrants
- light dimension chains along the outside
- realistic photographed/scanned-paper look (NOT a digital "old paper" filter)

{nuance_clause}

{detail_instructions}

CRITICAL — dimensions must add up:
- All dimension annotations along each axis must be internally consistent.
- The sum of the segments along the top edge must equal the overall building width.
- The sum of the segments along the left edge must equal the overall building depth.
- Use German decimal notation (1,75 or 1.750 for 1.75 m). Stay in the metric
  family — millimetres or metres, consistent within the drawing.
- If you draw an overall dimension chain plus inner segment chains, every chain
  must total the same overall length.

{feedback_hint}

Output requirements:
- one single sheet of paper, top-down view
- no perspective, no isometric angle
- include a hand-lettered title near the bottom: "{title_text}"
- looks like it came from an architect's physical paper drawing archive

The result should look like a real paper floorplan scanned from an old plan set,
NOT a modern CAD export.
"""

VERIFY_PROMPT = """\
You are inspecting a synthetic architectural drawing (an elevation OR a
floorplan). Verify TWO things:

A) INTERNAL CONSISTENCY — every dimension chain along each axis must sum to
   the overall labeled total. Inner detail chains and outer overall chains
   must match. Top edge must equal bottom edge. Left edge must equal right.

B) FEATURE-CORRECTNESS — every dimension SEGMENT (the spacing between two
   adjacent tick marks) must:
   • align with a real visible architectural feature (door, window, wall
     section, dormer, balcony, room boundary, storey level)
   • carry a value that's architecturally plausible for what it's labeling

   This is the strict part. Two visually-equal windows must not carry
   different segment labels. A tick mark placed mid-window is wrong — it
   should sit at the feature's edge.

Typical sizes to compare against (residential):
  • Front door:            0.90-1.10 m  (double / French / Terrassen: 1.40-1.80 m)
  • Interior door:         0.70-0.90 m
  • Window, standard:      0.80-1.50 m wide
  • Window, large/picture: 1.50-3.00 m wide
  • Window, narrow/slot:   0.30-0.70 m wide
  • Storey height (residential): 2.40-3.00 m; attic: 2.20-2.70 m
  • Wall section between features: 0.30-2.50 m typically
  • Building footprint:    6-15 m per side for SFH; up to 25 m for Doppelhaus

Tolerances:
  • round to 2 decimal places (1,75 vs 1.75)
  • mm vs m notation (1750 vs 1.75) — both fine
  • <3 % numerical drift on sums

Be strict about:
  • segments that don't sum to overall totals
  • tick marks placed where there's no architectural feature
  • dimension segment values that contradict what they visually measure
    (e.g. a labeled 1,26 in the spot where there's clearly a 0,90 door)
  • two visually-identical windows with different segment labels

Return JSON ONLY:
{
  "consistent": true|false,
  "sums_ok": true|false,
  "features_match": true|false,
  "issues": [
    "<one short string per problem, naming the axis + the offending value>",
    ...
  ],
  "feedback": "<one specific 1-2 sentence instruction for the next attempt>"
}
"""


# ── house + target discovery ─────────────────────────────────────────────────

def list_houses() -> list[Path]:
    return sorted(p for p in HOUSES_DIR.iterdir() if p.is_dir())


def load_house(key: str) -> dict | None:
    p = HOUSES_DIR / key / f"{key}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def list_content_refs(house_dir: Path) -> list[Path]:
    refs: list[Path] = []
    for ext in (".avif", ".png", ".jpg", ".jpeg", ".webp"):
        refs.extend(house_dir.glob(f"*{ext}"))
    refs = [p for p in refs if "-syn-" not in p.name]
    return sorted(refs)


def _floorplan_target(key: str, floor: str) -> dict:
    slug = floor.lower().replace(" ", "").replace(".", "").replace("ö", "oe")
    return {
        "kind": "floorplan",
        "floor": floor,
        "title": f"GRUNDRISS {floor.upper()}",
        "filename": f"{key}-syn-floorplan-{slug}.png",
        "prompt_args": {"floor_label": floor, "title_text": f"GRUNDRISS {floor.upper()}"},
    }


def determine_targets(house: dict, kind_filter: str | None = None) -> list[dict]:
    """Order:
        1. Ground-floor plan (EG, or Hochparterre / first available as fallback)
        2. North, South, East, West elevations
        3. Remaining floorplans in build-up order (KG → UG → … → Spitzboden)

    The EG comes FIRST because it's the single most useful anchor for everything
    else — once we have a footprint with windows and doors, the elevations can
    align their facade rhythm to it, and later floorplans can stack on it.
    """
    key = f"house-{house['id']}"

    order = ["KG", "UG", "Hochparterre", "EG", "1. OG", "2. OG", "3. OG", "DG", "Spitzboden"]
    levels = house.get("levels") or ["EG"]
    ordered_levels = sorted(levels, key=lambda f: order.index(f) if f in order else 999)

    # Pick the "ground" floor: prefer EG, then Hochparterre, then the first
    # non-basement level in build-up order.
    ground = None
    for candidate in ("EG", "Hochparterre"):
        if candidate in ordered_levels:
            ground = candidate
            break
    if ground is None:
        for level in ordered_levels:
            if level not in ("KG", "UG"):
                ground = level
                break
    if ground is None and ordered_levels:
        ground = ordered_levels[0]

    targets: list[dict] = []

    if kind_filter in (None, "floorplan") and ground is not None:
        targets.append(_floorplan_target(key, ground))

    if kind_filter in (None, "elevation"):
        for view, title in [
            ("north", "NORDANSICHT"),
            ("south", "SÜDANSICHT"),
            ("east", "OSTANSICHT"),
            ("west", "WESTANSICHT"),
        ]:
            targets.append({
                "kind": "elevation",
                "view": view,
                "title": title,
                "filename": f"{key}-syn-elevation-{view}.png",
                "prompt_args": {"view_label": title, "title_text": title},
            })

    if kind_filter in (None, "floorplan"):
        for floor in ordered_levels:
            if floor == ground:
                continue
            targets.append(_floorplan_target(key, floor))

    return targets


# ── image preparation ────────────────────────────────────────────────────────

def prepare_image(src: Path, out_name: str) -> Path:
    """Convert any image to a JPG suitable for OpenAI (RGB, ≤2048px). Cached."""
    PREPARED_DIR.mkdir(parents=True, exist_ok=True)
    out = PREPARED_DIR / f"{out_name}.jpg"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    with Image.open(src) as im:
        im = im.convert("RGB")
        max_side = 2048
        w, h = im.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            im = im.resize((int(w * scale), int(h * scale)))
        im.save(out, "JPEG", quality=92)
    return out


def prepare_style_ref(key: str, file: str) -> Path:
    src = render_scene(key, file)
    out_name = f"styleref-{key}-{Path(file).stem}"
    return prepare_image(src, out_name)


def pick_house_style_refs(elev_pool: list[Path], fp_pool: list[Path],
                          elev_meta: list[tuple[str, str]], fp_meta: list[tuple[str, str]],
                          seed_for_house: int) -> tuple[list[Path], list[Path], list[str], list[str]]:
    """Choose 2 elevation + 2 floorplan style refs ONCE per house. Bias toward
    sampling from multiple source houses (h21 + h22 vs two from h21) when possible.

    Returns (elev_paths, fp_paths, elev_descs, fp_descs). The descs are
    'house-21-elevation-tal' style strings used for the manifest."""
    rng = random.Random(seed_for_house)

    def pick(pool: list[Path], meta: list[tuple[str, str]], n: int) -> tuple[list[Path], list[str]]:
        # Group meta by source-house to bias toward cross-house mix.
        by_house: dict[str, list[int]] = defaultdict(list)
        for i, (k, _) in enumerate(meta):
            by_house[k].append(i)
        houses = list(by_house.keys())
        rng.shuffle(houses)
        idxs: list[int] = []
        for h in houses:
            if len(idxs) >= n:
                break
            idxs.append(rng.choice(by_house[h]))
        # Top up if we still need more (shouldn't happen if n <= len(houses))
        while len(idxs) < n:
            cand = rng.randrange(len(meta))
            if cand not in idxs:
                idxs.append(cand)
        paths = [pool[i] for i in idxs[:n]]
        # Source filenames already encode the house (e.g. 'house-21-elevation-tal.jpg'),
        # so the stem alone is the canonical desc.
        descs = [Path(meta[i][1]).stem for i in idxs[:n]]
        return paths, descs

    elev_paths, elev_descs = pick(elev_pool, elev_meta, MAX_STYLE)
    fp_paths, fp_descs = pick(fp_pool, fp_meta, MAX_STYLE)
    return elev_paths, fp_paths, elev_descs, fp_descs


def pick_preset(seed_for_house: int) -> str:
    return random.Random(seed_for_house + 1).choices(PRESETS, weights=PRESET_WEIGHTS, k=1)[0]


# ── manifest I/O ─────────────────────────────────────────────────────────────

def load_manifest(key: str) -> dict:
    p = SYNTHETIC_DIR / key / "manifest.json"
    if not p.exists():
        return {"key": key, "linked_house": key, "drawings": []}
    return json.loads(p.read_text())


def save_manifest(manifest: dict) -> None:
    p = SYNTHETIC_DIR / manifest["key"] / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def update_manifest_entry(manifest: dict, entry: dict) -> None:
    manifest["drawings"] = [d for d in manifest["drawings"] if d["file"] != entry["file"]]
    manifest["drawings"].append(entry)


# ── prior-image context ─────────────────────────────────────────────────────

def describe_prior(d: dict) -> str:
    if d["kind"] == "elevation":
        return f"the {d.get('view')} elevation of this same house ({d.get('title')})"
    if d["kind"] == "floorplan":
        return f"the {d.get('floor')} floorplan of this same house"
    return d["kind"]


def build_prior_context_clause(priors: list[dict]) -> str:
    if not priors:
        return ""
    descs = ", ".join(describe_prior(p) for p in priors)
    return (
        "Cross-view consistency anchors:\n"
        f"In addition to the photos, you are given previously-generated drawings of this same house — {descs}. "
        "Match the building's massing, roof shape, dormer count, window proportions, materials, and facade rhythm "
        "so this new drawing reads as the same building. For floorplans following elevations: ensure the "
        "wall openings (windows, doors, dormers) along each facade align horizontally with what the elevations show."
    )


# ── floorplan dimension verification ────────────────────────────────────────

def verify_drawing(client, png_path: Path) -> tuple[bool, str, dict]:
    """Ask GPT-4o vision to check both dimensional sum consistency AND
    feature-correctness (each segment matches a visible feature with a
    plausible value). Used for both elevations and floorplans.

    Returns (is_consistent, feedback_for_regeneration, full_report_dict)."""
    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    resp = client.chat.completions.create(
        model=VERIFY_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": VERIFY_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False, "Could not parse previous dimensions; use clearer hand-lettered numerals.", {"raw": raw}

    ok = bool(data.get("consistent", False))
    feedback = (data.get("feedback") or "").strip()
    return ok, feedback, data


# ── core generation loop ─────────────────────────────────────────────────────

def call_image_edit(client, prompt: str, image_paths: list[Path], *, size: str) -> bytes:
    handles = [open(p, "rb") for p in image_paths]
    try:
        result = client.images.edit(
            model=MODEL,
            image=handles,
            prompt=prompt,
            size=size,
            quality="high",
            n=1,
        )
        return base64.b64decode(result.data[0].b64_json)
    finally:
        for h in handles:
            h.close()


def append_prompt_index(entry: dict) -> None:
    """Append a single line of JSON to data/synthetic/prompt_index.jsonl."""
    PROMPT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with open(PROMPT_INDEX, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def generate_target(
    client,
    key: str,
    target: dict,
    *,
    style_paths: list[Path],
    content_paths: list[Path],
    prior_meta: list[dict],
    preset: str,
    nuance_profile: dict,
    twist: str,
    feedback_hint: str = "",
    dry_run: bool = False,
) -> tuple[Path | None, str]:
    """Generate one drawing. Returns (output_path_or_None, prompt_text)."""
    out_path = SYNTHETIC_DIR / key / target["filename"]

    prior_paths = [SYNTHETIC_DIR / key / p["filename"] for p in prior_meta
                   if (SYNTHETIC_DIR / key / p["filename"]).exists()]
    prior_paths = prior_paths[:MAX_PRIOR]

    images = (style_paths[:MAX_STYLE]
              + content_paths[:MAX_CONTENT]
              + prior_paths)[:MAX_TOTAL_IMAGES]
    if not images:
        raise RuntimeError(f"{key}: no images available")

    prior_clause = build_prior_context_clause(prior_meta)
    detail_clause = DETAIL_INSTRUCTIONS[preset]
    nuance_clause = render_nuance_clause(nuance_profile, twist)

    feedback_block = (
        f"\nIMPORTANT — previous attempt feedback to fix:\n  {feedback_hint}\n"
        if feedback_hint else ""
    )
    if target["kind"] == "elevation":
        prompt = PROMPT_ELEVATION.format(
            prior_context=prior_clause,
            nuance_clause=nuance_clause,
            detail_instructions=detail_clause,
            feedback_hint=feedback_block,
            **target["prompt_args"],
        )
        size = "1536x1024"
    else:
        prompt = PROMPT_FLOORPLAN.format(
            prior_context=prior_clause,
            nuance_clause=nuance_clause,
            detail_instructions=detail_clause,
            feedback_hint=feedback_block,
            **target["prompt_args"],
        )
        size = "1024x1024"

    if dry_run:
        return None, prompt

    png_bytes = call_image_edit(client, prompt, images, size=size)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png_bytes)
    return out_path, prompt


def process_house(
    client,
    key: str,
    house: dict,
    *,
    args,
    style_elev_pool: list[Path],
    style_fp_pool: list[Path],
    style_elev_meta: list[tuple[str, str]],
    style_fp_meta: list[tuple[str, str]],
) -> None:
    manifest = load_manifest(key)
    manifest["linked_house"] = key
    manifest["model"] = house.get("model")
    manifest["manufacturer"] = house.get("manufacturer")
    manifest["building_type"] = house.get("building_type")

    # Per-house seed: deterministic from the house id so re-runs pick the
    # same style refs + preset. Add args.seed_offset for one-shot resampling.
    seed = (house["id"] * 7919) + args.seed_offset
    style_elev_paths, style_fp_paths, elev_descs, fp_descs = pick_house_style_refs(
        style_elev_pool, style_fp_pool, style_elev_meta, style_fp_meta, seed,
    )
    preset = pick_preset(seed)
    nuance_profile = pick_nuance_profile(seed)
    manifest["preset"] = preset
    manifest["nuance_profile"] = {
        "lettering": nuance_profile["lettering"],
        "paper": nuance_profile["paper"],
        "capture_id": nuance_profile["capture"][0],
        "capture": nuance_profile["capture"][1],
        "line_weight": nuance_profile["line_weight"],
        "persona_id": nuance_profile["persona"][0],
        "persona": nuance_profile["persona"][1],
    }
    manifest["style_refs_house"] = {
        "elevation": elev_descs,
        "floorplan": fp_descs,
    }

    targets = determine_targets(house, kind_filter=args.kind)
    house_keys_used = sorted({d.split("-")[0] + "-" + d.split("-")[1]
                              for d in elev_descs + fp_descs})
    print(f"\n{key}: {(house.get('model') or '?')[:60]}  "
          f"preset={preset}  capture={nuance_profile['capture'][0]}  "
          f"persona={nuance_profile['persona'][0]}  refs={'+'.join(house_keys_used)}  "
          f"({len(targets)} targets)")

    house_dir = HOUSES_DIR / key
    content_sources = list_content_refs(house_dir)[:MAX_CONTENT]
    if not content_sources:
        print("  ⚠ no content references in house folder; skipping")
        return
    content_paths = [prepare_image(p, f"content-{key}-{p.stem}") for p in content_sources]

    # Track outputs produced (or already on disk) IN ORDER, so each new
    # generation sees the prior ones.
    prior_meta: list[dict] = []

    for target in targets:
        out_path = SYNTHETIC_DIR / key / target["filename"]
        if out_path.exists():
            print(f"  ✓ {target['filename']} (exists)")
            prior_meta.append(target)
            continue

        style_paths = style_elev_paths if target["kind"] == "elevation" else style_fp_paths

        # Both elevations and floorplans get the verification + retry loop.
        # Verification is the same call regardless of kind (sum-consistency
        # + feature-correctness, see VERIFY_PROMPT).
        attempts = 1 if args.no_verify else args.max_attempts
        feedback_hint = ""
        verify_report: dict = {}
        twist = pick_twist(seed, target["filename"])
        last_prompt = ""

        for attempt in range(1, attempts + 1):
            tag = f" [attempt {attempt}/{attempts}]" if attempts > 1 else ""
            print(f"  → {target['filename']}{tag}", end=" ", flush=True)
            t0 = time.time()
            try:
                _, last_prompt = generate_target(
                    client, key, target,
                    style_paths=style_paths,
                    content_paths=content_paths,
                    prior_meta=prior_meta,
                    preset=preset,
                    nuance_profile=nuance_profile,
                    twist=twist,
                    feedback_hint=feedback_hint,
                    dry_run=args.dry_run,
                )
                if not args.dry_run:
                    append_prompt_index({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "house": key,
                        "file": target["filename"],
                        "kind": target["kind"],
                        "attempt": attempt,
                        "preset": preset,
                        "twist": twist,
                        "nuance": {
                            "lettering": nuance_profile["lettering"],
                            "paper": nuance_profile["paper"],
                            "capture": nuance_profile["capture"][0],
                            "persona": nuance_profile["persona"][0],
                            "line_weight": nuance_profile["line_weight"],
                        },
                        "prompt": last_prompt,
                    })
                if args.dry_run:
                    print("(dry-run)")
                    break
                gen_secs = time.time() - t0
                size_kb = out_path.stat().st_size // 1024
                print(f"({gen_secs:.0f}s, {size_kb} KB)", end="", flush=True)

                if args.no_verify:
                    print()
                    break

                print(" → verifying", end=" ", flush=True)
                ok, feedback, report = verify_drawing(client, out_path)
                verify_report = report
                if ok:
                    print("✓")
                    break
                # Inconsistent — keep retrying if budget left.
                issues = report.get("issues") or []
                short_issue = (issues[0] if issues else feedback or "inconsistent")[:80]
                print(f"✗ — {short_issue}")
                feedback_hint = feedback or "Align each dimension segment with a real feature; sums must match overall totals."
                if attempt < attempts:
                    out_path.unlink(missing_ok=True)
                    time.sleep(args.sleep)
                    continue
                # Out of retries: keep last attempt, flag in manifest.
                print(f"  ⚠ kept last attempt despite verification failure")

            except KeyboardInterrupt:
                print("\n  ⏹ interrupted")
                raise
            except Exception as e:
                msg = str(e).lower()
                if "rate limit" in msg or "too many requests" in msg or "429" in msg:
                    print(f"\n  ⏸ rate limit — sleeping 60s")
                    time.sleep(60)
                    continue
                print(f"\n  ✗ {type(e).__name__}: {e}")
                time.sleep(5)
                break

        if out_path.exists():
            entry = {
                "file": target["filename"],
                "kind": target["kind"],
                "view": target.get("view"),
                "floor": target.get("floor"),
                "title": target["title"],
                "source": "generated",
                "model": MODEL,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "preset": preset,
                "twist": twist,
                "capture": nuance_profile["capture"][0],
                "persona": nuance_profile["persona"][0],
                "style_refs": elev_descs if target["kind"] == "elevation" else fp_descs,
                "content_refs": [Path(p).name for p in content_paths],
                "prior_refs": [p["filename"] for p in prior_meta],
                "label_status": "unlabeled",
            }
            if verify_report:
                entry["verify_report"] = verify_report
                entry["verify_passed"] = bool(verify_report.get("consistent"))
            update_manifest_entry(manifest, entry)
            save_manifest(manifest)
            prior_meta.append(target)

        time.sleep(args.sleep)

    # House-level wrap-up: count verification failures across all drawings
    # we actually produced and flag the house as "difficult" if ≥50% failed.
    # Difficult houses are NOT skipped — flagging lets downstream training
    # corpus filtering drop them (or sample less from them).
    if not args.dry_run:
        record_difficult_if_needed(manifest)


def record_difficult_if_needed(manifest: dict) -> None:
    """Inspect the just-saved manifest and append to difficult_houses.jsonl if
    too many drawings failed verification. Always prints a one-line summary."""
    drawings = manifest.get("drawings", [])
    if not drawings:
        return
    # A drawing only has verify_passed if it ran through verification at all
    # (skipped under --no-verify). Count only those.
    verified = [d for d in drawings if "verify_passed" in d]
    if not verified:
        return
    failed = [d for d in verified if not d.get("verify_passed")]
    fail_ratio = len(failed) / len(verified)
    key = manifest["key"]
    print(f"  ↳ {key}: {len(failed)}/{len(verified)} drawings failed verification "
          f"({fail_ratio:.0%})")
    if fail_ratio < DIFFICULT_HOUSE_FAIL_RATIO:
        return
    issues: list[str] = []
    for d in failed:
        report = d.get("verify_report") or {}
        for issue in (report.get("issues") or [])[:2]:
            issues.append(f"{d['file']}: {issue}")
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "house": key,
        "model": manifest.get("model"),
        "manufacturer": manifest.get("manufacturer"),
        "preset": manifest.get("preset"),
        "nuance": manifest.get("nuance_profile", {}),
        "drawings_verified": len(verified),
        "drawings_failed": len(failed),
        "fail_ratio": round(fail_ratio, 2),
        "failed_files": [d["file"] for d in failed],
        "issues": issues[:10],
    }
    DIFFICULT_HOUSES_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DIFFICULT_HOUSES_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"  ⚠ flagged {key} as difficult — logged to "
          f"{DIFFICULT_HOUSES_LOG.relative_to(REPO)}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("house", nargs="?", help="one house key (e.g. 'house-1'); omit to do all")
    ap.add_argument("--dry-run", action="store_true", help="plan only — no API calls")
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between calls (default 1.0)")
    ap.add_argument("--kind", choices=["elevation", "floorplan"], help="restrict to one kind")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the dimension-consistency verification pass entirely")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="max attempts per drawing including verification retries (default 3)")
    ap.add_argument("--seed-offset", type=int, default=0,
                    help="shift the per-house deterministic seed (use to resample style refs)")
    args = ap.parse_args()

    load_dotenv(REPO / ".env")
    if not args.dry_run and not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY not set in .env — see .env.example")

    client = None
    if not args.dry_run:
        from openai import OpenAI
        client = OpenAI()

    print("Preparing style references…")
    style_elev = [prepare_style_ref(k, f) for k, f in STYLE_REFS_ELEVATION]
    style_fp = [prepare_style_ref(k, f) for k, f in STYLE_REFS_FLOORPLAN]
    print(f"  {len(style_elev)} elevation refs + {len(style_fp)} floorplan refs cached")

    keys = [args.house] if args.house else [p.name for p in list_houses()]

    for key in keys:
        if key in SKIP_KEYS:
            print(f"\nskip {key}: has real drawings (used as style reference)")
            continue
        house = load_house(key)
        if house is None:
            print(f"\nskip {key}: not found")
            continue
        process_house(
            client, key, house, args=args,
            style_elev_pool=style_elev, style_fp_pool=style_fp,
            style_elev_meta=STYLE_REFS_ELEVATION, style_fp_meta=STYLE_REFS_FLOORPLAN,
        )

    print("\ndone.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped.")
        sys.exit(130)
