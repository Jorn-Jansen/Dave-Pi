"""The AI (chat, with web search) and speech recognition (Whisper), on Groq's free tier, like Dave on the PC and iPhone."""

import json
import re
import time

from . import config, web

API = "https://api.groq.com/openai/v1"
MODEL = "openai/gpt-oss-120b"
FALLBACK = "openai/gpt-oss-20b"   # its own daily allowance, for when the big one's is used up
_fallback_until = 0
_search = True


class GroqError(Exception):
    pass


def _key():
    key = config.get("groq_key")
    if not key:
        raise GroqError(config.say("I need a Groq key first: sudo dave setup", "Ik heb eerst een Groq-sleutel nodig: sudo dave setup"))
    return key


def _wait(text):
    """Groq's "try again in 10m5.2s" / "in 2.5s" → seconds."""
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([0-9.]+)s)?", text)
    if not m or not any(m.groups()):
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)


def chat(messages, tools=None, search=True):
    """One chat turn; returns the assistant's message (dict, maybe with tool_calls)."""
    global _fallback_until, _search
    headers = {"Authorization": f"Bearer {_key()}"}
    for attempt in range(4):
        body = {"model": FALLBACK if time.time() < _fallback_until else MODEL, "messages": messages, "reasoning_effort": "low"}
        all_tools = list(tools or [])
        if search and _search:
            all_tools.append({"type": "browser_search"})
        if all_tools:
            body["tools"] = all_tools
        try:
            status, data = web.json_request("POST", API + "/chat/completions", body, headers, timeout=60)
        except OSError as e:
            raise GroqError(config.say("I can't reach the internet right now.", "Ik kan nu niet bij het internet.")) from e
        if 200 <= status < 300 and data:
            return data["choices"][0]["message"]
        text = json.dumps(data) if data else ""
        if status in (400, 403) and "browser_search" in text and _search:
            _search = False  # web search isn't allowed on this account: carry on without it
            continue
        if status == 400 and "tool_use_failed" in text and attempt < 3:  # a made-up or misspelled command: just ask again
            continue
        if status == 429:
            wait = _wait(text)
            daily = "per day" in text
            if wait is not None and wait <= 65 and not daily:  # the per-minute limit (8,000 tokens a minute on the free tier): just wait
                print(f"(Groq: per-minute limit, waiting {wait:.0f} s)", flush=True)
                time.sleep(wait + 0.5)
                continue
            if daily and body["model"] == MODEL:  # the day's allowance is used up: the smaller model for a while
                _fallback_until = time.time() + 3600
                continue
            print(f"(Groq: {(data or {}).get('error', {}).get('message', text)[:300]})", flush=True)
            minutes = max(1, round((wait or 60) / 60))
            if daily:
                raise GroqError(config.say(f"I've used up today's free AI allowance. In about {minutes} minutes I can do a bit again.",
                                           f"Mijn gratis AI-tegoed voor vandaag is op. Over zo'n {minutes} minuten kan ik weer wat."))
            raise GroqError(config.say(f"I'm asking the AI too fast. Try again in {minutes} minutes.", f"Ik vraag de AI te snel. Probeer het over {minutes} minuten nog eens."))
        if status == 413:  # one question bigger than the per-minute allowance (a long web search, a long conversation)
            print(f"(Groq: {(data or {}).get('error', {}).get('message', text)[:300]})", flush=True)
            raise GroqError(config.say("That got too long for the free AI. Try asking it shorter.", "Dat werd te lang voor de gratis AI. Vraag het eens korter."))
        if status == 401:
            raise GroqError(config.say("My Groq key doesn't work. Check it with: sudo dave setup", "Mijn Groq-sleutel werkt niet. Check hem met: sudo dave setup"))
        if attempt < 3 and status >= 500:
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
    try:
        status, data = web.json_request("POST", API + "/audio/transcriptions", body,
                                        {"Authorization": f"Bearer {_key()}", "Content-Type": kind}, timeout=30)
    except OSError as e:
        raise GroqError(config.say("I can't reach the internet right now.", "Ik kan nu niet bij het internet.")) from e
    if not (200 <= status < 300) or not data:
        raise GroqError(f"Whisper {status}")
    text = (data.get("text") or "").strip()
    # Whisper's classic made-up lines when it hears only noise
    if re.fullmatch(r"(?i)\W*(thank you( for watching)?|bedankt voor het kijken|ondertiteld door.*|you)\W*", text):
        return ""
    return text
