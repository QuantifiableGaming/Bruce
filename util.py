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
    node = data
    for k in keys[:-1]:
        node = node[k]
    node[keys[-1]] = value
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        y.dump(data, fh)
    os.replace(tmp, path)
