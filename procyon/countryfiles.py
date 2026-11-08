"""Download the latest Big CTY country file from country-files.com.

cty.dat tells which country (DXCC entity) a callsign belongs to. It is made
and kept up to date by Jim Reisert AD1C and published under the MIT licence
at https://www.country-files.com/ - we use his "Big CTY" (the version for
everyday logging, which also lists thousands of individual callsigns). WSJT-X
installs a copy too, but only as new as that WSJT-X release.

Only happens when the user presses "Update country data" (Settings →
Advanced). Nothing about the user is sent: just two plain web requests.
The file goes into Procyon's own data folder, never into WSJT-X's.
"""

import io
import logging
import os
import re
import zipfile
from urllib.parse import urlsplit

import aiohttp

from . import __version__
from .geo import Cty
from .system import cty_version

log = logging.getLogger("procyon")

HOME_URL = os.environ.get("PROCYON_CTY_URL", "https://www.country-files.com/")
USER_AGENT = f"Procyon/{__version__} (amateur radio; github.com/KR1TTR/procyon-ft8)"
ZIP_RE = re.compile(r'href="(https?://[^"]+/bigcty/download/\d{4}/bigcty-(\d{8})\.zip)"')
MAX_ZIP = 10 * 1024 * 1024
FILE = "cty.dat"
NOTICE = "cty-copyright.txt"
CREDIT = ("cty.dat is Big CTY by Jim Reisert AD1C, from https://www.country-files.com/ "
          "(MIT licence, below).\n"
          "[Added by Procyon: the copyright holder is Jim Reisert AD1C - the notice\n"
          " below comes from his zip, where the name after the year is left blank.]\n\n")
# A copy ships inside Procyon (procyon/cty/) so new installs start current and
# no WSJT-X copy is needed. Refresh it before a release:
#   python3 -m procyon.countryfiles procyon/cty
BUNDLED_DIR = os.path.join(os.path.dirname(__file__), "cty")

# Calls a working file must place correctly before it replaces the old one.
CHECKS = {"K1ABC": "United States", "VE3ABC": "Canada", "G4ABC": "England",
          "JA1ABC": "Japan", "VK2ABC": "Australia"}


class CtyError(Exception):
    """A plain-language reason the update didn't happen (the old file stays)."""


def _check(path: str):
    try:
        c = Cty(path)
    except (ValueError, IndexError, OSError) as e:
        raise CtyError("The downloaded file doesn't look like a country file - kept the one you had.") from e
    if len(c.prefixes) < 300 or not c.version:
        raise CtyError("The downloaded file doesn't look like a country file - kept the one you had.")
    for call, country in CHECKS.items():
        got = (c.lookup(call) or {}).get("country")
        if got != country:
            raise CtyError(f"The downloaded file puts {call} in {got or 'no country'} - kept the one you had.")


async def update(data_dir: str, have: str = "") -> dict:
    """Fetch the newest Big CTY if it's newer than `have` ('yyyy-mm-dd').
    Returns {"version", "changed"}; raises CtyError with a plain message."""
    home = urlsplit(HOME_URL).netloc
    timeout = aiohttp.ClientTimeout(total=60)
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, timeout=timeout) as s:
            async with s.get(HOME_URL) as r:
                if r.status != 200:
                    raise CtyError(f"country-files.com answered with an error ({r.status}) - try again later.")
                page = await r.text(errors="replace")
            # The front page links the latest releases; take the newest Big CTY,
            # and only from the same site.
            links = [(d, u) for u, d in ZIP_RE.findall(page) if urlsplit(u).netloc == home]
            if not links:
                raise CtyError("Couldn't find the Big CTY download on country-files.com - "
                               "the site may have changed. Your country data wasn't changed.")
            date, url = max(links)
            version = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            if have and version <= have:
                return {"version": have, "changed": False}
            async with s.get(url) as r:
                if r.status != 200:
                    raise CtyError(f"The download failed ({r.status}) - try again later.")
                data = b""
                async for chunk in r.content.iter_chunked(64 * 1024):
                    data += chunk
                    if len(data) > MAX_ZIP:
                        break
    except CtyError:
        raise
    except Exception as e:  # noqa: BLE001 - offline, DNS, timeout...
        log.info("country data update: no answer (%s)", e.__class__.__name__)
        raise CtyError("Couldn't reach country-files.com - is this computer on the internet? "
                       "Your country data wasn't changed.") from e
    if len(data) > MAX_ZIP:
        raise CtyError("The download was far bigger than a country file - kept the one you had.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = {n.lower(): n for n in z.namelist()}
            if FILE not in names:
                raise CtyError("The download didn't contain cty.dat - kept the one you had.")
            cty = z.read(names[FILE])
            notice = z.read(names["copyright.txt"]) if "copyright.txt" in names else b""
    except zipfile.BadZipFile as e:
        raise CtyError("The download was damaged - try again. Your country data wasn't changed.") from e

    os.makedirs(data_dir, exist_ok=True)
    final = os.path.join(data_dir, FILE)
    tmp = final + ".new"
    with open(tmp, "wb") as f:
        f.write(cty)
    try:
        _check(tmp)
    except Exception:
        os.remove(tmp)
        raise
    got = cty_version(tmp)
    os.replace(tmp, final)    # all at once: never a half-written file
    with open(os.path.join(data_dir, NOTICE), "wb") as f:
        f.write(CREDIT.encode() + notice)
    log.info("country data updated: Big CTY %s from %s", got, url)
    return {"version": got, "changed": True}


if __name__ == "__main__":   # refresh the bundled copy: python3 -m procyon.countryfiles procyon/cty
    import asyncio
    import sys
    folder = sys.argv[1] if len(sys.argv) > 1 else BUNDLED_DIR
    have = cty_version(os.path.join(folder, FILE))
    print(asyncio.run(update(folder, have)))
