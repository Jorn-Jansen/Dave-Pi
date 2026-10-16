"""
The TVs (Android TV / Google TV: a Philips The One, a Chromecast with Google TV…): on/off, volume, keys and apps, over
Wi-Fi as their own remote (the way the Google TV app on a phone does it). Each one has a name ("woonkamer", "slaapkamer"),
one is the default (used when you don't say which), and each is paired once with a code on its screen: sudo dave tv setup.
When one is in deep standby and doesn't answer, "on" also sends a Wake-on-LAN packet.
"""

import asyncio
import os
import socket

from . import config

FOLDER = os.path.dirname(config.PATH)
CERT, KEY = os.path.join(FOLDER, "tv_cert.pem"), os.path.join(FOLDER, "tv_key.pem")

APPS = {  # what you say → the app on the TV
    "netflix": "com.netflix.ninja", "youtube": "com.google.android.youtube.tv", "disney": "com.disney.disneyplus",
    "prime": "com.amazon.amazonvideo.livingroom", "amazon": "com.amazon.amazonvideo.livingroom", "spotify": "com.spotify.tv.android",
    "videoland": "nl.rtl.videoland.v2", "npo": "nl.uitzendinggemist", "hbo": "com.wbd.stream", "max": "com.wbd.stream",
    "plex": "com.plexapp.android", "twitch": "tv.twitch.android.app", "kodi": "org.xbmc.kodi",
}
# The app's own link, which most TVs open more reliably than "launch this package" (the ones Home Assistant uses)
LINKS = {
    "com.netflix.ninja": "https://www.netflix.com/title", "com.google.android.youtube.tv": "https://www.youtube.com",
    "com.amazon.amazonvideo.livingroom": "https://app.primevideo.com", "com.disney.disneyplus": "https://www.disneyplus.com",
    "com.spotify.tv.android": "spotify://", "com.plexapp.android": "plex://", "tv.twitch.android.app": "twitch://home",
    "com.wbd.stream": "https://play.max.com",
}
# A link to something specific (a series, a film, a video) → the app it opens in
SITES = {
    "netflix.com": "com.netflix.ninja", "youtube.com": "com.google.android.youtube.tv", "youtu.be": "com.google.android.youtube.tv",
    "primevideo.com": "com.amazon.amazonvideo.livingroom", "amazon.": "com.amazon.amazonvideo.livingroom", "disneyplus.com": "com.disney.disneyplus",
    "max.com": "com.wbd.stream", "spotify.com": "com.spotify.tv.android", "videoland.com": "nl.rtl.videoland.v2", "npo": "nl.uitzendinggemist",
}
KEYS = {  # what the AI can press
    "power": "POWER", "volume_up": "VOLUME_UP", "volume_down": "VOLUME_DOWN", "mute": "VOLUME_MUTE", "home": "HOME", "back": "BACK",
    "play_pause": "MEDIA_PLAY_PAUSE", "play": "MEDIA_PLAY", "pause": "MEDIA_PAUSE", "next": "MEDIA_NEXT", "previous": "MEDIA_PREVIOUS",
    "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT", "ok": "DPAD_CENTER",
    "channel_up": "CHANNEL_UP", "channel_down": "CHANNEL_DOWN", "input": "TV_INPUT", "settings": "SETTINGS",
}


class TVError(Exception):
    pass


# --- Which TVs there are ---

def all_tvs():
    """{name: {host, mac, model}}. (A TV set up before there could be several becomes "tv".)"""
    tvs = dict(config.get("tvs") or {})
    if not tvs and config.get("tv_host"):
        tvs = {"tv": {"host": config.get("tv_host"), "mac": config.get("tv_mac"), "model": config.get("tv_name")}}
        config.load().update({"tvs": tvs, "tv_default": "tv"}); config.save()
    return tvs


def default():
    tvs = all_tvs()
    name = config.get("tv_default")
    return name if name in tvs else (next(iter(tvs)) if tvs else "")


def _simple(text):
    return "".join(c for c in (text or "").lower() if c.isalnum())


