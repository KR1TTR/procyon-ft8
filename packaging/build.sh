#!/usr/bin/env bash
# Build Procyon's release packages into dist/.
#   packaging/build.sh            all three
#   packaging/build.sh deb        Debian/Ubuntu/Mint/Raspberry Pi OS package
#   packaging/build.sh linux      Linux zip for other distributions
#   packaging/build.sh windows    Windows zip with Python included, and the
#                                 Windows installer (needs NSIS: apt install nsis,
#                                 or MAKENSIS=/path/to/makensis)
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
HAMLIB_WIN="4.7.2"           # Hamlib for Windows (radio control), from Hamlib's GitHub releases
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
Recommends: libhamlib-utils
Suggests: wsjtx
Maintainer: $MAINTAINER
Description: friendly FT8 and FT4 station software
 Procyon is FT8/FT4 station software with its own decoder and encoder
 and a clean, modern web interface: waterfall, band activity with filters, automatic QSO
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
      --implementation cp --abi "cp$tag" -d "$CACHE/wheels-win" numpy aiohttp sounddevice pyserial
  mkdir -p "$d/$top/python/Lib/site-packages"
  for w in "$CACHE"/wheels-win/*.whl; do unzip -qo "$w" -d "$d/$top/python/Lib/site-packages"; done
  # 3. Procyon and its launchers
  copy_app "$d/$top"
  cp "$PKG/Procyon.bat" "$PKG/Procyon DEMO ONLY - no radio.bat" "$ROOT/LICENSE" "$d/$top/"
  sed "s/{VERSION}/$VERSION/; s/{DATE}/$DATE/" "$PKG/README-Windows.txt" > "$d/$top/README.txt"
  crlf "$d/$top/Procyon.bat" "$d/$top/Procyon DEMO ONLY - no radio.bat" "$d/$top/README.txt" "$d/$top/LICENSE"
  # 4. Hamlib (rigctld/rigctl for radio control), so Windows needs no WSJT-X
  add_hamlib_windows "$d/$top/hamlib"
  rm -f "$DIST/$top.zip"
  (cd "$d" && zip -qr "$DIST/$top.zip" "$top")
  echo "  built dist/$top.zip ($(du -h "$DIST/$top.zip" | cut -f1))"
  build_windows_installer "$d/$top"
}

# Hamlib's official 64-bit Windows build: only the two programs Procyon runs,
# the DLLs they need, and Hamlib's licence files. The download is checked
# against Hamlib's own SHA256SUM file. Hamlib is GPL/LGPL, so its source
# code goes into dist/ too, to be attached to the release next to the build.
add_hamlib_windows() {
  local out="$1" v="$HAMLIB_WIN" c="$CACHE/hamlib-$HAMLIB_WIN"
  local url="https://github.com/Hamlib/Hamlib/releases/download/$v"
  mkdir -p "$c"
  for f in "hamlib-w64-$v.zip" "SHA256SUM-w64-$v" "hamlib-$v.tar.gz" "SHA256SUM-$v"; do
    [ -f "$c/$f" ] || curl -fsSL -o "$c/$f" "$url/$f"
  done
  (cd "$c" && grep " hamlib-w64-$v.zip\$" "SHA256SUM-w64-$v" | sha256sum -c --quiet - \
           && sha256sum -c --quiet "SHA256SUM-$v") \
    || { echo "  Hamlib download doesn't match its checksum - NOT BUILT" >&2; exit 1; }
  local tmp="$BUILD/hamlib-w64"
  rm -rf "$tmp" "$out"; mkdir -p "$tmp" "$out/bin"
  unzip -q "$c/hamlib-w64-$v.zip" -d "$tmp"
  local src="$tmp/hamlib-w64-$v"
  cp "$src"/bin/{rigctld.exe,rigctl.exe,libhamlib-4.dll,libusb-1.0.dll,libgcc_s_seh-1.dll,libwinpthread-1.dll} "$out/bin/"
  cp "$src"/{COPYING.txt,COPYING.LIB.txt,AUTHORS.txt,THANKS.txt} "$out/"
  cat > "$out/README.txt" <<EOT
Hamlib $v - radio control for Procyon (rigctld.exe and rigctl.exe)

These are the official 64-bit Windows programs from the Hamlib project
(https://hamlib.github.io), unchanged. Hamlib is free software: the programs
are under the GNU General Public License (COPYING.txt), the library under the
GNU Lesser General Public License (COPYING.LIB.txt).

Source code: hamlib-$v.tar.gz, attached to the same Procyon release on GitHub
(https://github.com/KR1TTR/procyon-ft8/releases), or from
https://github.com/Hamlib/Hamlib/releases/tag/$v
EOT
  crlf "$out/README.txt"
  cp "$c/hamlib-$v.tar.gz" "$DIST/"
  rm -rf "$tmp"
}

# The installer packs the same folder as the zip (Start-menu shortcuts run
# pythonw.exe, so there's no console window).
build_windows_installer() {
  local src="$1" out="$DIST/Procyon-$VERSION-windows-setup.exe"
  local nsis="${MAKENSIS:-$(command -v makensis || true)}"
  if [ -z "$nsis" ]; then
    echo "  skipped the Windows installer: NSIS isn't installed (sudo apt install nsis)"
    return
  fi
  rm -f "$out"
  (cd "$PKG/windows" && "$nsis" -V2 -DVERSION="$VERSION" -DSRC="$src" -DOUT="$out" procyon.nsi)
  echo "  built dist/$(basename "$out") ($(du -h "$out" | cut -f1))"
}

echo "Procyon $VERSION ($DATE)"
# The tests run first: a package is only built if every one passes.
# (SKIP_TESTS=1 packaging/build.sh ... to build anyway, e.g. while debugging.)
if [ "${SKIP_TESTS:-0}" != 1 ]; then
  if ! python3 tests/run_tests.py > "$BUILD.tests.log" 2>&1; then
    tail -n 25 "$BUILD.tests.log"
    echo "  NOT BUILT: a test failed (full output in build.tests.log)" >&2
    exit 1
  fi
  echo "  tests: $(tail -n 1 "$BUILD.tests.log" | sed 's/^ *//')"
  rm -f "$BUILD.tests.log"
fi
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
    Procyon-"$VERSION"-windows.zip Procyon-"$VERSION"-windows-setup.exe procyon-"$VERSION"-arch.tar.gz \
    $( [ -f "Procyon-$VERSION-windows.zip" ] && echo hamlib-"$HAMLIB_WIN".tar.gz ) 2>/dev/null) > SHA256SUMS )
echo "  wrote dist/SHA256SUMS"
