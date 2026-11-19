"""Downloads and unzips the Vosk speech model, so voice control works with no manual steps."""
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile

MODEL_URL = "https://alphacephei.com/vosk/models/{name}.zip"


def model_ready(model_path):
    """A Vosk model folder has an 'am' or 'conf' folder inside it."""
    return os.path.isdir(model_path) and any(
        os.path.isdir(os.path.join(model_path, d)) for d in ("am", "conf", "graph"))


def download_model(model_path, url=None, log=print):
    """Download the model zip and unzip it to model_path. Return True when the model is ready."""
    if model_ready(model_path):
        return True
    name = os.path.basename(os.path.normpath(model_path))
    parent = os.path.dirname(os.path.abspath(model_path))
    os.makedirs(parent, exist_ok=True)
    url = url or MODEL_URL.format(name=name)
    tmp = os.path.join(tempfile.gettempdir(), name + ".zip")

    log(f"Downloading the voice model ({name}). This happens one time only.")
    req = urllib.request.Request(url, headers={"User-Agent": "valorant-coach-setup"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done, last = 0, 0.0
            while True:
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if time.monotonic() - last > 0.5:
                    last = time.monotonic()
                    pct = f"{done * 100 // total}%" if total else f"{done // 1_000_000} MB"
                    print(f"\r  {pct} ({done // 1_000_000} of {max(total, done) // 1_000_000} MB)  ",
                          end="", flush=True)
        print()
    except Exception as exc:
        log(f"Download failed: {exc}")
        log(f"You can download it by hand from {url} and unzip it into the '{parent}' folder.")
        return False

    try:
        log("Unzipping...")
        with zipfile.ZipFile(tmp) as zf:
            bad = [n for n in zf.namelist() if n.startswith("/") or ".." in n.split("/")]
            if bad:
                raise ValueError("The zip file has unsafe paths.")
            top = {n.split("/")[0] for n in zf.namelist() if n.strip("/")}
            work = tempfile.mkdtemp(prefix="vosk_")
            zf.extractall(work)
        # The zip normally holds one folder with the model name. Move it into place.
        src = os.path.join(work, top.pop()) if len(top) == 1 else work
        if os.path.exists(model_path):
            shutil.rmtree(model_path)
        shutil.move(src, model_path)
        shutil.rmtree(work, ignore_errors=True)
    except Exception as exc:
        log(f"Unzip failed: {exc}")
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    if model_ready(model_path):
        log(f"Voice model ready: {model_path}")
        return True
    log("The download did not contain a Vosk model.")
    return False
