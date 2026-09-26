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
Score live an. Der Client leitet den Audiostream an den Backend-Server auf
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
`ws://localhost:8000/ws/deepgram`. Beenden mit Ctrl+C. `pause` und `resume`
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
