#!/usr/bin/env bash
# Procyon FT8 - setup for the Linux zip (any distribution).
# Checks what Procyon needs, says exactly what to install if anything is
# missing, and adds Procyon to your applications menu. Needs no admin rights.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ok=1
say() { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

say ""
say "  Procyon FT8 setup"
say "  -----------------"

# Which package manager, for the install hints
if have apt; then PM="sudo apt install"; P_NUMPY=python3-numpy; P_AIOHTTP=python3-aiohttp; P_PULSE=pulseaudio-utils; P_HAMLIB=libhamlib-utils
elif have dnf; then PM="sudo dnf install"; P_NUMPY=python3-numpy; P_AIOHTTP=python3-aiohttp; P_PULSE=pulseaudio-utils; P_HAMLIB=hamlib
elif have pacman; then PM="sudo pacman -S"; P_NUMPY=python-numpy; P_AIOHTTP=python-aiohttp; P_PULSE=libpulse; P_HAMLIB=hamlib
else PM="(your package manager) install"; P_NUMPY=numpy; P_AIOHTTP=aiohttp; P_PULSE=pulseaudio-utils; P_HAMLIB=hamlib
fi
missing=()

if ! have python3; then
  say "  ✗ Python 3 is missing"; missing+=(python3); ok=0
else
  say "  ✓ Python $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { say "  ✗ Python 3.10 or newer is needed"; ok=0; }
  python3 -c 'import numpy' 2>/dev/null && say "  ✓ numpy" || { say "  ✗ numpy is missing"; missing+=("$P_NUMPY"); ok=0; }
  python3 -c 'import aiohttp' 2>/dev/null && say "  ✓ aiohttp" || { say "  ✗ aiohttp is missing"; missing+=("$P_AIOHTTP"); ok=0; }
fi

if have parec && have pacat; then
  say "  ✓ PulseAudio/PipeWire audio tools"
elif python3 -c 'import sounddevice' 2>/dev/null; then
  say "  ✓ sounddevice (PortAudio) audio"
else
  say "  ✗ No audio tools: install the PulseAudio tools (or: pip install sounddevice)"; missing+=("$P_PULSE"); ok=0
fi

if have jt9 && have ft8code; then
  say "  ✓ WSJT-X (jt9 and ft8code)"
else
  say "  ✗ WSJT-X is missing - install it from https://wsjt.sourceforge.io (or your package manager)"; missing+=(wsjtx); ok=0
fi

if have rigctld || have rigctld-wsjtx; then
  say "  ✓ Hamlib rigctld (radio control)"
else
  say "  ! Hamlib rigctld not found - only needed for radio control (CAT/PTT)"; missing+=("$P_HAMLIB")
fi

# Menu entry and icon, for this user only
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$APPS" "$ICONS"
cp "$HERE/procyon/web/icon.svg" "$ICONS/procyon.svg"
sed "s|^Exec=.*|Exec=\"$HERE/run.sh\"|" "$HERE/procyon.desktop" > "$APPS/procyon.desktop"
chmod +x "$HERE/run.sh"
have update-desktop-database && update-desktop-database "$APPS" >/dev/null 2>&1
say "  ✓ Added Procyon FT8 to your applications menu"

say ""
if [ ${#missing[@]} -gt 0 ]; then
  say "  To install what's missing:"
  say "    $PM ${missing[*]}"
  say ""
fi
if [ $ok = 1 ]; then
  say "  All set. Start Procyon from your menu, or run:  $HERE/run.sh"
  say "  New to it? Try:  $HERE/run.sh --demo"
else
  say "  Install the items above, then run this setup again."
fi
say ""
