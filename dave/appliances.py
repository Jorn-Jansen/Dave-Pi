"""
The washer and dryer (SmartThings): Dave keeps an eye on them and says when one is almost done (5 minutes left) and when
it's done, and sends it to your phone too when there's a phone channel (phone_topic, the ntfy app). Checked once a minute
while nothing runs, every 10 seconds while one runs, and every 5 seconds in its last 2 minutes, so you hear it right away (each
check is one small web request: next to nothing for the Pi).
"""

import threading
import time

from . import config, log, smartthings, web

on_news = None  # (message): something to say (set by the main loop)
_last = {}      # device id → its last (state, job)
_almost = set()
_done = set()   # said "done" already (until it runs a new program)
_devices = (0.0, [])  # (when fetched, the washers and dryers): the list itself only every 10 minutes


def start():
    if not config.get("appliance_alerts"):
        return
    threading.Thread(target=_loop, daemon=True).start()


def _loop():
    while True:
        wait = 60
        try:
            wait = check()
        except smartthings.SmartThingsError as e:
            print(f"(appliances: {e})", flush=True)
        except Exception as e:
            print(f"(appliances: {type(e).__name__}: {e})", flush=True)
        time.sleep(wait)


def _machines():
    """The washers and dryers (with their kind), from SmartThings' device list, fetched again every 10 minutes."""
    global _devices
    if time.time() - _devices[0] > 600:
        found = []
        for device in smartthings.devices():
            kind = next((k for k in ("washer", "dryer") if any(c.startswith(f"{k}OperatingState") for c in device["capabilities"])), None)
            if kind:
                found.append((device, kind))
        _devices = (time.time(), found)
    return _devices[1]


def _value(main, cap, attr):
    return (main.get(cap, {}).get(attr) or {}).get("value")


def check():
    """One look at the washers and dryers; says what changed. Returns how many seconds until the next look."""
    if not (config.get("smartthings_token") or config.get("smartthings_refresh")):
        return 300
    running = []  # how long each running machine can wait until its next look
    for device, kind in _machines():
        main = (smartthings._call("GET", f"/devices/{device['id']}/status") or {}).get("components", {}).get("main", {})
        state = _value(main, f"{kind}OperatingState", "machineState")
        job = _value(main, f"{kind}OperatingState", f"{kind}JobState")
        remaining = _value(main, f"samsungce.{kind}OperatingState", "remainingTime")
        before, before_job = _last.get(device["id"], (None, None))
        _last[device["id"]] = (state, job)
        name = device["name"]
        finished = job in ("finish", "finished")
        if state == "run" and not finished:
            _done.discard(device["id"])  # (a new program: it may say "done" again at the end)
            # It says how long it still takes: wait until just before the end (at most 5 minutes at a time, in case it's paused
            # or the program changes), then look every 5 seconds
            if isinstance(remaining, (int, float)) and remaining > 2:
                running.append(max(10, min(300, (remaining - 2) * 60)))
            else:
                running.append(5 if isinstance(remaining, (int, float)) else 10)
        if device["id"] in _done:
            continue
        # Some report "finish" as the job while the machine still says "run" for a moment: that's done too
        if before == "run" and state == "run" and finished:
            _almost.discard(device["id"])
            _done.add(device["id"])
            _tell(config.say(f"The {name} is done.", f"De {name} is klaar."))
            continue
        if state == "run" and isinstance(remaining, (int, float)) and 0 < remaining <= 5 and device["id"] not in _almost:
            _almost.add(device["id"])
            _tell(config.say(f"The {name} is almost done: about {int(remaining)} minutes left.", f"De {name} is bijna klaar: nog zo'n {int(remaining)} minuten."))
        if before == "run" and state != "run":
            _almost.discard(device["id"])
            if state == "pause":
                continue
            _done.add(device["id"])
            _tell(config.say(f"The {name} is done.", f"De {name} is klaar.") if job in (None, "none", "finish", "finished", "cooling", "wrinklePrevent")
                  else config.say(f"The {name} stopped ({job}).", f"De {name} is gestopt ({job})."))
    return min(running) if running else 60  # (nothing running: once a minute)


def _tell(message):
    print(f"🧺 {message}", flush=True)
    log.write(f"zegt: {message}", "wasmachine/droger")
    if on_news:
        on_news(message)
    phone(config.get("name"), message)


def phone(title, message):
    """To your phone through ntfy (the free ntfy app, subscribed to phone_topic), when that's set."""
    topic = config.get("phone_topic")
    if not topic:
        return
    try:
        web.request("POST", f"https://ntfy.sh/{topic}", message.encode("utf-8"), {"Title": title.encode("utf-8").decode("latin-1", "ignore")}, timeout=10)
    except OSError as e:
        print(f"(phone notification failed: {e})", flush=True)
