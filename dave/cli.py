"""
`dave`: the speaker itself (`dave run`, what the service runs) and everything to set up and test it from the terminal:

  sudo dave setup                      the basics: Groq key, name, language, voice
  sudo dave ask "zet de lampen op paars" ask something (typed instead of spoken), and hear the answer
  sudo dave chat                       keep typing questions (Ctrl+C to stop)
  sudo dave say "hallo"                just say something (tests the voice)
  sudo dave voice offline | azure | auto | install [pim|ronnie|mls|nathalie]   which voice
  sudo dave hue setup | list           connect the Hue Bridge / show lights and rooms
  sudo dave smartthings token <token>  connect SmartThings with a personal token (valid 24 hours)
  sudo dave smartthings login          connect SmartThings for good (OAuth)
  sudo dave smartthings list | status <device>
  sudo dave tv setup [naam] | list | default <naam> | remove <naam>   TVs (Google TV / Android TV), each paired once
  sudo dave tv on|off|status [naam]    and  sudo dave tv app netflix --tv slaapkamer
  sudo dave tv app https://www.netflix.com/title/80057281   a series or film itself
  sudo dave tv app netflix | apps | add-app "Ziggo GO" com.ziggo.tv
  sudo dave test-speaker | test-mic    check the sound
  sudo dave memory [list] | add "…" | remove <nr or words> | clear   what Dave remembers for good
  sudo dave status                     what's set up, and the service
  sudo dave update                     get the newest Dave from GitHub and install it
  sudo dave config [key] [value]       see or change one setting
"""

import getpass
import os
import subprocess
import sys
import threading
import time

from . import brain, config, groq, hue, listen, smartthings, timers, tv, voice


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    command, rest = args[0], args[1:]
    handlers = {"run": run, "ask": ask, "chat": chat, "say": say, "setup": setup, "hue": hue_command, "smartthings": smartthings_command, "tv": tv_command,
                "update": update, "voice": voice_command, "memory": memory_command, "test-speaker": test_speaker, "test-mic": test_mic, "status": status, "config": config_command}
    if command not in handlers:
        print(f"Onbekend commando: {command}\n{__doc__}")
        return 1
    if command not in ("run",) and os.geteuid() != 0 and not os.access(config.PATH, os.W_OK):
        print("Tip: gebruik sudo (de instellingen staan in /etc/dave, alleen voor root).")
    try:
        return handlers[command](rest) or 0
    except KeyboardInterrupt:
        print()
        return 130
    except (groq.GroqError, hue.HueError, smartthings.SmartThingsError, tv.TVError) as e:
        print(f"⚠ {e}")
        return 1
    except OSError as e:  # no connection, a missing program…: a short line, not a wall of text
        print(f"⚠ {getattr(e, 'reason', None) or e}")
        return 1


# --- The speaker ---

def run(_):
    """Always listening for the wake phrase, then: listen, think, answer. What the service runs."""
    timers.on_done = lambda message: (voice.tone("alarm"), voice.speak(message))
    phrase = config.get("wake_phrase") or f"hey {config.get('name').lower()}"
    print(f"{config.get('name')} op de Pi draait. Luistert naar \"{phrase}\".", flush=True)
    if not config.get("groq_key"):
        print("⚠ Nog geen Groq-sleutel: sudo dave setup", flush=True)
    wake = None
    while True:
        mic = None
        try:
            wake = wake or listen.WakeWord(phrase)
            mic = listen.Mic()
            while True:
                if not wake.heard(mic.read()):
                    continue
                print("· wekwoord gehoord", flush=True)
                voice.tone("listen")
                follow_up = True
                while follow_up:  # a question back ("which room?") keeps listening, without the wake word
                    audio = listen.record(mic)
                    if not audio:
                        voice.tone("done")
                        break
                    voice.tone("heard")
                    text = groq.transcribe(audio)
                    print(f"jij: {text}", flush=True)
                    if not text:
                        break
                    answer = brain.ask(text, on_action=lambda n, a, r: print(f"  ({n}) {r}", flush=True))
                    print(f"{config.get('name')}: {answer}", flush=True)
                    mic.close()  # (not hearing itself)
                    voice.speak(answer)
                    mic = listen.Mic()
                    follow_up = answer.rstrip().endswith("?")
                    if follow_up:
                        voice.tone("listen")
        except KeyboardInterrupt:
            raise
        except (groq.GroqError, hue.HueError, smartthings.SmartThingsError) as e:
            print(f"⚠ {e}", flush=True)
            try:
                voice.speak(str(e))
            except Exception:
                pass
        except Exception as e:
            print(f"⚠ {type(e).__name__}: {e} (opnieuw over 15 s)", flush=True)
            time.sleep(15)
        finally:
            if mic:
                mic.close()


