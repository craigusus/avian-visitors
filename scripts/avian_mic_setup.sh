#!/usr/bin/env bash
# Point BirdNET recording straight at a USB microphone, bypassing PulseAudio.
#
# Combined USB mic + headphone adapters often reach PulseAudio with an
# output-only profile, so the default source becomes a ".monitor" of the
# headphone jack and every recording is silence ("Human, 0.0"). This tells
# PulseAudio to ignore the capture card (udev PULSE_IGNORE) and sets REC_CARD
# to the card's stable ALSA id, which survives card renumbering.
#
#   avian_mic_setup.sh --list            show capture cards
#   avian_mic_setup.sh --auto            set up the only USB capture card
#   avian_mic_setup.sh --card <id>       set up this card id (see --list)
#   avian_mic_setup.sh --test [secs]     record a short sample and report its level
#   avian_mic_setup.sh --remove          undo, restoring the previous REC_CARD
#
# --force replaces a REC_CARD you set yourself. Safe to re-run, e.g. after
# swapping microphones. Run as the BirdNET-Pi user; it uses sudo for udev.
set -euo pipefail

CONF=${AVIAN_BIRDNET_CONF:-/etc/birdnet/birdnet.conf}
ASOUND=${AVIAN_ASOUND_ROOT:-/proc/asound}
RULE=/etc/udev/rules.d/89-avian-usb-mic.rules

usage() {
  cat >&2 <<EOF
Usage: $0 --list | --auto [--force] | --card <id> [--force] | --test [secs] | --remove
EOF
  exit 64
}

die() { printf 'avian_mic_setup: %s\n' "$*" >&2; exit 1; }

conf_value() {
  awk -v key="$1" -F= '$1 == key { v = substr($0, index($0, "=") + 1); gsub(/^"|"$/, "", v); print v; exit }' "$CONF"
}

# Rewrite one KEY= line in place. Writing through the existing file keeps its
# owner, mode, and the /etc/birdnet symlink, which the web UI also edits.
set_conf_value() {
  local key=$1 value=$2 target temp
  target=$(readlink -f -- "$CONF")
  temp=$(mktemp)
  awk -v key="$key" -v value="$value" -F= '
    $1 == key { print key "=\"" value "\""; done = 1; next }
    { print }
    END { if (!done) print key "=\"" value "\"" }' "$target" >"$temp"
  if [ -w "$target" ]; then
    cat "$temp" >"$target"
  else
    sudo tee "$target" <"$temp" >/dev/null
  fi
  rm -f -- "$temp"
}

valid_card_id() { [[ "$1" =~ ^[A-Za-z0-9_-]+$ ]]; }

# Print "<id> <number> <name>" for every card with a capture device. USB
# cards are marked with a trailing "usb" field.
capture_cards() {
  local dir id number name kind
  for dir in "$ASOUND"/card[0-9]*; do
    [ -d "$dir" ] || continue
    compgen -G "$dir/pcm*c" >/dev/null || continue
    id=$(cat "$dir/id")
    number=${dir##*/card}
    name=$(sed -n "s/^ *$number \[[^]]*\]: *//p" "$ASOUND/cards" | head -n 1)
    kind=other
    [ -e "$dir/usbid" ] && kind=usb
    printf '%s %s %s %s\n' "$id" "$number" "$kind" "$name"
  done
}

list_cards() {
  local cards
  cards=$(capture_cards)
  if [ -z "$cards" ]; then
    echo 'No capture devices found. Check the mic is plugged in, then run: arecord -l'
    return
  fi
  echo 'Capture cards (id, number, type, name):'
  printf '%s\n' "$cards" | sed 's/^/  /'
  echo "REC_CARD is currently: $(conf_value REC_CARD)"
  [ -f "$RULE" ] && echo "PulseAudio ignores: $(sed -n 's/.*ATTR{id}=="\([^"]*\)".*/\1/p' "$RULE")"
  return 0
}

stop_user_pulseaudio() {
  local user uid
  user=$(conf_value BIRDNET_USER)
  [ -n "$user" ] || user=$USER
  uid=$(id -u "$user")
  # The recording script no longer starts PulseAudio for a direct ALSA card;
  # a running instance may still hold the device until it is restarted.
  if pgrep -u "$uid" -x pulseaudio >/dev/null 2>&1; then
    sudo -u "$user" XDG_RUNTIME_DIR="/run/user/$uid" pulseaudio -k >/dev/null 2>&1 || true
  fi
}

apply_card() {
  local card=$1 force=$2 current previous wanted temp
  valid_card_id "$card" || die "invalid card id: $card"
  [ -d "$ASOUND/$card" ] || die "no sound card with id '$card' (see --list)"
  compgen -G "$ASOUND/$card"/pcm*c >/dev/null || die "card '$card' has no capture device"
  wanted="plughw:CARD=$card,DEV=0"
  current=$(conf_value REC_CARD)
  previous=$current
  # Re-running keeps the original value to restore, not our own setting.
  if [ -f "$RULE" ]; then
    previous=$(sed -n 's/^# previous REC_CARD=//p' "$RULE")
  fi
  case "$current" in
    ''|default|pulse|"$wanted") ;;
    plughw:CARD=*,DEV=0)
      # A card this script set earlier, e.g. before a mic swap.
      [ -f "$RULE" ] || [ "$force" = 1 ] \
        || die "REC_CARD is already set to '$current'; re-run with --force to replace it"
      ;;
    *)
      [ "$force" = 1 ] \
        || die "REC_CARD is already set to '$current'; re-run with --force to replace it"
      ;;
  esac

  temp=$(mktemp)
  printf '%s\n' \
    '# Managed by avian_mic_setup.sh: PulseAudio leaves this capture card to BirdNET.' \
    "# previous REC_CARD=$previous" \
    "SUBSYSTEM==\"sound\", KERNEL==\"card*\", ATTR{id}==\"$card\", ENV{PULSE_IGNORE}=\"1\"" >"$temp"
  sudo install -o root -g root -m 0644 "$temp" "$RULE"
  rm -f -- "$temp"
  sudo udevadm control --reload
  sudo udevadm trigger --subsystem-match=sound --action=change

  set_conf_value REC_CARD "$wanted"
  stop_user_pulseaudio
  sudo systemctl try-restart birdnet_recording.service
  echo "BirdNET now records from $wanted; PulseAudio ignores card '$card'."
  echo 'Note: the live audio stream is off while BirdNET uses a direct ALSA card.'
  echo "Check the level with: $0 --test"
}

