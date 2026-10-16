"""
The Dreame robot vacuums (the Dreamehome app): start, pause, stop, back to the dock, "where are you?" (it makes a sound), how
it's doing and its battery, and Dave says when it's done or stuck (to your phone too).

Dreame has no public API and only works through its own cloud, so this uses the cloud part of the Dreame integration for
Home Assistant (github.com/Tasshack/dreame-vacuum, MIT licence): `sudo dave update` puts its latest release in
/opt/dave/vendor, it isn't part of Dave. Log in once with the Dreamehome account: sudo dave stofzuiger login.
"""

import os
import sys
import threading
import time

from . import config, log

VENDOR = os.environ.get("DAVE_VENDOR", "/opt/dave/vendor")
START, PAUSE, DOCK, STOP, LOCATE = (2, 1), (2, 2), (3, 1), (4, 2), (7, 1)  # (siid, aiid): Dreame's own numbers
STATE, ERROR, BATTERY, CHARGING = (2, 1), (2, 2), (3, 1), (3, 2)            # (siid, piid)
STATES = {1: ("cleaning", "aan het zuigen"), 2: ("idle", "niets aan het doen"), 3: ("paused", "gepauzeerd"), 4: ("stuck / has an error", "vast of in storing"),
          5: ("going back to the dock", "op weg naar het station"), 6: ("charging", "aan het opladen"), 7: ("mopping", "aan het dweilen"),
          8: ("drying its mop", "zijn mop aan het drogen"), 9: ("washing its mop", "zijn mop aan het wassen"), 10: ("going back to wash", "terug om te wassen"),
          11: ("mapping", "een kaart aan het maken"), 12: ("vacuuming and mopping", "aan het zuigen en dweilen"), 13: ("charged", "opgeladen"),
          14: ("updating", "aan het updaten"), 22: ("emptying its bin", "zijn bak aan het legen"), 27: ("spot cleaning", "een plekje aan het zuigen"),
          29: ("waiting", "aan het wachten"), 30: ("cleaning its station", "zijn station aan het schoonmaken")}
BUSY = {1, 5, 7, 10, 12, 27}  # (out cleaning: worth looking more often)

on_news = None
_lock = threading.RLock()
_cloud = None
_thread = None


class VacuumError(Exception):
    pass


def ready():
    return bool(config.get("dreame_user") and (config.get("dreame_key") or config.get("dreame_password")))


def _protocol():
    if VENDOR not in sys.path:
        sys.path.insert(0, VENDOR)
    try:
        from dreame_vacuum.dreame.protocol import DreameVacuumDreameHomeCloudProtocol
    except ImportError as e:
        raise VacuumError(config.say("The Dreame part isn't installed yet: sudo dave update", "Het Dreame-deel is nog niet geïnstalleerd: sudo dave update")) from e
    return DreameVacuumDreameHomeCloudProtocol


def _login():
    """The Dreame cloud, logged in (once per run; the key it gives is kept, so the password is only needed again when it runs out)."""
    global _cloud
    if not ready():
        raise VacuumError(config.say("Not logged in to Dreame yet: sudo dave stofzuiger login", "Nog niet ingelogd bij Dreame: sudo dave stofzuiger login"))
    with _lock:
        if _cloud and _cloud.logged_in:
            return _cloud
        cloud = _protocol()(config.get("dreame_user"), config.get("dreame_password"), "dreame", country=config.get("dreame_country") or "eu",
                            auth_key=config.get("dreame_key") or None)
        if not cloud.login():
            if config.get("dreame_key") and config.get("dreame_password"):
                config.set("dreame_key", "")
                return _login()
            raise VacuumError(config.say("Dreame didn't accept the login: sudo dave stofzuiger login", "Dreame nam het inloggen niet aan: sudo dave stofzuiger login"))
        if cloud.auth_key and cloud.auth_key != config.get("dreame_key"):
            config.set("dreame_key", cloud.auth_key)
        _cloud = cloud
        return cloud


