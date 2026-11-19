"""Twitch chat for the coach: personality votes and the !coach command.

Reading chat needs no account: the coach joins your channel's chat as an anonymous
viewer. To answer !coach in chat, the coach needs a chat account and its OAuth token
(twitch_oauth_token in secrets.yaml). Without a token, the coach only reads chat.

Chat commands:
  !vote savage     one vote for a personality. One vote for each viewer in each round.
                   At the end of the round, the personality with the most votes takes over.
  !coach           the session numbers, in chat (needs the token). One answer each 30 seconds.

The coach never sends anything to the game. It only reads and writes Twitch chat.
"""
import queue
import random
import re
import socket
import ssl
import threading
import time

HOST, PORT = "irc.chat.twitch.tv", 6697
LINE = re.compile(r"^(?:@\S+ )?:(?P<user>[^!\s]+)!\S+ PRIVMSG #(?P<chan>\S+) :(?P<text>.*)$")


def parse_line(line):
    """(user, text) for a chat message line, else None."""
    m = LINE.match(line.strip())
    return (m["user"].lower(), m["text"].strip()) if m else None


class Votes:
    """Chat votes for the next personality. One vote for each viewer; a new vote replaces the old one."""

    def __init__(self, names):
        self.names = [n.lower() for n in names]
        self.by_user = {}

    def add(self, user, text):
        parts = text.lower().split()
        if len(parts) < 2 or parts[0] != "!vote":
            return None
        choice = parts[1].strip("!.,")
        if choice not in self.names:
            return None
        self.by_user[user] = choice
        return choice

    def winner(self, rng=random):
        """(name, votes) with the most votes, or None. A tie is broken at random. Clears the votes."""
        if not self.by_user:
            return None
        counts = {}
        for c in self.by_user.values():
            counts[c] = counts.get(c, 0) + 1
        top = max(counts.values())
        name = rng.choice(sorted(n for n, k in counts.items() if k == top))
        self.by_user = {}
        return name, top


class TwitchChat:
    """Reads one Twitch channel's chat in a background thread. Messages go to self.messages."""

    def __init__(self, channel, nick="", token="", log=print, connect=None):
        self.channel = channel.lower().lstrip("#").strip()
        self.token = token.strip()
        if self.token and not self.token.startswith("oauth:"):
            self.token = "oauth:" + self.token
        self.nick = (nick.lower().strip() if self.token and nick else f"justinfan{random.randint(10000, 99999)}")
        self.can_send = bool(self.token and nick)
        self.messages = queue.Queue()
        self.log = log
        self.connect = connect or self._connect
        self.sock = None
        self.running = False
        self.lock = threading.Lock()

    def start(self):
        if not self.channel:
            self.log("[twitch] No channel set. Twitch chat is off.")
            return
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass

    def send(self, text):
        if not self.can_send or not self.sock:
            return False
        try:
            with self.lock:
                self.sock.sendall(f"PRIVMSG #{self.channel} :{text[:450]}\r\n".encode("utf-8"))
            return True
        except OSError as exc:
            self.log(f"[twitch] Could not send to chat: {exc}")
            return False

    def _connect(self):
        raw = socket.create_connection((HOST, PORT), timeout=15)
        return ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)

    def _run(self):
        wait = 2
        while self.running:
            try:
                self.sock = self.connect()
                if self.token:
                    self.sock.sendall(f"PASS {self.token}\r\n".encode())
                self.sock.sendall(f"NICK {self.nick}\r\nJOIN #{self.channel}\r\n".encode())
                self.log(f"[twitch] Reading chat of #{self.channel}" + (" (can answer !coach)." if self.can_send else " (read only)."))
                self.sock.settimeout(300)
                wait = 2
                buf = ""
                while self.running:
                    data = self.sock.recv(4096)
                    if not data:
                        raise OSError("chat closed the connection")
                    buf += data.decode("utf-8", errors="replace")
                    *lines, buf = buf.split("\r\n")
                    for line in lines:
                        if line.startswith("PING"):
                            with self.lock:
                                self.sock.sendall(("PONG" + line[4:] + "\r\n").encode())
                        elif "Login authentication failed" in line or "Improperly formatted auth" in line:
                            self.log("[twitch] The chat token was refused. The coach reads chat only.")
                            self.token, self.can_send = "", False
                            self.nick = f"justinfan{random.randint(10000, 99999)}"
                            raise OSError("login failed")
                        else:
                            msg = parse_line(line)
                            if msg:
                                self.messages.put(msg)
            except OSError as exc:
                if not self.running:
                    break
                self.log(f"[twitch] Chat connection lost ({exc}). Trying again in {wait} s.")
                time.sleep(wait)
                wait = min(wait * 2, 60)


def stats_line(won, lost, rounds, personality):
    """The !coach answer: the session numbers in one chat line."""
    s = lambda k: sum(getattr(r, k) for r in rounds)
    parts = [f"Score {won}-{lost}", f"Deaths {s('deaths')}"]
    if s("judged_bursts"):
        parts.append(f"Moving first shots {s('moving_bursts')}/{s('judged_bursts')}")
    if s("stops"):
        parts.append(f"Counter-strafes {s('counter_stops')}/{s('stops')}")
    if s("long_sprays"):
        parts.append(f"Long sprays {s('long_sprays')}")
    parts.append(f"Coach: {personality}")
    return " | ".join(parts)


def from_config(cfg):
    tc = cfg.get("twitch") or {}
    if not tc.get("enabled") or not str(tc.get("channel") or "").strip():
        return None
    from .ai import load_secrets

    secrets = load_secrets((cfg.get("ai") or {}).get("secrets_file", "secrets.yaml"))
    return TwitchChat(str(tc["channel"]), str(tc.get("bot_name") or ""), str(secrets.get("twitch_oauth_token") or ""))
