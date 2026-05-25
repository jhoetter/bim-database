#!/usr/bin/env python3
"""Scaffold a new house record: writes data/houses/house-<ID>.json with all
schema fields present (most null) and creates an empty house-<ID>/ image
folder. Invoked via `make new-house ID=24 MODEL="My new house"`."""
import argparse
import json
from pathlib import Path

BASE = Path(__file__).parent.parent
OUT = BASE / "data" / "houses"


def stub(rec_id: int, model: str) -> dict:
    return {
        "id": rec_id,
        "model": model,
        "manufacturer": None,
        "source": "catalog",
        "source_url": None,
        "source_origin": None,

        "building_type": None,
        "construction": None,
        "roof_type": None,
        "style": None,
        "energy_standard": None,

        "year_built": None,
        "has_basement": None,
        "levels": None,

        "area_m2": None,
        "rooms": None,
        "floors": None,
        "price_eur": None,
        "price_on_request": False,

        "site": None,
        "character": None,
        "agent_notes": None,
        "tags": [],

        "images": [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--model", required=True)
    args = ap.parse_args()

    # Each house = data/houses/house-N/ with metadata + images + PDF inside.
    folder = OUT / f"house-{args.id}"
    if folder.exists():
        raise SystemExit(f"refusing to overwrite existing {folder.relative_to(BASE)}/")
    folder.mkdir(parents=True)
    out = folder / f"house-{args.id}.json"
    out.write_text(json.dumps(stub(args.id, args.model), indent=2, ensure_ascii=False) + "\n")
    print(f"created  {folder.relative_to(BASE)}/")
    print(f"created  {out.relative_to(BASE)}")
    print(f"next:    drop image files into {folder.relative_to(BASE)}/ and edit")
    print(f"         {out.relative_to(BASE)} — see AGENTS.md for fields.")


if __name__ == "__main__":
    main()
