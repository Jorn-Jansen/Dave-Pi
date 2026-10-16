"""
The ATAG thermostat (ATAG One): how warm it is, set the temperature, the program (automatic / manual / …), whether the
boiler is burning, the hot water. Over your own Wi-Fi (its local API on port 10000, like Home Assistant's ATAG
integration), no internet needed. Paired once: sudo dave thermostaat setup, then you confirm it on the thermostat's screen.
"""

import json
import socket
import time
import uuid
import urllib.error
import urllib.request

from . import config

PORT = 10000
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AtagOneAPI/x; http://atag.one/)", "X-OneApp-Version": "dave-pi", "Content-Type": "application/json"}
MODES = {1: "manual", 2: "automatic", 3: "vacation", 4: "extend", 5: "fireplace"}
DUTCH = {"manual": "handmatig", "automatic": "automatisch (klokprogramma)", "vacation": "vakantie", "extend": "verlengd", "fireplace": "open haard"}


class ThermostatError(Exception):
    pass


def _mac():
    """The 'address' Dave pairs as (made once, then always the same)."""
    mac = config.get("thermostat_mac")
    if not mac:
        mac = "-".join(f"{b:02X}" for b in uuid.uuid4().bytes[:6])
        config.set("thermostat_mac", mac)
    return mac


def _post(host, path, body, timeout=10):
    req = urllib.request.Request(f"http://{host}:{PORT}/{path}", data=json.dumps(body).encode(), headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as e:
        raise ThermostatError(config.say(f"I can't reach the thermostat at {host}.", f"Ik kan de thermostaat op {host} niet bereiken.")) from e
    except ValueError as e:
        raise ThermostatError("The thermostat sent something I don't understand.") from e


def _status(reply):
    """2 = Dave may, 1 = waiting for you to confirm it on the thermostat, 3 = refused."""
    try:
        return next(iter(reply.values()))["acc_status"]
    except (StopIteration, KeyError, AttributeError, TypeError):
        return None


def _auth():
    return {"user_account": "", "mac_address": _mac()}


def discover(seconds=25):
    """Listens for the thermostat's 'I'm here' message on the network (it sends one every few seconds). Returns its address."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(seconds)
        s.bind(("", 11000))
        try:
            data, (host, _) = s.recvfrom(64)
            if data.startswith(b"ONE"):
                return host
        except socket.timeout:
            pass
    return None


def pair(host, wait=90, on_waiting=None):
    """Asks the thermostat for access; you confirm it on its screen. Returns True when allowed."""
    body = {"pair_message": {"seqnr": 0, "account_auth": _auth(), "accounts": {"entries": [
        {"user_account": "", "mac_address": _mac(), "device_name": f"Dave ({socket.gethostname()})", "account_type": 0}]}}}
    start, told = time.time(), False
    while time.time() - start < wait:
        status = _status(_post(host, "pair", body))
        if status == 2:
            config.set("thermostat_host", host)
            return True
        if status == 3:
            raise ThermostatError(config.say("The thermostat refused Dave.", "De thermostaat heeft Dave geweigerd."))
        if not told and on_waiting:
            on_waiting()
            told = True
        time.sleep(3)
    raise ThermostatError(config.say("Nothing was confirmed on the thermostat in time.", "Er is niets op tijd bevestigd op de thermostaat."))


def _host():
    host = config.get("thermostat_host")
    if not host:
        raise ThermostatError(config.say("The thermostat isn't set up yet: sudo dave thermostaat setup",
                                         "De thermostaat is nog niet ingesteld: sudo dave thermostaat setup"))
    return host


def read():
    """Everything it reports, as one dict (room_temp, ch_mode_temp, ch_mode, boiler_status, dhw_water_temp, outside_temp…)."""
    reply = _post(_host(), "retrieve", {"retrieve_message": {"seqnr": 0, "account_auth": _auth(), "info": 71}})
    if _status(reply) not in (2, None):
        raise ThermostatError(config.say("The thermostat doesn't let me in anymore: sudo dave thermostaat setup",
                                         "De thermostaat laat me er niet meer in: sudo dave thermostaat setup"))
    data = reply.get("retrieve_reply", {})
    values = {}
    for group in ("configuration", "status", "control", "report"):
        values.update(data.get(group) or {})
    values.update((data.get("report") or {}).get("details") or {})
    return values


def _update(control, configuration=None):
    reply = _post(_host(), "update", {"update_message": {"seqnr": 0, "account_auth": _auth(), "control": control, "configuration": configuration or {}}})
    if _status(reply) not in (2, None):
        raise ThermostatError(config.say("The thermostat didn't accept that.", "De thermostaat nam dat niet aan."))


def status():
    v = read()
    boiler = int(v.get("boiler_status") or 0)
    mode = MODES.get(v.get("ch_mode"), str(v.get("ch_mode")))
    parts = [config.say(f"inside {v.get('room_temp')} °C", f"binnen {v.get('room_temp')} °C"),
             config.say(f"set to {v.get('ch_mode_temp')} °C", f"ingesteld op {v.get('ch_mode_temp')} °C"),
             config.say(f"program: {mode}", f"programma: {DUTCH.get(mode, mode)}")]
    if boiler & 2:
        parts.append(config.say("heating now", "de verwarming brandt nu"))
    elif boiler & 4:
        parts.append(config.say("heating hot water now", "hij maakt nu warm water"))
    if v.get("outside_temp") is not None:
        parts.append(config.say(f"outside {v.get('outside_temp')} °C", f"buiten {v.get('outside_temp')} °C"))
    if v.get("dhw_water_temp") is not None:
        parts.append(config.say(f"hot water {v.get('dhw_water_temp')} °C", f"warm water {v.get('dhw_water_temp')} °C"))
    return ", ".join(parts)


def set_temperature(target=None, change=None):
    """A temperature (4–27 °C), or warmer / colder by [change] degrees from what it's set to."""
    if target is None:
        current = float(read().get("ch_mode_temp") or 20)
        target = current + float(change or 0)
    target = round(max(4.0, min(27.0, float(target))) * 2) / 2  # (it takes half degrees)
    _update({"ch_mode_temp": target})
    return config.say(f"thermostat set to {target:g} °C", f"thermostaat op {target:g} °C gezet")


def set_mode(mode):
    want = mode.lower().strip()
    aliases = {"auto": "automatic", "automatisch": "automatic", "klokprogramma": "automatic", "programma": "automatic", "schedule": "automatic",
               "handmatig": "manual", "vakantie": "vacation", "away": "vacation", "weg": "vacation", "open haard": "fireplace", "haard": "fireplace"}
    want = aliases.get(want, want)
    code = next((k for k, v in MODES.items() if v == want), None)
    if code is None:
        raise ThermostatError(config.say(f"Unknown program {mode}.", f"Onbekend programma {mode}."))
    if code == 3:  # vacation: for a day, starting now (like the ATAG app)
        from datetime import datetime
        _update({"ch_mode": code, "vacation_duration": 86400}, {"start_vacation": int((datetime.utcnow() - datetime(2000, 1, 1)).total_seconds())})
    else:
        _update({"ch_mode": code})
    return config.say(f"program: {want}", f"programma: {DUTCH[want]}")
