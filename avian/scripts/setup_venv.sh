#!/usr/bin/env bash
# Create or update the AvianVisitors tooling venv.
#
# The illustration tools and the frame screenshotter get their own venv so
# their packages never upgrade anything in BirdNET's ~/BirdNET-Pi/birdnet
# venv (e.g. Pillow 12 breaks its streamlit). Safe to re-run.
#
#   setup_venv.sh             Pillow only: build_masks.py, import_bundle.py
#   setup_venv.sh --cutout    also rembg + onnxruntime for cutout.py (large)
#   setup_venv.sh --shoot     also Playwright for frame/shoot.py
#
# The venv lives at $AVIAN_VENV (default ~/avian-venv).
set -euo pipefail

here=$(cd -P -- "$(dirname -- "$0")" && pwd -P)
repo=$(dirname -- "$(dirname -- "$here")")
venv=${AVIAN_VENV:-$HOME/avian-venv}

requirements=("$here/requirements-base.txt")
while [ "$#" -gt 0 ]; do
  case "$1" in
    --cutout) requirements+=("$here/requirements.txt") ;;
    --shoot) requirements+=("$repo/frame/requirements-shoot.txt") ;;
    *) echo "Usage: $0 [--cutout] [--shoot]" >&2; exit 64 ;;
  esac
  shift
done

if [ ! -x "$venv/bin/python" ]; then
  echo "Creating the AvianVisitors venv at $venv"
  python3 -m venv "$venv"
fi
"$venv/bin/pip" install -q --upgrade pip
for file in "${requirements[@]}"; do
  "$venv/bin/pip" install -q -r "$file"
done
echo "AvianVisitors venv ready: $venv/bin/python"