REGIONS = {"eu", "us", "cn", "ru", "sg", "in", "kr"}
EUROPE = {"nl", "be", "de", "fr", "uk", "gb", "es", "it", "at", "ch", "pl", "se", "dk", "no", "fi", "pt", "ie", "lu", "cz", "gr", "europe", "europa"}


def region(text):
    """Dreame's server region: 'eu' for Europe (also when a country like 'nl' is given), None when unknown."""
    text = (text or "eu").strip().lower()
    return "eu" if text in EUROPE else text if text in REGIONS else None


def login(user, password, country="eu"):
    global _cloud
    country = region(country)
    if not country:
        raise VacuumError(config.say("Unknown region: use eu (Europe), us, cn, ru or sg.", "Onbekende regio: gebruik eu (Europa), us, cn, ru of sg."))
    before = {k: config.get(k) for k in ("dreame_user", "dreame_password", "dreame_country", "dreame_key")}
    config.set("dreame_user", user.strip())
    config.set("dreame_password", password)
    config.set("dreame_country", country)
    config.set("dreame_key", "")
    _cloud = None
    try:
        _login()
    except VacuumError:
        for k, v in before.items():  # (a wrong password: back to how it was)
            config.set(k, v)
        raise
    return vacuums()


def vacuums():
    """[{did, name, model, online}] from the Dreamehome account."""
    with _lock:
        data = _login().get_devices() or {}
    records = (data.get("page") or {}).get("records") or []
    out = []
    for d in records:
        if ".vacuum." not in str(d.get("model", "")):
            continue
        name = d.get("customName") or (d.get("deviceInfo") or {}).get("displayName") or d.get("model")
        if not any(w in str(name).lower() for w in ("stofzuiger", "robot", "dreame", "vacuum")):
            name = config.say(f"vacuum {name}", f"stofzuiger {name}")  # ("de stofzuiger Beneden", not "de Beneden")
        out.append({"did": str(d.get("did")), "name": name,
                    "model": d.get("model"), "online": d.get("online", True), "_raw": d})
    return out


def _simple(text):
    return "".join(c for c in (text or "").lower() if c.isalnum())


def find(which="", one=False):
    """The vacuums meant: by name; "alle" / "allebei": all; nothing said: the only one, or (one=True, to make them do something)
    a question which one, (one=False, for how they're doing) all of them."""
    found = vacuums()
    if not found:
        raise VacuumError(config.say("There's no robot vacuum in the Dreamehome account.", "Er staat geen robotstofzuiger in het Dreamehome-account."))
    if _simple(which) in ("alle", "all", "allebei", "beide", "both", "allebeide", "allemaal"):
        return found
    want = _simple(which).replace("stofzuigers", "").replace("stofzuiger", "").replace("robot", "").replace("vacuum", "")
    if want:
        hit = [v for v in found if want in _simple(v["name"]) or _simple(v["name"]) in want]
        if not hit:
            raise VacuumError(config.say(f"I don't know a vacuum called {which}. There are: {', '.join(v['name'] for v in found)}.",
                                         f"Ik ken geen stofzuiger die {which} heet. Er zijn: {', '.join(v['name'] for v in found)}."))
        return hit
    if len(found) == 1 or not one:
        return found
    raise VacuumError(config.say(f"Which one: {' or '.join(v['name'] for v in found)}? (Or say: both.)",
                                 f"Welke: {' of '.join(v['name'] for v in found)}? (Of zeg: allebei.)"))


def _send(vacuum, method, params):
    with _lock:
        cloud = _login()
        cloud._did = vacuum["did"]
        try:
            cloud._handle_device_info(vacuum["_raw"])  # (which of Dreame's servers this one is on)
        except Exception:
            pass
        result = cloud.send(method, params)
    if result is None:
        raise VacuumError(config.say(f"The {vacuum['name']} didn't answer (is it on Wi-Fi?).", f"De {vacuum['name']} antwoordde niet (zit hij op de wifi?)."))
    return result


def _values(vacuum):
    keys = {STATE: "state", ERROR: "error", BATTERY: "battery", CHARGING: "charging"}
    result = _send(vacuum, "get_properties", [{"did": vacuum["did"], "siid": s, "piid": p} for s, p in keys])
    out = {}
    for r in result or []:
        key = keys.get((r.get("siid"), r.get("piid")))
        if key and r.get("code", 0) == 0:
            out[key] = r.get("value")
    return out


