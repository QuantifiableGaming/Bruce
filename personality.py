import copy
import os

from .util import load_yaml

BASE = "calm"
REQUIRED = ["reset", "credit", "focus", "focus_names", "tips", "match_good", "answers",
            "agent_select", "agent_role", "agent_any", "debrief"]


def _merge(base, extra):
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def available(folder="personalities"):
    names = []
    for fn in sorted(os.listdir(folder)):
        if fn.endswith(".yaml"):
            data = load_yaml(os.path.join(folder, fn)) or {}
            names.append((fn[:-5], data.get("name", fn[:-5]), data.get("description", "")))
    return names


def load(name, folder="personalities", path=None):
    """Load a personality. Lists it leaves out come from the calm base personality."""
    base = load_yaml(os.path.join(folder, f"{BASE}.yaml"))
    file_path = path or os.path.join(folder, f"{name}.yaml")
    if not os.path.exists(file_path):
        choices = ", ".join(n for n, _, _ in available(folder))
        raise SystemExit(f"Unknown personality '{name}'. Choose one of: {choices}")
    lines = _merge(base, load_yaml(file_path))
    missing = [k for k in REQUIRED if k not in lines]
    if missing:
        raise SystemExit(f"Personality file is missing: {', '.join(missing)}")
    lines["key"] = name
    return lines


class PersonalityPicker:
    """Gives the personality for each match.

    "random" picks a new personality every match and says its name.
    "mix" picks a new personality every round and does not say its name.
    """

    def __init__(self, choice, folder="personalities", path=None, pool=None, rng=None):
        import random

        self.folder = folder
        self.rng = rng or random.Random()
        self.is_mix = choice == "mix" and path is None
        self.is_random = choice in ("random", "mix") and path is None
        self.last = None
        if self.is_random:
            names = [n for n, _, _ in available(folder)]
            self.pool = [n for n in (pool or names) if n in names] or names
        else:
            self.fixed = load(choice, folder, path)

    def next(self):
        if not self.is_random:
            return self.fixed
        options = [n for n in self.pool if n != self.last] or self.pool
        self.last = self.rng.choice(options)
        return load(self.last, self.folder)
