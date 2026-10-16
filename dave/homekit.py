"""
The thermostat through Apple HomeKit, over your own Wi-Fi (no cloud, no Apple device needed): for the ATAG One Zone, the one
with the ATAG Zone app, which doesn't have the older ATAG One's own connection anymore. Dave becomes its HomeKit "owner"
once (sudo dave thermostaat setup, with the 8-digit HomeKit code the thermostat shows on its screen), like Home Assistant's
HomeKit Controller does, with the same library (aiohomekit). The pairing is kept next to the settings (only readable by root).

HomeKit's thermostat: the temperature now and the one it's set to, and its mode: off, heat (manual: holds the temperature) or
auto (the thermostat's own schedule).
"""

import asyncio
import contextlib
import os
import pathlib
import re
import shutil
import subprocess

from . import config

PAIRING_FILE = os.path.join(os.path.dirname(config.PATH) or ".", "homekit.json")
ALIAS = "thermostat"
APPLE = "-0000-1000-8000-0026BB765291"
# HomeKit's own numbers for a thermostat and its values
THERMOSTAT, CURRENT, TARGET, HEATING_NOW, MODE = "4A", "11", "35", "F", "33"
MODES = {0: "off", 1: "heat", 2: "cool", 3: "auto"}


class HomeKitError(Exception):
    pass


def _short(kind):
    """'0000004A-0000-1000-8000-0026BB765291' → '4A' (how HomeKit's own types are known here)."""
    kind = str(kind).upper()
    if kind.endswith(APPLE):
        kind = kind[: -len(APPLE)].lstrip("0") or "0"
    return kind


@contextlib.asynccontextmanager
async def _controller():
    try:
        from aiohomekit import Controller
        from aiohomekit.characteristic_cache import CharacteristicCacheFile
        from aiohomekit.zeroconf import ZeroconfServiceListener
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
    except ImportError as e:
        raise HomeKitError(config.say("HomeKit isn't installed yet: sudo dave update", "HomeKit is nog niet geïnstalleerd: sudo dave update")) from e
    zeroconf = AsyncZeroconf()
    controller = Controller(async_zeroconf_instance=zeroconf, char_cache=CharacteristicCacheFile(pathlib.Path(PAIRING_FILE).with_name("homekit-cache.json")))
    async with zeroconf:
        browser = AsyncServiceBrowser(zeroconf.zeroconf, ["_hap._tcp.local.", "_hap._udp.local."], listener=ZeroconfServiceListener())  # (Wi-Fi and Thread: aiohomekit wants both)
        try:
            async with controller:
                if os.path.exists(PAIRING_FILE):
                    controller.load_data(PAIRING_FILE)
                yield controller
        finally:
            await browser.async_cancel()


def _run(coro):
    try:
        return asyncio.run(coro)
    except HomeKitError:
        raise
    except Exception as e:  # (aiohomekit's own errors: not reachable, wrong code…)
        raise HomeKitError(f"HomeKit: {type(e).__name__}: {e}") from e


def paired():
    return os.path.exists(PAIRING_FILE) and ALIAS in (_read_file() or {})


def _read_file():
    import json
    try:
        with open(PAIRING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# --- Finding and pairing ---

async def _find(seconds):
    async with _controller() as controller:
        await asyncio.sleep(seconds)  # (zeroconf needs a moment to hear everything on the network)
        found = []
        async for discovery in controller.async_discover(1):
            d = discovery.description
            found.append({"id": d.id, "name": d.name, "model": getattr(d, "model", ""), "address": d.address,
                          "category": int(d.category), "paired": discovery.paired})
        return found


def _avahi():
    """The HomeKit devices avahi (the Pi's own network finder) knows. Python's zeroconf, next to avahi on the Pi, doesn't
    always hear a slow device; avahi does. [{id, name, model, address, port, category, paired, ff, c#, s#, pv}]."""
    if not shutil.which("avahi-browse"):
        return []
    try:
        out = subprocess.run(["avahi-browse", "-rpt", "_hap._tcp"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = {}
    for line in out.splitlines():
        f = line.split(";")
        if len(f) < 10 or f[0] != "=" or f[2] != "IPv4":
            continue
        txt = dict(kv.split("=", 1) for kv in re.findall(r'"([^"]*)"', ";".join(f[9:])) if "=" in kv)
        if "id" not in txt:
            continue
        name = re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1))), f[3])
        found[txt["id"].lower()] = {"id": txt["id"].lower(), "name": name, "model": txt.get("md", ""), "address": f[7], "port": int(f[8]),
                                    "category": int(txt.get("ci", 1)), "paired": not (int(txt.get("sf", 0)) & 1),
                                    "ff": int(txt.get("ff", 0)), "sf": int(txt.get("sf", 0)), "c#": int(txt.get("c#", 0)),
                                    "s#": int(txt.get("s#", 0)), "pv": txt.get("pv", "1.1")}
    return list(found.values())


