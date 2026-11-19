"""Local web server for the visual region editor (editor.html).

It listens on 127.0.0.1 only, so no other computer can connect. It reads and writes
config.yaml, takes screenshots, and tests the HUD reading on the current screenshot.
"""
import copy
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

REGION_KEYS = ("ammo", "health", "banner", "enemy_search", "gun_ignore")
COLORS = ("red", "yellow", "purple")
GUN_SIDES = ("right", "left")


def _yaml_rt():
    from ruamel.yaml import YAML

    y = YAML()  # Round-trip mode keeps the comments in config.yaml.
    y.preserve_quotes = True
    y.width = 4096
    return y


def read_config(config_path):
    with open(config_path, encoding="utf-8") as fh:
        return _yaml_rt().load(fh)


def validate_regions(regions):
    """Return a clean {name: [x, y, w, h]} dict, or raise ValueError."""
    clean = {}
    for key, value in regions.items():
        if key not in REGION_KEYS:
            raise ValueError(f"Unknown region: {key}")
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise ValueError(f"Region {key} needs four numbers.")
        x, y, w, h = (float(v) for v in value)
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
            raise ValueError(f"Region {key} is outside the screen.")
        if x + w > 1.0001 or y + h > 1.0001:
            raise ValueError(f"Region {key} goes past the edge of the screen.")
        clean[key] = [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]
    return clean


def write_regions(config_path, regions):
    """Change only the region numbers in config.yaml. Comments and other settings stay."""
    from ruamel.yaml.comments import CommentedSeq

    regions = validate_regions(regions)
    y = _yaml_rt()
    with open(config_path, encoding="utf-8") as fh:
        data = y.load(fh)
    for key, values in regions.items():
        seq = data["regions"].get(key)
        if isinstance(seq, list) and len(seq) == 4:
            for i, v in enumerate(values):
                seq[i] = v  # Change values in place, so end-of-line comments stay.
        else:
            new = CommentedSeq(values)
            new.fa.set_flow_style()
            data["regions"][key] = new
    tmp = config_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        y.dump(data, fh)
    os.replace(tmp, config_path)
    return regions


def write_enemy_color(config_path, color):
    from .util import set_config_value

    if color not in COLORS:
        raise ValueError(f"Unknown colour: {color}")
    set_config_value(config_path, ["enemy_highlight", "color"], color)


def write_gun_side(config_path, side):
    from .util import set_config_value

    if side not in GUN_SIDES:
        raise ValueError(f"Unknown gun side: {side}")
    set_config_value(config_path, ["gun_side"], side)


def mirror_region(region):
    """Flip a region to the other side of the screen."""
    x, y, w, h = region
    return [round(1 - x - w, 4), y, w, h]


def test_regions(cfg, regions, frame, color=None):
    """Run the real HUD reading and enemy detection with the given regions."""
    from .vision import EnemyDetector

    cfg = copy.deepcopy(dict(cfg))
    cfg["regions"] = dict(cfg["regions"])
    cfg["regions"].update(validate_regions(regions))
    if color:
        if color not in COLORS:
            raise ValueError(f"Unknown colour: {color}")
        cfg["enemy_highlight"] = dict(cfg["enemy_highlight"], color=color)
    out = {}
    try:
        from .hud import HudReader

        reader = HudReader(cfg)
        out["ammo"] = reader.ammo(frame)
        out["health"] = reader.health(frame)
        out["banner"] = reader.banner(frame)
        score = reader.score(frame)
        out["score"] = None if score is None else f"{score[0]}-{score[1]}"
    except Exception as exc:  # Tesseract missing or not on PATH.
        out["ocr_error"] = f"Text reading is not available: {exc}"
    detector = EnemyDetector(cfg)
    enemy = detector.detect(frame)
    # A picture of the pixels that match the enemy highlight colour, for the editor to show.
    import base64

    import numpy as np

    mask, _ = detector.mask(frame)
    rgba = np.zeros((mask.shape[0], mask.shape[1], 4), np.uint8)
    rgba[mask > 0] = (255, 0, 255, 230)
    ok, png = cv2.imencode(".png", rgba)
    out["enemy_mask"] = "data:image/png;base64," + base64.b64encode(png.tobytes()).decode() if ok else None
    out["enemy_color"] = str(cfg.get("enemy_highlight", {}).get("color", "red"))
    out["enemy"] = {
        "pixels": enemy["pixels"],
        "visible": bool(enemy["visible"]),
        "head_offset": None if enemy["head_offset"] is None else round(enemy["head_offset"], 1),
        "ignored": enemy.get("ignored", 0),
    }
    return out


