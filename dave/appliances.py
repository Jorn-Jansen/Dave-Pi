"""
The washer and dryer (SmartThings): Dave keeps an eye on them and says when one is almost done (5 minutes left) and when
it's done, and sends it to your phone too when there's a phone channel (phone_topic, the ntfy app). Checked every minute.
"""

import threading
import time

from . import config, smartthings, web

on_news = None  # (message): something to say (set by the main loop)
_last = {}      # device id → its last state
_almost = set()


def start():
    if not config.get("appliance_alerts"):
        return
    threading.Thread(target=_loop, daemon=True).start()


def _loop():
    while True:
        try:
            check()
        except smartthings.SmartThingsError as e:
            print(f"(appliances: {e})", flush=True)
        except Exception as e:
            print(f"(appliances: {type(e).__name__}: {e})", flush=True)
        time.sleep(60)


def _value(main, cap, attr):
    return (main.get(cap, {}).get(attr) or {}).get("value")


def check():
    """One look at the washers and dryers; says what changed."""
    if not (config.get("smartthings_token") or config.get("smartthings_refresh")):
        return
    for device in smartthings.devices():
        kind = next((k for k in ("washer", "dryer") if any(c.startswith(f"{k}OperatingState") for c in device["capabilities"])), None)
        if not kind:
            continue
        main = (smartthings._call("GET", f"/devices/{device['id']}/status") or {}).get("components", {}).get("main", {})
        state = _value(main, f"{kind}OperatingState", "machineState")
        job = _value(main, f"{kind}OperatingState", f"{kind}JobState")
        remaining = _value(main, f"samsungce.{kind}OperatingState", "remainingTime")
        before = _last.get(device["id"])
        _last[device["id"]] = state
        name = device["name"]
        if state == "run" and isinstance(remaining, (int, float)) and 0 < remaining <= 5 and device["id"] not in _almost:
            _almost.add(device["id"])
            _tell(config.say(f"The {name} is almost done: about {int(remaining)} minutes left.", f"De {name} is bijna klaar: nog zo'n {int(remaining)} minuten."))
        if before == "run" and state != "run":
            _almost.discard(device["id"])
            if state == "pause":
                continue
            _tell(config.say(f"The {name} is done.", f"De {name} is klaar.") if job in (None, "none", "finish", "finished", "cooling", "wrinklePrevent")
                  else config.say(f"The {name} stopped ({job}).", f"De {name} is gestopt ({job})."))


def _tell(message):
    print(f"🧺 {message}", flush=True)
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
