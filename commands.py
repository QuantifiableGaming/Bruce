"""Turns recognized speech into coach commands. No network, no AI model: simple keyword rules."""
import re

# Commands that never give performance guidance. They run at once, even mid-round.
INSTANT = {"mute", "unmute", "personality", "help", "repeat", "wake"}


def normalize(text):
    return " ".join(re.sub(r"[^a-z' ]", " ", text.lower()).split())


def split_wake(text, wake_words):
    """Return (heard_wake_word, command_after_it). The command can be empty."""
    words = normalize(text).split()
    wake = {w.lower() for w in wake_words}
    for i, w in enumerate(words):
        if w in wake:
            return True, " ".join(words[i + 1:])
    return False, ""


def _has(text, phrases):
    padded = f" {text} "
    return any(f" {p} " in padded for p in phrases)


# Words the speech recognizer often hears instead of the real word.
SOUND_ALIKE = {
    "school": "score", "schools": "score", "scores": "score", "scored": "score", "skull": "score",
    "scar": "score", "scorn": "score", "store": "score", "sore": "score",
    "mewed": "mute", "moot": "mute", "newt": "mute",
    "sasha": "sassy", "sassi": "sassy", "sexy": "sassy", "hike": "hype", "tuff": "tough", "karm": "calm",
}

# Example sentences for each intent. An unknown sentence that is close to one of these gets that intent.
EXAMPLES = {
    "score": ["what's the score", "what is the score", "tell me the score"],
    "doing": ["how am i doing", "how am i going", "how did i go that round", "give me an update"],
    "focus": ["what should i work on", "what can i improve on", "give me a tip"],
    "help": ["what can i say", "what can you do", "what can i ask"],
    "repeat": ["say that again", "what did you say"],
}


def _fix_words(text):
    return " ".join(SOUND_ALIKE.get(w, w) for w in text.split())


def _closest(text, min_ratio=0.74):
    import difflib

    best, best_ratio = None, min_ratio
    for intent, examples in EXAMPLES.items():
        for example in examples:
            ratio = difflib.SequenceMatcher(None, text, example).ratio()
            if ratio > best_ratio:
                best, best_ratio = intent, ratio
    return best


def parse(text, personalities):
    """Return (intent, argument). personalities is a list of personality keys, for example ["calm", "hype"]."""
    raw = normalize(text)
    if not raw:
        return ("wake", None)
    t = _fix_words(raw)
    if _has(t, ["unmute", "talk again", "you can talk", "come back", "start talking", "speak again"]):
        return ("unmute", None)
    if _has(t, ["mute", "quiet", "shut up", "stop talking", "be quiet", "silence", "pipe down"]):
        return ("mute", None)
    for name in list(personalities) + ["random"]:
        if _has(t, [name]):
            return ("personality", name)
    if _has(t, ["help", "what can you do", "what can i say", "what can i ask", "commands", "options"]):
        return ("help", None)
    if _has(t, ["repeat", "say that again", "what did you say", "say that", "pardon", "come again"]):
        return ("repeat", None)
    if _has(t, ["score", "scoreline"]):
        return ("score", None)
    if _has(t, ["work on", "focus", "improve", "fix", "better", "tip", "advice", "what should i"]):
        return ("focus", None)
    if _has(t, ["how am i", "how'm i", "how did i", "how'd i", "how was that", "how's it", "how is it", "update",
                "stats", "doing", "going", "performance", "report"]):
        return ("doing", None)
    close = _closest(t)
    if close:
        return (close, None)
    return ("unknown", text)
