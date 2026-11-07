# Procyon

<img src="procyon/web/icon.svg" alt="" width="96">

**Version 0.9.65** · 6 November 2026
Created by **Justin Stapleton, KR1TTR**, with **Claude Code** (Claude Opus 5.5).

A simpler, friendlier FT8 station (FT4 too, as a beta). Procyon uses WSJT-X's
own decoder (`jt9`) and message encoder (`ft8code`), so decoding performance
matches WSJT-X, and puts a clean, modern interface on top.

## Install

You need **WSJT-X** installed (Procyon uses its FT8 decoder and encoder, and
the Hamlib copy it includes): https://wsjt.sourceforge.io

| System | Download | How |
|---|---|---|
| Linux Mint, Ubuntu, Debian, Raspberry Pi OS | `procyon_0.9.65_all.deb` | Double-click it, or `sudo apt install ./procyon_0.9.65_all.deb`. Then start **Procyon** from your menu, or run `procyon`. |
| Arch Linux, Manjaro, EndeavourOS | `procyon-0.9.65-arch.tar.gz` | `tar xf procyon-0.9.65-arch.tar.gz`, `cd procyon-0.9.65-arch`, `makepkg -si`. Install WSJT-X too (`wsjtx`, from the repos or the AUR) and `hamlib` for radio control. |
| Other Linux (Fedora, openSUSE, ...) | `procyon-0.9.65-linux.zip` | Unzip, run `./setup.sh` (checks what's needed and adds a menu entry), then start Procyon from the menu or `./run.sh`. |
| macOS 11+ (Apple Silicon and Intel) | `Procyon-0.9.65-macos.dmg` (or `.pkg`) | Open the disk image and drag **Procyon** to Applications (or double-click the `.pkg`). The app isn't signed yet, so the first time: System Settings → Privacy & Security → **Open Anyway**. If WSJT-X is missing, Procyon offers to install it for you. |
| Windows 10/11 (64-bit) | `Procyon-0.9.65-windows-setup.exe` | Double-click it (no administrator password needed), then start **Procyon** from the Start menu. Python is included. To update, run the newer setup the same way. Or, without installing: `Procyon-0.9.65-windows.zip` - right-click → **Extract All**, then double-click **Procyon.bat** in the extracted folder. |

Add `--demo` (or use **Procyon DEMO ONLY - no radio** in the Windows Start menu, or
*Settings → Advanced → Demo band*) to try it with a simulated band: nothing is
transmitted. Demo mode is always off when Procyon starts, so it can't be left on
by mistake, and the practice log only exports as plain text, never as a logbook
file. Close WSJT-X while using Procyon;
they can't share the radio and sound card.

Running from this source folder works too: `./run.sh` (Linux).

### Building the packages

```bash
packaging/build.sh            # all three, into dist/
packaging/build.sh deb        # or just one: deb, linux, windows
packaging/macos/build.sh      # macOS: Procyon.app, .dmg and .pkg (run on a Mac)
```

The macOS packages are built on GitHub's Macs by
`.github/workflows/macos.yml`: publishing a release attaches the `.dmg`, `.pkg`
and `SHA256SUMS-macos` to it, and *Actions → macOS build → Run workflow* builds
the current code on demand. It runs the same `packaging/macos/build.sh`.

The macOS build bundles python.org's universal Python and the Python packages
(merged into universal binaries), so it needs no Python on the user's Mac. It
is ad-hoc signed; set `SIGN_ID="Developer ID Application: ..."` to sign for
distribution (the script prints the notarization commands).

The Windows build downloads Python's official embeddable build from
python.org and the Windows versions of numpy, aiohttp and sounddevice from
PyPI (cached in `packaging/cache/`).

## Using it

The built-in **user guide** (Settings → *User guide*, or the About card)
explains every part of the screen. In short:

1. **Settings** (gear icon): enter your callsign and grid, then pick the radio's
   audio input and output. To control the radio, turn on *Control my radio*
   and enter the Hamlib model number (`rigctl -l` lists them), the serial port
   and the baud rate; Procyon starts `rigctld` for you. (Already running your
   own `rigctld`? Point Procyon at it in *Settings → Advanced*.)
2. **Pick a band** from the frequency button in the header, and **FT8 or FT4**
   with the switch beside it (FT4: 7.5-second cycles, for quicker QSOs and
   contests; the band menu then offers the FT4 frequencies, e.g. 14.080).
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
  activity with a **Reply** button, a chime (switch it off in *Settings → Display → Alerts*)
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
- **TX audio drive:** the audio level sent to the radio when you transmit. Use
  *Tune* to send a steady carrier while you watch the radio's ALC. (The **RX
  audio** meter at the top is the other direction: the radio's audio coming in.
  Set it with the radio's AF/volume knob, aiming for about -30 dB.)
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
- **Look up a station on QRZ:** right-click a callsign in Band activity, a
  caller alert or the Logbook (press and hold on a touch screen), or use the
  *QRZ ↗* link in the QSO panel. It opens the station's qrz.com page in a new tab.
- **Edit the log:** click any logbook entry to change its comment or power, or
  to delete it (*Delete QSO*, then *Are you sure?* to confirm) - e.g. a second entry
  made when a station missed your 73 and sent RR73 again later. A copy of the
  log from before the change is kept as `procyon_log.adi.bak`. Deleting doesn't
  touch QRZ: remove an uploaded QSO there too.
- **POTA activations:** press *POTA* in the QSO panel, enter your park (or
  several - US-1234, US-5678, ... - for a 2-fer or more), and optionally the
  grid and state you're operating from. Your CQ becomes `CQ POTA KR1TTR EN80`,
  every QSO is tagged with the park(s) (`MY_SIG=POTA`, `MY_SIG_INFO`), and a
  counter shows how many of the 10 QSOs a valid activation needs you've made
  this UTC day. Each park also gets its own log file, saved as you go in
  `pota/` next to your logbook and named the way pota.app expects
  (`KR1TTR@US-1234-20261021.adi`); the POTA window has download links. The
  QSOs are in your main logbook as well, so QRZ uploads include them.
  Procyon doesn't send anything to pota.app - you upload the files there yourself. The
  activation survives a restart; *End activation* when you're done. Hunters:
  stations calling `CQ POTA` get a green POTA tag in Band activity.
- **Portable calls:** QSOs where both calls are portable or unusual
  (`KR1TTR/P` working `P4/WE9V`) follow the usual convention: full calls when
  calling and at the end, reports with each side's base call
  (`<P4/WE9V> KR1TTR -05`). Every message is checked with WSJT-X's encoder,
  and both full calls are logged. (A call like `KR1TTR/QRP` can't send
  "CQ POTA" in FT8; its CQ goes out as a plain CQ.)
- **Upload to QRZ:** put your QRZ Logbook API key in *Settings → Logbook →
  Online logbooks*. An *Upload to QRZ* button then
  appears next to *Export ADIF*, showing how many QSOs haven't been sent yet.
  Nothing is sent until you click, so it never clashes with GridTracker, and a
  portable station can log offline and send everything once it's back online.
  Each QSO remembers where it has been uploaded (standard ADIF fields), so a
  failed upload just stays queued; QSOs the service already has count as done.
  The demo log is never uploaded.
- **All messages:** expand to see or edit the six standard messages, or force a
  particular one to go next.
- **Logbook:** standard ADIF (`~/.local/share/procyon/procyon_log.adi`), exported
  with *Export ADIF*. The *NEW* and *NEW BAND* badges are based on this log.
  Demo mode keeps a separate log (`procyon_demo_log.adi`), so simulated QSOs
  never mix with real ones.
- **Arrange the panels:** *Settings → Display → Layout* chooses how the panels sit on
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

- **Easiest:** in *Settings → Station & radio*, press **Detect my radio**. Procyon asks each
  USB serial port who it is (Yaesu, Kenwood, Elecraft and Icom radios answer), and fills
  in the model, port and baud. Then press **Test radio** (it reads the frequency, never
  transmits) and **Save**. If the radio can't be reached, the status line says why.
- **Coming from WSJT-X?** Click *Import settings from WSJT-X* at the top of
  Settings. It fills in your call, grid, radio, serial port, baud, PTT, sound
  card, power and GridTracker address. Check it over, then press Save.
- **Otherwise,** in *Settings → Station & radio*, start typing your radio's name (e.g. `KX3`,
  `7300`) and pick it from the list, then choose the serial port, baud and how
  to key the transmitter (CAT, the RTS/DTR line as used by Digirig-style
  interfaces, or VOX).
