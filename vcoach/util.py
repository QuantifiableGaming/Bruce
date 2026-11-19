import numpy as np


def load_yaml(path):
    """Read a YAML file. ruamel.yaml in pure-Python mode works on every new Python version."""
    from ruamel.yaml import YAML

    with open(path, encoding="utf-8") as fh:
        return YAML(typ="safe", pure=True).load(fh)


def crop_box(shape, region):
    """Convert a fractional region [x, y, w, h] to pixel bounds (x0, y0, x1, y1)."""
    h, w = shape[:2]
    x, y, rw, rh = region
    x0 = int(round(x * w))
    y0 = int(round(y * h))
    x1 = int(round((x + rw) * w))
    y1 = int(round((y + rh) * h))
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def crop(frame, region):
    x0, y0, x1, y1 = crop_box(frame.shape, region)
    return frame[y0:y1, x0:x1]


def median(values):
    return float(np.median(values)) if len(values) else 0.0


def set_config_value(path, keys, value):
    """Change one setting in a YAML file and keep its comments."""
    import os

    from ruamel.yaml import YAML

    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    with open(path, encoding="utf-8") as fh:
        data = y.load(fh)
    from ruamel.yaml.comments import CommentedMap

    node = data
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = CommentedMap()      # A new section, for example after an update added it.
        node = node[k]
    node[keys[-1]] = value
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        y.dump(data, fh)
    os.replace(tmp, path)


def ensure_config(path, default_path=None):
    """Make sure the settings file exists and has every setting.

    The first time, copy the default file. After an update, add settings that are
    new in the default file. Settings the player already has never change.
    """
    import os
    import shutil

    from ruamel.yaml import YAML

    default_path = default_path or os.path.join(os.path.dirname(os.path.abspath(path)), "config.default.yaml")
    if not os.path.exists(default_path):
        return False
    if not os.path.exists(path):
        shutil.copy(default_path, path)
        text = open(path, encoding="utf-8").read().split("\n")
        text = [line for line in text if not line.startswith(("# Valorant coach DEFAULT", "# Your own settings",
                                                              "# the first time it starts"))]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(text))
        return True

    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    with open(path, encoding="utf-8") as fh:
        mine = y.load(fh)
    with open(default_path, encoding="utf-8") as fh:
        default = y.load(fh)

    def fill(target, source):
        changed = False
        for key, value in source.items():
            if key not in target:
                target[key] = value
                changed = True
            elif isinstance(value, dict) and isinstance(target[key], dict):
                changed = fill(target[key], value) or changed
        return changed

    if mine is None or not fill(mine, default):
        return False
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        y.dump(mine, fh)
    os.replace(tmp, path)
    return True


def coach_name(cfg):
    """The coach's name is its wake word ("bruce" -> "Bruce")."""
    word = str(((cfg or {}).get("voice_control") or {}).get("wake_word") or "bruce").strip()
    return " ".join(w.capitalize() for w in word.split()) or "Bruce"
