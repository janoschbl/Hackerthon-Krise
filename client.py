import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

import numpy as np
import sounddevice as sd
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState


app = FastAPI(title="Live-Transkript Client")
ROOT_DIR = Path(__file__).resolve().parent
app.mount("/assets", StaticFiles(directory=ROOT_DIR / "assets"), name="assets")
templates = Jinja2Templates(directory=ROOT_DIR / "templates")
logger = logging.getLogger(__name__)
DEFAULT_STT_WS_URL = "ws://172.16.1.224:8000/ws/deepgram"


def backend_health_url() -> str:
    load_dotenv()
    websocket_url = os.getenv("STT_WS_URL", DEFAULT_STT_WS_URL)
    parts = urlsplit(websocket_url)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme)
    if not scheme or not parts.netloc:
        raise ValueError("STT_WS_URL muss eine ws://- oder wss://-Adresse sein")
    return urlunsplit((scheme, parts.netloc, "/health", "", ""))


def fetch_backend_health() -> bool:
    with urlopen(backend_health_url(), timeout=1.5) as response:
        if response.status != 200:
            return False
        payload = json.load(response)
        return isinstance(payload, dict) and payload.get("status") == "ok"


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/backend/health")
async def backend_health():
    try:
        online = await asyncio.to_thread(fetch_backend_health)
    except (OSError, ValueError, json.JSONDecodeError):
        online = False
    return {"online": online}


@app.websocket("/ws/deepgram")
async def proxy_deepgram(websocket: WebSocket):
    load_dotenv()
    upstream_url = os.getenv("STT_WS_URL", DEFAULT_STT_WS_URL)
    await websocket.accept()
    try:
        async with websockets.connect(upstream_url, max_size=None) as upstream:
            async def browser_to_backend():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def backend_to_browser():
                async for message in upstream:
                    if websocket.client_state != WebSocketState.CONNECTED:
                        return
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = {
                asyncio.create_task(browser_to_backend()),
                asyncio.create_task(backend_to_browser()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                try:
                    task.result()
                except websockets.ConnectionClosed:
                    if websocket.client_state == WebSocketState.CONNECTED:
                        await websocket.close(code=1011, reason="Backend-WebSocket geschlossen")
                except Exception:
                    logger.exception("Fehler beim Weiterleiten des WebSocket-Streams")
                    if websocket.client_state == WebSocketState.CONNECTED:
                        await websocket.close(code=1011, reason="Fehler beim Backend-Stream")
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Client konnte keine Backend-Verbindung herstellen")
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011, reason="Backend-Verbindung fehlgeschlagen")


async def run_microphone_client():
    load_dotenv()
    server_url = os.getenv("STT_WS_URL", DEFAULT_STT_WS_URL)
    sample_rate = 16_000
    loop = asyncio.get_running_loop()
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)

    def on_audio(indata, _frames, _time_info, status):
        if status:
            print(f"Mikrofon-Hinweis: {status}", file=sys.stderr)
        pcm = (np.clip(indata[:, 0], -1.0, 1.0) * 32767).astype("<i2").tobytes()


        volume_norm = np.linalg.norm(indata) / np.sqrt(len(indata))
        print(f'Lautstärke (RMS): {volume_norm:.4f}')

        def enqueue():
            try:
                audio_queue.put_nowait(pcm)
            except asyncio.QueueFull:
                print("Audio-Puffer voll; ein Chunk wurde verworfen.", file=sys.stderr)

        loop.call_soon_threadsafe(enqueue)

    print(f"Verbinde mit {server_url} …")
    async with websockets.connect(server_url, max_size=None) as ws:
        stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=1280,  # 80 ms chunks
            latency="low",
            callback=on_audio,
        )
        stream.start()
        print("Mikrofon streamt. Mit Ctrl+C beenden.")

        async def send_audio():
            while True:
                await ws.send(await audio_queue.get())

        sender = asyncio.create_task(send_audio())
        try:
            async for message in ws:
                if not isinstance(message, str):
                    continue
                event = json.loads(message)
                kind = event.get("event")
                transcript = (event.get("transcript") or "").strip()
                if kind == "transcript":
                    if transcript:
                        print(f"\rLive: {transcript[:120]}", end="", flush=True)
                elif kind == "end_of_turn" and transcript:
                    print(f"\nTurn: {transcript}")
                elif kind == "jev_prediction":
                    print(f"JEv: {event.get('result')}")
                elif kind == "jev_error":
                    print(f"JEv-Fehler: {event.get('message')}", file=sys.stderr)
                elif kind == "error":
                    print(f"WebSocket-Fehler: {event.get('message')}", file=sys.stderr)
        finally:
            stream.stop()
            stream.close()
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            try:
                await ws.send(json.dumps({"event": "stop"}))
            except websockets.ConnectionClosed:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(run_microphone_client())
    except KeyboardInterrupt:
        print("\nAufnahme beendet.")
