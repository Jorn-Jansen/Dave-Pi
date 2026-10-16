"""
Timers, reminders and alarms, like Dave on the PC: kept in the settings so they survive a restart, checked every second.

- A timer: "a timer for 10 minutes for the pizza".
- A reminder: "remind me tomorrow at 9 to put the bins out", once or repeating (daily, weekdays, weekly).
- An alarm: "wake me up at 7", once or repeating. It keeps going off (with the lights slowly getting brighter before it,
  when that's on) until you say "stop" or "snooze".
"""

import threading
import time
import uuid
from datetime import datetime, timedelta

from . import config

on_due = None    # (kind, text, item): something is due (set by the main loop)
on_ring = None   # (item, round): the alarm is still ringing (every 30 s, until stop or snooze)
on_sunrise = None  # (item, level 0..1): the lights before an alarm

_lock = threading.RLock()
_thread = None
_ringing = None          # the alarm going off right now
_ring_stop = threading.Event()


# --- The list ---

def _items():
    return [dict(i) for i in (config.get("schedule") or [])]


def _save(items):
    config.set("schedule", items)


def start():
    """Starts checking (once)."""
    global _thread
    with _lock:
        if _thread is None:
            _thread = threading.Thread(target=_loop, daemon=True)
            _thread.start()


def _loop():
    while True:
        time.sleep(1)
        try:
            _check()
        except Exception as e:
            print(f"(schedule: {e})", flush=True)


