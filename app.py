import asyncio
from collections import deque
import json
import logging
import math
import os
import statistics
import struct
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from dotenv import load_dotenv
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from starlette.websockets import WebSocketState

from typesafe_sdk import TypeSafeClient

from SVG_Animation import AnimationPlayer
from dfplayer_audio import AudioPlayer

logger = logging.getLogger(__name__)
load_dotenv()

display_player = AnimationPlayer()
audio_player = AudioPlayer()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The laptop can import app.py without GPIO hardware; the Pi starts the
    # player automatically when SPI0 is enabled.
    enabled = os.getenv("DISPLAY_ENABLED", "auto").lower()
    if enabled == "1" or (enabled == "auto" and os.path.exists("/dev/spidev0.0")):
        display_player.start()
    if os.getenv("AUDIO_ENABLED", "0") == "1":
        audio_player.start()
    yield
    audio_player.stop()
    display_player.stop()


app = FastAPI(lifespan=lifespan)

LAST_PREDICTION_SECONDS = 3.0
MIN_VOLUME = 0.03
last_prediction = None
IDLE_PREDICTION = {"prediction": {"score": "0"}, "text": None, "volume": None}


def choice_score(prediction) -> int:
    """Read the 0..9 choice from the JEv answer without coercing bad values."""
    value = prediction.get("score") if isinstance(prediction, dict) else prediction
    for _ in range(4):
        if isinstance(value, dict):
            for key in ("choice", "value", "answer", "score"):
                if key in value:
                    value = value[key]
                    break
            else:
                raise ValueError(f"JEv-Score hat kein Choice-Feld: {value!r}")
        else:
            break
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"Ungültiger JEv-Score: {value!r}")
    if isinstance(value, str) and (len(value) != 1 or not value.isdigit()):
        raise ValueError(f"Ungültiger JEv-Score: {value!r}")
    score = int(value)
    if not 0 <= score <= 9:
        raise ValueError(f"JEv-Score außerhalb 0..9: {score}")
    return score


def audio_volume(audio: bytes) -> float:
    """Return the RMS of 16-bit little-endian PCM as a value from 0 to 1."""
    if len(audio) < 2:
        return 0.0
    samples = struct.iter_unpack("<h", audio[:len(audio) - len(audio) % 2])
    count = len(audio) // 2
    return math.sqrt(sum(sample * sample for (sample,) in samples) / count) / 32768


def remember_prediction(prediction, text: str, volume: float | None):
    global last_prediction
    last_prediction = (
        time.monotonic(),
        {"prediction": prediction, "text": text, "volume": volume},
    )


def reset_prediction_to_idle(expected=None) -> bool:
    """Reset the latest run without queuing a new audio track."""
    global last_prediction
    if last_prediction is None or (expected is not None and last_prediction is not expected):
        return False
    if last_prediction[1] == IDLE_PREDICTION:
        return False
    display_player.set_score(0)
    last_prediction = (time.monotonic(), IDLE_PREDICTION.copy())
    return True


def note_transcript_activity():
    """Keep the current run visible while new speech is arriving."""
    global last_prediction
    if last_prediction is not None and last_prediction[1] != IDLE_PREDICTION:
        last_prediction = (time.monotonic(), last_prediction[1])


def apply_prediction(prediction, text: str, volume: float | None) -> int:
    """Show the emotion and queue its matching DFPlayer track from folder 06."""
    score = choice_score(prediction)
    display_player.set_score(score)
    audio_player.play(score, volume)
    remember_prediction(prediction, text, volume)
    return score


TOXICITY_STATE = """Bewerte die Toxizität der Nachricht auf einer ganzzahligen Skala von 0 bis 9.
0 bedeutet freundlich, neutral oder sachlich. 1-2 bedeutet leicht unhöflich
oder schroff. 3-5 bedeutet klar unfreundlich oder respektlos, aber ohne
aggressives Anschreien oder grobe vulgäre Beschimpfung. 6-7 bedeutet stark
aggressives Anschreien, einen aggressiven Befehl oder eine grobe vulgäre
Beschimpfung; Beispiele sind 'JETZT SEI DOCH ENDLICH STILL!' und 'HALT DIE FRESSE!'.
Großschreibung und Ausrufezeichen können den Score erhöhen, wenn sie    
aggressives Anschreien vermitteln. 8 bedeutet besonders schwere, gezielte
Einschüchterung oder wiederholte schwere Angriffe, sofern dies aus dem Text
hervorgeht. 9 bedeutet eine konkrete, ernsthafte Drohung mit Gewalt oder
körperlichem Schaden. Beleidigungen und Anschreien allein sind keine Drohung.
Bewerte nur den Text und erfinde keinen fehlenden Kontext."""

