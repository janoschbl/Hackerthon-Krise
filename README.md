# Hackerthon-Krise

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
