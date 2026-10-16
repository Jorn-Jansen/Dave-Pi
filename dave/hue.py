"""Philips Hue: lights, plugs, rooms and scenes through the Hue Bridge in your home (local network, also without internet)."""

import re

from . import config, log, web

COLORS = {
    # Dutch and English names → RGB
    "rood": (255, 0, 0), "red": (255, 0, 0), "oranje": (255, 120, 0), "orange": (255, 120, 0), "geel": (255, 220, 0), "yellow": (255, 220, 0),
    "groen": (0, 255, 0), "green": (0, 255, 0), "blauw": (0, 60, 255), "blue": (0, 60, 255), "lichtblauw": (90, 180, 255), "light blue": (90, 180, 255),
    "cyaan": (0, 255, 255), "cyan": (0, 255, 255), "turquoise": (0, 220, 200), "paars": (150, 0, 255), "purple": (150, 0, 255),
    "violet": (140, 60, 255), "roze": (255, 80, 180), "pink": (255, 80, 180), "magenta": (255, 0, 255), "wit": (255, 255, 255), "white": (255, 255, 255),
}
WHITES = {"warm": 454, "warm wit": 454, "warm white": 454, "neutraal": 300, "neutral": 300, "koud": 200, "cool": 200, "koel": 200, "daglicht": 170, "daylight": 170}


class HueError(Exception):
    pass


def _base():
    bridge, user = config.get("hue_bridge"), config.get("hue_user")
    if not bridge or not user:
        raise HueError(config.say("Hue isn't set up yet: sudo dave hue setup", "Hue is nog niet ingesteld: sudo dave hue setup"))
    return f"{bridge}/api/{user}"


def _bridge_request(method, address, body=None):
    """To the bridge: https first (newer bridges want that; it has its own certificate), then plain http."""
    last = None
    for scheme in ("https", "http"):
        try:
            return web.json_request(method, f"{scheme}://{address}", body, timeout=8, insecure=True)
        except OSError as e:
            last = e
    host = address.split("/")[0]
    raise HueError(config.say(f"I can't reach the Hue Bridge at {host}. Check its address in the Hue app (Settings → Bridges → ⓘ) and run: sudo dave hue setup",
                              f"Ik kan de Hue Bridge op {host} niet bereiken. Check het adres in de Hue-app (Instellingen → Bridges → ⓘ) en doe: sudo dave hue setup")) from last


def _call(method, path, body=None):
    status, data = _bridge_request(method, _base() + path, body)
    if isinstance(data, list) and data and "error" in data[0]:
        raise HueError(data[0]["error"].get("description", "Hue error"))
    return data


def discover():
    """The bridges in your home (through Philips' discovery service)."""
    try:
        _, data = web.json_request("GET", "https://discovery.meethue.com/", timeout=8)
        return [b["internalipaddress"] for b in data or [] if b.get("internalipaddress")]
    except OSError:
        return []


def pair(bridge):
    """After pressing the bridge's round button: a username for Dave. Raises HueError when the button wasn't pressed."""
    _, data = _bridge_request("POST", f"{bridge}/api", {"devicetype": "dave#raspberrypi"})
    if isinstance(data, list) and data:
        if "success" in data[0]:
            return data[0]["success"]["username"]
        if data[0].get("error", {}).get("type") == 101:
            raise HueError(config.say("Press the round button on the Hue Bridge first, then try again within 30 seconds.",
                                      "Druk eerst op de ronde knop op de Hue Bridge, en probeer het daarna binnen 30 seconden opnieuw."))
    raise HueError(f"Unexpected answer from the bridge: {data}")


def lights():
    return _call("GET", "/lights") or {}


def groups():
    return _call("GET", "/groups") or {}


def scenes():
    return _call("GET", "/scenes") or {}


def describe():
    """Everything there is, for the AI and for `dave hue list`: rooms with their lights, on/off and brightness."""
    all_lights, all_groups = lights(), groups()
    lines, seen = [], set()
    for gid, g in all_groups.items():
        if g.get("type") not in ("Room", "Zone"):
            continue
        members = []
        for lid in g.get("lights", []):
            light = all_lights.get(lid)
            if not light:
                continue
            seen.add(lid)
            members.append(_light_line(light))
        lines.append(f"{g['type']} \"{g['name']}\": " + (", ".join(members) or "(empty)"))
    rest = [_light_line(l) for lid, l in all_lights.items() if lid not in seen]
    if rest:
        lines.append("Not in a room: " + ", ".join(rest))
    names = sorted({s.get("name", "") for s in scenes().values() if s.get("name")})
    if names:
        lines.append("Scenes: " + ", ".join(names[:40]))
    return "\n".join(lines) or "No Hue lights found."


