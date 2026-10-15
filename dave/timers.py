"""Timers ("set a timer for 10 minutes for the pizza"): they go off with a sound and Dave saying what it was for."""

import threading
import time

from . import config

_timers = {}
_lock = threading.Lock()
_next = 1
on_done = None  # set by the main loop: called with the message to say


def add(seconds, label=""):
    global _next
    seconds = max(1, int(seconds))
    with _lock:
        tid = _next
        _next += 1
        timer = threading.Timer(seconds, _fire, [tid])
        timer.daemon = True
        _timers[tid] = {"label": label.strip(), "at": time.time() + seconds, "timer": timer, "seconds": seconds}
        timer.start()
    return f"timer {tid}: {_duration(seconds)}" + (f" ({label})" if label else "") + f", until {time.strftime('%H:%M:%S', time.localtime(time.time() + seconds))}"


def _fire(tid):
    with _lock:
        t = _timers.pop(tid, None)
    if t and on_done:
        what = t["label"] or _duration(t["seconds"])
        on_done(config.say(f"Your timer is done: {what}.", f"Je timer is klaar: {what}."))


def describe():
    with _lock:
        if not _timers:
            return "No timers."
        return "; ".join(f"{t['label'] or 'timer'}: {_duration(t['at'] - time.time())} left" for t in _timers.values())


def cancel(label=""):
    with _lock:
        hits = [tid for tid, t in _timers.items() if not label or label.lower() in t["label"].lower()]
        for tid in hits:
            _timers.pop(tid)["timer"].cancel()
    return len(hits)


def _duration(seconds):
    seconds = int(round(seconds))
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    parts = []
    if h: parts.append(f"{h} " + config.say("hour" if h == 1 else "hours", "uur"))
    if m: parts.append(f"{m} " + config.say("minute" if m == 1 else "minutes", "minuut" if m == 1 else "minuten"))
    if s and not h: parts.append(f"{s} " + config.say("second" if s == 1 else "seconds", "seconde" if s == 1 else "seconden"))
    return " ".join(parts) or config.say("0 seconds", "0 seconden")
