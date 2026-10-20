#!/usr/bin/env bash
# Build Procyon FT8 for macOS: a universal (Apple Silicon + Intel) Procyon.app with
# its own Python, then a drag-to-install .dmg and a double-click .pkg installer.
#
#   packaging/macos/build.sh            # app + dmg + pkg, into dist/
#   packaging/macos/build.sh app        # just the .app
#   SIGN_ID="Developer ID Application: Name (TEAMID)" packaging/macos/build.sh
#                                       # sign for distribution (notarize notes at the end)
#
# Needs: macOS with Xcode command line tools (swiftc, lipo, codesign, hdiutil, pkgbuild)
# and internet on the first run (Python and the wheels are cached in packaging/cache/).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CACHE="$ROOT/packaging/cache"
BUILD="$ROOT/packaging/build/macos"
DIST="$ROOT/dist"
WHAT="${1:-all}"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/procyon/__init__.py")"
PY_FULL=3.13.9
PY_MINOR=3.13
PKG_URL="https://www.python.org/ftp/python/$PY_FULL/python-$PY_FULL-macos11.pkg"
SIGN_ID="${SIGN_ID:--}"          # "-" = ad-hoc (runs on this Mac; other Macs show a Gatekeeper warning)
APP="$BUILD/Procyon.app"
FW="$APP/Contents/Frameworks/Python.framework"
PYV="$FW/Versions/$PY_MINOR"
PYLIB="$PYV/lib/python$PY_MINOR"
SITE="$PYLIB/site-packages"

say() { printf '\n==> %s\n' "$*"; }
is_macho() { file "$1" | grep -q 'Mach-O'; }
# rm -rf that refuses to run on an empty path
rmrf() { for p in "$@"; do [ -n "$p" ] && rm -rf -- "$p"; done; return 0; }

[ -n "$VERSION" ] || { echo "can't read the version from procyon/__init__.py"; exit 1; }
mkdir -p "$CACHE" "$DIST"
rmrf "${BUILD:?}"; mkdir -p "$BUILD"

# ---------------------------------------------------------------- Python
say "Python $PY_FULL (universal2, from python.org)"
[ -f "$CACHE/python-$PY_FULL-macos11.pkg" ] || curl -fL -o "$CACHE/python-$PY_FULL-macos11.pkg" "$PKG_URL"
pkgutil --expand-full "$CACHE/python-$PY_FULL-macos11.pkg" "$BUILD/pyexp"
mkdir -p "$APP/Contents/Frameworks" "$APP/Contents/MacOS" "$APP/Contents/Resources" "$FW/Versions"
cp -a "$BUILD/pyexp/Python_Framework.pkg/Payload/Versions/$PY_MINOR" "$FW/Versions/"
ln -s "$PY_MINOR" "$FW/Versions/Current"
ln -s Versions/Current/Python "$FW/Python"
ln -s Versions/Current/Resources "$FW/Resources"
rmrf "$BUILD/pyexp"

# Make the interpreter find its library next to itself, not in /Library/Frameworks
install_name_tool -change "/Library/Frameworks/Python.framework/Versions/$PY_MINOR/Python" \
    "@executable_path/../Python" "$PYV/bin/python$PY_MINOR"
# bin/python3.13 is a stub that re-executes the real interpreter inside Python.app
install_name_tool -change "/Library/Frameworks/Python.framework/Versions/$PY_MINOR/Python" \
    "@executable_path/../../../../Python" "$PYV/Resources/Python.app/Contents/MacOS/Python"

