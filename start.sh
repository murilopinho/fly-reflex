#!/usr/bin/env bash
# Fly Reflex — one command: open the game, start the daemons, run the fly.
#
#   ./start.sh           open FNF and let the fly play it for real
#   ./start.sh --demo    just watch the brain, no game, no key presses
#   ./start.sh --train   score her on the synthetic ladder; no game, no keyboard
#   ./start.sh --watch   open the game but only print presses, inject nothing
#   ./start.sh --portal  same as normal, but ask for screen-share permission
#                        once: capture then runs silently at full speed
#                        instead of popping a Spectacle notification per frame
#
# Flags combine, in any order: `--watch --portal` is the safe pair (silent
# capture, nothing injected). They used to not combine — only the first
# argument was read, so the second was silently dropped.
#
# You still pick the song yourself — the fly reads falling notes, it cannot
# read menus.
#
# The fly starts DISARMED and will not touch the keyboard until you press F9.
# Press F9 again, or just type anything at all, and it lets go immediately.
set -euo pipefail
cd "$(dirname "$0")"

GAME_URL="https://www.kbhgames.com/game/friday-night-funkin/"
MODES=" $* "

if [[ "$MODES" == *" --demo "* ]]; then
  exec python3 run.py --demo
fi

if [[ "$MODES" == *" --train "* ]]; then
  exec python3 train.py
fi

# ydotool: the daemon must be up and its socket writable by us
if ! systemctl is-active --quiet ydotool; then
  echo "ydotoold is not running. Start it with:  sudo systemctl start ydotool"
  exit 1
fi
export YDOTOOL_SOCKET="${YDOTOOL_SOCKET:-/tmp/.ydotool_socket}"
if [[ ! -w "$YDOTOOL_SOCKET" ]]; then
  echo "cannot write to $YDOTOOL_SOCKET — see the systemd override in STATUS.md"
  exit 1
fi

# screen capture daemon (Spectacle in DBus mode — far faster than re-spawning it)
if ! gdbus call --session --dest org.freedesktop.DBus \
      --object-path /org/freedesktop/DBus \
      --method org.freedesktop.DBus.ListNames 2>/dev/null | grep -q org.kde.Spectacle; then
  echo "starting screen capture daemon..."
  nohup spectacle -s >/dev/null 2>&1 &
  sleep 2
fi

if ! pgrep -f "kbhgames.com/game/friday-night-funkin" >/dev/null; then
  echo "opening Friday Night Funkin'..."
  # not --kiosk: kiosk is exclusive fullscreen, and KWin puts that above every
  # stays-on-top window, so the brain overlay was invisible for the whole run
  nohup firefox --new-window "$GAME_URL" >/dev/null 2>&1 &
  sleep 6
fi

cat <<'EOF'

  Click Play Now, pick a song, and start it.
  Then press F9 to hand the fly the keyboard.

  F9 again — or any other key — takes it straight back, so you can
  type while it is still running. The panel shows who has the keyboard.
  Drag the panel anywhere; Esc closes it.

EOF

EXTRA=()
[[ "$MODES" == *" --portal "* ]] && EXTRA+=(--portal)
[[ "$MODES" == *" --watch "* ]] && EXTRA+=(--dry-run)
exec python3 run.py "${EXTRA[@]}"