def ask(rest):
    quiet = "--quiet" in rest
    text = " ".join(r for r in rest if r != "--quiet").strip()
    if not text:
        print('Gebruik: sudo dave ask "zet de woonkamer op paars" (--quiet: niet uitspreken)')
        return 1
    timers.on_done = lambda message: (print(f"⏰ {message}"), None if quiet else voice.speak(message))
    answer = brain.ask(text, on_action=lambda n, a, r: print(f"  ({n}) {r}"))
    print(f"{config.get('name')}: {answer}")
    if not quiet:
        voice.speak(answer)
    _wait_for_timers()


def chat(_):
    print(f"Typ je vraag aan {config.get('name')} (Ctrl+C om te stoppen; begin met '-' om het antwoord niet uit te spreken).")
    timers.on_done = lambda message: (print(f"\n⏰ {message}"), voice.speak(message))
    while True:
        try:
            text = input("jij: ").strip()
        except EOFError:
            return
        if not text:
            continue
        quiet = text.startswith("-")
        answer = brain.ask(text.lstrip("-").strip(), on_action=lambda n, a, r: print(f"  ({n}) {r}"))
        print(f"{config.get('name')}: {answer}")
        if not quiet:
            threading.Thread(target=voice.speak, args=(answer,), daemon=True).start()


def _wait_for_timers():
    """`dave ask "timer van 10 seconden"`: wait for short timers so you can hear them go off."""
    from . import timers as t
    with t._lock:
        soon = [x["at"] for x in t._timers.values() if x["at"] - time.time() < 120]
    if soon:
        print(f"(wacht {round(max(soon) - time.time())} s op de timer…)")
        time.sleep(max(0, max(soon) - time.time()) + 4)


def say(rest):
    text = " ".join(rest) or config.say("Hi, I'm Dave on your Raspberry Pi.", "Hoi, ik ben Dave op je Raspberry Pi.")
    print(f"({voice.engine()}) {text}")
    voice.speak(text)


# --- Setting up ---

def _ask_setting(key, prompt, secret=False):
    current = config.get(key)
    hint = f" [{config.shown(key, current)}]" if current else ""
    value = (getpass.getpass if secret else input)(f"{prompt}{hint}: ").strip()
    if value:
        config.set(key, value)


def setup(_):
    print("Dave op de Pi instellen. Enter = zo laten.\n")
    print("1. Groq-sleutel (gratis): ga naar console.groq.com/keys, log in, klik op Create API Key en plak hem hier.")
    _ask_setting("groq_key", "   Groq-sleutel", secret=True)
    _ask_setting("name", "2. Naam")
    _ask_setting("language", "3. Taal (nl-NL of en-US)")
    config.set("wake_phrase", config.get("wake_phrase") or "hey dave")
    _ask_setting("wake_phrase", "4. Wekwoord (Engelse woorden werken het best, bv. hey dave)")
    print("5. Stem: met een Azure-sleutel klinkt hij natuurlijk (portal.azure.com → Speech, gratis 500.000 tekens per maand). Leeg = offline stem.")
    _ask_setting("azure_key", "   Azure-sleutel", secret=True)
    if config.get("azure_key"):
        _ask_setting("azure_region", "   Azure-regio")
        _ask_setting("azure_voice", "   Azure-stem (bv. nl-NL-FennaNeural, nl-NL-MaartenNeural)")
    print("\nKlaar. Test met: sudo dave say  en  sudo dave ask \"hoe laat is het?\"")
    print("Lampen: sudo dave hue setup   ·   Samsung: sudo dave smartthings token <token>")
    print("Starten (ook na herstart): sudo systemctl enable --now dave")


