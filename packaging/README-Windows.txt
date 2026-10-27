PROCYON for Windows
===================

Version {VERSION} - {DATE}
Created by Justin Stapleton, KR1TTR, with Claude Code (Claude Opus 5.5)


BEFORE YOU START
----------------
Install WSJT-X from https://wsjt.sourceforge.io  (Procyon uses its FT8
decoder and encoder, and the copy of Hamlib it includes for radio control).
You don't need to install Python - it's included in this folder.


STARTING PROCYON
----------------
EASIEST: use the installer instead of this zip -
Procyon-x.y.z-windows-setup.exe puts Procyon in your Start menu, runs it
without a console window, and updates or uninstalls cleanly.

To run Procyon from this zip without installing it:
1. EXTRACT THE WHOLE ZIP FIRST. Double-clicking a zip only lets you look
   inside it - Procyon can't run from there. Right-click the zip and choose
   "Extract All...", pick somewhere permanent (e.g. Documents), then open the
   extracted "Procyon-x.y.z-windows" folder.
   (Tip: before extracting, right-click the zip > Properties > tick
   "Unblock" > OK. That stops Windows asking about each file.)
2. In the extracted folder, double-click "Procyon.bat". A small window
   opens, then Procyon opens in your web browser. Keep the small window
   open; closing it stops Procyon.
3. To try it without a radio, double-click "Procyon DEMO ONLY - no radio.bat"
   instead. It starts a simulated band: the stations are not real, nothing
   is transmitted, and its practice log can't be uploaded anywhere. For
   real operating, always start "Procyon.bat".

If Windows shows "Windows protected your PC", click "More info" then
"Run anyway". This appears for any new program that isn't from a big
publisher.


FIRST-TIME SETUP
----------------
Open Settings (the gear icon). If you already use WSJT-X, click
"Import settings from WSJT-X" at the top - it fills in your call, grid,
radio, COM port, PTT and sound card. Check it, then press Save.
Close WSJT-X before using Procyon; they can't share the radio and sound card.


USING IT FROM A TABLET OR ANOTHER PC
------------------------------------
Settings > Remote access: choose a PIN and turn on "Allow other devices on
my network". If Windows Firewall asks whether to allow Python on networks,
choose "Private networks" only.

Your settings and logbook are kept in  %LOCALAPPDATA%\Procyon

Procyon is free software under the GNU General Public License v3
(see LICENSE). Only transmit with a valid amateur radio licence.

USE AT YOUR OWN RISK: Procyon is provided as is, without warranty of any
kind. Its author accepts no responsibility for any damage, loss or harm
from its use, to equipment, data, logbooks or otherwise. You are
responsible for how your station transmits.