TOXICITY_QUESTIONS = {
    "score": {
        "type": "choice",
        "instructions": "Gib genau einen Toxizitäts-Score von 0 bis 9 zurück.",
        "criteria": {str(score): f"Score {score}" for score in range(10)},
    },
}


def predict_toxicity(input_string: str):

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    jev_client = TypeSafeClient(
        api_key=api_key,
        base_url="https://openrouter.ai/api",
    )
    result = jev_client.system_one(
        model="jev-1.13",
        state=f"{TOXICITY_STATE}\n\nNachricht: {input_string}",
        questions=TOXICITY_QUESTIONS,
    )
    return jsonable_encoder(result.answers)


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/hardware/status")
def hardware_status():
    return {
        "display_running": bool(display_player._thread and display_player._thread.is_alive()),
        "display_error": str(display_player.error) if display_player.error else None,
        "audio_running": bool(audio_player._thread and audio_player._thread.is_alive()),
        "audio_error": str(audio_player.error) if audio_player.error else None,
        "last_audio_track": audio_player.last_track,
        "last_audio_at": audio_player.last_played_at,
        "last_audio_busy_seen": audio_player.last_busy_seen,
    }


@app.get("/jev/get-prediction")
def get_prediction(input_string: str):
    result = predict_toxicity(input_string)
    apply_prediction(result, input_string, None)
    return {"input": input_string, "result": result}


@app.get("/jev/get-last-prediction")
def get_last_prediction():
    latest = last_prediction
    if latest is None:
        return {"prediction": None, "text": None, "volume": None}
    timestamp, result = latest
    if result != IDLE_PREDICTION and time.monotonic() - timestamp > LAST_PREDICTION_SECONDS:
        reset_prediction_to_idle(latest)
    return last_prediction[1]


