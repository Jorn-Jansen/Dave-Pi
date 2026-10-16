"""Dave's settings: /etc/dave/config.json (only readable by root: it holds your keys)."""

import json
import os
import threading

PATH = os.environ.get("DAVE_CONFIG", "/etc/dave/config.json")

DEFAULTS = {
    "name": "Dave",
    "language": "nl-NL",            # what Dave speaks (nl-NL or en-US)
    "personality": "normal",        # normal, chill, hyped, butler, sarcastic
    "wake_phrase": "hey dave",
    "memories": [],
    "schedule": [],                 # timers, reminders and alarms (they survive a restart)
    "stopwatch": {},
    "appliance_alerts": True,       # say when the washer / dryer is (almost) done
    "phone_topic": "",              # also to your phone: an ntfy channel (the free ntfy app), e.g. the one PC Dave uses                 # what Dave remembers for good ("onthoud dat…"); sudo dave memory
    "groq_key": "",                 # free at console.groq.com/keys: the AI and speech recognition
    # The voice: Azure (natural, online) when there's a key, otherwise Piper (offline) if installed, otherwise espeak-ng
    "voice_engine": "auto",         # auto (Azure when there's a key), azure, or offline (Piper if installed, else espeak)
    "azure_key": "",
    "azure_region": "westeurope",
    "azure_voice": "nl-NL-FennaNeural",
    "piper_model": "",              # e.g. /opt/dave/voices/nl_NL-mls-medium.onnx
    # Sound
    "mic_device": "default",        # ALSA device, see `arecord -L`
    "speaker_device": "default",    # see `aplay -L`
    "mic_sensitivity": 1.0,         # higher = picks up quieter speech
    # Philips Hue (sudo dave hue setup)
    "hue_bridge": "",
    "hue_user": "",
    # The TV (Android TV / Google TV, e.g. Philips The One): sudo dave tv setup
    "tv_host": "",
    "tv_mac": "",
    "tv_name": "",
    "tvs": {},                      # {"woonkamer": {"host": …, "mac": …, "model": …}}
    "tv_default": "",               # the one used when you don't say which
    "tv_apps": {},                  # your own apps: {"Ziggo GO": "com.ziggo.tv"} (sudo dave tv add-app)
    # The ATAG thermostat (sudo dave thermostaat setup)
    "thermostat_host": "",
    "thermostat_mac": "",
    "thermostat_kind": "",          # "homekit" (ATAG One Zone) or "" (the older ATAG One's own connection)
    # Lists, shared with the phones (sudo dave lijst koppel)
    "lists": [],
    "lists_code": "",
    "lists_since": "",
    "lists_device": "",
    # SmartThings (sudo dave smartthings token … or sudo dave smartthings login)
    "smartthings_token": "",
    "smartthings_client_id": "",
    "smartthings_client_secret": "",
    "smartthings_redirect": "https://httpbin.org/get",
    "smartthings_refresh": "",
    "smartthings_access": "",
    "smartthings_expires": 0,
    # Home Connect (Bosch, Siemens…: the dishwasher): sudo dave homeconnect setup
    "homeconnect_client_id": "",
    "homeconnect_client_secret": "",
    "homeconnect_refresh": "",
    "homeconnect_access": "",
    "homeconnect_expires": 0,
}

OLD = {"alarm_lights", "alarm_room"}  # (settings Dave doesn't have anymore)
SECRETS = {"homeconnect_client_secret", "homeconnect_refresh", "homeconnect_access", "lists_code", "groq_key", "azure_key", "hue_user", "smartthings_token", "smartthings_client_secret", "smartthings_refresh", "smartthings_access"}

_cache = None
_mtime = None


def _stamp():
    """What the file is now: its time, size and inode. (Every save replaces the file, so the inode changes too: the time alone
    isn't enough on Linux, where it can be the same for two saves close together, and another Dave process's change would be lost.)"""
    s = os.stat(PATH)
    return (s.st_mtime_ns, s.st_size, s.st_ino)


def _changed_on_disk():
    try:
        return _stamp() != _mtime
    except OSError:
        return False


def load():
    global _cache, _mtime
    if _cache is None or _changed_on_disk():  # (another Dave process, like the service, may have changed it)
        try:
            _mtime = _stamp()
        except OSError:
            _mtime = None
        data = {}
        try:
            with open(PATH, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print(f"(couldn't read {PATH}: {e})")
        for old in OLD:
            data.pop(old, None)
        _cache = {**json.loads(json.dumps(DEFAULTS)), **data}  # (a fresh copy of the defaults: lists and dicts not shared)
    return _cache


def get(key):
    return load().get(key, DEFAULTS.get(key))


_saving = threading.RLock()  # (timers, the washer and the lists each have a thread: one save at a time)


def set(key, value):
    with _saving:
        load()[key] = value
        save()


def save():
    with _saving:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        temp = PATH + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(_cache if _cache is not None else load(), f, indent=2, ensure_ascii=False)  # (what is in memory now, also when the file changed meanwhile)
        os.chmod(temp, 0o600)
        os.replace(temp, PATH)
        global _mtime
        _mtime = _stamp()


def dutch():
    return str(get("language")).lower().startswith("nl")


def say(english, dutch_text):
    """English or Dutch, in Dave's language."""
    return dutch_text if dutch() else english


def shown(key, value):
    """A setting for showing on screen: keys only partly."""
    if key in SECRETS and value:
        text = str(value)
        return text[:4] + "…" + text[-4:] if len(text) > 12 else "…"
    return value