def _light_line(light):
    state = light.get("state", {})
    if not state.get("reachable", True):
        return f"\"{light['name']}\" (unreachable)"
    if not state.get("on"):
        return f"\"{light['name']}\" off"
    bri = state.get("bri")
    return f"\"{light['name']}\" on" + (f" {round(bri / 254 * 100)}%" if bri is not None else "")


def _simple(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _targets(target):
    """Which lights: 'all', a room or zone, or one light (by name, roughly). Returns [(kind, id, name)]."""
    want = _simple(target or "")
    if want in ("", "all", "alle", "alles", "allelampen", "alllights", "everything", "overal", "huis", "hethuis", "house"):
        return [("group", "0", config.say("all lights", "alle lampen"))]
    all_groups, all_lights = groups(), lights()
    for kind, items in (("group", all_groups), ("light", all_lights)):
        for i, item in items.items():
            if _simple(item.get("name", "")) == want:
                return [(kind, i, item["name"])]
    for kind, items in (("group", all_groups), ("light", all_lights)):
        found = [(kind, i, item["name"]) for i, item in items.items()
                 if want and (want in _simple(item.get("name", "")) or _simple(item.get("name", "")) in want)]
        if found:
            return found
    known = ", ".join([g["name"] for g in all_groups.values() if g.get("type") in ("Room", "Zone")] + [l["name"] for l in all_lights.values()])
    raise HueError(config.say(f"I don't know a Hue light or room called {target}. There are: {known}.", f"Ik ken geen Hue-lamp of kamer die {target} heet. Er zijn: {known}."))


def _xy(rgb):
    """RGB → the CIE xy colour Hue uses."""
    r, g, b = [c / 255 for c in rgb]
    r, g, b = [((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92 for c in (r, g, b)]
    x = r * 0.664511 + g * 0.154324 + b * 0.162028
    y = r * 0.283881 + g * 0.668433 + b * 0.047685
    z = r * 0.000088 + g * 0.072310 + b * 0.986039
    total = x + y + z
    return [round(x / total, 4), round(y / total, 4)] if total else [0.3227, 0.329]


def _color(name):
    """A colour by name or #hex: a state change for Hue."""
    text = (name or "").strip().lower()
    if text in WHITES:
        return {"ct": WHITES[text]}
    if text in COLORS:
        return {"xy": _xy(COLORS[text])}
    m = re.fullmatch(r"#?([0-9a-f]{6})", text)
    if m:
        h = m.group(1)
        return {"xy": _xy((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))}
    raise HueError(config.say(f"I don't know the colour {name}.", f"De kleur {name} ken ik niet."))


def set_lights(target="", on=None, brightness=None, color="", scene=""):
    """Turn lights on/off, set brightness (0-100) or a colour, or start a scene. Returns what was done."""
    if scene:
        want = _simple(scene)
        found = [(sid, s) for sid, s in scenes().items() if want and (want in _simple(s.get("name", "")))]
        if not found:
            known = ", ".join(sorted({s.get("name", "") for s in scenes().values() if s.get("name")}))
            raise HueError(config.say(f"I don't have a scene called {scene}. The scenes are: {known}.", f"Ik heb geen scène die {scene} heet. De scènes zijn: {known}."))
        sid, s = found[0]
        _call("PUT", "/groups/" + (s.get("group") or "0") + "/action", {"scene": sid})
        log.write(f"Hue: scène {s.get('name')}")
        return config.say(f"Scene {s['name']} is on.", f"Scène {s['name']} staat aan.")
    state = {}
    if on is not None:
        state["on"] = bool(on)
    if brightness is not None:
        b = max(0, min(100, int(brightness)))
        if b == 0:
            state["on"] = False
        else:
            state["on"] = True
            state["bri"] = max(1, round(b / 100 * 254))
    if color:
        state.update(_color(color))
        state["on"] = True
    if not state:
        raise HueError(config.say("What should I do with the lights?", "Wat moet ik met de lampen doen?"))
    names = []
    for kind, i, name in _targets(target):
        _call("PUT", f"/groups/{i}/action" if kind == "group" else f"/lights/{i}/state", state)
        names.append(name)
    log.write(f"Hue: {', '.join(names)} → {state}")
    return ", ".join(names) + ": " + ", ".join(f"{k}={v}" for k, v in state.items())
