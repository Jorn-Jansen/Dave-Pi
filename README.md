# Dave voor Raspberry Pi

Een slimme speaker zoals een Google Home: zeg **"hey Dave"** en vraag wat je wilt. Dave bedient je **Philips Hue**-lampen en je
**Samsung SmartThings**-apparaten (wasmachine, droger, tv…), zet timers, zoekt dingen op en praat terug. Hetzelfde brein als
[Dave voor Windows](https://github.com/Jorn-Jansen/Dave-Windows) en [Dave voor iPhone](https://github.com/Jorn-Jansen/Dave-iPhone), maar dan
zonder scherm.

## Wat je nodig hebt

- Een Raspberry Pi 4 of 5 (2 GB is genoeg) met **Raspberry Pi OS (64-bit)**
- Een microfoon (USB, of een ReSpeaker HAT) en een speaker
- Een gratis **Groq**-sleutel: [console.groq.com/keys](https://console.groq.com/keys)

## Installeren

```bash
sudo apt install -y git
git clone https://github.com/Jorn-Jansen/Dave-Pi.git
cd Dave-Pi
sudo ./install.sh
sudo dave setup
```

Bijwerken later: `cd Dave-Pi && git pull && sudo ./install.sh`

## Testen (ook zonder microfoon en speaker)

| Commando | Wat het doet |
|---|---|
| `sudo dave status` | Wat er is ingesteld |
| `sudo dave ask "zet de woonkamer op paars"` | Iets vragen door te typen (zegt het antwoord ook, `--quiet` = alleen tekst) |
| `sudo dave chat` | Blijven typen met Dave |
| `sudo dave say "hallo"` | Alleen iets zeggen (test de stem) |
| `sudo dave test-speaker` / `sudo dave test-mic` | Het geluid testen |
| `sudo dave config` | Alle instellingen zien; `sudo dave config <naam> <waarde>` om er een te veranderen |

## Lampen en apparaten

**Philips Hue:** `sudo dave hue setup` — druk als hij erom vraagt op de ronde knop van de Hue Bridge. Daarna: *"hey Dave, doe de lampen uit"*,
*"zet de slaapkamer op 30 procent"*, *"maak de woonkamer paars"*, *"zet de scène ontspannen aan"*.

**SmartThings (Samsung):**

- Snel proberen: maak een token op [account.smartthings.com/tokens](https://account.smartthings.com/tokens) (vink apparaten lezen en
  bedienen aan) en: `sudo dave smartthings token <token>`. Let op: Samsung laat zo'n token na 24 uur verlopen.
- Voor altijd: `sudo dave smartthings login` en volg de stappen (eenmalig een eigen SmartThings-app aanmaken).

Daarna: *"is de wasmachine klaar?"*, *"hoe lang duurt de droger nog?"*, *"zet de tv uit"*.

**De tv (Google TV / Android TV, zoals Philips The One):** `sudo dave tv setup` — typ het IP-adres van de tv en de code die op
de tv verschijnt. Zet op de tv ook *inschakelen via netwerk* aan. Daarna: *"hey Dave, zet de tv aan"*, *"tv harder"*, *"zet Netflix op de tv"*.
Meerdere tv's (ook een Chromecast met Google TV): geef ze elk een naam bij `sudo dave tv setup` (*"zet Netflix op de slaapkamer-tv"*), en kies
met `sudo dave tv default <naam>` welke hij pakt als je geen naam noemt. `sudo dave tv list` laat ze zien (⭐ = standaard).

## De verwarming (ATAG-thermostaat)

`sudo dave thermostaat setup` — Dave zoekt de thermostaat op je netwerk; bevestig daarna het verzoek op het scherm van de thermostaat.
Daarna: *"hoe warm is het binnen?"*, *"zet de verwarming op 21 graden"*, *"iets warmer"*, *"zet de verwarming terug op het klokprogramma"*.
In de terminal: `sudo dave thermostaat`, `sudo dave thermostaat 20.5`, `sudo dave thermostaat auto`.

## Timers, herinneringen, wekker en stopwatch

- *"Zet een timer van 10 minuten voor de pizza"*, *"hoe lang duurt mijn timer nog?"*
- *"Herinner me morgen om 9 uur aan de vuilnis"*, *"elke werkdag om 8 uur: pillen"*
- *"Maak me om 7 uur wakker"*, *"zet een wekker om 6:30 op werkdagen"*. Hij gaat af tot je *"hey Dave, stop"* of *"snooze"* zegt.
- *"Start de stopwatch"*, *"ronde"*, *"pauze"*, *"ga verder"*, *"stop de stopwatch"*

Alles blijft bewaard, ook na een herstart. In de terminal: `sudo dave timers`, `sudo dave alarm`, `sudo dave alarm 7:30`, `sudo dave alarm remove 7:30`, `sudo dave alarm stop`, `sudo dave stopwatch`.

## Lijsten (gedeeld met Dave op je iPhone)

*"Maak een boodschappenlijst"*, *"zet melk en eieren op de boodschappenlijst"*, *"wat staat er op de lijst?"*, *"vink de melk af"*.
De lijsten van het huis staan ook in het tabblad **Lijsten** van Dave op je iPhone, ook buiten je wifi: ze gaan versleuteld via ntfy.sh,
over een kanaal dat bij de huiscode hoort. Koppelen: `sudo dave lijst koppel` (code of QR-code), en in de app: Lijsten → 🏠.
In de terminal: `sudo dave lijst`, `sudo dave lijst maak Klusjes`, `sudo dave lijst boodschappen + melk, kaas`, `sudo dave lijst boodschappen - kaas`.
Code uitgelekt? `sudo dave lijst koppel nieuw`.

## Wasmachine en droger

Dave zegt vanzelf als de wasmachine of droger **bijna klaar** is (nog 5 minuten) en als hij **klaar** is. Ook op je telefoon:
installeer de gratis app **ntfy**, abonneer op een kanaal (bv. dezelfde als Dave op je PC gebruikt) en doe `sudo dave phone <kanaal>`.
Uitzetten: `sudo dave config appliance_alerts false`.

## Altijd aan

```bash
sudo systemctl enable --now dave     # start nu en bij elke herstart
sudo dave log                        # het logboek: alles wat hij deed en waarom (sudo dave log live / hue / 200)
journalctl -u dave -f                # meekijken wat hij hoort en doet
```

## Geluid instellen

Hoort hij niets of zegt hij niets? Kijk welke apparaten er zijn en stel ze in:

```bash
arecord -l      # microfoons, bv. "card 1, device 0" → plughw:1,0
aplay -l        # speakers
sudo dave config mic_device plughw:1,0
sudo dave config speaker_device plughw:1,0
sudo systemctl restart dave
```

Een natuurlijke stem: zet een Azure Speech-sleutel in `sudo dave setup` (gratis 500.000 tekens per maand). Zonder sleutel praat Dave
met een offline stem.

## Licentie

[PolyForm Strict 1.0.0](LICENSE): je mag Dave downloaden en gebruiken voor persoonlijk, niet-commercieel gebruik; je mag hem niet veranderen, kopiëren, verder verspreiden of verkopen. Gebruikt [Vosk](https://alphacephei.com/vosk/) (Apache 2.0) en [androidtvremote2](https://github.com/tronikos/androidtvremote2) (Apache 2.0).
