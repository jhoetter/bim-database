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

SYNTHETIC_DIR = REPO / "data" / "synthetic"
PREPARED_DIR = REPO / "tmp" / "synthetic-prepared"

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
- slightly wrinkled paper texture, subtle shadows + folds + paper grain
- clean centered composition, simple ground line
- light hatching / cross-hatching
- realistic photographed-paper look

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
- slightly wrinkled paper texture, realistic photographed-paper look

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


def determine_targets(house: dict, kind_filter: str | None = None) -> list[dict]:
    """Order matters: elevations first (N, S, E, W), then floorplans (KG/EG/OG/DG/Spitzboden).

    Elevations are generated before floorplans so a floorplan can use the
    elevations as priors (window/door rhythm)."""
    key = f"house-{house['id']}"
    targets: list[dict] = []

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
        # Floorplan order: KG → EG → OG → DG → Spitzboden (build-up).
        order = ["KG", "UG", "Hochparterre", "EG", "1. OG", "2. OG", "3. OG", "DG", "Spitzboden"]
        levels = house.get("levels") or ["EG"]
        for floor in sorted(levels, key=lambda f: order.index(f) if f in order else 999):
            slug = floor.lower().replace(" ", "").replace(".", "").replace("ö", "oe")
            targets.append({
                "kind": "floorplan",
                "floor": floor,
                "title": f"GRUNDRISS {floor.upper()}",
                "filename": f"{key}-syn-floorplan-{slug}.png",
                "prompt_args": {"floor_label": floor, "title_text": f"GRUNDRISS {floor.upper()}"},
            })

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


def generate_target(
    client,
    key: str,
    target: dict,
    *,
    style_paths: list[Path],
    content_paths: list[Path],
    prior_meta: list[dict],
    preset: str,
    feedback_hint: str = "",
    dry_run: bool = False,
) -> Path | None:
    """Generate one drawing. Returns the output path on success."""
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

    feedback_block = (
        f"\nIMPORTANT — previous attempt feedback to fix:\n  {feedback_hint}\n"
        if feedback_hint else ""
    )
    if target["kind"] == "elevation":
        prompt = PROMPT_ELEVATION.format(
            prior_context=prior_clause,
            detail_instructions=detail_clause,
            feedback_hint=feedback_block,
            **target["prompt_args"],
        )
        size = "1536x1024"
    else:
        prompt = PROMPT_FLOORPLAN.format(
            prior_context=prior_clause,
            detail_instructions=detail_clause,
            feedback_hint=feedback_block,
            **target["prompt_args"],
        )
        size = "1024x1024"

    if dry_run:
        return None

    png_bytes = call_image_edit(client, prompt, images, size=size)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png_bytes)
    return out_path


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
    manifest["preset"] = preset
    manifest["style_refs_house"] = {
        "elevation": elev_descs,
        "floorplan": fp_descs,
    }

    targets = determine_targets(house, kind_filter=args.kind)
    house_keys_used = sorted({d.split("-")[0] + "-" + d.split("-")[1]
                              for d in elev_descs + fp_descs})
    print(f"\n{key}: {(house.get('model') or '?')[:60]}  "
          f"preset={preset}  refs={'+'.join(house_keys_used)}  "
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

        for attempt in range(1, attempts + 1):
            tag = f" [attempt {attempt}/{attempts}]" if attempts > 1 else ""
            print(f"  → {target['filename']}{tag}", end=" ", flush=True)
            t0 = time.time()
            try:
                generate_target(
                    client, key, target,
                    style_paths=style_paths,
                    content_paths=content_paths,
                    prior_meta=prior_meta,
                    preset=preset,
                    feedback_hint=feedback_hint,
                    dry_run=args.dry_run,
                )
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
                "model": MODEL,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "preset": preset,
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
