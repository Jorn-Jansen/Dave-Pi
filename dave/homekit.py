"""
The thermostat through Apple HomeKit, over your own Wi-Fi (no cloud, no Apple device needed): for the ATAG One Zone, the one
with the ATAG Zone app, which doesn't have the older ATAG One's own connection anymore. Dave becomes its HomeKit "owner"
once (sudo dave thermostaat setup, with the 8-digit HomeKit code from the thermostat's sticker or menu), like Home Assistant's
HomeKit Controller does, with the same library (aiohomekit). The pairing is kept next to the settings (only readable by root).

HomeKit's thermostat: the temperature now and the one it's set to, and its mode: off, heat (manual: holds the temperature) or
auto (the thermostat's own schedule).
"""

import asyncio
import contextlib
import os
import pathlib

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


def discover(seconds=6):
    """The HomeKit devices on the network: [{id, name, model, address, category, paired}]."""
    return _run(_find(seconds))


def find_thermostat(seconds=6):
    """The thermostat that's free to pair (HomeKit category 9 = thermostat; the ATAG's gateway says 2 = bridge, model ATAG…)."""
    devices = discover(seconds)
    for d in devices:
        if not d["paired"] and (d["category"] == 9 or "ATAG" in (d["model"] or "").upper() or "THERMO" in (d["name"] or "").upper()):
            return d
    return None


def _pin(code):
    digits = "".join(c for c in str(code) if c.isdigit())
    if len(digits) != 8:
        raise HomeKitError(config.say("The HomeKit code has 8 digits (123-45-678).", "De HomeKit-code heeft 8 cijfers (123-45-678)."))
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


async def _pair(device_id, pin):
    async with _controller() as controller:
        if ALIAS in controller.aliases:
            with contextlib.suppress(Exception):  # (the old one may be gone already)
                await controller.remove_pairing(ALIAS)
        discovery = await controller.async_find(device_id)
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


def pair(device_id, code):
    try:
        _run(_pair(device_id, _pin(code)))
    except HomeKitError as e:
        if "AuthenticationError" in str(e):
            raise HomeKitError(config.say("That HomeKit code isn't right. Check the sticker or the thermostat's menu and try again.",
                                          "Die HomeKit-code klopt niet. Kijk op de sticker of in het menu van de thermostaat en probeer het opnieuw.")) from e
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
