#!/usr/bin/env bash
# launch_from_icon.sh — what Warrant-Demo.desktop runs.
#
# The icon file can be copied to any Linux desktop: it does not know where the
# repo is. This script finds it, points the icon at the Warrant picture, and
# starts the demo.
#
# Which copy of the repo (searched only under $HOME, so /sandbox is never used):
#   1. a copy with warrant/scripts/demo.env  (host-only file, never committed)
#   2. otherwise ~/Warrant
#   3. otherwise the most recently changed copy
#
# Usage: launch_from_icon.sh [path-of-the-.desktop-file]

set -uo pipefail

DESKTOP_FILE="${1:-}"
say() { echo "[ICON] $*"; }

found=()
while IFS= read -r script; do
  found+=("${script%/warrant/scripts/start_demo.sh}")
done < <(find "$HOME" -maxdepth 6 -path '*/warrant/scripts/start_demo.sh' -not -path '*/.*' 2>/dev/null)

if [ "${#found[@]}" -eq 0 ]; then
  say "Warrant repo not found under $HOME (looked for warrant/scripts/start_demo.sh)."
  read -r -p "Press Enter to close." _
  exit 1
fi

repo=""
for r in "${found[@]}"; do
  [ -f "$r/warrant/scripts/demo.env" ] && { repo="$r"; break; }
done
if [ -z "$repo" ]; then
  for r in "${found[@]}"; do
    [ "$r" = "$HOME/Warrant" ] && { repo="$r"; break; }
  done
fi
if [ -z "$repo" ]; then
  newest=0
  for r in "${found[@]}"; do
    t=$(date -r "$r/warrant/scripts/start_demo.sh" +%s 2>/dev/null || echo 0)
    [ "$t" -gt "$newest" ] && { newest=$t; repo="$r"; }
  done
fi

[ "${#found[@]}" -gt 1 ] && say "found ${#found[@]} copies; using the one below"
say "repo: $repo"

# Point the icon at the picture (takes effect from the next time it is shown).
picture="$repo/warrant/scripts/warrant-icon.png"
if [ -n "$DESKTOP_FILE" ] && [ -w "$DESKTOP_FILE" ] && [ -f "$picture" ] \
   && ! grep -qxF "Icon=$picture" "$DESKTOP_FILE"; then
  content=""
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in Icon=*) line="Icon=$picture" ;; esac
    content+="$line"$'\n'
  done < "$DESKTOP_FILE"
  printf '%s' "$content" > "$DESKTOP_FILE"   # in place: keeps permissions and trust
  say "icon picture set"
fi

exec bash "$repo/warrant/scripts/start_demo.sh"