def hue_command(rest):
    sub = rest[0] if rest else "list"
    if sub == "setup":
        bridges = hue.discover()
        bridge = bridges[0] if bridges else ""
        typed = input(f"IP-adres van de Hue Bridge [{bridge or 'niet gevonden, typ hem'}]: ").strip()
        bridge = typed or bridge
        if not bridge:
            print("Geen bridge. Zie het IP-adres in de Hue-app: Instellingen → Bridges → (i).")
            return 1
        input("Druk nu op de ronde knop bovenop de Hue Bridge, en daarna hier op Enter…")
        user = hue.pair(bridge)
        config.load().update({"hue_bridge": bridge, "hue_user": user}); config.save()
        print("✅ Gekoppeld met de Hue Bridge.\n")
        print(hue.describe())
    elif sub == "list":
        print(hue.describe())
    else:
        print("Gebruik: sudo dave hue setup | list")
        return 1


def smartthings_command(rest):
    sub = rest[0] if rest else "list"
    if sub == "token":
        token = rest[1] if len(rest) > 1 else getpass.getpass("SmartThings-token (account.smartthings.com/tokens): ").strip()
        config.set("smartthings_token", token)
        print("Token opgeslagen (Samsung laat deze na 24 uur verlopen; voor altijd: sudo dave smartthings login).\n")
        print(smartthings.describe())
    elif sub == "login":
        print("Eenmalig: een eigen SmartThings-app aanmaken, zodat Dave voor altijd ingelogd blijft.")
        print("  a) Installeer de SmartThings CLI op een computer (of hier): sudo apt install -y nodejs npm && sudo npm i -g @smartthings/cli")
        print("  b) Run: smartthings apps:create   → kies 'OAuth-In App', een naam, scopes r:devices:* x:devices:* r:locations:*")
        print(f"     en als Redirect URI: {config.get('smartthings_redirect')}")
        print("  c) Je krijgt een OAuth Client Id en Client Secret: vul ze hieronder in.\n")
        _ask_setting("smartthings_client_id", "Client Id")
        _ask_setting("smartthings_client_secret", "Client Secret", secret=True)
        print(f"\nOpen deze link, log in met je Samsung-account en kies je locatie:\n\n  {smartthings.login_url()}\n")
        print("Je komt daarna op een pagina waarvan het adres '?code=XXXX' bevat (bij httpbin staat hij ook bij \"args\").")
        smartthings.finish_login(input("Plak de code (of het hele adres): ").strip().split("code=")[-1].split("&")[0].strip('"'))
        print("✅ SmartThings is gekoppeld (Dave vernieuwt zijn toegang zelf).\n")
        print(smartthings.describe())
    elif sub == "list":
        print(smartthings.describe())
    elif sub == "status":
        print(smartthings.status(smartthings.find(" ".join(rest[1:]))))
    else:
        print("Gebruik: sudo dave smartthings token <token> | login | list | status <apparaat>")
        return 1


