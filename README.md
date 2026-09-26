# Hackerthon-Krise

## Live-Transkription mit Deepgram

`app.py` bietet `/ws/deepgram` als direkten WebSocket-Stream zu Deepgram Flux.
Flux Multilingual verwendet `language_hint=de`, um deutsche Sprache bevorzugt
zu erkennen. Nach spätestens drei Sekunden Stille beendet Deepgram einen begonnenen Turn.
Der Client sendet rohe PCM-Audiodaten als Binärframes: 16 kHz, mono, signed
16-bit little-endian (`linear16`). Deepgram liefert laufende `Update`-Texte
und bei erkannter Sprechpause einen finalen `EndOfTurn`. Die Serverantwort
enthält dabei `event: "transcript"` oder `event: "end_of_turn"` sowie das
Originalfeld `deepgram_event` und die übrigen Deepgram-Daten.

Trage `DEEPGRAM_API_KEY` und `OPENROUTER_API_KEY` in `.env` ein und installiere
die Abhängigkeiten mit `uv sync`. Starte den Server:

```bash
uv run uvicorn app:app --reload
```

Für die Website starte zusätzlich den separaten Client-Webserver in einem
zweiten Terminal:

```bash
uv run uvicorn client:app --reload --port 8001
```

Öffne <http://localhost:8001>. Die Seite nimmt nach Klick auf den
Mikrofon-Button Browser-Audio auf, zeigt Transkript-Updates und den Choice-
Score sowie die passende Avatar-Animation live an. Der Client leitet den Audiostream an den Backend-Server auf
Port 8000 weiter; bei jedem finalen Turn ruft dieser JEv mit
`OPENROUTER_API_KEY` auf. Der Mikrofonzugriff funktioniert auf `localhost`
oder über HTTPS.

`GET /jev/get-last-prediction` liefert die letzte Vorhersage als `prediction`,
den erkannten `text` und die mediane Mikrofonlautstärke als `volume` (RMS von
0 bis 1). Audio-Chunks unter 3 % Lautstärke zählen nicht zum Median. Drei
Sekunden nach der letzten Vorhersage sind alle drei Felder wieder `null`.
`GET /jev/get-prediction?input_string=...` behält sein bisheriges Format mit
`input` und `result`.

Alternativ kannst du den Laptop-Mikrofon-Client im Terminal starten. Er streamt
16-kHz-Mono-PCM über WebSocket und zeigt Live-Transkripte:

```bash
uv run python client.py
```

Mit `STT_WS_URL` lässt sich die Serveradresse ändern; Standard ist
`ws://172.16.1.224:8000/ws/deepgram`. Beenden mit Ctrl+C. `pause` und `resume`
stehen als WebSocket-Steuerereignisse bereit; `stop` finalisiert den aktuellen
Turn und schließt den Deepgram-Stream. Die generische Anbieter-Proxy-Route
`/ws/stt` bleibt ebenfalls verfügbar.

## STT-WebSocket

`app.py` stellt `/ws/stt` als WebSocket-Proxy bereit. Vor dem Start müssen
`STT_BITII_WS_URL` auf die WebSocket-URL des STT-Dienstes und bei Bedarf
`STT_BITII_API_KEY` gesetzt sein. Der Client sendet Audio als Binärframes.
JSON-Konfiguration für den jeweiligen STT-Anbieter wird unverändert an den
Anbieter weitergeleitet. Transkript-Events des Anbieters werden unverändert
zurückgegeben.

Client-Steuerung erfolgt über JSON:

```json
{"event":"pause"}
{"event":"resume"}
{"event":"last_sentence"}
{"event":"stop"}
```

`pause` verwirft eingehende Audioframes bis `resume` gesendet wird.
`last_sentence` antwortet mit dem zuletzt vom Anbieter als final markierten
Textsegment, zum Beispiel `{"event":"last_sentence","text":"Hallo."}`.
`stop` sendet `{"action":"stop"}` an den STT-Anbieter.

Beispiel im Browser (Audio-Chunks müssen vom Audiocode als `ArrayBuffer` oder
`Blob` geliefert werden):

```js
const ws = new WebSocket("ws://localhost:8000/ws/stt");
ws.binaryType = "arraybuffer";
ws.onmessage = ({ data }) => {
  if (typeof data === "string") console.log(JSON.parse(data));
};

// Nach dem Öffnen zuerst die Anbieter-Konfiguration senden, falls erforderlich:
ws.onopen = () => ws.send(JSON.stringify({ language: "de" }));

// Für jeden Audio-Chunk:
// ws.send(audioChunkArrayBuffer);

// Steuerung:
// ws.send(JSON.stringify({ event: "pause" }));
// ws.send(JSON.stringify({ event: "resume" }));
// ws.send(JSON.stringify({ event: "last_sentence" }));
// ws.send(JSON.stringify({ event: "stop" }));
```

## Raspberry Pi, Avatar-Display und Audio

`app.py` läuft auf dem Raspberry Pi unter `guenther@172.16.1.224` auf Port 8000.
`client.py` läuft auf dem Laptop und verbindet sich über die obige
`STT_WS_URL` mit dem Pi. Die Weboberfläche startet lokal mit
`uv run uvicorn client:app --port 8001`; alternativ nimmt
`uv run python client.py` direkt über das Laptop-Mikrofon auf.