class EditorServer:
    def __init__(self, root, config_path="config.yaml", image_path="calibration_raw.png",
                 html_path="editor.html", port=8765):
        self.root = root
        self.config_path = os.path.join(root, config_path)
        self.image_path = os.path.join(root, image_path)
        self.html_path = os.path.join(root, html_path)
        self.httpd = None
        self.port = port
        self.pages = {"/": self.html_path, "/editor.html": self.html_path}
        self.install = {"running": False, "log": [], "found": None}

    # Hooks for a bigger server (the control panel). Return False when the path is not handled.
    def extra_get(self, handler, path):
        return False

    def extra_post(self, handler, path, body):
        return False

    def on_shutdown(self):
        pass

    def start_install(self):
        if self.install["running"]:
            return
        from .tesseract_setup import install_tesseract

        self.install = {"running": True, "log": [], "found": None}

        def run():
            ok = False
            try:
                ok = install_tesseract(log=lambda m: (self.install["log"].append(m), print(m)))
            except Exception as exc:
                self.install["log"].append(f"Install failed: {exc}")
            self.install["found"] = ok
            self.install["running"] = False

        threading.Thread(target=run, daemon=True).start()

    def capture(self, delay):
        from .capture import ScreenSource

        time.sleep(max(0, min(30, delay)))
        monitor = int(read_config(self.config_path).get("monitor", 1))
        _, frame = ScreenSource(monitor).read_frame()
        if frame is None:
            raise RuntimeError("Windows cannot capture the screen right now. Unlock the PC and try again.")
        cv2.imwrite(self.image_path, frame)
        return frame.shape

    def make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # Keep the console quiet.
                pass

            def _send(self, code, body, ctype="application/json"):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode("utf-8")
                elif isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json_body(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def do_GET(self):
                path = self.path.split("?")[0]
                if path in server.pages:
                    with open(server.pages[path], "rb") as fh:
                        return self._send(200, fh.read(), "text/html; charset=utf-8")
                if path == "/image":
                    if not os.path.exists(server.image_path):
                        return self._send(404, {"error": "No screenshot yet."})
                    with open(server.image_path, "rb") as fh:
                        return self._send(200, fh.read(), "image/png")
                if path == "/api/tesseract":
                    from .hud import find_tesseract

                    st = dict(server.install)
                    st["path"] = find_tesseract(str(read_config(server.config_path).get("tesseract_cmd") or ""))
                    return self._send(200, st)
                if path == "/api/state":
                    cfg = read_config(server.config_path)
                    regions = {k: [float(v) for v in cfg["regions"][k]] for k in REGION_KEYS if k in cfg["regions"]}
                    return self._send(200, {
                        "regions": regions,
                        "enemy_color": str(cfg.get("enemy_highlight", {}).get("color", "red")),
                        "gun_side": str(cfg.get("gun_side", "right")),
                        "has_image": os.path.exists(server.image_path),
                        "image_time": os.path.getmtime(server.image_path) if os.path.exists(server.image_path) else None,
                    })
                try:
                    if server.extra_get(self, path) is not False:
                        return None
                except Exception as exc:
                    return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
                return self._send(404, {"error": "Not found"})

            def do_POST(self):
                path = self.path.split("?")[0]
                try:
                    body = self._json_body()
                    if path == "/api/regions":
                        saved = write_regions(server.config_path, body.get("regions", {}))
                        if body.get("enemy_color"):
                            write_enemy_color(server.config_path, body["enemy_color"])
                        if body.get("gun_side"):
                            write_gun_side(server.config_path, body["gun_side"])
                        return self._send(200, {"ok": True, "regions": saved})
                    if path == "/api/install_tesseract":
                        server.start_install()
                        return self._send(200, {"ok": True})
                    if path == "/api/test":
                        if not os.path.exists(server.image_path):
                            return self._send(400, {"error": "Take or load a screenshot first."})
                        from .util import load_yaml

                        frame = cv2.imread(server.image_path)
                        result = test_regions(load_yaml(server.config_path), body.get("regions", {}), frame,
                                              body.get("enemy_color"))
                        return self._send(200, result)
                    if path == "/api/capture":
                        shape = server.capture(float(body.get("delay", 10)))
                        return self._send(200, {"ok": True, "width": shape[1], "height": shape[0]})
                    if path == "/api/upload":
                        # The page sends a picture the user picked (PNG or JPG), as raw bytes in base64.
                        import base64

                        import numpy as np

                        raw = base64.b64decode(body["data"].split(",")[-1])
                        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                        if frame is None:
                            return self._send(400, {"error": "That file is not a picture."})
                        cv2.imwrite(server.image_path, frame)
                        return self._send(200, {"ok": True})
                    if path == "/api/shutdown":
                        self._send(200, {"ok": True})
                        server.on_shutdown()
                        threading.Thread(target=server.httpd.shutdown, daemon=True).start()
                        return None
                    if server.extra_post(self, path, body) is not False:
                        return None
                    return self._send(404, {"error": "Not found"})
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                except Exception as exc:
                    return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

        return Handler

    def start(self):
        """Bind to a free local port, starting at the chosen one."""
        for port in range(self.port, self.port + 20):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), self.make_handler())
                self.port = port
                return f"http://127.0.0.1:{port}/"
            except OSError:
                continue
        raise OSError("No free port for the editor.")

    def serve(self):
        self.httpd.serve_forever()
        self.httpd.server_close()