@app.websocket("/ws/stt")
async def stt_websocket(websocket: WebSocket):
    upstream_url = os.getenv("STT_BITII_WS_URL")
    if not upstream_url:
        await websocket.close(code=1011, reason="STT_BITII_WS_URL is not configured")
        return

    await websocket.accept()
    headers = {}
    api_key = os.getenv("STT_BITII_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with websockets.connect(upstream_url, additional_headers=headers) as upstream:
            paused = False
            latest_sentence = ""

            async def client_to_stt():
                nonlocal paused
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        if not paused:
                            await upstream.send(message["bytes"])
                        continue

                    raw_text = message.get("text")
                    if raw_text is None:
                        continue
                    try:
                        event = json.loads(raw_text)
                    except json.JSONDecodeError:
                        await upstream.send(raw_text)
                        continue

                    action = event.get("event") if isinstance(event, dict) else None
                    if action == "pause":
                        paused = True
                        await websocket.send_json({"event": "paused"})
                    elif action == "resume":
                        paused = False
                        await websocket.send_json({"event": "resumed"})
                    elif action == "last_sentence":
                        await websocket.send_json({
                            "event": "last_sentence",
                            "text": latest_sentence,
                        })
                    elif action == "stop":
                        await upstream.send(json.dumps({"action": "stop"}))
                    else:
                        await upstream.send(raw_text)

            async def stt_to_client():
                nonlocal latest_sentence
                async for message in upstream:
                    if websocket.client_state != WebSocketState.CONNECTED:
                        return
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        try:
                            update = json.loads(message)
                        except (json.JSONDecodeError, TypeError):
                            update = None
                        if isinstance(update, dict):
                            text = update.get("text")
                            if update.get("is_final") is True and isinstance(text, str) and text.strip():
                                latest_sentence = text.strip()
                        await websocket.send_text(message)

            tasks = {
                asyncio.create_task(client_to_stt()),
                asyncio.create_task(stt_to_client()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    except WebSocketDisconnect:
        pass
    except Exception:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011, reason="STT connection failed")


@app.websocket("/ws/deepgram")
async def deepgram_websocket(websocket: WebSocket):
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        await websocket.close(code=1011, reason="DEEPGRAM_API_KEY is not configured")
        return

    params = {
        "model": "flux-general-multi",
        "encoding": "linear16",
        "sample_rate": "16000",
        "language_hint": "de",
        "eot_timeout_ms": "3000",
    }
    upstream_url = f"wss://api.deepgram.com/v2/listen?{urlencode(params)}"
    await websocket.accept()

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers={"Authorization": f"Token {api_key}"},
        ) as upstream:
            paused = False
            stopped = False
            recent_volumes = deque(maxlen=256)
            idle_task = None

            def cancel_idle_reset():
                nonlocal idle_task
                if idle_task is not None:
                    idle_task.cancel()
                    idle_task = None

            def schedule_idle_reset():
                nonlocal idle_task
                cancel_idle_reset()
                expected = last_prediction
                if expected is None or expected[1] == IDLE_PREDICTION:
                    return

                async def reset_after_silence():
                    await asyncio.sleep(LAST_PREDICTION_SECONDS)
                    if reset_prediction_to_idle(expected):
                        await websocket.send_json({"event": "choice_reset", "score": 0})

                idle_task = asyncio.create_task(reset_after_silence())

            async def client_to_deepgram():
                nonlocal paused, stopped
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    audio = message.get("bytes")
                    if audio is not None:
                        if not paused and not stopped:
                            now = time.monotonic()
                            volume = audio_volume(audio)
                            if volume >= MIN_VOLUME:
                                recent_volumes.append((now, volume))
                            while recent_volumes and now - recent_volumes[0][0] > LAST_PREDICTION_SECONDS:
                                recent_volumes.popleft()
                            await upstream.send(audio)
                        continue

                    raw = message.get("text")
                    if raw is None:
                        continue
                    try:
                        control = json.loads(raw)
                    except json.JSONDecodeError:
                        await websocket.send_json({"event": "error", "message": "Expected JSON control message"})
                        continue
                    action = control.get("event") if isinstance(control, dict) else None
                    if action == "pause":
                        if not stopped:
                            paused = True
                            await websocket.send_json({"event": "paused"})
                    elif action == "resume":
                        if not stopped:
                            paused = False
                            await websocket.send_json({"event": "resumed"})
                    elif action == "stop":
                        if not stopped:
                            stopped = True
                            await upstream.send(json.dumps({"type": "ForceEndTurn"}))
                            await upstream.send(json.dumps({"type": "CloseStream"}))
                    else:
                        await websocket.send_json({
                            "event": "error",
                            "message": "Supported controls are pause, resume, and stop",
                        })

            async def deepgram_to_client():
                async for message in upstream:
                    if websocket.client_state != WebSocketState.CONNECTED:
                        return
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                        continue
                    try:
                        payload = json.loads(message)
                    except (json.JSONDecodeError, TypeError):
                        await websocket.send_text(message)
                        continue

                    if isinstance(payload, dict) and payload.get("type") == "TurnInfo":
                        payload["deepgram_event"] = payload.get("event")
                        payload["event"] = (
                            "end_of_turn"
                            if payload.get("deepgram_event") == "EndOfTurn"
                            else "transcript"
                        )
                        if (payload.get("transcript") or "").strip():
                            note_transcript_activity()
                            if payload["event"] == "end_of_turn":
                                cancel_idle_reset()
                            else:
                                schedule_idle_reset()
                    await websocket.send_json(payload)
                    if isinstance(payload, dict) and payload.get("event") == "end_of_turn":
                        transcript = (payload.get("transcript") or "").strip()
                        if transcript:
                            try:
                                now = time.monotonic()
                                volumes = [volume for timestamp, volume in recent_volumes
                                           if now - timestamp <= LAST_PREDICTION_SECONDS]
                                median_volume = statistics.median(volumes) if volumes else None
                                recent_volumes.clear()
                                prediction = await asyncio.to_thread(
                                    predict_toxicity, transcript
                                )
                                score = apply_prediction(prediction, transcript, median_volume)
                                await websocket.send_json({
                                    "event": "jev_prediction",
                                    "transcript": transcript,
                                    "result": prediction,
                                    "score": score,
                                })
                                schedule_idle_reset()
                            except Exception as error:
                                await websocket.send_json({
                                    "event": "jev_error",
                                    "transcript": transcript,
                                    "message": str(error),
                                })
                                schedule_idle_reset()

            tasks = {
                asyncio.create_task(client_to_deepgram()),
                asyncio.create_task(deepgram_to_client()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            cancel_idle_reset()
            for task in done:
                task.result()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Deepgram-WebSocket ist fehlgeschlagen")
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011, reason="Deepgram connection failed")
