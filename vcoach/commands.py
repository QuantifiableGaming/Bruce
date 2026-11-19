"""Turns recognized speech into coach commands. No network, no AI model: simple keyword rules."""
import re

# Commands that never give performance guidance. They run at once, even mid-round.
INSTANT = {"mute", "unmute", "personality", "help", "repeat", "wake", "mark", "intro", "outro", "replay", "challenge"}
# Commands for content creators. mark, intro and outro run at once. roast, hype, rate and recap
# talk about your play, so they wait for the end of the round like the coaching answers.
CREATOR = {"mark", "replay", "intro", "outro", "roast", "hype_me", "rate", "recap"}


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


CHAT_WORDS = {"hey", "hay", "hi", "oi", "yo", "hello", "a"}   # "Hey Bruce": the recognizer often hears "a Bruce".


def wake_mode(text, wake_words):
    """ "chat" when the wake word comes right after "hey" ("Hey Bruce, ..."), else "coach"."""
    words = normalize(text).split()
    wake = {w.lower() for w in wake_words}
    for i, w in enumerate(words):
        if w in wake:
            return "chat" if i > 0 and words[i - 1] in CHAT_WORDS else "coach"
    return "coach"


SWITCH_WORDS = ["switch", "switch to", "change to", "swap to", "go", "use", "be", "turn into", "personality", "mode",
                "coach"]
# What the voice model often hears for some personality names.
NAME_SOUNDS = {"flirty": ["flirt", "flirtatious", "flirting", "flirty mode"],
               "degen": ["degenerate", "the gen", "dejan", "the jen", "degens", "deejay"]}


def _has(text, phrases):
    padded = f" {text} "
    return any(f" {p} " in padded for p in phrases)


HELP_PHRASES = ["help", "what can you do", "what can i say", "what can i ask", "what can you say", "what do you do",
                "what should i say", "what do i say", "how do i use you", "how does this work", "commands",
                "command", "list commands", "what are the commands", "options", "instructions", "menu"]

# The spoken answer to "Bruce, help". One list for every personality, so new commands are always in it.
HELP_TEXT = ("Say Hey {coach} to chat about anything. "
             "Say {coach}, then: how am I doing, what should I work on, what's the score, say that again, "
             "mute, unmute, or switch to a personality. In the Range: start the challenge. For videos: mark that, replay that, rate that round, roast me, "
             "hype me up, recap, say hi, and sign off.")

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
    "help": ["what can i say", "what can you do", "what can i ask", "what are the commands", "give me the commands"],
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
    if _has(t, ["stop the challenge", "cancel the challenge", "end the challenge", "stop challenge", "cancel challenge"]):
        return ("challenge", "stop")
    if _has(t, ["challenge", "start the challenge", "range challenge", "test my stops"]):
        return ("challenge", "start")
    if _has(t, ["replay that", "replay it", "replay this", "instant replay", "run it back", "run that back",
                "show the replay", "play it back", "replay"]):
        return ("replay", None)
    if _has(t, ["mark that", "mark it", "mark this", "clip that", "clip it", "clip this", "bookmark", "save that",
                "save the clip", "mark",
                # Heard on 17 Nov in a match: the voice model hears these for "mark that" and "clip that".
                "mock that", "mock it", "clip fat", "clip dat", "clip than", "clip at"]):
        return ("mark", None)
    if _has(t, ["hype me", "hype me up", "pump me up", "gas me up", "motivate me", "get me going"]):
        return ("hype_me", None)
    if _has(t, ["roast me", "roast", "insult me", "be mean"]):
        return ("roast", None)
    if _has(t, ["rate that", "rate the round", "rate my round", "rate that round", "rate me", "out of ten",
                "rate it",
                # The voice model hears "rate" as "right" or "write".
                "right that round", "write that round", "right the round", "right my round",
                "rights at last round", "rights that round", "right at last round", "rate last round"]):
        return ("rate", None)
    if _has(t, ["recap", "sum up", "summary", "story so far"]):
        return ("recap", None)
    if _has(t, ["say hi", "say hello", "intro", "introduce yourself", "hi chat", "hello chat", "say hi to chat"]):
        return ("intro", None)
    if _has(t, ["outro", "sign off", "say bye", "say goodbye", "wrap it up", "bye chat"]):
        return ("outro", None)
    # A personality name counts only with a switch word, or alone ("Bruce, savage"), so a chat
    # such as "I feel calm today" does not change the coach.
    short = len(t.split()) <= 3
    switch = _has(t, SWITCH_WORDS)
    for name in list(personalities) + ["random", "mix"]:
        if _has(t, [name] + NAME_SOUNDS.get(name, [])) and (short or switch):
            return ("personality", name)
    if _has(t, HELP_PHRASES):
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
