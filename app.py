import asyncio
import json
import os

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

app = FastAPI()


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/jev/get-prediction")
def get_prediction(input_string: str):
    from client import client, state, questions

    result = client.system_one(
        model="jev-1.13",
        state=f"{state}\n\nNachricht: {input_string}",
        questions=questions,
    )
    return {"input": input_string, "result": result.answers}


@app.websocket("/ws/stt")
async def stt_websocket(websocket: WebSocket):
    """Proxy audio and transcripts; accept pause/resume/stop/last_sentence events."""
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
                        # Provider config/control JSON passes through unchanged.
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
