"""The player's view of the coach log.

logs/coach_log.txt has only what the player needs: what you said, what the coach said, the marks,
replays and slow motion replays with their times, and the match and round results.
logs/debug_log.txt has every line, for finding and fixing problems.
"""
import re
import time

_HEARD = re.compile(r"^\[heard\] (?:follow-up: )?'(.*?)'(?: -> (\S+)|$)")
_ROUND = re.compile(r"^(Round \d+: [^|]+)\|\s*(score [^|]+)")


def player_line(line):
    """The player's version of one log line, or None when the line is for debugging only."""
    line = line.rstrip()
    if line.startswith("[heard] "):
        m = _HEARD.match(line)
        if not m or m.group(2) == "ignored" or not m.group(1).strip():
            return None              # the wake word alone, or the coach's own voice
        return f"You: {m.group(1)}"
    if line.startswith("[coach] "):
        text = line[len("[coach] "):]
        if text.startswith("(not spoken: the round is live) "):
            return "Coach (not spoken, the round was live): " + text[len("(not spoken: the round is live) "):]
        if text.startswith("(not spoken) "):
            return None
        return f"Coach: {text}"
    if line.startswith("[death] "):
        return line[len("[death] "):].split(" Seen by:")[0]
    if line.startswith("[mark] "):
        return "Marked: " + line[len("[mark] "):]
    if line.startswith("[replay] "):
        text = line[len("[replay] "):]
        if text.startswith(("Playing", "OBS error", "Could not", "The round is live")):
            return "Replay: " + text
        return None
    if line.startswith(("[match] Match on", "[match] Match over", "[match] Agent:")):
        return line[len("[match] "):]
    m = _ROUND.match(line)
    if m:
        return f"{m.group(1).strip()}, {m.group(2).strip()}."
    if line.startswith(("Coach started", "Coach stopped", "Report saved", "Personality:", "[update]")):
        return line
    if "ERROR" in line or line.startswith("Traceback"):
        return "Error: something went wrong. The details are in logs\\debug_log.txt."
    return None


class LogFiles:
    """Takes everything the coach prints. Writes all of it to the debug log, and the player's view,
    with the time of day, to the coach log."""

    def __init__(self, stream, debug_path, player_path):
        import os

        for p in (debug_path, player_path):
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        self.stream = stream
        self.debug = open(debug_path, "w", encoding="utf-8")
        self.player = open(player_path, "w", encoding="utf-8")
        self.part = ""
        self.last_error = 0.0

    def write(self, text):
        self.stream.write(text)
        try:
            self.debug.write(text)
            self.debug.flush()
            self.part += text
            while "\n" in self.part:
                line, self.part = self.part.split("\n", 1)
                out = player_line(line)
                if out and out.startswith("Error:"):
                    if time.monotonic() - self.last_error < 10:
                        continue         # one error line for a traceback, not one per line
                    self.last_error = time.monotonic()
                if out:
                    self.player.write(f"{time.strftime('%H:%M:%S')}  {out}\n")
                    self.player.flush()
        except (OSError, ValueError):
            pass
        return len(text)

    def flush(self):
        self.stream.flush()
