#!/usr/bin/env bash
# Render every page of a PDF to JPEG at the chosen DPI.
# Usage: scripts/render_page.sh <pdf-path> <out-prefix> [dpi=200]
set -euo pipefail
pdf="${1:?usage: render_page.sh <pdf-path> <out-prefix> [dpi=200]}"
out="${2:?usage: render_page.sh <pdf-path> <out-prefix> [dpi=200]}"
dpi="${3:-200}"
pdftoppm -jpeg -jpegopt quality=92 -r "$dpi" "$pdf" "$out"
ls "${out}"-*.jpg