def status(which=""):
    lines = []
    for v in find(which or ""):
        if not v["online"]:
            lines.append(config.say(f"The {v['name']} is offline.", f"De {v['name']} is offline."))
            continue
        values = _values(v)
        state = config.say(*STATES.get(values.get("state"), (f"in state {values.get('state')}", f"in toestand {values.get('state')}")))
        battery = values.get("battery")
        lines.append(config.say(f"The {v['name']} is {state}", f"De {v['name']} is {state}") + (f", {battery}% " + config.say("battery", "batterij") if battery is not None else "")
                     + (config.say(" (it has an error)", " (hij heeft een storing)") if values.get("error") not in (None, 0) and values.get("state") != 4 else "") + ".")
    return " ".join(lines)


def _act(which, action, done, both):
    """[action] on the vacuums meant; [done] / [both]: (English, Dutch) of what to say for one ({} = its name) / for several."""
    names = []
    for v in find(which or "", one=True):
        result = _send(v, "action", {"did": v["did"], "siid": action[0], "aiid": action[1], "in": []})
        if isinstance(result, dict) and result.get("code", 0) not in (0, None):
            raise VacuumError(config.say(f"The {v['name']} didn't do it (code {result.get('code')}).", f"De {v['name']} deed het niet (code {result.get('code')})."))
        names.append(v["name"])
    text = config.say(*both) if len(names) > 1 else config.say(*done).format(names[0])
    log.write(f"stofzuiger: {text}")
    return text


def start(which=""):
    return _act(which, START, ("The {} is starting to clean.", "De {} gaat zuigen."), ("The vacuums are starting to clean.", "De stofzuigers gaan zuigen."))


def pause(which=""):
    return _act(which, PAUSE, ("The {} is paused.", "De {} is gepauzeerd."), ("The vacuums are paused.", "De stofzuigers zijn gepauzeerd."))


def stop(which=""):
    return _act(which, STOP, ("The {} stopped.", "De {} is gestopt."), ("The vacuums stopped.", "De stofzuigers zijn gestopt."))


def dock(which=""):
    return _act(which, DOCK, ("The {} is going back to its dock.", "De {} gaat terug naar zijn station."),
                ("The vacuums are going back to their docks.", "De stofzuigers gaan terug naar hun station."))


def locate(which=""):
    return _act(which, LOCATE, ("The {} is making a sound so you can find it.", "De {} maakt geluid zodat je hem vindt."),
                ("The vacuums are making a sound so you can find them.", "De stofzuigers maken geluid zodat je ze vindt."))


# --- Keeping an eye on them (the service): done / stuck ---

def watch():
    global _thread
    if not ready() or (_thread and _thread.is_alive()):
        return
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()


def _loop():
    last = {}
    while True:
        wait = 600
        try:
            for v in vacuums():
                if not v["online"]:
                    continue
                state = _values(v).get("state")
                before = last.get(v["did"])
                last[v["did"]] = state
                if state in BUSY:
                    wait = 90
                if before in BUSY and state in (2, 6, 13):  # (was out cleaning or on its way back, now home)
                    _tell(config.say(f"The {v['name']} is done and back on its dock.", f"De {v['name']} is klaar en staat weer in zijn station."))
                elif state == 4 and before != 4:
                    _tell(config.say(f"The {v['name']} is stuck or has an error.", f"De {v['name']} zit vast of heeft een storing."))
        except VacuumError as e:
            print(f"(vacuum: {e})", flush=True)
            wait = 900
        except Exception as e:
            print(f"(vacuum: {type(e).__name__}: {e})", flush=True)
            wait = 900
        time.sleep(wait)


def _tell(message):
    from . import appliances
    print(f"🧹 {message}", flush=True)
    log.write(f"zegt: {message}", "stofzuiger")
    if on_news:
        on_news(message)
    appliances.phone(config.get("name"), message)
