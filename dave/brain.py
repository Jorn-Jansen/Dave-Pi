"""
What Dave says and does: the AI with its commands (Hue, SmartThings, timers, volume), like the PC and iPhone Dave but
for the house. Several commands in one go ("turn off the lights and tell me when the washer is done") just work:
the AI calls them one after the other and then says what happened.
"""

import json
import re
import subprocess
import time

from . import config, groq, hue, smartthings, timers, tv

PERSONALITIES = {
    "normal": "",
    "chill": "Your personality: chill and laid-back. Talk casually, like a relaxed friend who's never in a hurry. React to what the user actually asked.",
    "hyped": "Your personality: HYPED. Super energetic and enthusiastic about the specific thing the user asked, with exclamation marks. Still give the real answer.",
    "butler": "Your personality: a refined, formal butler. Polite and elegant, address the user as 'sir' (in Dutch 'meneer'), with dry wit about what they asked.",
    "sarcastic": "Your personality: sarcastic, with dry humour. Tease the user a little about what they asked, never mean, and always actually do it.",
}

_history = []
_last = 0


def _tool(name, description, properties=None, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": list(required)}}}


def _s(description):
    return {"type": "string", "description": description}


TOOLS = [
    _tool("hue", "Philips Hue lights and plugs: see what there is and what's on ('which lights are on?'), or change them: on/off, brightness, "
          "colour, a scene ('turn the living room purple', 'lights off', 'dim the bedroom to 30%', 'start the relax scene').",
          {"action": {"type": "string", "enum": ["list", "set"]},
           "target": _s("A room, zone or light by name; empty or 'all' for all lights"),
           "on": {"type": "boolean"},
           "brightness": {"type": "integer", "description": "0-100"},
           "color": _s("A colour name (red, purple, warm white, …) or #rrggbb"),
           "scene": _s("A Hue scene's name, exactly as it's called (in its own language: don't translate it)")}, ["action"]),
    _tool("smartthings", "Samsung SmartThings devices (washer, dryer, TV, plugs…): 'list' what there is, 'status' of one ('is the washer done?', "
          "'how long does the dryer still take?'), or a 'command' ('turn off the TV': capability switch, command off; pause the washer: "
          "capability washerOperatingState, command setMachineState, arguments ['pause']; washers and dryers need remote control on).",
          {"action": {"type": "string", "enum": ["list", "status", "command"]},
           "device": _s("The device's name or kind (washer, dryer, tv)"),
           "capability": _s("For command: e.g. switch, washerOperatingState, dryerOperatingState, audioVolume"),
           "command": _s("For command: e.g. on, off, setMachineState, setVolume"),
           "arguments": {"type": "array", "items": {"type": "string"}, "description": "For command: its arguments, e.g. ['pause'] or ['20']"}}, ["action"]),
    _tool("tv", "The TV (Google TV / Android TV): turn it on or off, its volume ('TV louder': volume_up, value 3 = three steps), mute, "
          "what's on ('status'), open an app ('put Netflix on the TV': action app, value Netflix), or press a key (home, back, play_pause, ok, up…). " +
          "A specific series, film or video ('put Stranger Things on Netflix', 'play The Boys on Prime'): first look up its page on the web, then " +
          "action app with value = that link (netflix.com/title/<id>, primevideo.com/detail/…, youtube.com/watch?v=…, disneyplus.com/…) and play = true.",
          {"action": {"type": "string", "enum": ["on", "off", "status", "app", "volume_up", "volume_down", "mute", "home", "back", "play_pause",
                                                 "play", "pause", "next", "previous", "up", "down", "left", "right", "ok", "channel_up", "channel_down", "input", "settings"]},
           "value": _s("For app: the app's name (Netflix, YouTube, Disney+, Videoland…) or the link to a series/film/video; for volume: how many steps"),
           "play": {"type": "boolean", "description": "For a series/film/video link: start playing it"},
           "tv": _s("Which TV, by its name (see the list); empty = the default one. Only fill it in when the user said which.")}, ["action"]),
    _tool("timer", "Timers: set one ('a timer for 10 minutes for the pizza'), list them ('how long does my timer still take?') or cancel.",
          {"action": {"type": "string", "enum": ["set", "list", "cancel"]},
           "seconds": {"type": "number", "description": "For set: how long, in seconds"},
           "label": _s("What it's for (short), or which to cancel (empty = all)")}, ["action"]),
    _tool("volume", "This speaker's volume: louder, quieter or a level ('volume to 40%').",
          {"level": {"type": "integer", "description": "0-100"}, "change": {"type": "string", "enum": ["up", "down"]}}),
    _tool("set_personality", "Change how you talk: normal, chill, hyped, butler or sarcastic ('be a butler', 'act normal').",
          {"personality": {"type": "string", "enum": list(PERSONALITIES)}}, ["personality"]),
]


def _tools():
    """The commands, with the TVs there are in the TV command's description (so the AI picks the right one by name)."""
    tools = json.loads(json.dumps(TOOLS))
    tvs = tv.all_tvs()
    if len(tvs) > 1:
        for t in tools:
            if t["function"]["name"] == "tv":
                t["function"]["description"] += f" The TVs: {', '.join(tvs)} (default: {tv.default()})."
    return tools


def _system():
    name = config.get("name")
    language = "Dutch" if config.dutch() else "English"
    personality = PERSONALITIES.get(config.get("personality"), "")
    return (f"{personality}\n\nYou are {name}, a voice assistant on a smart speaker in the user's home (a Raspberry Pi), like a Google Home. "
            f"Answer in {language} unless the user speaks another language. Your answers are spoken: one or two short sentences, plain speech, "
            "no lists, no markdown, no emoji, no links. Use your commands for the lights (Hue), the TV, Samsung devices (SmartThings), timers and "
            "the volume; when the user asks for several things at once, do all of them. Look things up on the web for news, weather and facts. "
            f"It's {time.strftime('%A %d %B %Y, %H:%M')} now. Only end with a question when you really need an answer (then the speaker keeps " +
            "listening); never add filler questions like 'Anything else?' or 'Is er verder nog iets?', just stop after the answer.").strip()


def _run(name, args):
    """Carries out a command; returns what happened (for the AI to tell the user)."""
    try:
        if name == "hue":
            if args.get("action") == "list":
                return hue.describe()
            return hue.set_lights(args.get("target", ""), args.get("on"), args.get("brightness"), args.get("color", ""), args.get("scene", ""))
        if name == "smartthings":
            action = args.get("action")
            if action == "list":
                return smartthings.describe()
            device = smartthings.find(args.get("device", ""))
            if action == "status":
                return smartthings.status(device)
            arguments = [int(a) if isinstance(a, str) and a.lstrip("-").isdigit() else a for a in (args.get("arguments") or [])]  # (numbers as numbers)
            return smartthings.command(device, args.get("capability", "switch"), args.get("command", ""), arguments)
        if name == "tv":
            return tv.control(args.get("action", "status"), str(args.get("value") or ""), args.get("tv") or "", bool(args.get("play")))
        if name == "timer":
            action = args.get("action")
            if action == "set":
                return timers.add(float(args.get("seconds") or 60), args.get("label", ""))
            if action == "cancel":
                return f"cancelled {timers.cancel(args.get('label', ''))} timer(s)"
            return timers.describe()
        if name == "volume":
            return set_volume(args.get("level"), args.get("change"))
        if name == "set_personality":
            config.set("personality", args.get("personality", "normal"))
            return f"personality is now {config.get('personality')}"
    except (hue.HueError, smartthings.SmartThingsError, tv.TVError) as e:
        return f"Failed: {e}"
    except Exception as e:
        return f"Failed ({type(e).__name__}): {e}"
    return f"Unknown command {name}"


def set_volume(level=None, change=None):
    """The speaker's volume (ALSA mixer)."""
    current = get_volume()
    target = level if level is not None else (current + 15 if change == "up" else current - 15 if change == "down" else current)
    target = max(0, min(100, int(target)))
    for control in ("Master", "PCM", "Speaker", "Headphone"):
        if subprocess.run(["amixer", "-q", "sset", control, f"{target}%"], capture_output=True).returncode == 0:
            return f"volume {target}%"
    return "Failed: no volume control found"


def get_volume():
    for control in ("Master", "PCM", "Speaker", "Headphone"):
        out = subprocess.run(["amixer", "sget", control], capture_output=True, text=True).stdout
        m = re.search(r"\[(\d+)%\]", out)
        if m:
            return int(m.group(1))
    return 50


def ask(text, on_action=None):
    """Answers [text]: does what's asked (commands) and returns what to say. [on_action]: called with each command it runs."""
    global _history, _last
    if time.time() - _last > 600:  # follow-up questions work for 10 minutes
        _history = []
    _last = time.time()
    messages = [{"role": "system", "content": _system()}] + _history + [{"role": "user", "content": text}]
    for _ in range(5):  # commands, then the answer about them
        message = groq.chat(messages, _tools())
        calls = message.get("tool_calls") or []
        if not calls:
            answer = re.sub(r"\[\s*[a-zA-Z][a-zA-Z -]{1,20}?\s*\]\s*", "", (message.get("content") or "").strip())
            _history = (_history + [{"role": "user", "content": text}, {"role": "assistant", "content": answer}])[-12:]
            return answer or config.say("Done.", "Klaar.")
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            result = _run(name, args)
            if on_action:
                on_action(name, args, result)
            messages.append({"role": "tool", "tool_call_id": call.get("id", name), "content": str(result)[:3000]})
    return config.say("Sorry, that took too many steps.", "Sorry, dat werden te veel stappen.")