def tv_command(rest):
    # Which TV: "--tv slaapkamer" anywhere (else the default one)
    which = ""
    if "--tv" in rest:
        i = rest.index("--tv")
        which = " ".join(rest[i + 1:i + 2])
        rest = rest[:i] + rest[i + 2:]
    sub = rest[0] if rest else "list"
    if sub == "setup":
        print("Zet de tv aan. Het IP-adres staat op de tv bij Instellingen → Netwerk en internet → (je wifi) of in je router.")
        print("Let op: bij een kastje eronder (KPN, Ziggo) het adres van de tv of Chromecast zelf, niet dat van het kastje.")
        print("Tip: zet op de tv ook 'Inschakelen via wifi/netwerk' (of 'Snel starten') aan, zodat Dave hem uit standby kan halen.")
        name = (" ".join(rest[1:]) or input("Hoe heet deze tv? (bv. woonkamer, slaapkamer): ")).strip().lower() or "tv"
        known = tv.all_tvs().get(name, {})
        host = input(f"IP-adres van de tv [{known.get('host') or 'bv. 192.168.68.60'}]: ").strip() or known.get("host")
        if not host:
            return 1
        model = tv.setup(name, host, lambda: input("Er staat nu een code op het scherm. Typ hem hier: "))
        print(f"✅ {name} gekoppeld ({model}).")
        print(tv.describe())
        print(f"Probeer: sudo dave tv off {name}   ·   standaard-tv kiezen: sudo dave tv default <naam>")
    elif sub == "list":
        print(tv.describe())
    elif sub == "default":
        if len(rest) < 2:
            print(f"Standaard-tv: {tv.default() or '(geen)'}. Veranderen: sudo dave tv default <naam>")
        else:
            print(f"⭐ {tv.set_default(' '.join(rest[1:]))} is nu de standaard-tv.")
    elif sub == "remove":
        print(f"{tv.remove(' '.join(rest[1:]))} is verwijderd.")
        print(tv.describe())
    elif sub in ("on", "off", "status"):
        print(tv.control(sub, "", which or " ".join(rest[1:])))
    elif sub == "add-app":
        if len(rest) < 3:
            print('Gebruik: sudo dave tv add-app "Ziggo GO" com.ziggo.tv')
            print("De pakketnaam vind je in de Google Play-link van de app: play.google.com/store/apps/details?id=<pakketnaam>")
            return 1
        apps = dict(config.get("tv_apps") or {})
        apps[" ".join(rest[1:-1])] = rest[-1]
        config.set("tv_apps", apps)
        print(f"✅ {' '.join(rest[1:-1])} toegevoegd. Probeer: sudo dave tv app {' '.join(rest[1:-1])}")
    elif sub == "apps":
        print(", ".join(sorted(set(tv.APPS) | set(config.get("tv_apps") or {}))))
    else:
        print(tv.control(sub, " ".join(rest[1:]), which))


def config_command(rest):
    if not rest:
        for key, value in config.load().items():
            print(f"{key} = {config.shown(key, value)}")
    elif len(rest) == 1:
        print(config.shown(rest[0], config.get(rest[0])))
    else:
        value = " ".join(rest[1:])
        config.set(rest[0], float(value) if rest[0] == "mic_sensitivity" else value)
        print(f"{rest[0]} = {config.shown(rest[0], config.get(rest[0]))}")


def memory_command(rest):
    sub = (rest[0] if rest else "list").lower()
    memories = list(config.get("memories") or [])
    if sub == "list":
        if not memories:
            print("Dave onthoudt nog niets. Zeg \"onthoud dat…\" of: sudo dave memory add \"…\"")
        for i, m in enumerate(memories, 1):
            print(f"{i:>3}. {m}")
    elif sub == "add":
        print(brain.remember(" ".join(rest[1:])))
    elif sub in ("remove", "forget"):
        what = " ".join(rest[1:])
        if what.isdigit() and 1 <= int(what) <= len(memories):
            gone = memories.pop(int(what) - 1)
            config.set("memories", memories)
            print(f"Vergeten: {gone}")
        else:
            print(brain.forget(what))
    elif sub == "clear":
        if input(f"Alle {len(memories)} herinneringen vergeten? (j/n) ").strip().lower() in ("j", "ja", "y", "yes"):
            config.set("memories", [])
            print("Alles vergeten.")
    else:
        print('Gebruik: sudo dave memory [list] | add "…" | remove <nummer of woorden> | clear')
        return 1


def voice_command(rest):
    sub = (rest[0] if rest else "").lower()
    if sub in ("offline", "azure", "auto", "espeak"):
        config.set("voice_engine", sub)
        if sub == "offline" and voice.engine() == "espeak":
            print("Offline stem aan. Dit is nu espeak (robotachtig). Een veel mooiere offline stem: sudo dave voice install")
        if sub == "azure" and not config.get("azure_key"):
            print("Er is nog geen Azure-sleutel: zet hem met sudo dave setup")
        print(f"Stem: {voice.engine()}")
        voice.speak(config.say("This is my voice now.", "Zo klink ik nu."))
    elif sub == "install":
        name = (rest[1] if len(rest) > 1 else ("pim" if config.dutch() else "ryan")).lower()
        path = voice.install_piper(name)
        config.load().update({"piper_model": path, "voice_engine": "offline"}); config.save()
        print(f"✅ Offline stem {name} geïnstalleerd en aangezet. (Andere: {', '.join(voice.PIPER_VOICES)}; terug naar Azure: sudo dave voice azure)")
        voice.speak(config.say(f"Hi, I'm {config.get('name')}, now with an offline voice.", f"Hoi, ik ben {config.get('name')}, nu met een offline stem."))
    else:
        print(f"Nu: {voice.engine()} (instelling: {config.get('voice_engine')}).")
        print("  sudo dave voice offline     offline stem (Piper, of espeak als Piper er niet is)")
        print("  sudo dave voice install     Piper + een Nederlandse stem installeren (pim, ronnie, mls of nathalie)")
        print("  sudo dave voice azure       de Azure-stem (online)")
        print("  sudo dave voice auto        Azure als er een sleutel is, anders offline")