Der Pi benötigt SPI0 und ein ST7735-Display mit 160 × 128 Pixeln. Die
Hardware-Belegung in `SVG_Animation.py` ist SPI0/CE0, GPIO 24 (DC) und
GPIO 25 (Reset). Die fertigen PNGs in `assets/frames` stammen aus
`assets/animation.svg`; zur Laufzeit wird kein Browser benötigt. Bei jedem
JEv-Choice-Score von 0 bis 9 wechselt das Display mit den passenden
Übergangsframes zur jeweiligen Pose und spielt deren Idle-Animation.

`dfplayer_audio.py` spielt pro neuer Vorhersage höchstens einen Track aus
Ordner `06` der DFPlayer-SD-Karte. Scores 2–9 verwenden die zugeordneten
Zufalls-Tracks; bei 0–1 steuert die gemessene Mikrofonlautstärke Track 03
oder 04. Solange BUSY aktiv ist, wartet der Player höchstens drei Sekunden.
Die Belegung ist GPIO 14 (Pi-TX zum DFPlayer-RX), GPIO 15 (Pi-RX vom
DFPlayer-TX) und GPIO 26 (BUSY). Der Pi 5 sendet über den Hardware-UART0
`/dev/ttyAMA0`. `dfplayer_audio.py` wählt die TF-Karte und setzt die
Lautstärke einmal beim Start; ein neuer Score sendet den Play-Befehl sofort.
Die ältere softwaregetaktete Variante auf GPIO 5/22 bleibt in
`picodfplayer.py` zum Vergleich erhalten (MIT-Lizenz in
`PICODFPLAYER_LICENSE`).
Die MP3-Dateien müssen auf der DFPlayer-SD-Karte in `06/` liegen, etwa
`001.mp3` bis `011.mp3`.

Auf dem Pi installieren und starten:

```bash
uv sync --extra display
DISPLAY_ENABLED=1 AUDIO_ENABLED=1 uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

`DEEPGRAM_API_KEY` und `OPENROUTER_API_KEY` müssen in der `.env` des Pi
stehen. `DISPLAY_ENABLED=auto` startet die Anzeige, wenn `/dev/spidev0.0`
vorhanden ist; `DISPLAY_ENABLED=0` deaktiviert sie. Nur ein Prozess darf
das Display und die GPIO-Pins gleichzeitig verwenden.

Für den automatischen Start liegt die systemd-Unit in
`deploy/krise-backend.service`. Auf dem Pi wird sie unter
`~/.config/systemd/user/` installiert, mit `systemctl --user enable --now
krise-backend.service` aktiviert und durch `loginctl enable-linger guenther`
auch ohne Anmeldung beim Boot gestartet. Der installierte Dienst ist mit
`systemctl --user status krise-backend.service` prüfbar.

Ein separater Test für das Audiomodul lässt sich per SSH auf dem Pi ausführen:

```bash
cd ~/hackerthon-krise-laya
systemctl --user stop krise-backend.service
.venv/bin/python tools/test_dfplayer.py
systemctl --user start krise-backend.service
```

Das Skript spielt fest `/06/004.mp3` über UART0 und meldet, ob der BUSY-Pin während
des Abspielbefehls auf LOW wechselt. Der Backend-Dienst wird davor manuell
gestoppt und danach wieder gestartet, da nur ein Prozess die GPIO-Pins
belegen kann.

Der ältere GPIO-5-Test des kurzen DFPlayer-Protokolls ohne Prüfsumme kann bei
gestopptem Dienst separat ausgeführt werden; dazu muss die Verdrahtung wieder
auf GPIO 5 umgesteckt sein:

```bash
.venv/bin/python tools/test_dfplayer_simple.py
```

Es versucht zuerst `/06/004.mp3` und bei ausbleibendem BUSY-Signal den
globalen Track 4. Dieser kann eine andere Datei auf der SD-Karte sein.

Ein weiterer Test nutzt den Hardware-UART0 des Pi 5 auf GPIO 14 (TX,
physischer Pin 8) und GPIO 15 (RX, physischer Pin 10). Dafür muss der
DFPlayer entsprechend umgesteckt sein, `dtoverlay=uart0-pi5` unter `[pi5]`
in `/boot/firmware/config.txt` stehen und die serielle Linux-Konsole
deaktiviert sein. Nach einem Neustart sollten `pinctrl get 14` und
`pinctrl get 15` TXD0 beziehungsweise RXD0 zeigen. Bei gestopptem Backend:

```bash
.venv/bin/python -m pip install 'pyserial>=3.5'
.venv/bin/python tools/test_dfplayer_uart.py
```

Ohne Option startet das Skript die Wiedergabe direkt und misst die Zeit bis
BUSY LOW. Mit `--diagnose` setzt es das Modul zurück, fragt Status und Zahl
der TF-Dateien ab und zeigt Rohantworten an. Bei fehlender Wiedergabe testet
es zusätzlich kurze 8-Byte-Befehle.
