"""
The TV (Android TV / Google TV, like the Philips The One): on/off, volume, keys and apps, over Wi-Fi as its own remote
(the same way the Google TV app on a phone does it). Paired once with a code on the TV: sudo dave tv setup.
When it's in deep standby and doesn't answer, "on" also sends a Wake-on-LAN packet.
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
KEYS = {  # what the AI can press
    "power": "POWER", "volume_up": "VOLUME_UP", "volume_down": "VOLUME_DOWN", "mute": "VOLUME_MUTE", "home": "HOME", "back": "BACK",
    "play_pause": "MEDIA_PLAY_PAUSE", "play": "MEDIA_PLAY", "pause": "MEDIA_PAUSE", "next": "MEDIA_NEXT", "previous": "MEDIA_PREVIOUS",
    "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT", "ok": "DPAD_CENTER",
    "channel_up": "CHANNEL_UP", "channel_down": "CHANNEL_DOWN", "input": "TV_INPUT", "settings": "SETTINGS",
}


class TVError(Exception):
    pass


def _remote(host=None):
    try:
        from androidtvremote2 import AndroidTVRemote
    except ImportError as e:
        raise TVError("The TV module isn't installed: run sudo ./install.sh again") from e
    host = host or config.get("tv_host")
    if not host:
        raise TVError(config.say("The TV isn't set up yet: sudo dave tv setup", "De tv is nog niet ingesteld: sudo dave tv setup"))
    os.makedirs(FOLDER, exist_ok=True)
    return AndroidTVRemote(client_name=config.get("name"), certfile=CERT, keyfile=KEY, host=host)


def setup(host, ask_code):
    """Pairs with the TV at [host]: the TV shows a code, [ask_code]() returns what the user typed."""
    async def go():
        remote = _remote(host)
        await remote.async_generate_cert_if_missing()
        try:
            name, mac = await remote.async_get_name_and_mac()
        except Exception as e:
            raise TVError(config.say(f"I can't reach a TV at {host}. Is it on, and is the address right?",
                                     f"Ik kan geen tv bereiken op {host}. Staat hij aan, en klopt het adres?")) from e
        await remote.async_start_pairing()
        code = await asyncio.get_running_loop().run_in_executor(None, ask_code)
        await remote.async_finish_pairing(code.strip())
        return name, mac
    name, mac = asyncio.run(go())
    config.load().update({"tv_host": host, "tv_mac": mac, "tv_name": name})
    config.save()
    return name


def _wake():
    """Wake-on-LAN: the 'magic packet' to the TV's network address (for deep standby)."""
    mac = (config.get("tv_mac") or "").replace(":", "").replace("-", "")
    if len(mac) != 12:
        return False
    packet = bytes.fromhex("FF" * 6 + mac * 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for address in ("255.255.255.255", config.get("tv_host")):
            try:
                s.sendto(packet, (address, 9))
            except OSError:
                pass
    return True


async def _connected(attempts=1):
    remote = _remote()
    for i in range(attempts):
        try:
            await remote.async_connect()
            await asyncio.sleep(0.6)  # (its on/off state comes in right after connecting)
            return remote
        except Exception as e:
            if "auth" in type(e).__name__.lower():
                raise TVError(config.say("The TV doesn't know me anymore: pair again with sudo dave tv setup",
                                         "De tv kent me niet meer: koppel opnieuw met sudo dave tv setup")) from e
            if i == attempts - 1:
                raise TVError(config.say("I can't reach the TV. Is it plugged in, with 'turn on via network' on?",
                                         "Ik kan de tv niet bereiken. Zit de stekker erin, en staat 'inschakelen via netwerk' aan?")) from e
            await asyncio.sleep(2)


def control(action, value=""):
    """on, off, status, a key (volume_up, mute, home…) or app (Netflix, YouTube…). Returns what happened."""
    async def go():
        if action == "on":
            try:
                remote = await _connected()
            except TVError:
                if not _wake():
                    raise
                remote = await _connected(attempts=6)  # woken up: give it a moment
            if not remote.is_on:
                remote.send_key_command("POWER")
            remote.disconnect()
            return config.say("The TV is on.", "De tv staat aan.")
        remote = await _connected()
        try:
            if action == "off":
                if remote.is_on:
                    remote.send_key_command("POWER")
                return config.say("The TV is off.", "De tv staat uit.")
            if action == "status":
                app = remote.current_app or ""
                volume = (remote.volume_info or {}).get("level")
                return (config.say("on", "aan") if remote.is_on else config.say("off (standby)", "uit (standby)")) + \
                       (f", app {app}" if app and remote.is_on else "") + (f", volume {volume}" if volume is not None and remote.is_on else "")
            if action == "app":
                want = (value or "").lower().replace(" ", "").replace("+", "")
                apps = {**APPS, **{k.lower().replace(" ", ""): v for k, v in (config.get("tv_apps") or {}).items()}}  # (your own: sudo dave tv add-app)
                package = next((p for name, p in sorted(apps.items(), key=lambda x: -len(x[0])) if name in want), None)
                if not package:
                    if "." not in value:
                        raise TVError(config.say(f"I don't know the app {value}. Add it with: sudo dave tv add-app \"{value}\" <its package name>",
                                                 f"De app {value} ken ik niet. Voeg hem toe met: sudo dave tv add-app \"{value}\" <pakketnaam>"))
                    package = value  # a package name itself, like com.ziggo.tv
                if not remote.is_on:
                    remote.send_key_command("POWER")
                    await asyncio.sleep(4)
                # The app's own link first; if the TV doesn't switch to it, the package itself
                for target in [t for t in (LINKS.get(package), package) if t]:
                    remote.send_launch_app_command(target)
                    for _ in range(12):  # up to 6 s: did it open?
                        await asyncio.sleep(0.5)
                        if (remote.current_app or "") == package:
                            return config.say(f"{value} is open on the TV.", f"{value} staat open op de tv.")
                now = remote.current_app or "?"
                print(f"(tv: sent {package}, the TV now shows {now})")
                return config.say(f"I asked the TV to open {value}, but it still shows {now}. Is the app installed on the TV?",
                                  f"Ik heb de tv gevraagd {value} te openen, maar hij laat nog {now} zien. Staat de app op de tv?")
            key = KEYS.get(action) or KEYS.get(value)
            if not key:
                raise TVError(f"Unknown TV action {action} {value}")
            for _ in range(max(1, min(10, int(value) if str(value).isdigit() and action.startswith("volume") else 1))):
                remote.send_key_command(key)
                await asyncio.sleep(0.15)
            return f"key {key}"
        finally:
            await asyncio.sleep(1)  # (let the last command go out before hanging up)
            remote.disconnect()
    return asyncio.run(go())