- **Radio mode for FT8** (default *Data*) is applied whenever Procyon connects to
  the radio or changes band, as WSJT-X does. Choose *USB* if your interface needs
  it, or *Don't change* to leave the mode to you.
- **Waterfall range** (*Settings → Display → Waterfall*): set where it starts and ends to
  match your radio's receive filter (e.g. 300 to 3300 Hz). Your TX and RX offsets
  are kept inside it.
- Close WSJT-X before starting Procyon: they can't share the radio and sound card.

## GridTracker, JTAlert and loggers

Procyon sends the same network messages as WSJT-X (to 127.0.0.1 port 2237 by
default), so programs that work alongside WSJT-X work with Procyon too:
decodes, QSO status and logged QSOs. Clicking a station in GridTracker answers it
in Procyon, and GridTracker's halt button stops transmitting. Change it under
*Settings → Network → Companion apps*. The link is paused in demo mode, so simulated
stations never reach those programs.

## Using it from a tablet or another computer

1. On the computer running Procyon, open **Settings → Network → Remote access**, choose a
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
| FT4 encoder and waveform (ports of WSJT-X `genft4` / `gen_ft4wave`) | `procyon/ft4.py` |
| QSO auto-sequencer | `procyon/qso.py` |
| Country lookup (`cty.dat`), distance/bearing, ADIF log | `procyon/geo.py` |
| rigctld client (frequency, PTT) | `procyon/rig.py` |
| Simulated band for `--demo` | `procyon/demo.py` |
| PIN sign-in for remote devices | `procyon/auth.py` |
| Upload to QRZ (LoTW code kept, switched off) | `procyon/uploads.py` |
| WSJT-X-compatible UDP (GridTracker, JTAlert, loggers) | `procyon/udp.py` |
| WSJT-X import, Hamlib rig list | `procyon/setup_help.py` |
| Web server | `procyon/server.py` |
| Web UI | `procyon/web/` |
| Release packages | `packaging/` |