def _check():
    now = time.time()
    due, changed = [], False
    with _lock:
        items = _items()
        for item in items:
            # Before an alarm: the lights slowly brighter over 10 minutes (when that's on)
            if item["kind"] == "alarm" and config.get("alarm_lights") and on_sunrise and 0 < item["at"] - now <= 600:
                step = int((600 - (item["at"] - now)) // 60)  # 0..9
                if item.get("sunrise", -1) < step:
                    item["sunrise"] = step
                    changed = True
                    threading.Thread(target=on_sunrise, args=(item, (step + 1) / 10), daemon=True).start()
            if item["at"] <= now:
                if now - item["at"] < 3600:  # (ones missed by more than an hour while the Pi was off: not announced anymore)
                    due.append(dict(item))
                following = _next(item, now)
                if following:
                    item["at"] = following
                    item.pop("sunrise", None)
                else:
                    item["done"] = True
                changed = True
        if changed:
            _save([i for i in items if not i.get("done")])
    for item in due:
        if item["kind"] == "alarm":
            _ring(item)
        elif on_due:
            on_due(item["kind"], item.get("text", ""), item)


def _next(item, now):
    """When a repeating one is due again (None: once)."""
    repeat = item.get("repeat", "none")
    if repeat == "none":
        return None
    at = datetime.fromtimestamp(item["at"])
    while at.timestamp() <= now:
        at += timedelta(days=7 if repeat == "weekly" else 1)
        while repeat == "weekdays" and at.weekday() >= 5 or repeat == "weekend" and at.weekday() < 5:
            at += timedelta(days=1)
    return at.timestamp()


def _add(kind, at, text="", repeat="none"):
    item = {"id": uuid.uuid4().hex[:6], "kind": kind, "at": float(at), "text": text.strip(), "repeat": repeat or "none", "made": time.time()}
    with _lock:
        _save(_items() + [item])
    start()
    return item


# --- Timers ---

def timer(seconds, label=""):
    item = _add("timer", time.time() + max(1, float(seconds)), label)
    return f"timer: {duration(seconds)}" + (f" ({label})" if label else "") + f", until {_clock(item['at'])}"


# --- Reminders ---

def parse_when(when="", minutes=None):
    """'2026-10-17 09:00', '09:00' (today, or tomorrow when it's past) or minutes from now → a moment."""
    if minutes not in (None, ""):
        return time.time() + float(minutes) * 60
    when = (when or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(when, fmt).timestamp()
        except ValueError:
            pass
    try:
        t = datetime.strptime(when, "%H:%M")
        at = datetime.now().replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        return (at if at.timestamp() > time.time() + 30 else at + timedelta(days=1)).timestamp()
    except ValueError:
        raise ValueError(f"Unclear time: {when!r} (use YYYY-MM-DD HH:MM, HH:MM or minutes)")


def reminder(text, when="", minutes=None, repeat="none"):
    item = _add("reminder", parse_when(when, minutes), text, repeat)
    return f"reminder '{text}' at {_moment(item['at'])}" + (f", {repeat}" if repeat != "none" else "")


# --- Alarms ---

def alarm(when, repeat="none", label=""):
    item = _add("alarm", parse_when(when), label, repeat)
    return f"alarm at {_moment(item['at'])}" + (f", {repeat}" if repeat != "none" else "") + (f" ({label})" if label else "")


def _ring(item):
    """The alarm goes off: on_ring every 30 seconds, until stop() or snooze(), for at most 10 minutes."""
    global _ringing
    _ring_stop.clear()
    _ringing = item

    def go():
        global _ringing
        for round in range(20):
            if _ring_stop.is_set():
                break
            if on_ring:
                on_ring(item, round)
            if _ring_stop.wait(30):
                break
        _ringing = None
    threading.Thread(target=go, daemon=True).start()


def ringing():
    return _ringing is not None


def stop_alarm():
    was = ringing()
    _ring_stop.set()
    return "alarm stopped" if was else "no alarm is going off"


def snooze(minutes=9):
    item = _ringing
    _ring_stop.set()
    if not item:
        return "no alarm is going off"
    _add("alarm", time.time() + float(minutes) * 60, item.get("text", ""))
    return f"snoozed for {int(float(minutes))} minutes, until {_clock(time.time() + float(minutes) * 60)}"


# --- Seeing and cancelling ---

def describe(kind=""):
    items = sorted((i for i in _items() if not kind or i["kind"] == kind), key=lambda i: i["at"])
    if not items:
        return f"No {kind or 'timers, reminders or alarms'}s." if kind else "No timers, reminders or alarms."
    lines = []
    for i in items:
        if i["kind"] == "timer":
            lines.append(f"timer {i['text'] or ''}: {duration(i['at'] - time.time())} left".replace("  ", " "))
        else:
            lines.append(f"{i['kind']} {_moment(i['at'])}" + (f" ({i['repeat']})" if i.get("repeat", "none") != "none" else "") + (f": {i['text']}" if i.get("text") else ""))
    return "; ".join(lines)


def cancel(kind="", words=""):
    """Cancels the ones of [kind] with [words] in them, or at the time in them ("7 uur", "07:30") (no words: all of that kind).
    Returns what happened, also when nothing matched (so the AI can say so)."""
    import re
    w = (words or "").lower().strip()
    m = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\b", w)
    at = f"{int(m.group(1)):02}:{m.group(2) or '00'}" if m and int(m.group(1)) < 24 else None
    def hit(i):
        if kind and i["kind"] != kind:
            return False
        if not w or w in i.get("text", "").lower():
            return True
        return bool(at) and time.strftime("%H:%M", time.localtime(i["at"])) == at
    with _lock:
        items = _items()
        gone = [i for i in items if hit(i)]
        _save([i for i in items if i not in gone])
    if not gone:
        return f"Nothing cancelled: no {kind or 'timer, reminder or alarm'} matches '{words}'. There are: {describe(kind)}"
    return f"Cancelled {len(gone)}: " + "; ".join(f"{i['kind']} {_moment(i['at'])}" + (f" ({i['text']})" if i.get("text") else "") for i in gone)


def soon(seconds=120):
    """The latest moment something is due within [seconds] (for `dave ask "timer van 10 seconden"` to wait for it)."""
    times = [i["at"] for i in _items() if 0 < i["at"] - time.time() < seconds]
    return max(times) if times else None


# --- Words ---

def duration(seconds):
    seconds = int(round(max(0, float(seconds))))
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    parts = []
    if h: parts.append(f"{h} " + config.say("hour" if h == 1 else "hours", "uur"))
    if m: parts.append(f"{m} " + config.say("minute" if m == 1 else "minutes", "minuut" if m == 1 else "minuten"))
    if s and not h: parts.append(f"{s} " + config.say("second" if s == 1 else "seconds", "seconde" if s == 1 else "seconden"))
    return " ".join(parts) or config.say("0 seconds", "0 seconden")


def _clock(at):
    return time.strftime("%H:%M:%S", time.localtime(at))


def _moment(at):
    day = datetime.fromtimestamp(at).date()
    today = datetime.now().date()
    when = "today" if day == today else "tomorrow" if day == today + timedelta(days=1) else datetime.fromtimestamp(at).strftime("%a %d %b")
    return f"{when} {time.strftime('%H:%M', time.localtime(at))}"
