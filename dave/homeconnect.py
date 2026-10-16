"""
Home Connect (Bosch, Siemens, Neff…): the dishwasher and other appliances in the Home Connect app. How far it is, how long it
still takes, and Dave says when it's almost done and when it's done (to your phone too, like the washer). Starting a program
works when remote start is switched on at the appliance itself.

Through Home Connect's own API (the cloud), logged in once without a screen: `sudo dave homeconnect setup` shows a code that
you confirm on your phone. Home Connect allows 1000 requests a day, so Dave doesn't keep asking: he listens to their event
stream (one request a connection, the messages are free) and only asks when you do.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, log

BASE = os.environ.get("DAVE_HOMECONNECT_URL", "https://api.home-connect.com")
SCOPE = "IdentifyAppliance Monitor Control Settings"
ACCEPT = "application/vnd.bsh.sdk.v1+json"
KINDS = {"Dishwasher": ("dishwasher", "vaatwasser"), "Washer": ("washer", "wasmachine"), "Dryer": ("dryer", "droger"),
         "WasherDryer": ("washer-dryer", "was-droogcombinatie"), "Oven": ("oven", "oven"), "FridgeFreezer": ("fridge", "koelkast"),
         "CoffeeMaker": ("coffee machine", "koffiemachine"), "Hood": ("hood", "afzuigkap"), "Hob": ("hob", "kookplaat")}
STATES = {"Inactive": ("off", "uit"), "Ready": ("ready", "klaar om te starten"), "DelayedStart": ("waiting to start", "wacht om te starten"),
          "Run": ("running", "bezig"), "Pause": ("paused", "gepauzeerd"), "ActionRequired": ("needs you", "heeft je nodig"),
          "Finished": ("done", "klaar"), "Error": ("has an error", "heeft een storing"), "Aborting": ("stopping", "stopt")}

on_news = None  # (message): something to say (set by the main loop)
_thread = None
_almost, _done = set(), set()


class HomeConnectError(Exception):
    pass


# --- Logging in (once): the device flow ---

def _form(path, fields):
    req = urllib.request.Request(BASE + path, data=urllib.parse.urlencode(fields).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except OSError as e:
        raise HomeConnectError(config.say("I can't reach Home Connect.", "Ik kan Home Connect niet bereiken.")) from e


def login(on_code):
    """Home Connect's device flow: [on_code](link, code) shows where to confirm; waits until you did. Keeps the login."""
    client = config.get("homeconnect_client_id")
    if not client:
        raise HomeConnectError(config.say("First the Client ID: sudo dave homeconnect setup", "Eerst de Client ID: sudo dave homeconnect setup"))
    status, start = _form("/security/oauth/device_authorization", {"client_id": client, "scope": SCOPE})
    if status != 200 or "device_code" not in start:
        raise HomeConnectError(config.say(f"Home Connect didn't start the login: {start.get('error_description') or start.get('error') or status}",
                                          f"Home Connect startte het inloggen niet: {start.get('error_description') or start.get('error') or status}"))
    on_code(start.get("verification_uri_complete") or start.get("verification_uri"), start.get("user_code"))
    interval, until = float(start.get("interval") or 5), time.time() + float(start.get("expires_in") or 300)
    while time.time() < until:
        time.sleep(interval)
        fields = {"grant_type": "device_code", "device_code": start["device_code"], "client_id": client}
        if config.get("homeconnect_client_secret"):
            fields["client_secret"] = config.get("homeconnect_client_secret")
        status, token = _form("/security/oauth/token", fields)
        if status == 200 and token.get("access_token"):
            _keep(token)
            return
        error = token.get("error", "")
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            raise HomeConnectError(f"Home Connect: {token.get('error_description') or error or status}")
    raise HomeConnectError(config.say("Nothing was confirmed in time. Try again.", "Er is niets op tijd bevestigd. Probeer het opnieuw."))


def _keep(token):
    config.set("homeconnect_access", token["access_token"])
    if token.get("refresh_token"):
        config.set("homeconnect_refresh", token["refresh_token"])
    config.set("homeconnect_expires", time.time() + float(token.get("expires_in") or 86400) - 120)


def _token():
    if not config.get("homeconnect_refresh"):
        raise HomeConnectError(config.say("Home Connect isn't set up yet: sudo dave homeconnect setup",
                                          "Home Connect is nog niet ingesteld: sudo dave homeconnect setup"))
    if config.get("homeconnect_access") and time.time() < float(config.get("homeconnect_expires") or 0):
        return config.get("homeconnect_access")
    fields = {"grant_type": "refresh_token", "refresh_token": config.get("homeconnect_refresh")}
    if config.get("homeconnect_client_secret"):
        fields["client_secret"] = config.get("homeconnect_client_secret")
    status, token = _form("/security/oauth/token", fields)
    if status != 200 or not token.get("access_token"):
        raise HomeConnectError(config.say("The Home Connect login stopped working: sudo dave homeconnect setup",
                                          "Het inloggen bij Home Connect werkt niet meer: sudo dave homeconnect setup"))
    _keep(token)
    return token["access_token"]


