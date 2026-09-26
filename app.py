import asyncio
import json
import logging
import os
from urllib.parse import urlencode

from dotenv import load_dotenv
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from starlette.websockets import WebSocketState

from typesafe_sdk import TypeSafeClient

from fastapi.templating import Jinja2Templates

app = FastAPI()
logger = logging.getLogger(__name__)
load_dotenv()

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


@app.get("/jev/get-prediction")
def get_prediction(input_string: str):
    return {"input": input_string, "result": predict_toxicity(input_string)}


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

            async def client_to_deepgram():
                nonlocal paused, stopped
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    audio = message.get("bytes")
                    if audio is not None:
                        if not paused and not stopped:
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
                    await websocket.send_json(payload)
                    if isinstance(payload, dict) and payload.get("event") == "end_of_turn":
                        transcript = (payload.get("transcript") or "").strip()
                        if transcript:
                            try:
                                prediction = await asyncio.to_thread(
                                    predict_toxicity, transcript
                                )
                                await websocket.send_json({
                                    "event": "jev_prediction",
                                    "transcript": transcript,
                                    "result": prediction,
                                })
                            except Exception as error:
                                await websocket.send_json({
                                    "event": "jev_error",
                                    "transcript": transcript,
                                    "message": str(error),
                                })

            tasks = {
                asyncio.create_task(client_to_deepgram()),
                asyncio.create_task(deepgram_to_client()),
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
        logger.exception("Deepgram-WebSocket ist fehlgeschlagen")
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011, reason="Deepgram connection failed")
