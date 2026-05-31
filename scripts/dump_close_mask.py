"""Render the wall-ink mask with/without CLOSE so a human/vision-LLM can
verify the CLOSE reconstructs walls (not merges furniture/rooms).
Writes /tmp/mask_close0.png and /tmp/mask_close41.png. Pure.
"""
import cv2
import numpy as np
from PIL import Image
from api.corner_detect import _to_gray_array


def mask(gray, min_wall_px, close_k):
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    k = max(3, min_wall_px)
    ink = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    if close_k > 0:
        ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE,
                               cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k)))
    return ink


def main():
    img = Image.open("data/dataset/house-22/house-22-floorplan-eg.jpg").convert("RGB")
    gray, _, _ = _to_gray_array(img, None)
    for ck in [0, 41]:
        m = mask(gray, 8, ck)
        # downscale for quick viewing
        small = Image.fromarray(m).resize((1100, int(1100 * m.shape[0] / m.shape[1])))
        small.save(f"/tmp/mask_close{ck}.png")


if __name__ == "__main__":
    main()
