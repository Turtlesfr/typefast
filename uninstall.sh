#!/usr/bin/env bash
# Remove TypeFast for the current user. Add --purge to also delete settings,
# vocabulary and history.
set -euo pipefail

"$HOME/.local/bin/typefast" quit >/dev/null 2>&1 || true
rm -f "$HOME/.local/bin/typefast" \
      "$HOME/.local/share/applications/typefast.desktop" \
      "$HOME/.config/autostart/typefast.desktop"

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "$HOME/.config/typefast" "$HOME/.local/share/typefast"
    echo "TypeFast removed, with its settings, vocabulary and history."
else
    echo "TypeFast removed. Settings and vocabulary are kept in ~/.config/typefast (use --purge to delete them)."
fi
echo "You can delete this folder, including .venv, if you don't plan to reinstall."
