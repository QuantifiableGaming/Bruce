"""HTTPS connections that stay open between requests.

A new HTTPS connection needs about two round trips before the first byte of a request goes out.
From Perth to the US servers of Groq and ElevenLabs that is about 0.3 to 0.4 seconds for each
line. A connection that stays open skips that, so each coach line starts sooner (21 Nov).

One connection for each server and each thread. A connection that the server closed is opened
again once, at once.
"""
import http.client
import ssl
import threading
import urllib.parse

_local = threading.local()


class NetError(Exception):
    """The request could not reach the server (no HTTP answer)."""


def _conn(scheme, host, timeout, fresh=False):
    pool = getattr(_local, "pool", None)
    if pool is None:
        pool = _local.pool = {}
    key = (scheme, host)
    c = pool.get(key)
    if c is None or fresh:
        if c is not None:
            try:
                c.close()
            except OSError:
                pass
        if scheme == "http":          # A local AI (Ollama) or a test server.
            c = http.client.HTTPConnection(host, timeout=timeout)
        else:
            c = http.client.HTTPSConnection(host, timeout=timeout, context=ssl.create_default_context())
        pool[key] = c
    c.timeout = timeout
    if c.sock is not None:
        c.sock.settimeout(timeout)
    return c


def request(url, body=None, headers=None, timeout=8.0, method=None):
    """Send a request and return the open response (read it, or read it in parts).
    The caller must read the whole body before the next request on this thread."""
    parts = urllib.parse.urlsplit(url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    method = method or ("POST" if body is not None else "GET")
    headers = dict(headers or {})
    headers.setdefault("Connection", "keep-alive")
    last = None
    key = (parts.scheme, parts.netloc)
    for attempt in (0, 1):
        c = _conn(parts.scheme, parts.netloc, timeout, fresh=attempt == 1)
        try:
            c.request(method, path, body=body, headers=headers)
            return c.getresponse()
        except (http.client.RemoteDisconnected, http.client.ImproperConnectionState, http.client.BadStatusLine,
                ConnectionResetError, BrokenPipeError) as exc:
            # ImproperConnectionState: an earlier answer on this connection was not read to the end
            # (a timeout). A new connection is clean.
            last = exc        # The server closed the kept connection. Open a new one, once.
        except (OSError, http.client.HTTPException) as exc:
            _local.pool.pop(key, None)
            raise NetError(str(exc)) from None
    _local.pool.pop(key, None)
    raise NetError(str(last))