auto_setup() {
  local force=$1 usb count
  usb=$(capture_cards | awk '$3 == "usb"')
  count=$(printf '%s' "$usb" | grep -c . || true)
  case "$count" in
    0)
      echo 'No USB microphone found; leaving REC_CARD unchanged.'
      echo "Plug one in and run: $0 --auto  (or --list to see other cards)"
      ;;
    1) apply_card "${usb%% *}" "$force" ;;
    *)
      echo 'More than one USB capture card found; pick one with --card <id>:'
      printf '%s\n' "$usb" | sed 's/^/  /'
      ;;
  esac
}

remove_setup() {
  local previous current
  [ -f "$RULE" ] || { echo 'Nothing to remove.'; return; }
  previous=$(sed -n 's/^# previous REC_CARD=//p' "$RULE")
  current=$(conf_value REC_CARD)
  case "$current" in
    plughw:CARD=*,DEV=0) set_conf_value REC_CARD "${previous:-default}" ;;
    *) echo "REC_CARD was changed since setup ('$current'); leaving it as is." ;;
  esac
  sudo rm -f -- "$RULE"
  sudo udevadm control --reload
  sudo udevadm trigger --subsystem-match=sound --action=change
  sudo systemctl try-restart birdnet_recording.service
  echo "Removed. REC_CARD is now: $(conf_value REC_CARD)"
}

test_level() {
  local seconds=${1:-5} card sample active=0
  [[ "$seconds" =~ ^[0-9]+$ ]] && [ "$seconds" -ge 1 ] && [ "$seconds" -le 60 ] \
    || die '--test takes 1-60 seconds'
  card=$(conf_value REC_CARD)
  sample=$(mktemp --suffix=.wav)
  if systemctl is-active --quiet birdnet_recording.service; then
    active=1
    sudo systemctl stop birdnet_recording.service
  fi
  echo "Recording ${seconds}s from ${card:-default}; make some noise near the mic..."
  if [ -z "$card" ]; then
    arecord -q -f S16_LE -c1 -r48000 -d "$seconds" "$sample" || true
  else
    arecord -q -D "$card" -f S16_LE -c1 -r48000 -d "$seconds" "$sample" || true
  fi
  [ "$active" = 1 ] && sudo systemctl start birdnet_recording.service
  if [ -s "$sample" ]; then
    sox "$sample" -n stat 2>&1 | grep -E 'Maximum amplitude|RMS +amplitude' || true
    echo 'A maximum amplitude near 0.000 means silence: check the card and mic.'
  else
    echo 'Nothing was recorded; check the card with --list.'
  fi
  rm -f -- "$sample"
}

[ "${EUID:-$(id -u)}" -ne 0 ] || die 'run as the BirdNET-Pi user, not root'
[ -r "$CONF" ] || die "$CONF is missing; install BirdNET-Pi first"

action='' card='' force=0 seconds=''
while [ $# -gt 0 ]; do
  case "$1" in
    --list|--auto|--remove) action=${1#--} ;;
    --card) [ $# -ge 2 ] || usage; action=card; card=$2; shift ;;
    --test)
      action=test
      if [ $# -ge 2 ] && [[ "$2" =~ ^[0-9]+$ ]]; then seconds=$2; shift; fi
      ;;
    --force) force=1 ;;
    *) usage ;;
  esac
  shift
done

case "$action" in
  list) list_cards ;;
  auto) auto_setup "$force" ;;
  card) apply_card "$card" "$force" ;;
  remove) remove_setup ;;
  test) test_level "$seconds" ;;
  *) usage ;;
esac