Each 15-second cycle, audio is buffered at 12 kHz. At 13.4 s the cycle is
written to a WAV and decoded with `jt9 -8`. In FT4 the cycle is 7.5 s,
decoded at 6.2 s with `jt9 -5`. WSJT-X has no stand-alone FT4 encoder, so
`ft8code` packs the message (FT4 packs it the same way) and `ft4.py` does the
rest: scrambling, CRC, LDPC parity, tones and audio - checked bit for bit
against WSJT-X and by decoding with `jt9`. Transmit audio is synthesized at
the sound card's own rate and starts 0.5 s into the cycle (up to 1.5 s late if
needed). Procyon only plays transmit audio after the radio confirms it is
transmitting.

## The name

The name: *Procyon lotor* is the raccoon, and Procyon is also a binary star,
the brightest in Canis Minor. Hence the icon: a raccoon whose eyes are Procyon A
and Procyon B. (Earlier versions were called Skywave; settings and logbooks move
across automatically on first start.)

## Known limitations

- **WSJT-X must be installed.** Procyon uses its decoder (`jt9`), message
  encoder (`ft8code`) and country list (`cty.dat`); the WSJT-X program itself
  never runs. Radio control uses Hamlib (included with WSJT-X on Windows and
  macOS; the `hamlib` package on Linux).