def discover(seconds=6):
    """The HomeKit devices on the network: [{id, name, model, address, category, paired}] (Python's zeroconf and avahi together)."""
    seen = {d["id"].lower(): d for d in _avahi()}
    try:
        for d in _run(_find(seconds)):
            seen.setdefault(d["id"].lower(), d)
    except HomeKitError:
        if not seen:
            raise
    return list(seen.values())


def _is_thermostat(d):
    """HomeKit category 9 = thermostat; the ATAG's gateway says 2 = bridge, with model ATAG… and name Remote Gw Thermo."""
    return d["category"] == 9 or "ATAG" in (d["model"] or "").upper() or "THERMO" in (d["name"] or "").upper()


def find_thermostat(seconds=8):
    """(the thermostat that's free to pair or None, the thermostats seen, all HomeKit devices seen). A slow one on Wi-Fi
    isn't always heard right away: listens up to three times."""
    devices = []
    for _ in range(3):
        devices = discover(seconds)
        thermostats = [d for d in devices if _is_thermostat(d)]
        free = next((d for d in thermostats if not d["paired"]), None)
        if free or thermostats:
            return free, thermostats, devices
    return None, [], devices


def from_qr(text):
    """The 8 digits from a HomeKit QR code's text ("X-HM://0023ISYWYABCD": the setup code is the lowest 27 bits of the
    first 9 characters, in base 36). None when it isn't one."""
    text = str(text).strip().upper()
    if not text.startswith("X-HM://") or len(text) < 16:
        return None
    try:
        payload = int(text[7:16], 36)
    except ValueError:
        return None
    return f"{payload & 0x7FFFFFF:08d}"


def _pin(code):
    digits = from_qr(code) or "".join(c for c in str(code) if c.isdigit())
    if len(digits) != 8:
        raise HomeKitError(config.say("The HomeKit code has 8 digits (123-45-678).", "De HomeKit-code heeft 8 cijfers (123-45-678)."))
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def _from_avahi(controller, device):
    """aiohomekit's discovery for a device avahi found (when its own zeroconf didn't hear it)."""
    from aiohomekit.controller.ip.discovery import IpDiscovery
    from aiohomekit.model import Categories
    from aiohomekit.model.feature_flags import FeatureFlags
    from aiohomekit.model.status_flags import StatusFlags
    from aiohomekit.zeroconf import HomeKitService
    from aiohomekit.controller.abstract import TransportType
    description = HomeKitService(name=device["name"], id=device["id"].lower(), model=device.get("model", ""),
                                 feature_flags=FeatureFlags(device.get("ff", 0)), status_flags=StatusFlags(device.get("sf", 1)),
                                 config_num=device.get("c#", 1), state_num=device.get("s#", 1), category=Categories(device.get("category", 2)),
                                 protocol_version=device.get("pv", "1.1"), type="_hap._tcp.local.",
                                 address=device["address"], addresses=[device["address"]], port=device["port"])
    return IpDiscovery(controller.transports[TransportType.IP], description)


async def _pair(device, pin):
    device_id = device["id"]
    async with _controller() as controller:
        if ALIAS in controller.aliases:
            with contextlib.suppress(Exception):  # (the old one may be gone already)
                await controller.remove_pairing(ALIAS)
        discovery = None
        for _ in range(8 if device.get("port") else 30):  # (a slow thermostat on Wi-Fi can take a while to be heard again)
            async for d in controller.async_discover(1):
                if d.description.id.lower() == device_id.lower():
                    discovery = d
                    break
            if discovery:
                break
            await asyncio.sleep(1)
        if discovery is None and device.get("port"):
            discovery = _from_avahi(controller, device)  # (avahi heard it: pair with what it knows)
        if discovery is None:
            raise HomeKitError(config.say("I can't find the thermostat on the network right now. Try again in a minute.",
                                          "Ik vind de thermostaat nu niet op het netwerk. Probeer het over een minuutje nog eens."))
        finish = await discovery.async_start_pairing(ALIAS)
        pairing = await finish(pin)
        _write_file({ALIAS: pairing.pairing_data})  # (ourselves: aiohomekit 4 keeps a new pairing where save_data doesn't look)


def _write_file(data):
    from aiohomekit import hkjson
    os.makedirs(os.path.dirname(PAIRING_FILE) or ".", exist_ok=True)
    temp = PAIRING_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        f.write(hkjson.dumps_indented(data))
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, PAIRING_FILE)


