#!/usr/bin/env bash
# install_icon.sh — put a "Warrant Demo" icon on the GB10 desktop (Linux).
#
# Run once on the machine that runs the demo, from wherever the repo is:
#     warrant/scripts/install_icon.sh
#
# Writes warrant-demo.desktop (pointing at this repo's start_demo.sh) to the
# applications menu and the desktop. Run again if the repo moves.

set -euo pipefail

if [ "$(uname -s)" != "Linux" ]; then
  echo "install_icon.sh is for the GB10 (Linux). On this machine run warrant/scripts/start_demo.sh directly."
  exit 1
fi

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
START="$SCRIPTS/start_demo.sh"
ICON="$SCRIPTS/warrant-icon.png"
[ -f "$ICON" ] || ICON="media-playback-start"  # stock icon if the picture is missing
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

mkdir -p "$APPS"
cat > "$APPS/warrant-demo.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Warrant Demo
Comment=Start the Warrant demo (reset, start all services, open the dashboard). The incident is triggered separately.
Exec=bash "$START"
Icon=$ICON
Terminal=true
Categories=Utility;
EOF
chmod +x "$APPS/warrant-demo.desktop"
echo "installed: $APPS/warrant-demo.desktop"

if [ -d "$DESKTOP_DIR" ]; then
  cp "$APPS/warrant-demo.desktop" "$DESKTOP_DIR/"
  chmod +x "$DESKTOP_DIR/warrant-demo.desktop"
  # GNOME only launches desktop icons marked as trusted.
  gio set "$DESKTOP_DIR/warrant-demo.desktop" metadata::trusted true 2>/dev/null || true
  echo "installed: $DESKTOP_DIR/warrant-demo.desktop"
  echo "If the icon shows a warning, right-click it and choose 'Allow Launching'."
fi
