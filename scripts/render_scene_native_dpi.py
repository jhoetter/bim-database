"""M1.2 — re-render a dataset scene at the source scan's NATIVE dpi.

Many scenes were extracted at 300 dpi while the source PDF embeds a 600-dpi
scan (half the available resolution was discarded — faint 1-3px walls fell
below the ink threshold). This renders the SAME scene region at the higher
dpi by:
  1. rendering the full source page at 300 dpi and locating the existing scene
     within it via cv2.matchTemplate (exact, handles clip-expanded bboxes),
  2. cropping the corresponding 2x region from the 600-dpi page render,
  3. writing <stem>@600.jpg next to the scene.

Labels for the hi-res scene are the 300-dpi labels with coords x2 and
image_size_px x2 (see scripts/make_hires_labels.py). The hi-res scene is
served by all existing routes (score-walls/refine-wall/grid) under its @600
filename, giving 2x placement precision + faint walls that actually register.

Usage: PYTHONPATH=. .venv/bin/python scripts/render_scene_native_dpi.py
(currently hard-wired to house-22 EG; parameterize per-scene as needed).
"""
import fitz, numpy as np, cv2, json
from PIL import Image

PDF = "data/pdfs/incoming/house-22/house-22.pdf"
PAGE_INDEX = 1  # 0-indexed page 2
SCENE = "data/dataset/house-22/house-22-floorplan-eg.jpg"
OUT = "data/dataset/house-22/house-22-floorplan-eg@600.jpg"
SRC_DPI, DST_DPI = 300, 600


def _render(page, dpi):
    m = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=m, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return arr[:, :, :3] if pix.n >= 3 else arr


def main():
    doc = fitz.open(PDF)
    page = doc.load_page(PAGE_INDEX)
    p_src = _render(page, SRC_DPI)
    scene = cv2.cvtColor(np.asarray(Image.open(SCENE).convert("RGB")), cv2.COLOR_RGB2BGR)
    g_page = cv2.cvtColor(p_src, cv2.COLOR_RGB2GRAY)
    g_scene = cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(g_page, g_scene, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    x, y = loc
    h, w = g_scene.shape
    scale = DST_DPI // SRC_DPI
    p_dst = _render(page, DST_DPI)
    crop = p_dst[y * scale:(y + h) * scale, x * scale:(x + w) * scale]
    Image.fromarray(crop).save(OUT, quality=92)
    print(json.dumps({"match_score": round(float(score), 3),
                      "hires_wh": [crop.shape[1], crop.shape[0]]}))


if __name__ == "__main__":
    main()
