# Procyon FT8

<img src="procyon/web/icon.svg" alt="" width="96">

**Version 0.9.10** · 20 October 2026
Created by **Justin Stapleton, KR1TTR**, with **Claude Code** (Claude Opus 5.5).

A simpler, friendlier FT8 station. Procyon uses WSJT-X's own decoder (`jt9`)
and message encoder (`ft8code`), so decoding performance matches WSJT-X, and
puts a clean, modern interface on top.

## Install

You need **WSJT-X** installed (Procyon uses its FT8 decoder and encoder, and
the Hamlib copy it includes): https://wsjt.sourceforge.io

| System | Download | How |
|---|---|---|
| Linux Mint, Ubuntu, Debian, Raspberry Pi OS | `procyon_0.9.10_all.deb` | Double-click it, or `sudo apt install ./procyon_0.9.10_all.deb`. Then start **Procyon FT8** from your menu, or run `procyon`. |
| Arch Linux, Manjaro, EndeavourOS | `procyon-0.9.10-arch.tar.gz` | `tar xf procyon-0.9.10-arch.tar.gz`, `cd procyon-0.9.10-arch`, `makepkg -si`. Install WSJT-X too (`wsjtx`, from the repos or the AUR) and `hamlib` for radio control. |
| Other Linux (Fedora, openSUSE, ...) | `procyon-0.9.10-linux.zip` | Unzip, run `./setup.sh` (checks what's needed and adds a menu entry), then start Procyon from the menu or `./run.sh`. |
| macOS 11+ (Apple Silicon and Intel) | `Procyon-0.9.10-macos.dmg` (or `.pkg`) | Open the disk image and drag **Procyon** to Applications (or double-click the `.pkg`). The app isn't signed yet, so the first time: System Settings → Privacy & Security → **Open Anyway**. If WSJT-X is missing, Procyon offers to install it for you. |
| Windows 10/11 (64-bit) | `Procyon-0.9.10-windows.zip` | Right-click → **Extract All** (it won't run from inside the zip), then double-click **Procyon.bat** in the extracted folder. Python is included. See `README.txt` inside. |

Add `--demo` (or use **Procyon Demo.bat** on Windows) to try it with a
simulated band: nothing is transmitted. Close WSJT-X while using Procyon;
they can't share the radio and sound card.

Running from this source folder works too: `./run.sh` (Linux).

### Building the packages

```bash
packaging/build.sh            # all three, into dist/
packaging/build.sh deb        # or just one: deb, linux, windows
packaging/macos/build.sh      # macOS: Procyon.app, .dmg and .pkg (run on a Mac)
```

The macOS build bundles python.org's universal Python and the Python packages
(merged into universal binaries), so it needs no Python on the user's Mac. It
is ad-hoc signed; set `SIGN_ID="Developer ID Application: ..."` to sign for
distribution (the script prints the notarization commands).

The Windows build downloads Python's official embeddable build from
python.org and the Windows versions of numpy, aiohttp and sounddevice from
PyPI (cached in `packaging/cache/`).

## Using it

1. **Settings** (gear icon): enter your callsign and grid, then pick the radio's
   audio input and output. To control the radio, turn on *Control my radio*.
   Either point it at a running `rigctld`, or tick *Start rigctld for me* and
   enter the Hamlib model number (`rigctl -l` lists them), the serial port and
   the baud rate.
2. **Pick a band** from the frequency button in the header.
3. **Answer someone:** click any row in *Band activity*. Rows calling CQ have a
   green bar, and messages to you are highlighted in amber and pinned to the top.
4. **Or call CQ** with the *Call CQ* button. The first station to answer is
   worked automatically.

From there the QSO runs itself: grid, then report, then roger, then RR73/73.
The QSO is logged when it completes, and TX switches off. The progress
stepper shows where you are. Press **Halt** or **Esc** at any time to stop
transmitting.

- **Band activity and bands:** each cycle is tagged with the band it was heard
  on. After a band change, older cycles are dimmed with an amber band tag under a
  "Changed to …" divider. *Clear* empties the list on every connected device
  (and tells GridTracker to clear too).
- **Stations calling you:** messages addressed to you always show, whatever
  filter is on. A new station calling you also gets an alert above Band
  activity with a **Reply** button, a chime (switch it off in *Settings → Alerts*)
  and the browser tab title changes, so you notice even in another window.
- **Procyon team badge:** a small raccoon appears next to the callsigns of
  Procyon's developer (KR1TTR) and testers whenever they're decoded. It's added
  on your screen only - nothing extra is transmitted, and it shows whatever
  program they're using. Switch it off in *Settings → Display*.
- **Custom filter:** the box under the Band activity buttons. A word matches when
  a word in the message starts with it (calls, grids, `POTA`, contest exchanges),
  part of the country name matches (`germ`), or the continent code matches (`EU`).
  Separate several with commas to match any of them. It works together with the
  All/CQ/New/My QSO buttons, the funnel switch turns it off without erasing it,
  and matching words are underlined.
- **Exchange log:** under the transmit status, a cycle-by-cycle list of what
  you sent (red) and what you heard back (teal). It flags cycles with
  *No reply heard*, *repeat* when the other station resends (they probably
  didn't copy you), and *busy* when they're working someone else.
- **Waterfall:** click to set your receive frequency, Shift+click to set your
  transmit frequency. Decoded call signs are labelled on their traces.
- **Transmit in:** choose the :00/:30 or :15/:45 slot. Answering a station picks
  the right slot for you.
- **Radio power:** your rig's output in watts. It's written to every QSO in the log
  (ADIF `TX_PWR`).
- **Audio drive:** the audio level sent to the radio. Use *Tune* to send a steady
  carrier while you set ALC.
- **Log note:** an optional comment for the current QSO (ADIF `COMMENT`). Typing
  it after the QSO is logged updates the log entry.
- **Grids and distances:** Procyon remembers the grid of every station it hears
  send one (and the grids in your log), so distances work even when a message has
  no grid. A QSO logged without a grid gets it filled in if that station's grid
  shows up within the next 6 hours. If no grid is known, the distance is left
  blank for your own country and very large ones, where a country-wide estimate
  would mislead.
- **Reports in comments:** *Settings → Logbook* can also add the signal reports
  to each QSO's comment ("FT8 Sent: -05 Rcvd: -12"), after any note you typed.
  Off by default; the reports are always saved in the standard report fields.
- **Edit the log:** click any logbook entry to change its comment or power.
- **All messages:** expand to see or edit the six standard messages, or force a
  particular one to go next.
- **Logbook:** standard ADIF (`~/.local/share/procyon/procyon_log.adi`), exported
  with *Export ADIF*. The *NEW* and *NEW BAND* badges are based on this log.
  Demo mode keeps a separate log (`procyon_demo_log.adi`), so simulated QSOs
  never mix with real ones.
- **Arrange the panels:** *Settings → Layout* chooses how the panels sit on
  this device: *Automatic* (side by side on wide windows, stacked on narrow
  ones), *Side by side*, or *Stacked* - one panel per row, good for a phone or a
  portrait monitor. Side by side, drag Band activity, QSO or Logbook by the grip
  next to its title to move it to another column; stacked, use the ▲▼ buttons
  on each panel. To resize, drag the gap between two panels (side by side) or
  the bottom edge of Band activity or the Logbook (stacked); double-click it
  to undo. Each device remembers its own layout. *Reset panel layout*
  restores the default.

Settings and the log live in `~/.local/share/procyon/` (change with `--data DIR`).
## Setting up your radio

- **Easiest (Linux and macOS):** in *Settings → Radio*, press **Detect my radio**. Procyon asks each
  USB serial port who it is (Yaesu, Kenwood, Elecraft and Icom radios answer), and fills
  in the model, port and baud. Then press **Test radio** (it reads the frequency, never
  transmits) and **Save**. If the radio can't be reached, the status line says why.
- **Coming from WSJT-X?** Click *Import settings from WSJT-X* at the top of
  Settings. It fills in your call, grid, radio, serial port, baud, PTT, sound
  card, power and GridTracker address. Check it over, then press Save.
- **Otherwise,** in *Settings → Radio*, start typing your radio's name (e.g. `KX3`,
  `7300`) and pick it from the list, then choose the serial port, baud and how
  to key the transmitter (CAT, the RTS/DTR line as used by Digirig-style
  interfaces, or VOX).
- **Radio mode for FT8** (default *Data*) is applied whenever Procyon connects to
  the radio or changes band, as WSJT-X does. Choose *USB* if your interface needs
  it, or *Don't change* to leave the mode to you.
- **Waterfall range** (*Settings → Waterfall*): set where it starts and ends to
  match your radio's receive filter (e.g. 300 to 3300 Hz). Your TX and RX offsets
  are kept inside it.
- Close WSJT-X before starting Procyon: they can't share the radio and sound card.

## GridTracker, JTAlert and loggers

Procyon sends the same network messages as WSJT-X (to 127.0.0.1 port 2237 by
default), so programs that work alongside WSJT-X work with Procyon too:
decodes, QSO status and logged QSOs. Clicking a station in GridTracker answers it
in Procyon, and GridTracker's halt button stops transmitting. Change it under
*Settings → Companion apps*. The link is paused in demo mode, so simulated
stations never reach those programs.

## Using it from a tablet or another computer

1. On the computer running Procyon, open **Settings → Remote access**, choose a
   PIN (4 to 8 digits), and turn on **Allow other devices on my network**. No
   restart is needed.
2. Settings shows the address to open on the other device, e.g.
   `http://192.168.1.20:8073`. Open it there and enter the PIN.

Good to know:

- The computer running Procyon never asks for the PIN. Other devices stay signed
  in for 30 days, and changing the PIN signs them all out.
- Five wrong PINs lock that device out (30 s, then longer each time).
- Only the computer running Procyon can change remote access or the PIN.
- On a touchscreen, press and hold the waterfall to set your transmit frequency.
- With remote access off, Procyon only accepts connections from its own computer.
- This is meant for your home network. Don't port-forward Procyon to the
  internet; for access away from home, use a VPN such as Tailscale or WireGuard.

## How it works

| Piece | File |
| --- | --- |
| Timing, waterfall, decode, transmit, settings | `procyon/engine.py` |
| Sound cards: PulseAudio/PipeWire, or PortAudio (Windows) | `procyon/audio.py` |
| Where WSJT-X, cty.dat, data and serial ports live on each OS | `procyon/system.py` |
| Radio auto-detect and test (serial ID, Hamlib read-only test) | `procyon/radio_detect.py` |
| Finds or installs WSJT-X (macOS: right build for the CPU, no admin needed) | `procyon/wsjtx_install.py` |
| FT8 GFSK waveform (port of WSJT-X `gen_ft8wave`), message parsing | `procyon/ft8.py` |
| QSO auto-sequencer | `procyon/qso.py` |
| Country lookup (`cty.dat`), distance/bearing, ADIF log | `procyon/geo.py` |
| rigctld client (frequency, PTT) | `procyon/rig.py` |
| Simulated band for `--demo` | `procyon/demo.py` |
| PIN sign-in for remote devices | `procyon/auth.py` |
| WSJT-X-compatible UDP (GridTracker, JTAlert, loggers) | `procyon/udp.py` |
| WSJT-X import, Hamlib rig list | `procyon/setup_help.py` |
| Web server | `procyon/server.py` |
| Web UI | `procyon/web/` |
| Release packages | `packaging/` |

Each 15-second cycle, audio is buffered at 12 kHz. At 13.4 s the cycle is
written to a WAV and decoded with `jt9 -8`. Transmit audio is synthesized at
the sound card's own rate and starts 0.5 s into the cycle (up to 1.5 s late if
needed). Procyon only plays transmit audio after the radio confirms it is
transmitting.

## The name

The name: *Procyon lotor* is the raccoon, and Procyon is also a binary star,
the brightest in Canis Minor. Hence the icon: a raccoon whose eyes are Procyon A
and Procyon B. (Earlier versions were called Skywave; settings and logbooks move
across automatically on first start.)

## Roadmap

0.9.10: **fix for long or unusual callsigns** (e.g. 3E40CDW, PJ4/K1ABC,
GB13COL). Procyon used to send "3E40CDW KR1TTR EN80", which FT8 can't encode,
so it went out cut short as "3E40CDW KR1TT". It now writes such calls as
`<3E40CDW>`, as WSJT-X does, and will never transmit a message that would be
cut short - it stops and says why instead.

0.9.9: a **Quit** button (power icon, top right, station computer only): stops
Procyon and closes its terminal window; open tabs show "Procyon has stopped"
and reconnect by themselves when it's started again. Also a safety fix:
quitting or pressing Ctrl+C during a transmission now always puts the radio
back on receive before Procyon lets go of it.

0.9.8: a full-screen button in the header, next to Settings (same as F11).

0.9.7: the "Procyon FT8" logo is now a picture rather than text, so it looks
the same on every computer. Logo files for printing are in `branding/`.

0.9.6: resizable panels - drag the gap between panels to change their widths,
or (stacked) the bottom edge of Band activity or the Logbook to change its height.
New Arch Linux package (PKGBUILD).

0.9.5: choose the panel layout per device - Automatic, Side by side, or
Stacked (phones and portrait monitors), with ▲▼ buttons to order stacked panels.

0.9.4: the Procyon team badge - a raccoon next to the developer's (and later
testers') callsign in Band activity and the QSO panel (*Settings → Display*).

0.9.3: macOS port by Dewey A. "Andy" Lamb, KE4BFI (Mac app, .dmg/.pkg, finds
WSJT-X anywhere on a Mac and can install it), **Detect my radio** and **Test
radio** in Settings (Linux and macOS), plain-language radio-control errors, and
the audio drive slider now works in real time, even mid-transmission.

0.9.2: never miss a caller - messages to you always show, whatever the filters,
and a "calling you" alert (Reply button, chime, tab title) appears for any new
station calling you, including tail-enders during a QSO. The demo band now has
stations that call you after you've called CQ.

0.9.1: answering late - a transmission can now start up to 3 s after its
normal start (up to 3.5 s into the cycle), skipping the part already missed so
it still ends on time, as WSJT-X does.

Done: on-air operation (first QSO 18 October 2026, KF8CTG), PIN-protected
remote access, packages for Linux (.deb and zip) and Windows, GPLv3 licence.

Next:
1. **Windows test on real hardware** (the Surface), then release 1.0.
2. **Remote access over Spectrum Wi-Fi:** devices couldn't reach each other
   (router-side isolation). Try Ethernet for the station computer, or ask
   Spectrum to disable client isolation; then test from the tablet.
3. **Arch Linux package:** the PKGBUILD is in place (0.9.6) but needs a test on
   a real Arch system; publish it on the AUR once Procyon is on GitHub.
4. **Time accuracy** for home and field stations (no admin rights needed, and
   adjusts only Procyon's own timing):
   - A clock-health light next to the clock, using an occasional NTP/NIST check.
   - Band sync: use the median DT of many decoded stations (offline, e.g. POTA),
     applied gradually with a minimum number of stations, as JTSync does. If
     nothing decodes, search the recent audio shifted by up to about ±7 s to
     find the band's timing.
   - Setting: Off / Show me / Automatic.
5. **More modes, after tester feedback:**
   - **FT4** first: `jt9` already decodes it and the QSO flow is the same as FT8;
     needs Procyon's own FT4 encoder (no `ft4code` in WSJT-X) verified against
     `jt9`, 7.5 s cycle timing, and a mode switch in the header.
   - **WSPR**: `wsprd` decodes; needs an encoder and its own beacon-style screen
     (2-minute cycles, WSPRnet reporting, band hopping).
   - **A keyboard-mode screen** for free-text modes: received text scrolling
     like a chat, a typing box, macros (call, name, 73), and a "heard" list.
     Built once, it serves the modes below. The aim is to be the prettier,
     easier, more fun alternative to programs like fldigi: a few modes done
     really well, built into Procyon so nothing else needs installing.
     (fldigi's remote control may help while developing the screen.)
   - **JS8** (JS8Call), next after FT4: free-text QSOs built on FT8's signalling, with
     heartbeats, store-and-forward and relays. Closest to what Procyon
     already does; JS8Call is GPLv3 and maintained again (JS8Call-Improved,
     2.5.x in 2026). JS8Call includes a decoder program, `js8`, that reads
     .wav files much as `jt9` does, so Procyon may be able to use it for
     receiving; it still needs its own JS8 encoder for transmitting.
   - **FSQ**, later (Fast Simple QSO, ZL2AFP and ZL1BPU, 2015): a small but dedicated
     user base, and a good mode for curious users to discover. IFK+ with 33
     tones about 300 Hz wide, several speeds, and a directed protocol (calls
     addressed to a station, automatic replies, a heard list) that suits
     Procyon's design. Its modulation, coding and FSQCall protocol are
     published; fldigi's FSQ modem (GPL) is a reference. FSQCall itself is
     Windows-only, so Linux and Mac users have little choice today.
     Lowercase text sends faster in FSQ, so Procyon should encourage it.
   - **Not planned: FT2.** In practice it's too fast for its error correction:
     weaker signals need so many repeats that FT4 completes QSOs sooner.
   - **JTTY** (lowest priority, may be dropped): WSJT-X 3.2's new RTTY-like keyboard mode (4-tone, ~125 Hz,
     ~1.9 s frames, no fixed time slots; announced 23 Sep 2026 in 3.2.0-rc1).
     WSJT-X has no command-line JTTY tool, so Procyon would need its own
     decoder/encoder (port from WSJT-X's GPLv3 source) and a chat/contest-style
     screen. Revisit once WSJT-X 3.2 is generally released and the protocol is
     settled.
6. **Stats page** (Andy's idea): QSOs by band and day, countries and grids
   worked, best DX, busiest hours, heard vs worked - all from your own log.
7. **Product direction:** FT8 stays the front-facing product, with FT4 beside
   it on the same screen. The other modes (JS8, FSQ, ...) live behind a
   "More modes" entry for curious users to discover - findable, not secret.
   The name stays **Procyon FT8**; "Procyon Digi" is held in reserve in case
   the other modes ever become central.
8. Smaller ideas: custom frequencies and per-band memory in the band menu, dim
   the waterfall during your own transmissions, live S-meter and output power
   from the radio.
9. Browser polish: Brave shows its "New Email Alias" suggestion in Settings
   fields (seen on Linux and Windows). Mark the form and its fields
   `autocomplete="off"` (and give number fields clear types/names) so browsers
   stop offering autofill there; check in Brave.
10. Radio detection on Windows (currently Linux and macOS only).

Only transmit with a valid amateur licence, and keep your PC clock synced
(NTP). FT8 needs it to be within about a second.

## Credits and licence

Created by Justin Stapleton, KR1TTR, with Claude Code (Claude Opus 5.5).

macOS port and testing by Dewey A. "Andy" Lamb, KE4BFI (see `docs/macos-port.md`).

Procyon builds on:
- **WSJT-X** by Joe Taylor K1JT, Steve Franke K9AN and the WSJT Development Group:
  the FT8 decoder (`jt9`) and message encoder (`ft8code`). Procyon's transmit
  waveform code is a port of WSJT-X's `gen_ft8wave`.
- **Hamlib**, for radio control.
- **cty.dat** by Jim Reisert AD1C, for country lookups.

Procyon is free software under the GNU General Public License v3.