def ready():
    return bool(config.get("homeconnect_refresh"))


# --- Asking ---

def _call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {_token()}", "Accept": ACCEPT, "Content-Type": ACCEPT, "Accept-Language": "nl-NL"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            error = (json.loads(e.read() or b"{}").get("error") or {})
        except ValueError:
            error = {}
        if e.code == 404 and path.endswith("/programs/active"):
            return None  # (no program running)
        if e.code == 429:
            raise HomeConnectError(config.say("Home Connect's limit for today is reached (1000 requests). Tomorrow it works again.",
                                              "De limiet van Home Connect voor vandaag is bereikt (1000 vragen). Morgen werkt het weer.")) from e
        if e.code == 409 or error.get("key") in ("SDK.Error.HomeAppliance.Connection.Initialization.Failed", "SDK.Error.ApplianceNotConnected"):
            raise HomeConnectError(config.say("The appliance isn't connected to Home Connect right now (is it off or offline?).",
                                              "Het apparaat is nu niet verbonden met Home Connect (staat hij uit of offline?).")) from e
        raise HomeConnectError(f"Home Connect {e.code}: {error.get('description') or error.get('key') or ''}") from e
    except OSError as e:
        raise HomeConnectError(config.say("I can't reach Home Connect.", "Ik kan Home Connect niet bereiken.")) from e


def appliances():
    return ((_call("GET", "/api/homeappliances") or {}).get("data") or {}).get("homeappliances") or []


def _kind(a):
    return config.say(*KINDS.get(a.get("type"), (a.get("type", "appliance"), a.get("type", "apparaat"))))


def _name(a):
    """What to call it: its name in the app, or what it is ('vaatwasser')."""
    name = (a.get("name") or "").strip()
    if not name or name.lower() in (a.get("type", "").lower(), _kind(a).lower(), "dishwasher", "vaatwasser"):
        return _kind(a)  # ("de vaatwasser", not "de Vaatwasser")
    return name


def find(which=""):
    """The appliance meant: by name or kind ('vaatwasser'); nothing said: the dishwasher, or the only one."""
    found = appliances()
    if not found:
        raise HomeConnectError(config.say("There are no appliances in your Home Connect account.", "Er staan geen apparaten in je Home Connect-account."))
    want = (which or "").lower().strip()
    if want:
        for a in found:
            if want in _name(a).lower() or want in _kind(a).lower() or want in a.get("type", "").lower() or _kind(a).lower() in want:
                return a
        raise HomeConnectError(config.say(f"I don't have a {which} in Home Connect. There are: {', '.join(_name(a) for a in found)}.",
                                          f"Ik heb geen {which} in Home Connect. Er zijn: {', '.join(_name(a) for a in found)}."))
    return next((a for a in found if a.get("type") == "Dishwasher"), found[0])


def _values(items):
    return {i.get("key", "").split(".")[-1]: i.get("value") for i in items or []}


def _state(value):
    return config.say(*STATES.get(str(value or "").split(".")[-1], (str(value or "?").split(".")[-1],) * 2))


def _minutes(seconds):
    m = max(1, round(float(seconds) / 60))
    h, m = divmod(m, 60)
    return (f"{h} " + config.say("h", "uur") + (f" {m} min" if m else "")) if h else f"{m} " + config.say("minutes", "minuten")


def status(which=""):
    a = find(which)
    name = _name(a)
    if not a.get("connected", True):
        return config.say(f"The {name} is offline (off, or not on Wi-Fi).", f"De {name} is offline (uit, of niet op wifi).")
    s = _values(((_call("GET", f"/api/homeappliances/{a['haId']}/status") or {}).get("data") or {}).get("status"))
    parts = [config.say(f"The {name} is {_state(s.get('OperationState'))}", f"De {name} is {_state(s.get('OperationState'))}")]
    if str(s.get("OperationState", "")).endswith(("Run", "Pause", "DelayedStart")):
        active = _call("GET", f"/api/homeappliances/{a['haId']}/programs/active")
        if active:
            data = active.get("data") or {}
            o = _values(data.get("options"))
            program = data.get("name") or str(data.get("key", "")).split(".")[-1]
            if program:
                parts.append(config.say(f"program {program}", f"programma {program}"))
            if o.get("RemainingProgramTime") is not None:
                parts.append(config.say(f"about {_minutes(o['RemainingProgramTime'])} to go", f"nog zo'n {_minutes(o['RemainingProgramTime'])}"))
            if o.get("ProgramProgress") is not None:
                parts.append(f"{o['ProgramProgress']}%")
    if str(s.get("DoorState", "")).endswith("Open"):
        parts.append(config.say("the door is open", "de deur staat open"))
    return ", ".join(parts) + "."


