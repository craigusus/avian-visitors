#!/usr/bin/env bash
# Merge a community illustration set into this station, e.g.
#   import_bundle.sh https://github.com/<owner>/<repo>
# Runs import_bundle.py from ~/avian-venv (created on first use), never from
# BirdNET's own venv. See import_bundle.py --help for every option.
set -euo pipefail

here=$(cd -P -- "$(dirname -- "$0")" && pwd -P)
venv=${AVIAN_VENV:-$HOME/avian-venv}

[ "${EUID:-$(id -u)}" -ne 0 ] || { echo 'Run as the BirdNET-Pi user, not root.' >&2; exit 1; }
if ! "$venv/bin/python" -c 'import PIL' 2>/dev/null; then
  "$here/setup_venv.sh"
fi
exec "$venv/bin/python" "$here/import_bundle.py" "$@"
