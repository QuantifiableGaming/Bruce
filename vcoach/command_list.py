"""The voice command list, in one place, for the Commands page of the Control Panel.

{coach} is the coach's name (the wake word). {mute} and {shot} are the hotkeys.
Each command is (what to say, what it does, other words that also work).
"""

GROUPS = [
    ("Coaching", [
        ("{coach}, how am I doing?", "A short report on your match so far.", "how am I going, stats, update"),
        ("{coach}, what should I work on?", "Your most repeated mistake, with a tip.", "what can I work on, any advice, tip"),
        ("{coach}, what's the score?", "The current score.", ""),
        ("{coach}, say that again", "Repeats the last line.", "repeat, what did you say, pardon"),
    ]),
    ("Control", [
        ("{coach}, mute / unmute", "The coach stops or starts talking. Hotkey: {mute}.", "quiet, stop talking / talk again"),
        ("{coach}, switch to Savage", "Changes the personality. Say Mix or Random for a new one each round.", ""),
        ("{coach}, help", "Reads the command list.", "what can I say, what can you do"),
        ("Hotkey {shot}", "Saves a screenshot in the screenshots folder.", ""),
    ]),
    ("Chat and memory", [
        ("Hey {coach}, ...", "A chat about anything between rounds. For 8 seconds after an answer, ask again "
                             "without the name.", ""),
        ("Hey {coach}, remember that ...", "Keeps a fact about you in player_profile.md, for example your "
                                           "favourite food.", "don't forget that"),
        ("{coach}, what do you know about me?", "Reads the facts that you asked it to remember.", "what do you remember"),
        ("{coach}, forget that", "Removes the last fact.", "forget it, forget the last thing"),
    ]),
    ("Content creator", [
        ("{coach}, mark that", "Notes the moment in the match report. Presses your clip hotkey if you set one.",
         "clip that, bookmark"),
        ("{coach}, replay that", "Instant replay on your stream through OBS.", "instant replay, run it back"),
        ("{coach}, slow mo", "The last half of the replay at half speed, on your stream through OBS.",
         "slow motion, slow that down, replay that in slow motion"),
        ("{coach}, rate that round", "A score out of 10 for the last round, with the reason.", "rate me, out of ten"),
        ("{coach}, roast me", "A joke about your mistakes.", ""),
        ("{coach}, hype me up", "An energy line.", "pump me up, gas me up"),
        ("{coach}, recap", "The score, your deaths and your main mistake.", "sum up, story so far"),
        ("{coach}, say hi / sign off", "An intro or outro with your channel name.", "hi chat / bye chat, wrap it up"),
        ("{coach}, start the challenge", "In the Range: 20 stops, scored, with a result card.", "stop the challenge"),
    ]),
]

NOTES = [
    "The coach talks only between rounds, in the buy phase and in menus. It gives no tips in a live round.",
    "A coaching question in a live round gets its answer after the round.",
    "Mute, unmute, personality, help, mark, replay, slow mo, challenge, say hi and sign off work at once.",
]


def command_groups(cfg):
    """The list with your coach name and hotkeys filled in."""
    from .util import coach_name

    keys = (cfg or {}).get("coach") or {}
    values = {"coach": coach_name(cfg), "mute": str(keys.get("mute_hotkey") or "F8").upper(),
              "shot": str(keys.get("screenshot_hotkey") or "F9").upper()}
    out = []
    for name, items in GROUPS:
        out.append({"name": name, "items": [{"say": s.format(**values), "does": d.format(**values), "also": a}
                                            for s, d, a in items]})
    return {"coach": values["coach"], "groups": out, "notes": NOTES}