- **Online logbooks:** upload to QRZ.com only (needs your QRZ Logbook API key).
  For LoTW, eQSL or Club Log, use *Export ADIF* with their own tools.
- **FT4 is new** (since 0.9.37). It works on the air, but has had far less use
  than FT8.
- **Not signed:** the Mac app needs *Open Anyway* the first time, and Windows
  may show a "protect your PC" notice for the setup program (*More info* →
  *Run anyway*).
- **Remote access** works on your home network only, not over the internet.
- **One copy at a time:** a second start just opens the browser on the
  running one.
- **Detect my radio** can't tell Elecraft models apart (they all answer the
  same way); it asks which one you have.
- **FT8 and FT4 only** for now; other modes are on the roadmap.

## Changes

0.9.65: **changing band ends the QSO.** Picking another band (in Procyon or
on the radio's own dial) turns transmit off and clears the QSO, since the
station you were working can't hear you on the new band. Moving within the
same band (for example between the FT8 and FT4 frequencies) changes nothing.

0.9.64: a **user guide** built in: every part of the screen, what each
button and light does, how a QSO runs, the logbook, POTA, Settings tab by
tab, and the keys and mouse actions. Open it from Settings or the About card
(the setup guide links to it too). Also: the developer's callsign now shows
as a green pill next to the raccoon badge, and the POTA window no longer
mentions LoTW.

0.9.63: decodes show up as soon as the decoder finds them, like in WSJT-X:
Band activity fills in over a second or so instead of all at once, and a
reply to you is acted on in time for your next transmission (before, on a
busy band the decodes could land just after the next cycle had started, and
Procyon answered a cycle late). Procyon's reminder of long and portable calls
(e.g. P4/WE9V) now runs alongside instead of first, which saved about a
second per cycle. procyon.log notes every 10 minutes how long decoding takes.
Scott, KE8SAU, gets the tester badge.

0.9.62: on a Mac, Procyon now finds WSJT-X Improved (its app is called
"ws.app", with its radio control named rigctl-ws) and WSJT-X apps with other
names or inside a folder in Applications.

0.9.61: the Radio box in Settings is more forgiving: "ftdx10" now finds the
Yaesu FTDX-10 (spaces, hyphens and slashes don't matter). When the list of
radios is empty because WSJT-X isn't installed or found, it says so instead of
"no radio by that name", and the list fills in once WSJT-X is there (reopen
Settings - no restart needed).

0.9.60: LoTW upload taken out (for now). It needed ARRL's TQSL program and
certificate, which is too much setup for the easy start Procyon aims for. QRZ
upload is unchanged; QSOs already marked as sent to LoTW keep their tag.

0.9.59: a small Ohio flag at the bottom of the About card - Procyon is made in
Columbus, Ohio.

0.9.58: fix - switching between FT8 and FT4 while transmitting or in a QSO
in full screen seemed to freeze Procyon: its "are you sure?" question used
the browser's own pop-up, which could open behind the full-screen page. It's
now Procyon's own dialog, shown on top.

0.9.57: twice as many stars in the Night sky background (Settings →
Experimental → Background textures).

0.9.56: Procyon tells you when a new version is out - a small message at the
top of the screen with a link to get it (Settings → Advanced → New versions to
switch it off). It asks GitHub once a day for the newest version number; nothing
about you or your log is sent, and without internet it stays quiet.

0.9.55: on Linux, when the computer won't let Procyon open the radio's serial
port, it now says so plainly and tells you the fix (join the "dialout" group,
then log out and back in) - before, it wrongly said another program was using
the port. Detect my radio and Test radio say the same.

0.9.54: look up a station on QRZ - right-click a callsign in Band activity,
a "calling you" alert or the Logbook (press and hold on a touch screen) and
pick *Look up ... on QRZ*; the QSO panel has a *QRZ ↗* link for the station
you're working. It just opens qrz.com in a new tab: no QRZ password needed.

0.9.53: stations whose call only came through in coded form ("<...>") no
longer get a label on the waterfall - it told you nothing and took up room.
They still show in Band activity.

0.9.52: call signs on the waterfall no longer pile on top of each other on a
busy band. They're staggered in rows: stations calling CQ on top, stations
calling you below them, everyone else at the bottom. Neighbours that would
overlap step down into the next row.

0.9.51: the delete button's second click now reads "Are you sure?".

0.9.50: **delete a QSO from the log**. Click the entry in the Logbook, then
*Delete QSO* and click again to confirm. Handy when a station missed your 73,
sent RR73 again later and answering them logged the contact twice. It's also
taken out of the POTA park log; if it was already uploaded to QRZ, Procyon
reminds you to delete it there as well.

0.9.49: the Rig light now notices when the **radio is switched off** while
Procyon is running: it turns red and says "Radio off?" (before, it stayed
green, since Hamlib itself was still answering), and goes green again by
itself when the radio is back on. Hamlib's repeated complaints while the radio
is off are written to procyon.log once every 5 minutes with a count, instead of
every few seconds, so they no longer push the useful history out of the log.

0.9.48: **band sync** (*Settings → Advanced → Clock*). Procyon's timing can
follow the computer's clock as before, **sync to the band automatically**, or
sync **when you click the Clock light**. Band sync times Procyon by the
stations it hears (the middle value of their time offsets, once 8 or more are
heard) - handy in the field with no internet or GPS. It moves in small steps,
never during a transmission, and shifts only Procyon's own timing (cycles,
decoding, transmit start, the clock on screen); the computer's clock isn't
changed, and the correction starts from zero each time Procyon starts. With
sync on, the light reads e.g. "Synced +0.8 s".

0.9.47: **band clock check**. A Clock light in the header compares your
computer's clock with the stations you hear: the middle value of their time
offsets (DT) over the last two minutes, once at least 8 stations are heard -
no internet or GPS needed, so it works in the field. Green within ±0.5 s,
amber up to 1 s, red beyond; hover for what it means. It only measures for
now; automatic correction is planned next.

0.9.46: when **Detect my radio** finds an Elecraft (the K3, KX3, KX2 and K4 all
answer the same way), a short "Which one?" list appears beside the result
instead of having to type the model in. Companion-app messages (GridTracker)
now use a socket on this computer only, so Windows Firewall no longer asks
about network access just for that (it still asks if you turn on remote
access, which does need the network).

0.9.45: a **Windows installer** (`Procyon-0.9.45-windows-setup.exe`): installs
for you only (no administrator password), adds Procyon and the demo to the Start
menu (and the desktop, if you like) and an uninstaller to Windows' app list.
Procyon started this way runs **without a console window**, like on Linux: it
lives in your browser and its Quit button closes it; if it can't start it says
why in a message box. The zip is still there for running without installing.
**Restart reminder:** when a newer Procyon is installed while it's running,
a bar asks you to quit and start it again (it keeps running the old version
until then). **Tester badges:** Procyon's testers Andy KE4BFI, Jim KE8EON and Scott
KE8SAU get a star next to their calls and a purple pill around them (on this screen
only; *Settings → Display* switches team badges off).

0.9.44: **background textures** for the palettes (*Settings → Experimental*):
a faint starfield on Night sky (mostly white, some blue, a few yellow and
red), maple grain on Antique radio, fallen pine needles on Campfire; Classic
stays plain. They sit only behind the header and between the panels, and
"Background textures" switches them off on a device. Our own drawings,
rebuilt by `branding/make_textures.py`. The About card shows the new logo
with the tagline "Friendly digital ham software", coloured to suit each
palette (the white logo was hard to see on Classic). The QRZ import's
progress count no longer shows "500 of 250" (QRZ's count is per batch).

0.9.43: fix - "Import my QSOs from QRZ" said the logbook was empty. QRZ
sends the downloaded QSOs as plain text with < and > written as &lt; and
&gt;, which Procyon cut apart. It now reads that format (and the other one,
in case QRZ changes); if a page of QSOs lacks QRZ's record numbers it fetches
the whole logbook in one go, and it says so if fewer QSOs arrive than QRZ
reports.

0.9.42: **safety fix** - only Procyon's own page can talk to Procyon. Before,
any web site open in a browser on the same computer could have connected to
Procyon in the background and sent it commands (in the worst case "call CQ"
or "enable TX"). Now Procyon refuses connections and changes coming from any
other page, and only answers at this computer's own address (an IP address,
localhost or the computer's name), which also stops "DNS rebinding" tricks.
Refused attempts are written to procyon.log.

0.9.41: **logbook import** (*Settings → Advanced*). "Import my QSOs from
QRZ" downloads your QRZ logbook with your API key (downloading needs a QRZ
subscription); "Import an ADIF file" takes a file from qrz.com, WSJT-X or
another logging program. Either way NEW and "new band" then know everyone
you've worked before Procyon. QSOs already in the log are skipped (same call
and band within 15 minutes), the log is backed up first and kept in date
order, and QSOs from QRZ are marked as already uploaded. The logbook is now
read by each field's length, so a note containing "<" survives intact.

