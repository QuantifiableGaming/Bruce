"""Little facts about the player that the coach keeps between sessions.

The facts are in player_profile.md in the coach folder, one fact per line. The coach adds a fact
only when you ask it to ("Hey Friday, remember that my favourite food is pizza"). You can edit
or delete lines in Notepad. Updates, the share zip and GitHub never touch this file.
The AI gets these facts with each chat question and each coaching question.
"""
import os
import re

PATH = "player_profile.md"
MAX_FACTS = 50          # The oldest facts go first after this many.
MAX_CHARS = 200         # One fact is at most this long.
HEADER = ("# Player profile\n\n"
          "The coach reads these facts when you talk to it. One fact per line, each line starts with \"- \".\n"
          "You can change or delete lines. To add a fact by voice, say: Hey (wake word), remember that ...\n\n")


def clean_fact(text):
    """ "that my favourite food is pizza" -> "My favourite food is pizza." None when it is too short."""
    text = " ".join(str(text or "").split()).strip(" .,!?")
    text = re.sub(r"^(that|this|is that|about|me that)\s+", "", text, flags=re.I)
    if len(text.split()) < 2:
        return None
    text = text[:MAX_CHARS].rstrip()
    return text[0].upper() + text[1:] + ("" if text[-1] in ".!?" else ".")


class PlayerProfile:
    def __init__(self, path=PATH):
        self.path = path

    def facts(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return [line[2:].strip() for line in f if line.startswith("- ") and line[2:].strip()]
        except OSError:
            return []

    def _save(self, facts):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(HEADER + "".join(f"- {x}\n" for x in facts))
        os.replace(tmp, self.path)

    def add(self, text):
        """Save one fact. Returns the cleaned fact, or None when there was nothing to save."""
        fact = clean_fact(text)
        if not fact:
            return None
        facts = [x for x in self.facts() if x.lower() != fact.lower()]
        facts.append(fact)
        self._save(facts[-MAX_FACTS:])
        return fact

    def forget_last(self):
        """Remove the newest fact. Returns it, or None when the profile is empty."""
        facts = self.facts()
        if not facts:
            return None
        last = facts.pop()
        self._save(facts)
        return last
