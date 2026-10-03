"""Az Cut auto-update: checks GitHub Releases for a newer Setup.exe.

The installed app calls check_for_updates() on startup. If a newer release
exists, the dashboard shows a one-click update banner.
"""

import json
import os
import re
import sys
import urllib.request

if getattr(sys, "frozen", False):
    _BASE = sys._MEIPASS
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

REPO_FILE = os.path.join(_BASE, "update_repo.txt")
API_TIMEOUT = 6


def get_repo():
    """Return 'username/repo' from the bundled update_repo.txt, or None."""
    try:
        with open(REPO_FILE, encoding="utf-8") as f:
            repo = f.read().strip()
        if repo and "/" in repo and "username" not in repo.lower():
            return repo
    except OSError:
        pass
    return None


def _ver_tuple(v):
    v = re.sub(r"^[^0-9]*", "", str(v))
    parts = []
    for x in re.split(r"[.\-]", v):
        m = re.match(r"\d+", x)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts) or (0,)


def check_for_updates(current_version):
    """Return dict with update info. Never raises; silent on any failure."""
    repo = get_repo()
    if not repo:
        return {"ok": False, "reason": "no-repo"}
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={
        "User-Agent": "AzCut-Updater",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"ok": False, "reason": "network"}
    tag = str(data.get("tag_name", ""))
    dl_url, asset_name = None, None
    for a in data.get("assets", []) or []:
        name = a.get("name", "")
        if name.startswith("AzCut-Setup-") and name.endswith(".exe"):
            dl_url = a.get("browser_download_url")
            asset_name = name
            break
    if not dl_url:
        return {"ok": False, "reason": "no-asset"}
    latest_t = _ver_tuple(tag)
    cur_t = _ver_tuple(current_version)
    return {
        "ok": True,
        "update_available": latest_t > cur_t,
        "latest_version": re.sub(r"^[^0-9]*", "", tag),
        "current_version": current_version,
        "download_url": dl_url,
        "asset_name": asset_name,
    }