0.9.40: the **Campfire** palette (*Settings → Experimental*) has bark-brown
panels, with dirt-brown boxes and lists inside them, on its near-black night
background (the panels used to be forest green).

0.9.39: the FT8 / FT4 switch answers at once. It used to wait until the radio
had moved to the new frequency, which took several seconds when the radio was
slow to answer, so extra clicks queued up and flipped the mode back and forth.
Now the new mode lights up straight away, repeat clicks are ignored while it
switches, and the radio is retuned in the background.

0.9.38: the log's "sent" report is the one you actually sent. Messages are
rebuilt with the latest signal strength, so the log used to get the newest
measurement instead (e.g. -14 sent, -12 logged).

0.9.37: **FT4 (beta)**. An FT8 / FT4 switch next to the band in the header.
FT4 runs 7.5-second cycles with the same messages and QSO flow as FT8, so
QSOs take half the time (about 3 dB less sensitive - popular in contests).
Switching stops transmitting, starts Band activity and the QSO afresh, and
moves the radio to the band's FT4 frequency (e.g. 14.080) if it sat on the
FT8 one. FT4 QSOs are logged as MODE MFSK / SUBMODE FT4 (the ADIF standard);
GridTracker and other companion apps are told the mode. The demo band runs
FT4 too. The mode in use shows in teal on the switch.

