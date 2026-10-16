"""
Dave's logbook: everything he does and why, with who asked for it (the voice, a terminal command, a timer, the washer…).
In /var/log/dave.log, so it's still there after a restart; at 2 MB it moves to dave.log.1 and starts again (so the SD card
never fills up). Read it with: sudo dave log
"""

import os
import threading
import time

PATH = os.environ.get("DAVE_LOG") or (os.path.join(os.path.dirname(os.environ["DAVE_CONFIG"]), "dave.log")
                                      if os.environ.get("DAVE_CONFIG") else "/var/log/dave.log")
MAX = 2_000_000

default = "dave"  # who's asking, when nothing else is said (set by the command that runs)
_here = threading.local()
_lock = threading.Lock()


def by(source):
    """Who's asking for what this thread does from now on (e.g. "stem", "terminal")."""
    _here.source = source


def write(what, source=None):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{source or getattr(_here, 'source', None) or default}] {' '.join(str(what).split())}"
    with _lock:
        try:
            if os.path.exists(PATH) and os.path.getsize(PATH) > MAX:
                os.replace(PATH, PATH + ".1")
            with open(PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # (no logbook is no reason to stop working)


def lines():
    out = []
    for path in (PATH + ".1", PATH):
        try:
            with open(path, encoding="utf-8") as f:
                out += f.read().splitlines()
        except OSError:
            pass
    return out
