"""Deterministic cleanup: make house-22 EG canonically the lossless PNG scene.

Removes stale .jpg scene crops + their label files, ensures the manifest points
only to the .png, confirms labels are clean. Writes a result report to
/tmp/finalize_result.txt (display-independent). Idempotent.
"""
import json
import os

D = "data/dataset/house-22"
KEEP_SCENE = "house-22-floorplan-eg.png"
log = []

# 1. remove stale jpg scene crops (the q100 jpg + the -2 dedup), keep the PNG
for f in ["house-22-floorplan-eg.jpg", "house-22-floorplan-eg-2.jpg",
          "house-22-floorplan-eg-2.png"]:
    p = os.path.join(D, f)
    if os.path.exists(p):
        os.remove(p)
        log.append(f"removed {f}")

# 2. labels: keep only the label file matching the PNG scene; remove jpg-stem labels
ld = os.path.join(D, "labels")
if os.path.isdir(ld):
    for f in sorted(os.listdir(ld)):
        # label files are named by scene stem; the canonical scene stem is
        # 'house-22-floorplan-eg' (shared by .jpg and .png) -> keep one clean.
        if f.endswith(".json") and "-2" in f:
            os.remove(os.path.join(ld, f))
            log.append(f"removed label {f}")

# 3. manifest: ensure exactly one drawing, the PNG
mp = os.path.join(D, "manifest.json")
m = json.load(open(mp))
before = [d["file"] for d in m.get("drawings", [])]
m["drawings"] = [d for d in m.get("drawings", []) if d.get("file") == KEEP_SCENE]
json.dump(m, open(mp, "w"), indent=2, ensure_ascii=False)
log.append(f"manifest drawings before={before} after={[d['file'] for d in m['drawings']]}")

# 4. report final on-disk state
files = sorted(p for p in os.listdir(D) if os.path.isfile(os.path.join(D, p)))
labels = sorted(os.listdir(ld)) if os.path.isdir(ld) else []
log.append(f"FINAL files={files}")
log.append(f"FINAL labels={labels}")

# 5. scene sanity
from PIL import Image
sp = os.path.join(D, KEEP_SCENE)
if os.path.exists(sp):
    im = Image.open(sp)
    log.append(f"SCENE {KEEP_SCENE} {im.size} {im.format} {os.path.getsize(sp)//1024}KB")
else:
    log.append(f"ERROR: {KEEP_SCENE} missing!")

open("/tmp/finalize_result.txt", "w").write("\n".join(log) + "\n")
print("done")