def start(which="", program=""):
    """Starts a program (the one picked on the appliance, or one by name: eco, auto…), when remote start is on at the appliance."""
    a = find(which)
    name = _name(a)
    s = _values(((_call("GET", f"/api/homeappliances/{a['haId']}/status") or {}).get("data") or {}).get("status"))
    if s.get("RemoteControlStartAllowed") is False:
        raise HomeConnectError(config.say(f"Remote start is off on the {name}: switch it on at the appliance (often a button with a Wi-Fi or remote sign) and try again.",
                                          f"Op afstand starten staat uit op de {name}: zet het aan op het apparaat zelf (vaak een knop met een wifi- of afstandsteken) en probeer het opnieuw."))
    key = None
    if program:
        available = ((_call("GET", f"/api/homeappliances/{a['haId']}/programs/available") or {}).get("data") or {}).get("programs") or []
        want = program.lower().replace(" ", "")
        key = next((p["key"] for p in available if want in (p.get("name") or "").lower().replace(" ", "") or want in p["key"].lower()), None)
        if not key:
            raise HomeConnectError(config.say(f"The {name} has no program {program}. It has: ", f"De {name} heeft geen programma {program}. Wel: ")
                                   + ", ".join(p.get("name") or p["key"].split(".")[-1] for p in available))
    else:
        selected = (_call("GET", f"/api/homeappliances/{a['haId']}/programs/selected") or {}).get("data") or {}
        key = selected.get("key")
        if not key:
            raise HomeConnectError(config.say(f"Pick a program on the {name} first, or say which.", f"Kies eerst een programma op de {name}, of zeg welk."))
    _call("PUT", f"/api/homeappliances/{a['haId']}/programs/active", {"data": {"key": key}})
    log.write(f"Home Connect: {name} gestart ({key.split('.')[-1]})")
    return config.say(f"The {name} is started.", f"De {name} is gestart.")


# --- Listening (the service): almost done / done ---

def watch():
    global _thread
    if not ready() or (_thread and _thread.is_alive()):
        return
    _thread = threading.Thread(target=_listen, daemon=True)
    _thread.start()


def _listen():
    names = {}
    wait = 30
    while True:
        try:
            names = {a["haId"]: _name(a) for a in appliances()}
            req = urllib.request.Request(BASE + "/api/homeappliances/events", headers={"Authorization": f"Bearer {_token()}", "Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=120) as stream:  # (Home Connect sends a KEEP-ALIVE every 55 s)
                wait = 30
                event, ha = "", ""
                for raw in stream:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("id:"):
                        ha = line[3:].strip()
                    elif line.startswith("data:") and event in ("STATUS", "NOTIFY", "EVENT"):
                        try:
                            items = json.loads(line[5:].strip() or "{}").get("items") or []
                        except ValueError:
                            items = []
                        for item in items:
                            _handle(event, item.get("haId") or ha, item, names)
                    elif not line:
                        event = ""
                    if time.time() > float(config.get("homeconnect_expires") or 0):
                        break  # (the login runs out: a fresh one, and listen again)
        except HomeConnectError as e:
            print(f"(Home Connect: {e})", flush=True)
            wait = 600 if "limiet" in str(e) or "limit" in str(e) else max(wait, 120)
        except urllib.error.HTTPError as e:
            retry = e.headers.get("Retry-After") if e.headers else None
            wait = int(retry) if retry and retry.isdigit() else (900 if e.code == 429 else 120)
            print(f"(Home Connect events: {e.code}, again in {wait} s)", flush=True)
        except Exception as e:  # (the connection dropped, no internet…)
            if "timed out" not in str(e):
                print(f"(Home Connect events: {type(e).__name__}: {e})", flush=True)
        time.sleep(wait)
        wait = min(wait * 2, 900)  # (each reconnect counts toward the 1000 a day: not too often)


def _handle(event, ha, item, names):
    key, value = item.get("key", ""), item.get("value")
    name = names.get(ha) or config.say("appliance", "apparaat")
    if key.endswith("RemainingProgramTime") and isinstance(value, (int, float)):
        if 0 < value <= 300 and ha not in _almost:
            _almost.add(ha)
            _tell(config.say(f"The {name} is almost done: about {_minutes(value)} left.", f"De {name} is bijna klaar: nog zo'n {_minutes(value)}."))
        elif value > 600:
            _almost.discard(ha)
            _done.discard(ha)  # (a new program)
    elif key.endswith("ProgramFinished") or (key.endswith("OperationState") and str(value).endswith("Finished")):
        if ha not in _done:
            _done.add(ha)
            _almost.discard(ha)
            _tell(config.say(f"The {name} is done.", f"De {name} is klaar."))
    elif key.endswith("OperationState") and str(value).endswith("Run"):
        _done.discard(ha)
    elif key.endswith("ProgramAborted") and ha not in _done:
        _done.add(ha)
        _tell(config.say(f"The {name} stopped.", f"De {name} is gestopt."))


def _tell(message):
    from . import appliances
    print(f"🍽 {message}", flush=True)
    log.write(f"zegt: {message}", "home connect")
    if on_news:
        on_news(message)
    appliances.phone(config.get("name"), message)
