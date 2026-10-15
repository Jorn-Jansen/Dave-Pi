"""
The microphone: always listening for the wake phrase ("hey dave"), offline with Vosk (nothing leaves the Pi until it
hears it), then recording what you say until you stop talking.
"""

import array
import io
import json
import math
import os
import subprocess
import time
import wave

from . import config

RATE = 16000
CHUNK = 1600  # 0.1 s
MODEL = os.environ.get("DAVE_VOSK_MODEL", "/opt/dave/models/vosk-model-small-en-us-0.15")


class Mic:
    """The microphone as a stream of 0.1 s pieces (16 kHz, 16-bit mono), through arecord."""

    def __init__(self):
        self.process = subprocess.Popen(["arecord", "-q", "-D", config.get("mic_device"), "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def read(self):
        data = self.process.stdout.read(CHUNK * 2)
        if not data:
            err = self.process.stderr.read().decode(errors="ignore").strip()
            raise OSError(err or config.say("The microphone stopped.", "De microfoon is gestopt."))
        return data

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()


def level(chunk):
    """How loud a piece is (RMS, 0..32768)."""
    samples = array.array("h", chunk)
    return math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0


class WakeWord:
    """Hears the wake phrase in the stream (Vosk, offline, only listening for those words)."""

    def __init__(self, phrase):
        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        if not os.path.isdir(MODEL):
            raise OSError(f"The wake word model isn't there ({MODEL}): run sudo ./install.sh again")
        self.phrase = phrase.lower().strip()
        words = self.phrase.split()
        self.recognizer = KaldiRecognizer(Model(MODEL), RATE, json.dumps([self.phrase, " ".join(words[1:]) if len(words) > 1 else self.phrase, "[unk]"]))

    def heard(self, chunk):
        if self.recognizer.AcceptWaveform(chunk):
            text = json.loads(self.recognizer.Result()).get("text", "")
        else:
            text = json.loads(self.recognizer.PartialResult()).get("partial", "")
        if self.phrase in text:
            self.recognizer.Reset()
            return True
        return False


def record(mic, max_seconds=12, silence=0.9):
    """What's said after the wake word, until a moment of silence. Returns WAV bytes (None when nothing was said)."""
    pieces, noise = [], []
    started, quiet_since, start = False, None, time.time()
    sensitivity = float(config.get("mic_sensitivity") or 1)
    while time.time() - start < max_seconds:
        chunk = mic.read()
        pieces.append(chunk)
        loud = level(chunk)
        if len(pieces) <= 3:  # the first 0.3 s: how loud the room is
            noise.append(loud)
            continue
        threshold = max(350 / sensitivity, (sum(noise) / len(noise)) * 2.2 / sensitivity)
        if loud > threshold:
            started, quiet_since = True, None
        elif started:
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since > silence:
                break
        elif time.time() - start > 5:  # nothing said within 5 seconds
            return None
    if not started:
        return None
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(b"".join(pieces))
    return buffer.getvalue()