def pick(name=""):
    """The TV called [name] (roughly); no name: the default one. Returns (name, info)."""
    tvs = all_tvs()
    if not tvs:
        raise TVError(config.say("There's no TV set up yet: sudo dave tv setup", "Er is nog geen tv ingesteld: sudo dave tv setup"))
    want = _simple(name).replace("tv", "").replace("televisie", "")
    if want:
        for tv_name, info in tvs.items():
            if _simple(tv_name) == want:
                return tv_name, info
        for tv_name, info in tvs.items():
            if want in _simple(tv_name) or _simple(tv_name) in want or want in _simple(info.get("model")):
                return tv_name, info
        raise TVError(config.say(f"I don't have a TV called {name}. The TVs are: {', '.join(tvs)}.",
                                 f"Ik heb geen tv die {name} heet. De tv's zijn: {', '.join(tvs)}."))
    tv_name = default()
    return tv_name, tvs[tv_name]


def set_default(name):
    tv_name, _ = pick(name)
    config.set("tv_default", tv_name)
    return tv_name


def remove(name):
    tv_name, _ = pick(name)
    tvs = all_tvs()
    tvs.pop(tv_name)
    config.load()["tvs"] = tvs
    if config.get("tv_default") == tv_name:
        config.load()["tv_default"] = next(iter(tvs), "")
    config.save()
    return tv_name


def describe():
    tvs = all_tvs()
    if not tvs:
        return config.say("No TVs set up.", "Geen tv's ingesteld.")
    return "\n".join(f"{'⭐ ' if n == default() else '   '}{n}: {i.get('model') or '?'} ({i.get('host')})" for n, i in tvs.items())


# --- Talking to a TV ---

def _remote(host):
    try:
        from androidtvremote2 import AndroidTVRemote
    except ImportError as e:
        raise TVError("The TV module isn't installed: run sudo ./install.sh again") from e
    os.makedirs(FOLDER, exist_ok=True)
    return AndroidTVRemote(client_name=config.get("name"), certfile=CERT, keyfile=KEY, host=host)


def setup(name, host, ask_code):
    """Pairs with the TV at [host] and keeps it as [name]: the TV shows a code, [ask_code]() returns what the user typed."""
    async def go():
        remote = _remote(host)
        await remote.async_generate_cert_if_missing()
        try:
            model, mac = await remote.async_get_name_and_mac()
        except Exception as e:
            raise TVError(config.say(f"I can't reach a TV at {host}. Is it on, and is the address right?",
                                     f"Ik kan geen tv bereiken op {host}. Staat hij aan, en klopt het adres?")) from e
        await remote.async_start_pairing()
        code = await asyncio.get_running_loop().run_in_executor(None, ask_code)
        await remote.async_finish_pairing(code.strip())
        return model, mac
    model, mac = asyncio.run(go())
    tvs = all_tvs()
    tvs[name] = {"host": host, "mac": mac, "model": model}
    config.load()["tvs"] = tvs
    if config.get("tv_default") not in tvs:  # the first one is the default
        config.load()["tv_default"] = name
    config.save()
    return model


