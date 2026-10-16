"""The AI (chat, with web search) and speech recognition (Whisper), on Groq's free tier, like Dave on the PC and iPhone."""

import json
import re
import time

from . import config, web

API = "https://api.groq.com/openai/v1"
MODEL = "openai/gpt-oss-120b"
FALLBACK = "openai/gpt-oss-20b"   # its own daily allowance, for when the big one's is used up
_search = True
_limited = {}  # (key, model) → until when that key is at a limit for that model (then the next key in line is used)


class GroqError(Exception):
    pass


def keys():
    """The Groq keys in their order: the main one (groq_key), then the extra ones (groq_keys). Each question starts with the
    first; one at a limit (per minute, per day, any) is skipped until its limit is over, and the next one in line answers."""
    out = []
    for key in [config.get("groq_key")] + list(config.get("groq_keys") or []):
        key = (key or "").strip()
        if key and key not in out:
            out.append(key)
    if not out:
        raise GroqError(config.say("I need a Groq key first: sudo dave setup", "Ik heb eerst een Groq-sleutel nodig: sudo dave setup"))
    return out


def set_keys(new):
    """Saves the keys in this order (the first is the main one)."""
    new = [k.strip() for k in new if k and k.strip()]
    config.set("groq_key", new[0] if new else "")
    config.set("groq_keys", new[1:])


def _free(model):
    now = time.time()
    return [k for k in keys() if _limited.get((k, model), 0) <= now]


def _number(key):
    return keys().index(key) + 1 if key in keys() else "?"


def _note_limit(key, model, text):
    """[key] is at a limit for [model]: skipped until Groq says it's over. Returns (daily, seconds)."""
    wait = _wait(text)
    daily = "per day" in text
    seconds = wait if wait is not None else (3600 if daily else 60)
    _limited[(key, model)] = time.time() + seconds
    more = len(_free(model)) > 0
    print(f"(Groq key {_number(key)}: {'daily' if daily else 'per-minute'} limit for {model.split('/')[-1]}, {seconds:.0f} s"
          + ("; the next key" if more else "") + ")", flush=True)
    return daily, seconds


def _wait(text):
    """Groq's "try again in 10m5.2s" / "in 2.5s" → seconds."""
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m(?!s))?(?:([0-9.]+)(ms|s))?", text)
    if not m or not any(m.groups()):
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0) / (1000 if m.group(4) == "ms" else 1)


def chat(messages, tools=None, search=True):
    """One chat turn; returns the assistant's message (dict, maybe with tool_calls). Goes down the keys when one is at a limit;
    when they all are: a short wait for the big model, else the smaller one (its own allowance), else says how long it takes."""
    global _search
    daily_seen = False
    for attempt in range(12):
        model, key = None, None
        soonest_big = min([_limited.get((k, MODEL), 0) for k in keys()]) - time.time()
        for m in (MODEL, FALLBACK):
            free = _free(m)
            if m == FALLBACK and 0 < soonest_big <= 10:
                break  # (the big model is back in a few seconds: rather wait for it than use the smaller one)
            if free:
                model, key = m, free[0]
                break
        if key is None:
            soonest = min(_limited.values()) - time.time() if _limited else 0
            if soonest <= 65 and attempt < 11:
                print(f"(Groq: all keys at a limit, waiting {max(0, soonest):.0f} s)", flush=True)
                time.sleep(max(0, soonest) + 0.5)
                continue
            minutes = max(1, round(soonest / 60))
            if daily_seen:
                raise GroqError(config.say(f"I've used up today's free AI allowance on every key. In about {minutes} minutes I can do a bit again.",
                                           f"Mijn gratis AI-tegoed voor vandaag is op, op elke sleutel. Over zo'n {minutes} minuten kan ik weer wat."))
            raise GroqError(config.say(f"I'm asking the AI too fast. Try again in {minutes} minutes.", f"Ik vraag de AI te snel. Probeer het over {minutes} minuten nog eens."))
        body = {"model": model, "messages": messages, "reasoning_effort": "low"}
        all_tools = list(tools or [])
        if search and _search:
            all_tools.append({"type": "browser_search"})
        if all_tools:
            body["tools"] = all_tools
        try:
            status, data = web.json_request("POST", API + "/chat/completions", body, {"Authorization": f"Bearer {key}"}, timeout=60)
        except OSError as e:
            raise GroqError(config.say("I can't reach the internet right now.", "Ik kan nu niet bij het internet.")) from e
        if 200 <= status < 300 and data:
            return data["choices"][0]["message"]
        text = json.dumps(data) if data else ""
        if status in (400, 403) and "browser_search" in text and _search:
            _search = False  # web search isn't allowed on this account: carry on without it
            continue
        if status == 400 and "tool_use_failed" in text and attempt < 6:  # a made-up or misspelled command: just ask again
            continue
        if status == 429:
            daily, _ = _note_limit(key, model, text)
            daily_seen = daily_seen or daily
            continue
        if status == 413:  # one question bigger than the per-minute allowance (a long web search, a long conversation)
            print(f"(Groq: {(data or {}).get('error', {}).get('message', text)[:300]})", flush=True)
            raise GroqError(config.say("That got too long for the free AI. Try asking it shorter.", "Dat werd te lang voor de gratis AI. Vraag het eens korter."))
        if status == 401:
            print(f"(Groq key {_number(key)} doesn't work: skipped)", flush=True)
            for m in (MODEL, FALLBACK, "whisper"):
                _limited[(key, m)] = time.time() + 3600
            if _free(MODEL) or _free(FALLBACK):
                continue
            raise GroqError(config.say("My Groq key doesn't work. Check it with: sudo dave groq", "Mijn Groq-sleutel werkt niet. Check hem met: sudo dave groq"))
        if attempt < 11 and status >= 500:
            time.sleep(1.5)
            continue
        raise GroqError(f"Groq {status}: {text[:200]}")
    raise GroqError(config.say("The AI didn't answer.", "De AI gaf geen antwoord."))


def transcribe(wav, language=None):
    """What was said in [wav] (bytes)."""
    body, kind = web.multipart(
        {"model": "whisper-large-v3-turbo", "response_format": "json",
         "language": (language or config.get("language"))[:2],
         "prompt": f"{config.get('name')}, zet de lampen aan. {config.get('name')}, what's the weather tomorrow?"},
        {"file": ("speech.wav", wav, "audio/wav")})
    status, data = 0, None
    for key in _free("whisper") or keys()[:1]:
        try:
            status, data = web.json_request("POST", API + "/audio/transcriptions", body,
                                            {"Authorization": f"Bearer {key}", "Content-Type": kind}, timeout=30)
        except OSError as e:
            raise GroqError(config.say("I can't reach the internet right now.", "Ik kan nu niet bij het internet.")) from e
        if status == 429:
            _note_limit(key, "whisper", json.dumps(data) if data else "")
            continue
        break
    if not (200 <= status < 300) or not data:
        raise GroqError(f"Whisper {status}")
    text = (data.get("text") or "").strip()
    # Whisper's classic made-up lines when it hears only noise
    if re.fullmatch(r"(?i)\W*(thank you( for watching)?|bedankt voor het kijken|ondertiteld door.*|you)\W*", text):
        return ""
    return text
