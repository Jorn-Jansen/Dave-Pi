"""
SmartThings (Samsung): your washer, dryer, TV, plugs… through the official SmartThings API.

Two ways to connect:
- A personal access token (account.smartthings.com/tokens): quickest, but Samsung lets those expire after 24 hours.
- OAuth (sudo dave smartthings login): set up once, then Dave keeps itself logged in (it renews its access by itself).
"""

import base64
import time
import urllib.parse

from . import config, web

API = "https://api.smartthings.com/v1"
SCOPES = "r:devices:* x:devices:* r:locations:*"


class SmartThingsError(Exception):
    pass


# --- Access ---

def _token():
    if config.get("smartthings_refresh") and config.get("smartthings_client_id"):
        if time.time() > float(config.get("smartthings_expires") or 0) - 120:
            _refresh()
        return config.get("smartthings_access")
    if config.get("smartthings_token"):
        return config.get("smartthings_token")
    raise SmartThingsError(config.say("SmartThings isn't set up yet: sudo dave smartthings token <token>",
                                      "SmartThings is nog niet ingesteld: sudo dave smartthings token <token>"))


def _basic():
    pair = f"{config.get('smartthings_client_id')}:{config.get('smartthings_client_secret')}"
    return "Basic " + base64.b64encode(pair.encode()).decode()


def _token_request(form):
    body = urllib.parse.urlencode(form).encode()
    status, data = web.json_request("POST", "https://api.smartthings.com/oauth/token", body,
                                    {"Authorization": _basic(), "Content-Type": "application/x-www-form-urlencoded"})
    if status != 200 or not data or "access_token" not in data:
        raise SmartThingsError(f"SmartThings login failed ({status}): {data}")
    config.load()["smartthings_access"] = data["access_token"]
    config.load()["smartthings_refresh"] = data.get("refresh_token", config.get("smartthings_refresh"))
    config.load()["smartthings_expires"] = time.time() + int(data.get("expires_in", 86400))
    config.save()


def _refresh():
    _token_request({"grant_type": "refresh_token", "client_id": config.get("smartthings_client_id"),
                    "refresh_token": config.get("smartthings_refresh")})


def login_url():
    return ("https://api.smartthings.com/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": config.get("smartthings_client_id"), "response_type": "code",
        "redirect_uri": config.get("smartthings_redirect"), "scope": SCOPES}))


def finish_login(code):
    """The code from the address after logging in (…?code=XXXX): Dave is then logged in for good."""
    _token_request({"grant_type": "authorization_code", "client_id": config.get("smartthings_client_id"),
                    "code": code.strip(), "redirect_uri": config.get("smartthings_redirect")})


def _call(method, path, body=None):
    try:
        status, data = web.json_request(method, API + path, body, {"Authorization": f"Bearer {_token()}"}, timeout=15)
    except OSError as e:
        raise SmartThingsError(config.say("I can't reach SmartThings.", "Ik kan SmartThings niet bereiken.")) from e
    if status == 401:
        raise SmartThingsError(config.say("My SmartThings access has expired. Set it up again: sudo dave smartthings token <new token>",
                                          "Mijn SmartThings-toegang is verlopen. Stel hem opnieuw in: sudo dave smartthings token <nieuwe token>"))
    if status >= 300:
        raise SmartThingsError(f"SmartThings {status}: {data}")
    return data


# --- Devices ---

def devices():
    """[{id, name, capabilities}]"""
    items = (_call("GET", "/devices") or {}).get("items", [])
    return [{"id": d["deviceId"], "name": d.get("label") or d.get("name") or "?",
             "capabilities": sorted({c["id"] for comp in d.get("components", []) for c in comp.get("capabilities", [])})}
            for d in items]


def _simple(text):
    return "".join(c for c in text.lower() if c.isalnum())


def find(name):
    want = _simple(name or "")
    all_devices = devices()
    for d in all_devices:
        if _simple(d["name"]) == want:
            return d
    # also by kind: "wasmachine" / "washer", "droger" / "dryer", "tv"
    kinds = {"was": "washer", "wash": "washer", "droog": "dryer", "dry": "dryer", "tv": "tv", "televisie": "tv"}
    for d in all_devices:
        if want and (want in _simple(d["name"]) or _simple(d["name"]) in want):
            return d
    for word, kind in kinds.items():
        if word in want:
            for d in all_devices:
                if any(kind in c.lower() for c in d["capabilities"]) or kind in _simple(d["name"]):
                    return d
    raise SmartThingsError(config.say(f"I don't have a SmartThings device called {name}.", f"Ik heb geen SmartThings-apparaat dat {name} heet."))


def status(device):
    """What a device is doing: on/off, the washer or dryer's state and how long it still takes, and other values."""
    data = _call("GET", f"/devices/{device['id']}/status") or {}
    main = data.get("components", {}).get("main", {})
    facts = []
    def value(cap, attr):
        return (main.get(cap, {}).get(attr) or {}).get("value")
    if value("switch", "switch") is not None:
        facts.append(f"power {value('switch', 'switch')}")
    for kind in ("washer", "dryer"):
        state = value(f"{kind}OperatingState", "machineState")
        job = value(f"{kind}OperatingState", f"{kind}JobState")
        done_at = value(f"{kind}OperatingState", "completionTime")
        if state:
            facts.append(f"{kind} {state}" + (f" ({job})" if job and job != "none" else ""))
        remaining = value(f"samsungce.{kind}OperatingState", "remainingTime") or value(f"samsungce.{kind}OperatingState", "remainingTimeStr")
        if remaining and state == "run":
            facts.append(f"remaining: {remaining}")
        elif done_at and state == "run":
            try:
                left = time.mktime(time.strptime(done_at[:19], "%Y-%m-%dT%H:%M:%S")) - time.mktime(time.gmtime())
                if left > 0:
                    facts.append(f"done in about {round(left / 60)} minutes")
            except ValueError:
                pass
    if value("remoteControlStatus", "remoteControlEnabled") is not None:
        facts.append(f"remote control {'on' if value('remoteControlStatus', 'remoteControlEnabled') == 'true' else 'off'}")
    for cap, attr in (("temperatureMeasurement", "temperature"), ("powerMeter", "power"), ("audioVolume", "volume"),
                      ("tvChannel", "tvChannel"), ("mediaInputSource", "inputSource"), ("contactSensor", "contact"),
                      ("motionSensor", "motion"), ("battery", "battery")):
        v = value(cap, attr)
        if v is not None:
            facts.append(f"{attr} {v}")
    return f"{device['name']}: " + (", ".join(facts) or "no readable state")


def command(device, capability, cmd, arguments=None):
    """Send a command, e.g. ('switch', 'on'), ('washerOperatingState', 'setMachineState', ['pause'])."""
    _call("POST", f"/devices/{device['id']}/commands",
          {"commands": [{"component": "main", "capability": capability, "command": cmd, "arguments": arguments or []}]})
    return f"{device['name']}: {capability}.{cmd}({', '.join(map(str, arguments or []))})"


def describe():
    """All devices with what they can do (for the AI and `dave smartthings list`)."""
    interesting = ("switch", "washer", "dryer", "audioVolume", "tvChannel", "mediaInputSource", "switchLevel", "thermostat", "airConditioner", "lock")
    lines = []
    for d in devices():
        caps = [c for c in d["capabilities"] if any(i.lower() in c.lower() for i in interesting)]
        lines.append(f"\"{d['name']}\"" + (f" ({', '.join(caps)})" if caps else ""))
    return "\n".join(lines) or "No SmartThings devices found."