def _wake(info):
    """Wake-on-LAN: the 'magic packet' to the TV's network address (for deep standby)."""
    mac = (info.get("mac") or "").replace(":", "").replace("-", "")
    if len(mac) != 12:
        return False
    packet = bytes.fromhex("FF" * 6 + mac * 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for address in ("255.255.255.255", info.get("host")):
            try:
                s.sendto(packet, (address, 9))
            except OSError:
                pass
    return True


async def _connected(tv_name, info, attempts=1):
    remote = _remote(info["host"])
    for i in range(attempts):
        try:
            await remote.async_connect()
            await asyncio.sleep(0.6)  # (its on/off state comes in right after connecting)
            return remote
        except Exception as e:
            if "auth" in type(e).__name__.lower():
                raise TVError(config.say(f"The {tv_name} TV doesn't know me anymore: pair again with sudo dave tv setup",
                                         f"De tv {tv_name} kent me niet meer: koppel opnieuw met sudo dave tv setup")) from e
            if i == attempts - 1:
                raise TVError(config.say(f"I can't reach the {tv_name} TV. Is it plugged in, with 'turn on via network' on?",
                                         f"Ik kan de tv {tv_name} niet bereiken. Zit de stekker erin, en staat 'inschakelen via netwerk' aan?")) from e
            await asyncio.sleep(2)


def control(action, value="", which="", play=False):
    """On the TV called [which] (empty: the default one): on, off, status, a key (volume_up, mute, home…) or app (Netflix…)."""
    tv_name, info = pick(which)
    label = tv_name if len(all_tvs()) > 1 else config.say("the TV", "de tv")  # (only name it when there are several)

    async def go():
        if action == "on":
            try:
                remote = await _connected(tv_name, info)
            except TVError:
                if not _wake(info):
                    raise
                remote = await _connected(tv_name, info, attempts=6)  # woken up: give it a moment
            if not remote.is_on:
                remote.send_key_command("POWER")
            await asyncio.sleep(1)
            remote.disconnect()
            return config.say(f"{label} is on.", f"{label} staat aan.")
        remote = await _connected(tv_name, info)
        try:
            if action == "off":
                if remote.is_on:
                    remote.send_key_command("POWER")
                return config.say(f"{label} is off.", f"{label} staat uit.")
            if action == "status":
                app = remote.current_app or ""
                volume = (remote.volume_info or {}).get("level")
                return f"{tv_name}: " + (config.say("on", "aan") if remote.is_on else config.say("off (standby)", "uit (standby)")) + \
                       (f", app {app}" if app and remote.is_on else "") + (f", volume {volume}" if volume is not None and remote.is_on else "")
            if action == "app":
                link = value.strip() if value.strip().lower().startswith(("http://", "https://")) else ""
                want = (value or "").lower().replace(" ", "").replace("+", "")
                if link:  # a series / film / video itself: the app is the site's
                    want = next((p for site, p in SITES.items() if site in link.lower()), "")
                apps = {**APPS, **{k.lower().replace(" ", ""): v for k, v in (config.get("tv_apps") or {}).items()}}  # (your own: sudo dave tv add-app)
                package = want if link else next((p for name, p in sorted(apps.items(), key=lambda x: -len(x[0])) if name in want), None)
                if link and not package:
                    package = "?"
                if not package:
                    if "." not in value:
                        raise TVError(config.say(f"I don't know the app {value}. Add it with: sudo dave tv add-app \"{value}\" <its package name>",
                                                 f"De app {value} ken ik niet. Voeg hem toe met: sudo dave tv add-app \"{value}\" <pakketnaam>"))
                    package = value  # a package name itself, like com.ziggo.tv
                if not remote.is_on:
                    remote.send_key_command("POWER")
                    await asyncio.sleep(4)
                before = remote.current_app or ""
                if before == package:  # still "open" in the background (e.g. from before standby): home first, so opening it is real
                    remote.send_key_command("HOME")
                    await asyncio.sleep(1.5)
                print(f"(tv {tv_name}: was showing {before or '?'}, now {remote.current_app or '?'})")
                # A series' own link; otherwise the app's own link first, and if the TV doesn't switch to it, the package itself
                for target in ([link] if link else [t for t in (LINKS.get(package), package) if t]):
                    print(f"(tv {tv_name}: opening {target})")
                    remote.send_launch_app_command(target)
                    for _ in range(12):  # up to 6 s: did it open?
                        await asyncio.sleep(0.5)
                        if (remote.current_app or "") == package or (link and package == "?" and (remote.current_app or "") != before):
                            if play:  # its page is open: press play (that's where the focus usually is)
                                await asyncio.sleep(3.5)
                                remote.send_key_command("DPAD_CENTER")
                            return config.say(f"It's open on {label}.", f"Hij staat open op {label}.") if link else \
                                   config.say(f"{value} is open on {label}.", f"{value} staat open op {label}.")
                now = remote.current_app or "?"
                print(f"(tv {tv_name}: sent {package}, it now shows {now})")
                return config.say(f"I asked {label} to open {value}, but it still shows {now}. Is the app installed there?",
                                  f"Ik heb {label} gevraagd {value} te openen, maar hij laat nog {now} zien. Staat de app erop?")
            key = KEYS.get(action) or KEYS.get(value)
            if not key:
                raise TVError(f"Unknown TV action {action} {value}")
            for _ in range(max(1, min(10, int(value) if str(value).isdigit() and action.startswith("volume") else 1))):
                remote.send_key_command(key)
                await asyncio.sleep(0.15)
            return f"{tv_name}: key {key}"
        finally:
            await asyncio.sleep(1)  # (let the last command go out before hanging up)
            remote.disconnect()
    return asyncio.run(go())