def update(_):
    """The newest version from GitHub, installed (and the service restarted)."""
    try:
        source = open("/opt/dave/source").read().strip()
    except OSError:
        print("Ik weet niet waar de code staat. Doe: cd ~/Dave-Pi && git pull && sudo ./install.sh")
        return 1
    print(f"Bijwerken vanuit {source}…")
    owner = os.stat(source).st_uid  # (git pull as the user who cloned it, so the files stay theirs)
    pull = ["git", "-C", source, "pull", "--ff-only"]
    if os.geteuid() == 0 and owner != 0:
        import pwd
        pull = ["sudo", "-u", pwd.getpwuid(owner).pw_name] + pull
    if subprocess.run(pull).returncode != 0:
        print("⚠ git pull lukte niet (zie hierboven).")
        return 1
    return subprocess.run(["bash", os.path.join(source, "install.sh")]).returncode


# --- Testing ---

def test_speaker(_):
    print(f"Speaker: {config.get('speaker_device')} · stem: {voice.engine()}")
    voice.tone("listen")
    voice.speak(config.say("Can you hear me? This is the speaker test.", "Hoor je me? Dit is de speakertest."))
    print("Niets gehoord? Kijk welke apparaten er zijn met  aplay -l  en stel er een in, bv.: sudo dave config speaker_device plughw:1,0")


def test_mic(_):
    print(f"Microfoon: {config.get('mic_device')}. Zeg iets, 5 seconden…")
    mic = listen.Mic()
    pieces, peak = [], 0
    start = time.time()
    try:
        while time.time() - start < 5:
            chunk = mic.read()
            pieces.append(chunk)
            loud = listen.level(chunk)
            peak = max(peak, loud)
            print("\r" + "▮" * min(50, int(loud / 300)) + " " * 50, end="", flush=True)
    finally:
        mic.close()
    print(f"\nHardst: {int(peak)} (spraak is meestal boven de 1000).")
    if peak < 300:
        print("Heel zacht of niets: kijk met  arecord -l  welke microfoon er is, bv.: sudo dave config mic_device plughw:1,0")
        return
    if config.get("groq_key"):
        import io, wave
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(listen.RATE); w.writeframes(b"".join(pieces))
        print(f"Verstaan: \"{groq.transcribe(buffer.getvalue())}\"")


def status(_):
    print(f"Naam: {config.get('name')} · taal: {config.get('language')} · persoonlijkheid: {config.get('personality')}")
    print(f"Groq: {'✅' if config.get('groq_key') else '❌ (sudo dave setup)'} · stem: {voice.engine()}")
    print(f"Hue: {'✅ ' + config.get('hue_bridge') if config.get('hue_user') else '❌ (sudo dave hue setup)'}")
    st = "OAuth" if config.get("smartthings_refresh") else "token" if config.get("smartthings_token") else ""
    print(f"SmartThings: {'✅ ' + st if st else '❌ (sudo dave smartthings token …)'}")
    print(f"Geheugen: {len(config.get('memories') or [])} dingen (sudo dave memory)")
    tvs = tv.all_tvs()
    print(f"TV's: " + (", ".join(f"{'⭐' if n == tv.default() else ''}{n} ({i.get('model') or i.get('host')})" for n, i in tvs.items()) if tvs else "❌ (sudo dave tv setup)"))
    print(f"Wekwoord: \"{config.get('wake_phrase')}\" · model {'✅' if os.path.isdir(listen.MODEL) else '❌ ' + listen.MODEL}")
    result = subprocess.run(["systemctl", "is-active", "dave"], capture_output=True, text=True)
    print(f"Service: {result.stdout.strip() or 'onbekend'}  (logs: journalctl -u dave -f)")


if __name__ == "__main__":
    sys.exit(main())
