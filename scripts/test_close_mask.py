"""Test whether a morphological CLOSE on the wall-ink mask raises f1.

Walls on this scan have intermittent/faint ink along their length (verified:
solid bands at the ends, near-empty in the middle). A CLOSE bridges those
gaps so a full-length label scores on a continuous band. This measures
default vs CLOSE-augmented masks on BOTH scenes, writing results to
/tmp/close_result.txt. Pure measurement — changes nothing.

Run: PYTHONPATH=. .venv/bin/python scripts/test_close_mask.py
"""
import json
import urllib.request
import traceback

import cv2
import numpy as np
from PIL import Image

from api.corner_detect import _to_gray_array
from api.wall_score import _label_mask


def walls(f):
    url = f"http://127.0.0.1:12500/labels/dataset/house-22/{f}"
    d = json.load(urllib.request.urlopen(url, timeout=30))
    return [((l["geometry"]["start"][0], l["geometry"]["start"][1]),
             (l["geometry"]["end"][0], l["geometry"]["end"][1]))
            for l in d["labels"] if l["type"] == "wall"]


def score_variant(img, ws, min_wall_px, tol_px, close_k):
    gray, _, _ = _to_gray_array(img, None)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    k = max(3, min_wall_px)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    ink = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kern)
    if close_k > 0:
        ck = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k))
        ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, ck)
    h, w = ink.shape
    lab = _label_mask((h, w), ws, thick_px=tol_px)
    kk = max(3, 2 * tol_px + 1)
    dk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kk, kk))
    ink_d = cv2.dilate(ink, dk)
    lab_d = cv2.dilate(lab, dk)
    lp = int((lab > 0).sum())
    ip = int((ink > 0).sum())
    prec = float(((lab > 0) & (ink_d > 0)).sum()) / lp if lp else 0.0
    rec = float(((ink > 0) & (lab_d > 0)).sum()) / ip if ip else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return round(f1, 3), round(prec, 3), round(rec, 3)


def main():
    lines = []
    for fname, mwp, tol in [("house-22-floorplan-eg.jpg", 8, 9),
                            ("house-22-floorplan-eg@600.jpg", 16, 18)]:
        ws = walls(fname)
        lines.append(f"=== {fname} (min_wall_px={mwp} tol={tol}) ===")
        for ck in [0, 15, 25, 41, 61]:
            f1, p, r = score_variant(Image.open(f"data/dataset/house-22/{fname}").convert("RGB"),
                                     ws, mwp, tol, ck)
            lines.append(f"close_k={ck}: f1={f1} prec={p} rec={r}")
    open("/tmp/close_result.txt", "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        open("/tmp/close_result.txt", "w").write("ERROR\n" + traceback.format_exc())
