#!/usr/bin/env bash
# Publish the e-ink frame image from a BirdNET-Pi station, no panel needed.
#
# A systemd timer screenshots this station's own collage with shoot.py
# (1200x1600 PNG) every 15 minutes and writes it to the Extracted web root,
# so it is served at http://<this-host>/frame.png for a remote frame
# (install.sh --image-url) or anything else that wants the picture.
# Chromium is heavy, which is why this is opt-in.
#
#   serve_png.sh enable [--title T] [--subtitle S] [--every MINUTES]
#   serve_png.sh disable      stop the timer, remove the units and frame.png
#   serve_png.sh status
#
# Settings live in ~/.birdframe/frame-png.env; edit it and the next run
# picks them up. Safe to re-run.
set -euo pipefail

FRAME=$(cd -P -- "$(dirname -- "$0")" && pwd -P)
REPO=$(dirname -- "$FRAME")
VENV=${AVIAN_VENV:-$HOME/avian-venv}
CONF=/etc/birdnet/birdnet.conf
ENV_FILE=$HOME/.birdframe/frame-png.env
SERVICE=/etc/systemd/system/avian-frame-png.service
TIMER=/etc/systemd/system/avian-frame-png.timer

die() { printf 'serve_png: %s\n' "$*" >&2; exit 1; }
usage() {
  echo "Usage: $0 enable [--title T] [--subtitle S] [--every MINUTES] | disable | status" >&2
  exit 64
}

extracted_dir() {
  [ -r "$CONF" ] || die "$CONF is missing; this needs a BirdNET-Pi station"
  # shellcheck disable=SC1090
  (source "$CONF" && printf '%s\n' "${EXTRACTED:?}")
}

# Native libraries Chromium needs, installed through apt as in install.sh.
# Never run the user-writable venv's "playwright install-deps" through sudo.
BROWSER_PACKAGES=(
  xvfb fonts-noto-color-emoji fonts-unifont libfontconfig1 libfreetype6
  xfonts-scalable fonts-liberation fonts-ipafont-gothic fonts-wqy-zenhei
  fonts-tlwg-loma-otf fonts-freefont-ttf libasound2 libatk-bridge2.0-0
  libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 libdrm2 libgbm1
  libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1
  libxcomposite1 libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2
)

set_setting() {
  local key=$1 value=$2 temp
  [[ "$value" != *$'\n'* && "$value" != *"'"* ]] || die "$key cannot contain newlines or single quotes"
  temp=$(mktemp)
  grep -v "^$key=" "$ENV_FILE" >"$temp" || true
  printf "%s='%s'\n" "$key" "$value" >>"$temp"
  mv -f -- "$temp" "$ENV_FILE"
}

write_units() {
  local every=$1 temp_service temp_timer
  temp_service=$(mktemp)
  temp_timer=$(mktemp)
  cat >"$temp_service" <<EOF
[Unit]
Description=AvianVisitors frame.png snapshot
Wants=network-online.target
After=network-online.target caddy.service

[Service]
Type=oneshot
User=$USER
ExecStart=$FRAME/serve_png.sh run
Environment=PYTHONUNBUFFERED=1
UMask=0022
NoNewPrivileges=true
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=300
EOF
  cat >"$temp_timer" <<EOF
[Unit]
Description=Refresh the AvianVisitors frame.png on a schedule

[Timer]
OnActiveSec=2min
OnBootSec=3min
OnUnitActiveSec=${every}min

[Install]
WantedBy=timers.target
EOF
  sudo install -o root -g root -m 0644 "$temp_service" "$SERVICE"
  sudo install -o root -g root -m 0644 "$temp_timer" "$TIMER"
  rm -f -- "$temp_service" "$temp_timer"
}