def pair(device, code):
    """[device]: what discover / find_thermostat found."""
    try:
        _run(_pair(device, _pin(code)))
    except HomeKitError as e:
        if "AuthenticationError" in str(e):
            raise HomeKitError(config.say("That HomeKit code isn't right. Check the code on the thermostat's screen and try again.",
                                          "Die HomeKit-code klopt niet. Kijk nog eens naar de code op het scherm van de thermostaat en probeer het opnieuw.")) from e
        if "UnavailableError" in str(e) or "MaxPeersError" in str(e) or "BusyError" in str(e):
            raise HomeKitError(config.say("The thermostat won't pair now: it may already be in someone's Home app. Remove it there first.",
                                          "De thermostaat wil nu niet koppelen: misschien staat hij al in iemands Woning-app. Haal hem daar eerst weg.")) from e
        raise
    config.set("thermostat_kind", "homekit")


async def _unpair():
    async with _controller() as controller:
        if ALIAS in controller.aliases:
            await controller.remove_pairing(ALIAS)
    _write_file({})


def unpair():
    """Dave lets go of the thermostat (then the Home app or another hub can be its owner)."""
    _run(_unpair())
    config.set("thermostat_kind", "")


# --- Reading and setting ---

async def _with_thermostat(work):
    async with _controller() as controller:
        if ALIAS not in controller.aliases:
            raise HomeKitError(config.say("The thermostat isn't set up yet: sudo dave thermostaat setup",
                                          "De thermostaat is nog niet ingesteld: sudo dave thermostaat setup"))
        pairing = controller.aliases[ALIAS]
        accessories = await pairing.list_accessories_and_characteristics()
        chars = {}
        for accessory in accessories:
            for service in accessory.get("services", []):
                if _short(service.get("type")) != THERMOSTAT:
                    continue
                for c in service.get("characteristics", []):
                    chars[_short(c.get("type"))] = dict(c, aid=accessory["aid"])
        if TARGET not in chars:
            raise HomeKitError(config.say("I don't see a thermostat in that HomeKit device.", "Ik zie geen thermostaat in dat HomeKit-apparaat."))
        try:
            return await work(pairing, chars)
        finally:
            await pairing.close()


async def _values(pairing, chars, kinds):
    keys = [(chars[k]["aid"], chars[k]["iid"]) for k in kinds if k in chars]
    data = await pairing.get_characteristics(keys)
    return {k: data.get((chars[k]["aid"], chars[k]["iid"]), {}).get("value") for k in kinds if k in chars}


def read():
    """{"room_temp", "target", "heating" (True/False), "mode" (off/heat/auto)}."""
    async def work(pairing, chars):
        v = await _values(pairing, chars, [CURRENT, TARGET, HEATING_NOW, MODE])
        return {"room_temp": v.get(CURRENT), "target": v.get(TARGET), "heating": v.get(HEATING_NOW) == 1, "mode": MODES.get(v.get(MODE), "?")}
    return _run(_with_thermostat(work))


def set_temperature(target=None, change=None):
    async def work(pairing, chars):
        meta = chars[TARGET]
        value = target
        if value is None:
            value = float((await _values(pairing, chars, [TARGET]))[TARGET] or 20) + float(change or 0)
        low, high, step = float(meta.get("minValue", 4)), float(meta.get("maxValue", 27)), float(meta.get("minStep", 0.5)) or 0.5
        value = round(max(low, min(high, float(value))) / step) * step
        await _put(pairing, [(meta["aid"], meta["iid"], value)])
        return value
    return _run(_with_thermostat(work))


def set_mode(mode):
    """off, heat (manual) or auto (the schedule)."""
    code = {v: k for k, v in MODES.items()}.get(mode)
    async def work(pairing, chars):
        meta = chars.get(MODE)
        if not meta:
            raise HomeKitError(config.say("This thermostat can't switch programs through HomeKit.", "Deze thermostaat kan via HomeKit niet van programma wisselen."))
        allowed = meta.get("valid-values")
        if allowed and code not in allowed:
            raise HomeKitError(config.say(f"This thermostat doesn't do {mode} through HomeKit.", f"Deze thermostaat kan via HomeKit niet op {mode}."))
        await _put(pairing, [(meta["aid"], meta["iid"], code)])
    _run(_with_thermostat(work))


async def _put(pairing, values):
    result = await pairing.put_characteristics(values)
    problems = [v.get("description", "") for v in (result or {}).values() if v.get("status", 0) != 0]
    if problems:
        raise HomeKitError(config.say("The thermostat didn't accept that: ", "De thermostaat nam dat niet aan: ") + "; ".join(problems))
