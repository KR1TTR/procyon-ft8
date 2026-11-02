"""Is there a newer Procyon? Asks GitHub for the latest release, once a day.

Only the version number is asked for: no callsign, log or settings leave the
computer. GitHub's "latest" release skips pre-releases, so testers' betas
don't nag everyone. No answer (offline, in the field) just means no message."""

import logging
import os
import re

import aiohttp

from . import __version__

log = logging.getLogger("procyon")

RELEASES_URL = os.environ.get("PROCYON_RELEASES_URL",
                              "https://api.github.com/repos/KR1TTR/procyon-ft8/releases/latest")
CHECK_EVERY_SEC = 24 * 3600
USER_AGENT = f"Procyon/{__version__} (FT8; github.com/KR1TTR/procyon-ft8)"


def version_key(v: str) -> tuple:
    """'v1.0.2' -> (1, 0, 2); anything after the numbers (e.g. '-beta') is ignored."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", v.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(theirs: str, ours: str = __version__) -> bool:
    a, b = version_key(theirs), version_key(ours)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


async def latest() -> dict | None:
    """{"version", "url"} of the newest release, or None if GitHub can't be asked."""
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT,
                                                  "Accept": "application/vnd.github+json"}) as s:
            async with s.get(RELEASES_URL, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    log.info("new version check: GitHub answered %s", r.status)
                    return None
                j = await r.json(content_type=None)
    except Exception as e:  # noqa: BLE001 - offline, DNS, timeout: try again tomorrow
        log.info("new version check: no answer (%s)", e.__class__.__name__)
        return None
    tag = str(j.get("tag_name") or "")
    if j.get("draft") or j.get("prerelease") or not version_key(tag):
        return None
    return {"version": tag.lstrip("vV"), "url": str(j.get("html_url") or "")}
