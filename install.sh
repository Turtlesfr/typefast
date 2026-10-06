#!/usr/bin/env bash
# Install TypeFast for the current user. Run from a clone of the repository:
#   ./install.sh               install and start at login
#   ./install.sh --no-autostart
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOSTART=1
[[ "${1:-}" == "--no-autostart" ]] && AUTOSTART=0

echo "==> TypeFast installer"

if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
    echo
    echo "Warning: you are in a Wayland session. TypeFast only works under X11."
    echo "Before using it, log out, pick \"Ubuntu on Xorg\" (gear icon on the login screen) and log back in."
    echo
fi

APT_PACKAGES=(python3-venv python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
              xclip libportaudio2)
missing=()
for pkg in "${APT_PACKAGES[@]}"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if (( ${#missing[@]} )); then
    echo "==> Installing system packages: ${missing[*]}"
    sudo apt-get install -y "${missing[@]}"
fi

# The system Python, because PyGObject from apt is built for it.
echo "==> Creating the Python environment"
/usr/bin/python3 -m venv --system-site-packages "$DIR/.venv"
"$DIR/.venv/bin/pip" install --quiet --upgrade pip
"$DIR/.venv/bin/pip" install --quiet -e "$DIR"

echo "==> Adding the typefast command and the app launcher"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
ln -sf "$DIR/.venv/bin/typefast" "$HOME/.local/bin/typefast"

write_desktop() {
    cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Name=TypeFast
Comment=Dictate anywhere: live speech to text with a polished final pass
Exec=$HOME/.local/bin/typefast
Icon=$DIR/typefast/icons/typefast.svg
Terminal=false
Categories=Utility;Accessibility;
Keywords=dictation;speech;voice;transcription;
EOF
    if [[ "${2:-}" == "autostart" ]]; then
        echo "X-GNOME-Autostart-enabled=true" >> "$1"
    fi
}
write_desktop "$HOME/.local/share/applications/typefast.desktop"
if (( AUTOSTART )); then
    mkdir -p "$HOME/.config/autostart"
    write_desktop "$HOME/.config/autostart/typefast.desktop" autostart
fi

echo "==> Starting TypeFast"
"$HOME/.local/bin/typefast" quit >/dev/null 2>&1 || true
nohup "$HOME/.local/bin/typefast" >/dev/null 2>&1 &

cat <<EOF

Done. TypeFast is in the tray (microphone icon).
  * First launch opens Settings: paste your AssemblyAI API key (free at https://www.assemblyai.com).
  * Tap Ctrl 3 times to open the overlay, pick a language, press Enter, and talk.
  * Tap Ctrl 3 times again to finish: the live text is replaced by the polished version.
  * Optional: bind "typefast toggle" to a shortcut in Settings > Keyboard > Custom Shortcuts.
EOF
