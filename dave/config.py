"""Dave's settings: /etc/dave/config.json (only readable by root: it holds your keys)."""

import json
import os

PATH = os.environ.get("DAVE_CONFIG", "/etc/dave/config.json")

DEFAULTS = {
    "name": "Dave",
    "language": "nl-NL",            # what Dave speaks (nl-NL or en-US)
    "personality": "normal",        # normal, chill, hyped, butler, sarcastic
    "wake_phrase": "hey dave",
    "memories": [],
    "schedule": [],                 # timers, reminders and alarms (they survive a restart)
    "stopwatch": {},
    "alarm_lights": True,           # the Hue lights slowly brighter in the 10 minutes before an alarm
    "alarm_room": "",               # which room for that ("" = all lights)
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
    # SmartThings (sudo dave smartthings token … or sudo dave smartthings login)
    "smartthings_token": "",
    "smartthings_client_id": "",
    "smartthings_client_secret": "",
    "smartthings_redirect": "https://httpbin.org/get",
    "smartthings_refresh": "",
    "smartthings_access": "",
    "smartthings_expires": 0,
}

SECRETS = {"groq_key", "azure_key", "hue_user", "smartthings_token", "smartthings_client_secret", "smartthings_refresh", "smartthings_access"}

_cache = None
_mtime = None


def _changed_on_disk():
    try:
        return os.path.getmtime(PATH) != _mtime
    except OSError:
        return False


def load():
    global _cache, _mtime
    if _cache is None or _changed_on_disk():  # (another Dave process, like the service, may have changed it)
        try:
            _mtime = os.path.getmtime(PATH)
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
        _cache = {**json.loads(json.dumps(DEFAULTS)), **data}  # (a fresh copy of the defaults: lists and dicts not shared)
    return _cache


def get(key):
    return load().get(key, DEFAULTS.get(key))


def set(key, value):
    load()[key] = value
    save()


def save():
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    temp = PATH + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(_cache if _cache is not None else load(), f, indent=2, ensure_ascii=False)  # (what is in memory now, also when the file changed meanwhile)
    os.chmod(temp, 0o600)
    os.replace(temp, PATH)
    global _mtime
    _mtime = os.path.getmtime(PATH)


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