do_enable() {
  local every=15
  extracted_dir >/dev/null
  mkdir -p "$(dirname -- "$ENV_FILE")"
  if [ ! -f "$ENV_FILE" ]; then
    printf '%s\n' \
      '# Settings for serve_png.sh (frame.png on this station). Shell syntax.' \
      "FRAME_PNG_URL='http://localhost/'" \
      "FRAME_PNG_TITLE='Avian Visitors'" \
      "FRAME_PNG_SUBTITLE='Heard Today'" >"$ENV_FILE"
  fi
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --title) [ "$#" -ge 2 ] || usage; set_setting FRAME_PNG_TITLE "$2"; shift ;;
      --subtitle) [ "$#" -ge 2 ] || usage; set_setting FRAME_PNG_SUBTITLE "$2"; shift ;;
      --every)
        [ "$#" -ge 2 ] && [[ "$2" =~ ^[0-9]+$ ]] && [ "$2" -ge 5 ] || die '--every takes whole minutes, at least 5'
        every=$2; shift ;;
      *) usage ;;
    esac
    shift
  done

  echo '1/3  Installing Chromium system libraries...'
  sudo apt-get update -qq
  sudo apt-get install -y -qq --no-install-recommends "${BROWSER_PACKAGES[@]}"
  echo '2/3  Installing Playwright + Chromium into the AvianVisitors venv (a few minutes)...'
  "$REPO/avian/scripts/setup_venv.sh" --shoot
  "$VENV/bin/playwright" install chromium
  "$VENV/bin/python" - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    playwright.chromium.launch(headless=True).close()
PY
  echo '3/3  Installing the systemd timer...'
  write_units "$every"
  sudo systemctl daemon-reload
  sudo systemctl enable --now avian-frame-png.timer
  echo "Enabled. The first image appears in about 2 minutes at http://$(hostname).local/frame.png"
  echo "Render one now with: sudo systemctl start avian-frame-png.service"
}

do_run() {
  local extracted
  extracted=$(extracted_dir)
  FRAME_PNG_URL='http://localhost/' FRAME_PNG_TITLE='Avian Visitors' FRAME_PNG_SUBTITLE='Heard Today'
  # shellcheck disable=SC1090
  [ -f "$ENV_FILE" ] && source "$ENV_FILE"
  # shoot.py writes a temporary file beside --out and renames it into place,
  # so the web server never serves a half-written image.
  exec "$VENV/bin/python" "$FRAME/shoot.py" --url "$FRAME_PNG_URL" \
    --title "$FRAME_PNG_TITLE" --subtitle "$FRAME_PNG_SUBTITLE" \
    --timeout 180000 --out "$extracted/frame.png"
}

do_disable() {
  local extracted
  extracted=$(extracted_dir)
  if [ -f "$TIMER" ] || [ -f "$SERVICE" ]; then
    sudo systemctl disable --now avian-frame-png.timer 2>/dev/null || true
    sudo systemctl stop avian-frame-png.service 2>/dev/null || true
    sudo rm -f -- "$TIMER" "$SERVICE"
    sudo systemctl daemon-reload
  fi
  rm -f -- "$extracted/frame.png"
  echo 'Disabled and removed frame.png. Settings stay in '"$ENV_FILE"'.'
  echo 'To reclaim the browser space: rm -rf ~/.cache/ms-playwright'
}

do_status() {
  local extracted
  extracted=$(extracted_dir)
  systemctl list-timers --all avian-frame-png.timer --no-pager || true
  if [ -f "$extracted/frame.png" ]; then
    echo "frame.png updated $(date -r "$extracted/frame.png" '+%F %T'); last run log:"
    journalctl -u avian-frame-png.service -n 5 --no-pager 2>/dev/null || true
  else
    echo 'No frame.png yet.'
  fi
}

[ "${EUID:-$(id -u)}" -ne 0 ] || die 'run as the BirdNET-Pi user, not root'
[ "$#" -ge 1 ] || usage
action=$1
shift
case "$action" in
  enable) do_enable "$@" ;;
  run) [ "$#" -eq 0 ] || usage; do_run ;;
  disable) [ "$#" -eq 0 ] || usage; do_disable ;;
  status) [ "$#" -eq 0 ] || usage; do_status ;;
  *) usage ;;
esac
