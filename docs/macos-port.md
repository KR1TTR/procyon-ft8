# macOS port: what changed (on top of Procyon 0.9.2)

Done 18 October 2026 on an Apple Silicon Mac (macOS 26), tested with a Yaesu
FT-991A. The version number is still 0.9.2; bump it as you see fit.

## Result

- `dist/Procyon-0.9.2-macos.dmg` : drag-to-Applications disk image
- `dist/Procyon-0.9.2-macos.pkg` : double-click installer (installs to /Applications)
- Universal app (Apple Silicon + Intel), its own Python inside, macOS 11+.
- Unsigned (ad-hoc signed). On another Mac the first launch needs
  System Settings > Privacy & Security > **Open Anyway**. Signing and
  notarizing needs an Apple Developer ID; `build.sh` supports `SIGN_ID=`.

Verified on the Mac: install from the DMG, launch, Quit (server and rigctld both
stop), CAT control of the FT-991A (connect, read frequency, change bands
40/20/15/10 m and back), CAT PTT on/off with no audio. Verified: the WSJT-X
installer downloads and installs a native arm64 WSJT-X into a scratch folder.

**Not verified:** a real over-the-air FT8 transmission and TX audio level/ALC;
Demo Mode toggled from the app menu; the Intel build running on a real Intel Mac
(the bundled Python and packages were imported under Rosetta only); radio
auto-detection on anything except the FT-991A; Linux/Windows after these edits
(changes were written to leave them alone; the new modules degrade gracefully).

## New files

| File | What it is |
|---|---|
| `packaging/macos/build.sh` | Builds Procyon.app, the .dmg and the .pkg (`build.sh app` = app only). Run on a Mac with Xcode command line tools. |
| `packaging/macos/launcher.swift` | Native launcher (the app's executable): starts the bundled Python server, Dock icon, Quit, Open in Browser, Demo Mode toggle, writes `~/Library/Logs/Procyon/procyon.log`. Quit sends SIGTERM so Procyon halts TX and stops rigctld. |
| `packaging/macos/make_icon.swift` | Renders `procyon/web/icon.svg` onto a macOS-style tile and writes the .iconset. |
| `packaging/macos/requirements.txt` | Pinned Python packages bundled in the app (numpy, aiohttp, sounddevice, certifi). |
| `packaging/macos/Read Me First.txt` | Goes in the DMG: install steps, Open Anyway, microphone permission. |
| `procyon/radio_detect.py` | "Detect my radio" and "Test radio". See below. |
| `procyon/wsjtx_install.py` | "Install WSJT-X for me" (macOS only). See below. |
| `CHANGES-MACOS.md`, `CLAUDE.md` | These notes. |

## Changed files

- `procyon/system.py`
  - macOS WSJT-X lookup: `/Applications` and `~/Applications` (`wsjt*.app`,
    `JTDX*.app`), then Spotlight (`mdfind`) for anywhere else. It deliberately
    does **not** glob `~/Desktop` / `~/Downloads`: from an app bundle that makes
    macOS show a permission dialog which blocks startup.
  - `find_cty()` now also looks in `wsjtx.app/Contents/Resources/wsjtx/cty.dat`
    (the real location; before, country lookups silently found no file on Mac).
  - Bluetooth and debug-console serial ports are hidden on macOS.
  - New helper `spotlight(*mdfind_args)`.
- `procyon/rig.py`
  - rigctld now runs with `-vv` and its stderr is captured (`_drain`), logged, and
    translated to plain advice by `_why()` ("The radio isn't answering - check its
    model, the serial port and the baud rate...", port missing, port busy).
    Cause of the original failure: with a wrong model/baud rigctld does not exit,
    it keeps retrying silently, and Procyon only saw "Connect call failed".
  - `poll()` sets an error on `RPRT -n` replies and clears it when the radio recovers.
- `procyon/engine.py`
  - New commands `detect_radio`, `rig_test`, `install_wsjtx` (local client only, not
    from a tablet). `_radio_job()` stops Procyon's own rigctld while serial work runs
    (it owns the port), then restarts it. `_install_wsjtx()` runs in the background
    and broadcasts `{"type":"install", ...}` progress messages.
  - `state()` now includes `can_install_wsjtx`.
  - The 2 s rig poll pushes state when the **error text** changes, not only
    connected/frequency (otherwise the UI never saw the new messages).
- `procyon/web/index.html`, `app.js`, `style.css`
  - Settings > Radio: **Detect my radio** and **Test radio** buttons with a status line.
  - "WSJT-X wasn't found" banner: **Install WSJT-X for me** button with progress (macOS).
- `README.md`: macOS row in the install table, build instructions, Detect/Test docs,
  file table, removed "macOS testing" from the to-do list.

## How the new pieces work

**Radio detection** (`radio_detect.py`; posix only, uses `termios`, no pyserial).
For each USB serial port, at baud rates radios ship with, it asks "who are you?":
Yaesu/Kenwood/Elecraft `ID;` and Icom CI-V "read frequency" at default addresses.
It maps the answer to a Hamlib model by name, so model numbers are never hard-coded.
Never transmits; RTS/DTR are dropped immediately after opening a port. Known
limits: Elecraft and some Kenwood IDs are ambiguous (it fills port and baud and asks
the user to pick the model); an Icom with a non-default CI-V address is not found;
Windows returns no results (pick by hand). `test_rig()` runs `rigctl f m t` once
(read-only) and reports frequency/mode/PTT or a readable error.

**WSJT-X installer** (`wsjtx_install.py`). Picks the build for the real CPU
(`hw.optional.arm64`, so it also works if Python runs under Rosetta), prefers a
`wsjtx*.dmg` already on the Mac (found with Spotlight), else reads SourceForge's file
list (falls back to 3.0.0 if that fails) and downloads `...-ARM-Darwin.dmg` or
`...-Darwin.dmg`. It mounts the image, finds the `.app` containing `jt9` (or unpacks a
`.pkg` with `pkgutil --expand-full`), copies it to `~/Applications/wsjtx.app` (an
existing one is renamed `.old1`, never deleted), and verifies `jt9` and `ft8code`
are found. No admin password.

## Packaging notes (`build.sh`)

- Python is python.org's universal2 3.13.9 framework, embedded in the app. It is
  **not relocatable as shipped**: `bin/python3.13` and `Resources/Python.app/.../Python`
  link to `/Library/Frameworks/...`, and `_ssl` links OpenSSL by absolute path. The script
  rewrites those with `install_name_tool` and then **fails the build if anything still
  links to /Library/Frameworks/Python.framework** (that check caught a missing `ssl`).
- Packages are downloaded per CPU (arm64, x86_64) and every native file is merged with
  `lipo` into one universal binary.
- certifi is bundled because python.org's Python has no CA certificates; the WSJT-X
  downloader builds its SSL context from `certifi.where()`.
- Everything is signed inside-out (ad-hoc, or `SIGN_ID`). The self-test imports the
  app's modules on arm64 **and** x86_64 (Rosetta). It runs with
  `PYTHONDONTWRITEBYTECODE=1`, then re-verifies the signature: stray `.pyc` files in
  the bundle break the code-signature seal.
- Wheel and Python downloads are cached in `packaging/cache/` (safe to delete).

## Radio facts learned (Yaesu FT-991A)

- Hamlib model **1035** ("FT-991" covers the A), 38400 baud (Hamlib's default also works).
  Model 1036 is the FT-891, which sits right next to it in the radio list.
- The radio shows two USB serial ports; only the first (lower number) answers CAT.
