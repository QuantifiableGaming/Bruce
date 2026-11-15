"""Installs Tesseract OCR on Windows.

Order: 1) winget, 2) the newest 64-bit installer from the UB Mannheim GitHub releases,
3) open the download page in the browser so the player can install it by hand.
"""
import json
import os
import re
import subprocess
import tempfile
import urllib.request
import webbrowser

from .hud import find_tesseract

RELEASES_API = "https://api.github.com/repos/UB-Mannheim/tesseract/releases/latest"
WIKI = "https://github.com/UB-Mannheim/tesseract/wiki"


def _winget(log):
    try:
        log("Trying winget (Windows Package Manager)...")
        result = subprocess.run(
            ["winget", "install", "-e", "--id", "UB-Mannheim.TesseractOCR",
             "--accept-package-agreements", "--accept-source-agreements"],
            check=False)
        return result.returncode == 0 or find_tesseract() is not None
    except FileNotFoundError:
        log("winget is not available on this PC.")
        return False


def _github_installer(log):
    log("Getting the newest Tesseract installer from GitHub...")
    req = urllib.request.Request(RELEASES_API, headers={"User-Agent": "valorant-coach-setup",
                                                        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        release = json.loads(resp.read().decode("utf-8"))
    assets = [a for a in release.get("assets", [])
              if re.search(r"w64.*setup.*\.exe$", a.get("name", ""), re.I)]
    if not assets:
        log("No Windows installer was found in the newest release.")
        return False
    asset = assets[0]
    path = os.path.join(tempfile.gettempdir(), asset["name"])
    log(f"Downloading {asset['name']} ({asset.get('size', 0) // 1_000_000} MB)...")
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "valorant-coach-setup"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(path, "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    log("Starting the installer. Click Yes if Windows asks, and keep the default install folder.")
    subprocess.run([path], check=False)
    return find_tesseract() is not None


def install_tesseract(log=print):
    """Return True when Tesseract is installed at the end."""
    found = find_tesseract()
    if found:
        log(f"Tesseract is already installed: {found}")
        return True
    if os.name != "nt":
        log("Automatic install works on Windows only.")
        return False
    for step in (_winget, _github_installer):
        try:
            if step(log) and find_tesseract():
                log(f"Tesseract is installed: {find_tesseract()}")
                return True
        except Exception as exc:
            log(f"That did not work: {exc}")
    log("Automatic install did not work. Opening the download page.")
    log("Download the 64-bit installer (w64-setup), run it, and keep the default folder.")
    webbrowser.open(WIKI)
    return False
