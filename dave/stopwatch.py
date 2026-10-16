"""The stopwatch, like on the PC: start, lap, how long, pause, carry on, stop (with the laps), reset. Survives a restart."""

import time

from . import config


def _state():
    return dict(config.get("stopwatch") or {"on": False, "since": None, "banked": 0.0, "laps": []})


def elapsed(s=None):
    s = s or _state()
    return float(s.get("banked") or 0) + (time.time() - s["since"] if s.get("since") else 0)


def short(t):
    """'0:41.3', '12:03.0', '1:05:12'."""
    t = max(0.0, t)
    whole = int(t)
    if whole >= 3600:
        return f"{whole // 3600}:{whole // 60 % 60:02}:{whole % 60:02}"
    return f"{whole // 60}:{whole % 60:02}.{int((t - whole) * 10)}"


def spoken(t):
    whole = int(t)
    parts = []
    for n, one, many, een, veel in ((whole // 3600, "hour", "hours", "uur", "uur"), (whole // 60 % 60, "minute", "minutes", "minuut", "minuten"),
                                    (whole % 60, "second", "seconds", "seconde", "seconden")):
        if n:
            parts.append(f"{n} " + config.say(one if n == 1 else many, een if n == 1 else veel))
    if not parts:
        return config.say("less than a second", "minder dan een seconde")
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + config.say(" and ", " en ") + parts[-1]


def run(action):
    """Does what was asked; returns what to say."""
    s = _state()
    running = s.get("on") and s.get("since")
    if action == "start" and running:
        return config.say(f"It's already running: {spoken(elapsed(s))}.", f"Hij loopt al: {spoken(elapsed(s))}.")
    if action in ("start", "reset"):
        keep_going = action == "start" or running or not s.get("on")
        config.set("stopwatch", {"on": True, "since": time.time() if keep_going else None, "banked": 0.0, "laps": []})
        return config.say("Stopwatch started.", "Stopwatch gestart.") if action == "start" else config.say("Back to zero.", "Terug naar nul.")
    if not s.get("on"):
        return config.say("There's no stopwatch running. Say: start a stopwatch.", "Er loopt geen stopwatch. Zeg: start een stopwatch.")
    if action == "lap":
        total = elapsed(s)
        lap = total - (s["laps"][-1] if s["laps"] else 0)
        s["laps"].append(total)
        config.set("stopwatch", s)
        n = len(s["laps"])
        return config.say(f"Lap {n}: {short(lap)}", f"Ronde {n}: {short(lap)}") + (config.say(f" (total {short(total)})", f" (totaal {short(total)})") if n > 1 else "")
    if action == "pause":
        if not running:
            return config.say(f"It's already paused at {short(elapsed(s))}.", f"Hij staat al op pauze op {short(elapsed(s))}.")
        s.update(banked=elapsed(s), since=None)
        config.set("stopwatch", s)
        return config.say(f"Paused at {short(elapsed(s))}.", f"Gepauzeerd op {short(elapsed(s))}.")
    if action == "resume":
        if running:
            return config.say("It's still running.", "Hij loopt nog.")
        s["since"] = time.time()
        config.set("stopwatch", s)
        return config.say(f"Going again from {short(elapsed(s))}.", f"Hij loopt weer, vanaf {short(elapsed(s))}.")
    if action == "stop":
        end = elapsed(s)
        laps = [short(t - (s["laps"][i - 1] if i else 0)) for i, t in enumerate(s["laps"])][:10]
        config.set("stopwatch", {"on": False, "since": None, "banked": end, "laps": []})
        return config.say(f"Stopped at {short(end)}", f"Gestopt op {short(end)}") + \
               ((config.say(", laps: ", ", rondes: ") + ", ".join(laps)) if laps else "") + "."
    # check
    return spoken(elapsed(s)) + ("" if running else config.say(" (paused)", " (gepauzeerd)")) + \
           (config.say(f", {len(s['laps'])} lap" + ("s" if len(s["laps"]) > 1 else ""), f", {len(s['laps'])} ronde" + ("s" if len(s["laps"]) > 1 else "")) if s["laps"] else "") + "."