# Extension modules and OpenSSL also point at /Library/Frameworks: make those relative too
FWLIB="/Library/Frameworks/Python.framework/Versions/$PY_MINOR/lib"
for f in "$PYLIB"/lib-dynload/*.so; do
    for dep in $(otool -L "$f" | awk -v p="$FWLIB/" 'index($1, p) == 1 {print $1}' | sort -u); do
        install_name_tool -change "$dep" "@loader_path/../../$(basename "$dep")" "$f" 2>/dev/null
    done
done
for f in "$PYV"/lib/*.dylib; do
    [ -e "$f" ] || continue
    install_name_tool -id "@loader_path/$(basename "$f")" "$f" 2>/dev/null
    for dep in $(otool -L "$f" | awk -v p="$FWLIB/" 'index($1, p) == 1 {print $1}' | sort -u); do
        install_name_tool -change "$dep" "@loader_path/$(basename "$dep")" "$f" 2>/dev/null
    done
done

# Trim what an app doesn't need
for d in test idlelib tkinter turtledemo ensurepip lib2to3 "config-$PY_MINOR-darwin"; do rmrf "$PYLIB/$d"; done
rmrf "$PYLIB"/lib-dynload/_tkinter*
for d in include share Headers Frameworks; do rmrf "$PYV/$d"; done
rmrf "$PYV"/lib/libtcl* "$PYV"/lib/libtk* "$PYV"/lib/tcl* "$PYV"/lib/tk* "$PYV"/lib/Tk* \
     "$PYV"/lib/itcl* "$PYV"/lib/thread* "$PYV"/lib/pkgconfig
rmrf "$PYV"/bin/idle* "$PYV"/bin/pydoc* "$PYV"/bin/2to3* "$PYV"/bin/pip* "$PYV"/bin/*-intel64
find "$PYLIB" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
# python.org's signature is invalid once the binary is edited; everything is re-signed below
rmrf "$PYV/_CodeSignature"

# ---------------------------------------------------------------- Python packages
say "Python packages, one set per CPU, merged into universal binaries"
for arch in arm64 x86_64; do
    # always run: pip skips files already in the cache, and picks up new requirements
    if true; then
        python3 -m pip download -q --only-binary=:all: -r "$HERE/requirements.txt" \
            --python-version "$PY_MINOR" --implementation cp -d "$CACHE/wheels-$arch" \
            --platform "macosx_11_0_$arch" --platform "macosx_10_13_$arch" --platform "macosx_10_15_$arch" \
            --platform "macosx_10_9_$arch" --platform macosx_10_9_universal2 --platform macosx_11_0_universal2
    fi
    mkdir -p "$BUILD/stage-$arch"
    for w in "$CACHE/wheels-$arch"/*.whl; do unzip -q -o "$w" -d "$BUILD/stage-$arch"; done
done

mkdir -p "$SITE"
cp -a "$BUILD/stage-arm64/." "$SITE/"
( cd "$BUILD/stage-x86_64" && find . \( -type f -o -type l \) ) | while read -r f; do
    if [ ! -e "$SITE/$f" ]; then
        mkdir -p "$SITE/$(dirname "$f")"; cp -a "$BUILD/stage-x86_64/$f" "$SITE/$f"
    elif is_macho "$SITE/$f" && ! lipo -archs "$SITE/$f" | grep -q x86_64; then
        lipo -create "$SITE/$f" "$BUILD/stage-x86_64/$f" -output "$SITE/$f.fat" && mv "$SITE/$f.fat" "$SITE/$f"
    fi
done
rmrf "$BUILD/stage-arm64" "$BUILD/stage-x86_64"
find "$SITE" \( -name tests -o -name __pycache__ \) -type d -prune -exec rm -rf {} + 2>/dev/null || true
rmrf "$SITE/numpy/f2py" "$SITE/bin" "$SITE/numpy/_core/include" "$SITE/numpy/distutils"
rmrf "$SITE"/*.dist-info

# ---------------------------------------------------------------- Procyon itself
say "Procyon $VERSION"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/procyon" "$APP/Contents/Resources/"
mkdir -p "$BUILD/swift"
for arch in arm64 x86_64; do
    swiftc -O -target "$arch-apple-macos11" "$HERE/launcher.swift" -o "$BUILD/swift/launcher-$arch"
done
lipo -create "$BUILD/swift/launcher-arm64" "$BUILD/swift/launcher-x86_64" -output "$APP/Contents/MacOS/Procyon"

say "Icon"
swiftc -O "$HERE/make_icon.swift" -o "$BUILD/swift/make_icon"
"$BUILD/swift/make_icon" "$ROOT/procyon/web/icon.svg" "$BUILD/Procyon.iconset"
iconutil -c icns "$BUILD/Procyon.iconset" -o "$APP/Contents/Resources/Procyon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Procyon FT8</string>
  <key>CFBundleDisplayName</key><string>Procyon FT8</string>
  <key>CFBundleIdentifier</key><string>org.procyon.ft8</string>
  <key>CFBundleExecutable</key><string>Procyon</string>
  <key>CFBundleIconFile</key><string>Procyon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Procyon listens to your radio's sound card input to decode FT8 signals.</string>
  <key>NSHumanReadableCopyright</key>
  <string>Procyon FT8 is free software under the GNU GPL v3. Created by Justin Stapleton, KR1TTR.</string>
</dict></plist>
PLIST
cp "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE.txt"

# ---------------------------------------------------------------- Sign (inside-out)
if [ "$SIGN_ID" = - ]; then
    say "Signing (ad-hoc)"
    SIGN_OPTS=(--force --sign - --timestamp=none)
else
    say "Signing ($SIGN_ID)"
    SIGN_OPTS=(--force --sign "$SIGN_ID" --timestamp --options runtime)
fi
find "$APP" -type f | while read -r f; do
    if is_macho "$f"; then codesign "${SIGN_OPTS[@]}" "$f" >/dev/null 2>&1 || { echo "codesign failed: $f"; exit 1; }; fi
done
codesign "${SIGN_OPTS[@]}" "$PYV/Resources/Python.app" >/dev/null 2>&1
codesign "${SIGN_OPTS[@]}" "$FW" >/dev/null 2>&1
codesign "${SIGN_OPTS[@]}" "$APP" >/dev/null 2>&1
codesign --verify --deep --strict "$APP"

# Nothing may still depend on a system-wide Python install
LEFT=""
while read -r f; do
    is_macho "$f" || continue
    [ "$f" = "$PYV/Python" ] && continue          # the library itself names that path as its id
    if otool -L "$f" | tail -n +2 | grep -q "/Library/Frameworks/Python.framework"; then LEFT="$LEFT$f"$'\n'; fi
done < <(find "$APP" -type f)
if [ -n "$LEFT" ]; then echo "These still link to /Library/Frameworks/Python.framework:"; echo "$LEFT"; exit 1; fi

# ---------------------------------------------------------------- Self-test on both CPUs
say "Self-test (arm64 and, via Rosetta, x86_64)"
PY="$PYV/bin/python$PY_MINOR"
for arch in arm64 x86_64; do
    if [ "$arch" = x86_64 ] && ! arch -x86_64 /usr/bin/true 2>/dev/null; then echo "  (no Rosetta: skipping x86_64)"; continue; fi
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$APP/Contents/Resources" PYTHONNOUSERSITE=1 arch -"$arch" "$PY" -c \
      'import platform, ssl, certifi, numpy, aiohttp, sounddevice, procyon.server, procyon.radio_detect, procyon.wsjtx_install; ssl.create_default_context(cafile=certifi.where()); print("  ", platform.machine(), "ok: numpy", numpy.__version__, "aiohttp", aiohttp.__version__, "sounddevice", sounddevice.__version__)'
done

# The self-test must not have touched the sealed bundle
codesign --verify --deep --strict "$APP" || { echo "signature broken after self-test"; exit 1; }

if [ "$WHAT" = app ]; then
    rmrf "$DIST/Procyon.app"; cp -a "$APP" "$DIST/"
    say "Built $DIST/Procyon.app"; exit 0
fi

# ---------------------------------------------------------------- DMG
say "Disk image"
DMG="$DIST/Procyon-$VERSION-macos.dmg"
STAGE="$BUILD/dmg"; mkdir -p "$STAGE"
cp -a "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "$HERE/Read Me First.txt" "$STAGE/"
rm -f "$DMG"
hdiutil create -quiet -volname "Procyon FT8 $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -imagekey zlib-level=9 "$DMG"
if [ "$SIGN_ID" != - ]; then codesign --force --sign "$SIGN_ID" --timestamp "$DMG"; fi

# ---------------------------------------------------------------- PKG
say "Installer package"
PKG="$DIST/Procyon-$VERSION-macos.pkg"
pkgbuild --quiet --component "$APP" --install-location /Applications "$BUILD/component.pkg"
productbuild --quiet --package "$BUILD/component.pkg" "$PKG"

say "Done"
ls -lh "$DMG" "$PKG"
cat <<EOF

Unsigned builds: on another Mac, macOS blocks the first launch. Open System Settings >
Privacy & Security, scroll down and click 'Open Anyway' (see 'Read Me First.txt' in the disk image).
To remove that step, sign with an Apple Developer ID and notarize:
  SIGN_ID='Developer ID Application: ...' packaging/macos/build.sh
  xcrun notarytool submit dist/Procyon-$VERSION-macos.dmg --keychain-profile <profile> --wait
  xcrun stapler staple dist/Procyon-$VERSION-macos.dmg
EOF