0.9.36: the **Antique radio** palette (*Settings → Experimental*) is now a
stained-maple cabinet framing dark bakelite panels, after a 1930s tabletop
radio - it pairs well with the Tube glow waterfall. The Logbook's scrollbar
matches the others (slim, in the palette's colours) instead of the browser's
plain grey one.

0.9.35: a test build of the new Antique radio look (see 0.9.36).

0.9.34: when Procyon's last browser tab is closed, transmitting stops after 10
seconds, so it never keeps calling with nobody watching, and Procyon closes
itself after a minute so it doesn't run unseen, holding the radio and sound card
(*Settings → Advanced* can keep it running). A page reload doesn't count, and
tabs on other devices count as open.

0.9.33: on Linux, Procyon starts from the menu without a terminal window.
Its messages go to `procyon.log` in the data folder, and if it can't start, a
desktop notification says why. Stop it with the **Quit** button. (Running
`procyon` or `./run.sh` in a terminal still shows everything there.)

0.9.32: a few small finishing touches.

0.9.31: a built-in **setup guide** (Settings, the About card and the welcome
message link to it; it works offline): what you need, installing on each
system, setting the clock, first-time Settings, common radio setups (built-in
USB audio, Digirig, SignaLink, KX3), audio levels, a first QSO, and a
troubleshooting table.

0.9.30: **Detect my radio** now works on Windows too: it asks each USB COM
port which radio is there, the same read-only way as on Linux and macOS
(nothing is transmitted).

0.9.29: in demo mode, a striped **DEMO BAND** banner across the bottom of the
waterfall says the stations are simulated, nothing is transmitted and QSOs go
to a practice log - with a **Switch off** button.

0.9.28: for importing into another logging program such as N3FJP, *Settings →
Advanced → Logbook export* can make Export ADIF give only the QSOs not exported
before, so nothing gets imported twice; the button then shows how many are
new. "Mark every QSO as already exported" sets the starting point, and
"Download the whole log" still gives everything.

0.9.27: Procyon has its own contact address, shown in the About card and
used as the Linux packages' maintainer address.

0.9.26: the Classic palette is easier on the eyes - a light page with soft
grey panels and dark text, instead of white panels on darker grey.

0.9.25: colour palettes to try (*Settings → Experimental*, per device), mixing
freely with the waterfall schemes: **Classic** (light grey with a navy title bar,
in the spirit of Windows 95), **Antique radio** (stained maple and bakelite since 0.9.36) and
**Campfire** (night forest and bark), next to the standard **Night sky**.

0.9.24: the name is now simply **Procyon** everywhere it's shown - the
applications menu, window and tab titles, the "Procyon has stopped" message,
the Windows and Mac launchers - to match the logo.

0.9.23: demo mode can't be mistaken for the real thing. It's always off when
Procyon starts (switch it on in *Settings → Advanced*, or start with `--demo`);
the Windows launcher is now **Procyon DEMO ONLY - no radio.bat**; and the
practice log, POTA park logs included, only exports as a plain text list
marked "not real QSOs", never as a logbook file, so it can't be uploaded to
QRZ, LoTW or POTA by mistake. Also: the logo no longer says "FT8" (a new
descriptor may come later), and a plain-language "use at your own risk"
notice in the About card and README.

0.9.22: Procyon follows your callsign. Distances default to miles for US and
UK calls and kilometres everywhere else (*Settings → Display → Distances*:
Automatic, Kilometers or Miles), and the few words spelled differently -
color/colour, license/licence, kilometer/kilometre - use American spelling for
US calls and British spelling for everyone else.

0.9.21: a new **Experimental** tab in Settings, for things being tried out.
First up: waterfall colour schemes - *Lightning* (the standard look), *Classic*
(yellow on dark navy), *Tube glow* (a dim incandescent bulb) and *Ember*
(glowing coals), chosen per device. And clearer labels: the meter at the top is
now **RX audio** (set it with your radio's AF/volume) and the slider is **TX
audio drive** (set it with Tune while watching ALC).

0.9.20: distances in **miles** if you prefer (*Settings → Display*), and a
new **Advanced** tab in Settings for things most stations never touch: the
demo band, decoding depth, and connecting to a `rigctld` you run yourself.

0.9.19: no more "ghost" callers. When a signal is too weak to read outright,
WSJT-X's decoder may fill part of it in by guessing (for example "your call +
???"), and now and then that turns noise into a station calling you that
doesn't exist. Those decodes are now marked with a **?** and shown faintly;
they no longer set off the "calling you" alert or chime, and a CQ is never
answered automatically from one. Companion apps get them flagged as
low-confidence, as WSJT-X does.

0.9.18: each park's POTA log is saved as its own file while you operate
(`pota/` next to your logbook), kept up to date after every QSO or edit, and
a second activation of the same park on the same day adds to it. Ending an
activation with no QSOs now says so instead of pointing at an empty list.

0.9.17: **POTA activations.** A POTA button in the QSO panel: enter one park
or many, call `CQ POTA`, watch the 10-QSO counter, and download a log per park
for pota.app. QSOs between two portable stations (`/P`, `/QRP`, `P4/` ...)
now work, using base calls for the reports as is customary. Stations calling
CQ POTA are tagged in Band activity. The header now switches to its compact
look whenever its chips don't fit, so no button is pushed off screen.

0.9.16: the waterfall's timestamps are bold and bright with a dark outline,
so they stay readable over strong signals. The badge switch in *Settings →
Display* now reads "Show Procyon's team badges when decoded".

0.9.15: Settings has tabs - *Station & radio*, *Audio*, *Logbook*, *Display*
and *Network* - instead of one long scroll. One *Save* covers every tab, and
each device remembers the tab you used last.

0.9.14: *Test key* now shows QRZ's answer inside Settings (it was hidden
behind the Settings window).

0.9.13: one-click upload to QRZ.com and LoTW. Set them up in *Settings →
Online logbooks* and an upload button appears beside *Export ADIF*, with a
count of QSOs not yet sent. Uploads only happen when you click, so portable
stations can log offline and upload later, and GridTracker users aren't
affected. Each logbook entry shows where it has been uploaded.

0.9.12: Procyon understands DXpedition-style "two-in-one" messages, such as
`N3LSB RR73; KR1TTR <P4/WE9V> -06` (one transmission finishing one QSO and
starting the next, used by DXpeditions and multi-answer stations). The half
addressed to you now works like any reply - it moves your QSO along and raises
the "calling you" alert. When such a station sends your RR73 this way, the QSO
is logged and Procyon doesn't send a 73 back, as DXpeditions ask.

0.9.11: long or unusual callsigns now show by name instead of `<...>`.
Stations like 3E40CDW or P4/WE9V send their call as a short code; WSJT-X's
decoder forgot those codes between cycles, so their replies (even to you)
showed as `<...>` and Procyon couldn't tell who was calling. Procyon now
remembers every such callsign it has heard in full.

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

## Roadmap

Done: on-air operation (first QSO 18 October 2026, KF8CTG), FT4, POTA
activations, band clock sync, QRZ upload and import, PIN-protected remote
access, packages for Linux (.deb, Arch, zip), Windows (setup and zip) and
macOS, tested on Linux Mint, Manjaro, Windows and macOS. GPLv3 licence.

Next:
1. **1.0**: the first stable release, with the source code public on GitHub.
2. **Arch Linux:** publish the package on the AUR.
3. **More modes, after tester feedback:**
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
4. **Stats page** (Andy's idea): QSOs by band and day, countries and grids
   worked, best DX, busiest hours, heard vs worked - all from your own log.
5. **Product direction:** FT8 stays the front-facing product, with FT4 beside
   it on the same screen. The other modes (JS8, FSQ, ...) live behind a
   "More modes" entry for curious users to discover - findable, not secret.
   The name is simply **Procyon** (it was "Procyon FT8" until 0.9.24); a short
   descriptor after it may come back once the direction is clearer.
6. Smaller ideas: custom frequencies and per-band memory in the band menu, dim
   the waterfall during your own transmissions, live S-meter and output power
   from the radio.
7. **Standing on its own:** Procyon's own decoder and encoder, so WSJT-X no
   longer has to be installed. Other programs would only be used for
   importing settings while setting up a station (e.g. *Import settings from
   WSJT-X*). Today Procyon borrows WSJT-X's `jt9` (decoder), `ft8code`
   (message packing; Procyon already makes the tones and the FT4 coding
   itself) and `cty.dat` (country list, really from country-files.com).
   The decoder is the big part: JTDX (GPLv3, like Procyon) has a stronger
   FT8 decoder for crowded bands, but its decoder program only works inside
   JTDX, so it would have to be built into a program of Procyon's own.
   Radio control stays with Hamlib, as in most ham programs.

Only transmit with a valid amateur licence, and keep your PC clock synced
(NTP) - FT8 needs it to be within about a second. In the field without
internet, *band sync* (Settings → Advanced → Clock) can line Procyon up with
the stations you hear instead.

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

### Use at your own risk

Procyon is a personal project, shared as is, without warranty of any kind. You
use it entirely at your own risk: its author accepts no responsibility for any
damage, loss or harm that comes from using it, whether to your radio and
other equipment, your computer and data, your logbooks, or your standing with
your licensing authority. You are responsible for how your station transmits,
including staying within your licence privileges and your country's rules.
The GPL's own disclaimer of warranty and limitation of liability (sections 15
and 16 of `LICENSE`) applies in full.
