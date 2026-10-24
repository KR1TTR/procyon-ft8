#!/usr/bin/env bash
# Build Procyon's release packages into dist/.
#   packaging/build.sh            all three
#   packaging/build.sh deb        Debian/Ubuntu/Mint/Raspberry Pi OS package
#   packaging/build.sh linux      Linux zip for other distributions
#   packaging/build.sh windows    Windows zip with Python included
#   packaging/build.sh arch       Arch Linux PKGBUILD + the Linux zip (builds that first)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PKG="$ROOT/packaging"
VERSION=$(python3 -c 'import procyon; print(procyon.__version__)')
DATE=$(python3 -c 'import procyon; print(procyon.RELEASE_DATE)')
MAINTAINER="Justin Stapleton, KR1TTR <procyon@kr1ttr.radio>"
DIST="$ROOT/dist"
BUILD="$ROOT/build"
CACHE="$PKG/cache"           # downloads for the Windows build, reused between builds
PY_WIN="3.12.10"             # embeddable Python for Windows (python.org)
mkdir -p "$DIST" "$CACHE"
targets=("${@:-deb linux windows arch}")
[ $# -eq 0 ] && targets=(deb linux windows arch)

copy_app() {   # copy the procyon package (without caches) into $1
  mkdir -p "$1"
  tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' -cf - procyon | tar -C "$1" -xf -
}

crlf() { sed -i 's/\r\?$/\r/' "$@"; }   # Windows line endings

build_deb() {
  local d="$BUILD/deb" name="procyon_${VERSION}_all.deb"
  rm -rf "$d"
  copy_app "$d/usr/lib/procyon"
  install -Dm755 /dev/stdin "$d/usr/bin/procyon" <<EOF
#!/bin/sh
# Procyon $VERSION
export PYTHONPATH=/usr/lib/procyon\${PYTHONPATH:+:\$PYTHONPATH}
exec python3 -m procyon "\$@"
EOF
  install -Dm644 "$PKG/procyon.desktop" "$d/usr/share/applications/procyon.desktop"
  install -Dm644 "$ROOT/procyon/web/icon.svg" "$d/usr/share/icons/hicolor/scalable/apps/procyon.svg"
  install -Dm644 "$ROOT/README.md" "$d/usr/share/doc/procyon/README.md"
  install -Dm644 /dev/stdin "$d/usr/share/doc/procyon/copyright" <<EOF
Procyon $VERSION ($DATE)
Created by Justin Stapleton, KR1TTR, with Claude Code (Claude Opus 5.5).

License: GPL-3+
 On Debian systems, the full text of the GNU General Public License
 version 3 can be found in /usr/share/common-licenses/GPL-3.
EOF
  mkdir -p "$d/DEBIAN"
  local size; size=$(du -sk "$d/usr" | cut -f1)
  cat > "$d/DEBIAN/control" <<EOF
Package: procyon
Version: $VERSION
Section: hamradio
Priority: optional
Architecture: all
Installed-Size: $size
Depends: python3 (>= 3.10), python3-numpy, python3-aiohttp, pulseaudio-utils
Recommends: wsjtx, libhamlib-utils
Maintainer: $MAINTAINER
Description: friendly FT8 station built on the WSJT-X decoder
 Procyon puts a clean, modern web interface on WSJT-X's own FT8 decoder
 and encoder: waterfall, band activity with filters, automatic QSO
 sequencing, logbook, radio control through Hamlib, GridTracker support,
 and PIN-protected use from a tablet on your home network.
 Created by Justin Stapleton, KR1TTR, with Claude Code (Claude Opus 5.5).
EOF
  cat > "$d/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
EOF
  chmod 755 "$d/DEBIAN/postinst"
  fakeroot dpkg-deb --build "$d" "$DIST/$name" >/dev/null
  echo "  built dist/$name ($(du -h "$DIST/$name" | cut -f1))"
}

# Arch: a folder with the PKGBUILD and the Linux zip it installs from, as a
# tarball. Users unpack it and run "makepkg -si" inside.
build_arch() {
  local top="procyon-$VERSION-arch" d="$BUILD/arch" zip="procyon-$VERSION-linux.zip"
  [ -f "$DIST/$zip" ] || build_linux_zip
  rm -rf "$d"; mkdir -p "$d/$top"
  cp "$DIST/$zip" "$d/$top/"
  sed -e "s/@VERSION@/$VERSION/" -e "s/@SHA256@/$(sha256sum "$DIST/$zip" | cut -d' ' -f1)/" \
    "$PKG/arch/PKGBUILD.in" > "$d/$top/PKGBUILD"
  tar -C "$d" --owner=0 --group=0 -czf "$DIST/$top.tar.gz" "$top"
  echo "  built dist/$top.tar.gz ($(du -h "$DIST/$top.tar.gz" | cut -f1))"
}

build_linux_zip() {
  local top="procyon-$VERSION-linux" d="$BUILD/linux"
  rm -rf "$d"; mkdir -p "$d/$top"
  copy_app "$d/$top"
  cp "$ROOT/run.sh" "$ROOT/README.md" "$ROOT/LICENSE" "$PKG/procyon.desktop" "$d/$top/"
  install -m755 "$PKG/linux-setup.sh" "$d/$top/setup.sh"
  chmod 755 "$d/$top/run.sh"
  rm -f "$DIST/$top.zip"
  (cd "$d" && zip -qr "$DIST/$top.zip" "$top")
  echo "  built dist/$top.zip ($(du -h "$DIST/$top.zip" | cut -f1))"
}

build_windows_zip() {
  local top="Procyon-$VERSION-windows" d="$BUILD/windows"
  local embed="python-$PY_WIN-embed-amd64.zip" pyver="${PY_WIN%.*}"
  local tag="${pyver/./}"     # 3.12 -> 312
  rm -rf "$d"; mkdir -p "$d/$top/python"
  # 1. Python itself (official embeddable build from python.org)
  [ -f "$CACHE/$embed" ] || curl -fsSL -o "$CACHE/$embed" "https://www.python.org/ftp/python/$PY_WIN/$embed"
  unzip -q "$CACHE/$embed" -d "$d/$top/python"
  # let it find its site-packages and Procyon (one folder up)
  printf 'python%s.zip\r\n.\r\nLib\\site-packages\r\n..\r\nimport site\r\n' "$tag" > "$d/$top/python/python$tag._pth"
  # 2. Procyon's libraries, as Windows builds from PyPI
  mkdir -p "$CACHE/wheels-win"
  python3 -m pip download -q --only-binary=:all: --platform win_amd64 --python-version "$pyver" \
      --implementation cp --abi "cp$tag" -d "$CACHE/wheels-win" numpy aiohttp sounddevice
  mkdir -p "$d/$top/python/Lib/site-packages"
  for w in "$CACHE"/wheels-win/*.whl; do unzip -qo "$w" -d "$d/$top/python/Lib/site-packages"; done
  # 3. Procyon and its launchers
  copy_app "$d/$top"
  cp "$PKG/Procyon.bat" "$PKG/Procyon DEMO ONLY - no radio.bat" "$ROOT/LICENSE" "$d/$top/"
  sed "s/{VERSION}/$VERSION/; s/{DATE}/$DATE/" "$PKG/README-Windows.txt" > "$d/$top/README.txt"
  crlf "$d/$top/Procyon.bat" "$d/$top/Procyon DEMO ONLY - no radio.bat" "$d/$top/README.txt" "$d/$top/LICENSE"
  rm -f "$DIST/$top.zip"
  (cd "$d" && zip -qr "$DIST/$top.zip" "$top")
  echo "  built dist/$top.zip ($(du -h "$DIST/$top.zip" | cut -f1))"
}

echo "Procyon $VERSION ($DATE)"
for t in "${targets[@]}"; do
  case "$t" in
    deb) build_deb ;;
    linux) build_linux_zip ;;
    windows) build_windows_zip ;;
    arch) build_arch ;;
    *) echo "unknown target: $t" >&2; exit 1 ;;
  esac
done

# Checksums for whatever this version has in dist/ (any targets built so far)
( cd "$DIST" && sha256sum $(ls procyon_"$VERSION"_all.deb procyon-"$VERSION"-linux.zip \
    Procyon-"$VERSION"-windows.zip procyon-"$VERSION"-arch.tar.gz 2>/dev/null) > SHA256SUMS )
echo "  wrote dist/SHA256SUMS"
