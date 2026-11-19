"""Updates from GitHub (github.com/QuantifiableGaming/Bruce).

check() reads the VERSION file on GitHub and compares it with the local VERSION file.
update() downloads the newest program as a zip and copies its files over this folder.
It never touches your own files: config.yaml, secrets.yaml, reports, logs, recordings and the
other folders in KEEP. Each file that it replaces goes to backups/update_<date>/ first.
"""
import io
import os
import shutil
import urllib.request
import zipfile
from datetime import datetime

REPO = "QuantifiableGaming/Bruce"
BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/VERSION"
ZIP_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
AGENT = {"User-Agent": "ValorantCoach/1.0"}

# Your own files and folders. An update never writes these.
KEEP = {"config.yaml", "secrets.yaml", "player_profile.md", "calibration.png", "calibration_raw.png", "reports", "logs",
        "recordings", "screenshots", "videos", "parts", "models", "cache", "share", "backups", ".git"}


def parse(text):
    """"0.3.1" -> (0, 3, 1). Anything else -> None."""
    try:
        return tuple(int(p) for p in str(text).strip().lstrip("v").split("."))
    except ValueError:
        return None


def local_version(root):
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def _get(url, timeout):
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=timeout) as r:
        return r.read()


def check(root, timeout=5.0):
    """{"local", "remote", "newer"}. remote is None when GitHub cannot be reached."""
    local = local_version(root)
    try:
        remote = _get(VERSION_URL, timeout).decode("utf-8").strip()
    except Exception:
        return {"local": local, "remote": None, "newer": False}
    a, b = parse(local), parse(remote)
    return {"local": local, "remote": remote, "newer": bool(a is not None and b is not None and b > a)}


def _kept(rel):
    first = rel.replace("\\", "/").split("/")[0]
    return first in KEEP or "__pycache__" in rel


def apply_zip(root, data, when=None):
    """Copy the program files from a GitHub zip over root. Returns (changed files, backup folder)."""
    backup = os.path.join(root, "backups", f"update_{(when or datetime.now()):%Y%m%d_%H%M%S}")
    changed = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        prefix = os.path.commonprefix(names)
        prefix = prefix[:prefix.rfind("/") + 1]          # GitHub puts all files in "Bruce-main/".
        for name in names:
            rel = name[len(prefix):]
            if not rel or _kept(rel) or ".." in rel.split("/"):
                continue
            new = z.read(name)
            dest = os.path.join(root, *rel.split("/"))
            if os.path.exists(dest):
                with open(dest, "rb") as f:
                    if f.read() == new:
                        continue
                keep = os.path.join(backup, *rel.split("/"))
                os.makedirs(os.path.dirname(keep), exist_ok=True)
                shutil.copy2(dest, keep)
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(dest, "wb") as f:
                f.write(new)
            changed.append(rel)
    return changed, (backup if os.path.isdir(backup) else None)


def update(root, timeout=60.0):
    """Download and install the newest version. Prints what it does."""
    info = check(root)
    if info["remote"] is None:
        print("Could not reach GitHub. Check the internet connection and try again.")
        return 1
    print(f"Your version: {info['local']}. Version on GitHub: {info['remote']}.")
    with open(os.path.join(root, "requirements.txt"), "rb") as f:
        old_req = f.read()
    print("Downloading the newest version...")
    try:
        data = _get(ZIP_URL, timeout)
    except Exception as exc:
        print(f"The download failed: {exc}")
        return 1
    changed, backup = apply_zip(root, data)
    if not changed:
        print("All files are already up to date.")
        return 0
    print(f"Updated {len(changed)} files:")
    for rel in changed:
        print(f"  {rel}")
    if backup:
        print(f"The old files are in {os.path.relpath(backup, root)}.")
    with open(os.path.join(root, "requirements.txt"), "rb") as f:
        if f.read() != old_req:
            print("The list of Python packages changed. Press Install on the Home page of the Control Panel.")
    print("Done. Close and start the Control Panel and the coach again to use the new version.")
    return 0
