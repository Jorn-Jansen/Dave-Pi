"""
Dave's voice: Azure (natural, online) when there's a key; otherwise Piper (offline, a good voice) when it's installed;
otherwise espeak-ng (offline, robotic, always there). Played through ALSA (aplay).
"""

import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import wave
from xml.sax.saxutils import escape

from . import config, web

_playing = None
_lock = threading.Lock()

_LINK = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍⃣]")


def _speakable(text):
    """No links (the site's name instead) and no emoji: those would be read out letter by letter or as 'rocket'."""
    def short(m):
        host = re.sub(r"^(https?://)?(www\.)?", "", m.group(0)).split("/")[0]
        parts = host.split(".")
        return (parts[-2] if len(parts) >= 2 else host).capitalize()
    text = _LINK.sub(short, text)
    text = _EMOJI.sub("", text)
    text = re.sub(r"\[\s*[a-zA-Z][a-zA-Z -]{1,20}?\s*\]", "", text)  # mood tags like [excited]
    return re.sub(r"\*\*|__|`|#+ ", "", text).strip()


PIPER = "/opt/dave/piper/piper"
VOICES = "/opt/dave/voices"
# Offline voices (Piper): name → its file on Hugging Face (rhasspy/piper-voices)
PIPER_VOICES = {
    "pim": "nl/nl_NL/pim/medium/nl_NL-pim-medium", "ronnie": "nl/nl_NL/ronnie/medium/nl_NL-ronnie-medium",
    "mls": "nl/nl_NL/mls/medium/nl_NL-mls-medium", "nathalie": "nl/nl_BE/nathalie/medium/nl_BE-nathalie-medium",
    "ryan": "en/en_US/ryan/medium/en_US-ryan-medium", "amy": "en/en_US/amy/medium/en_US-amy-medium",
}


def _piper_binary():
    return PIPER if os.path.exists(PIPER) else shutil.which("piper")


def engine():
    """Which voice speaks: the setting voice_engine (auto = Azure when there's a key; offline = Piper, or espeak without it)."""
    choice = (config.get("voice_engine") or "auto").lower()
    piper = bool(config.get("piper_model") and os.path.exists(config.get("piper_model")) and _piper_binary())
    if choice in ("auto", "azure") and config.get("azure_key"):
        return "azure"
    if choice == "espeak":
        return "espeak"
    return "piper" if piper else "espeak"


def install_piper(name):
    """Downloads Piper (offline speech) and the voice [name] into /opt/dave. Returns the voice file."""
    import tarfile
    if name not in PIPER_VOICES:
        raise ValueError(f"Unknown voice {name}; choose from: {', '.join(PIPER_VOICES)}")
    if not os.path.exists(PIPER):
        print("Piper downloaden (± 25 MB)…", flush=True)
        status, data = web.request("GET", "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz", timeout=300)
        if status != 200:
            raise OSError(f"Piper download failed ({status})")
        archive = "/tmp/piper.tar.gz"
        with open(archive, "wb") as f:
            f.write(data)
        with tarfile.open(archive) as t:
            t.extractall("/opt/dave")  # (a folder "piper" with the program)
        os.unlink(archive)
    os.makedirs(VOICES, exist_ok=True)
    path = os.path.join(VOICES, os.path.basename(PIPER_VOICES[name]) + ".onnx")
    if not os.path.exists(path):
        print(f"Stem {name} downloaden (± 60 MB)…", flush=True)
        for ext in (".onnx", ".onnx.json"):
            status, data = web.request("GET", f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{PIPER_VOICES[name]}{ext}", timeout=600)
            if status != 200:
                raise OSError(f"Voice download failed ({status})")
            with open(path.replace(".onnx", ext), "wb") as f:
                f.write(data)
    return path


def speak(text):
    """Says [text]; returns when it's done (or stopped)."""
    text = _speakable(text)
    if not text:
        return
    wav = None
    kind = engine()
    try:
        if kind == "azure":
            wav = _azure(text)
        elif kind == "piper":
            wav = _piper(text)
    except Exception as e:
        print(f"(voice {kind} failed: {e}; using espeak)")
    if wav is None:
        wav = _espeak(text)
    play(wav)


def _azure(text):
    language = config.get("language")
    voice = config.get("azure_voice")
    ssml = (f"<speak version='1.0' xml:lang='{language}'><voice name='{voice}'>"
            f"<prosody rate='+0%'>{escape(text)}</prosody></voice></speak>")
    status, data = web.request("POST", f"https://{config.get('azure_region')}.tts.speech.microsoft.com/cognitiveservices/v1",
                               ssml.encode("utf-8"), {"Ocp-Apim-Subscription-Key": config.get("azure_key"),
                                                      "Content-Type": "application/ssml+xml",
                                                      "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
                                                      "User-Agent": "DavePi"}, timeout=15)
    if status != 200:
        raise RuntimeError(f"Azure {status}")
    return data


def _piper(text):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out = f.name
    try:
        subprocess.run([_piper_binary(), "--model", config.get("piper_model"), "--output_file", out], input=text.encode("utf-8"),
                       check=True, capture_output=True, timeout=60)
        with open(out, "rb") as f:
            return f.read()
    finally:
        os.unlink(out)


def _espeak(text):
    voice = "nl" if config.dutch() else "en-us"
    result = subprocess.run(["espeak-ng", "-v", voice, "-s", "165", "--stdout", text], capture_output=True, timeout=60)
    return result.stdout


def play(wav):
    """Plays WAV bytes on the speaker; stop() ends it early."""
    global _playing
    if not wav:
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav)
        path = f.name
    try:
        with _lock:
            _playing = subprocess.Popen(["aplay", "-q", "-D", config.get("speaker_device"), path],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _, err = _playing.communicate()
        if _playing.returncode not in (0, -15) and err:
            print(f"(aplay: {err.decode(errors='ignore').strip()})")
    finally:
        with _lock:
            _playing = None
        os.unlink(path)


def stop():
    with _lock:
        if _playing and _playing.poll() is None:
            _playing.terminate()


def tone(kind="listen"):
    """A short sound: 'listen' (rising, Dave is listening), 'heard' (falling), 'done' or 'alarm'."""
    notes = {"listen": [(660, .07), (990, .09)], "heard": [(880, .07), (587, .09)], "done": [(784, .08)],
             "alarm": [(880, .15), (0, .08), (880, .15), (0, .08), (880, .3)]}[kind]
    rate, frames = 22050, b""
    for freq, length in notes:
        n = int(rate * length)
        for i in range(n):
            fade = min(1, i / 300, (n - i) / 300)
            sample = 0 if freq == 0 else math.sin(2 * math.pi * freq * i / rate) * 0.35 * fade
            frames += struct.pack("<h", int(sample * 32767))
    path = tempfile.mktemp(suffix=".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(frames)
    with open(path, "rb") as f:
        data = f.read()
    os.unlink(path)
    play(data)
