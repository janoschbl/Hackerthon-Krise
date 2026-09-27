import asyncio
import json
import unittest
from unittest.mock import patch

from PIL import Image
from starlette.websockets import WebSocketState

import app
from SVG_Animation import BGR, brighten_background


class IdleDisplayTests(unittest.TestCase):
    def setUp(self):
        self.previous = app.last_prediction
        app.last_prediction = None

    def tearDown(self):
        app.last_prediction = self.previous

    def test_idle_reset_returns_choice_zero_and_does_not_play_audio(self):
        with patch.object(app.display_player, "set_score") as display, \
             patch.object(app.audio_player, "play") as audio, \
             patch.object(app.time, "monotonic", side_effect=[10, 14, 14]):
            app.remember_prediction({"score": "7"}, "Hallo", .4)
            self.assertEqual(app.get_last_prediction(), app.IDLE_PREDICTION)
            self.assertEqual(app.choice_score(app.get_last_prediction()["prediction"]), 0)
            display.assert_called_once_with(0)
            audio.assert_not_called()

    def test_old_timer_cannot_reset_new_prediction(self):
        with patch.object(app.display_player, "set_score") as display:
            app.remember_prediction({"score": "7"}, "Alt", None)
            old = app.last_prediction
            app.remember_prediction({"score": "3"}, "Neu", None)
            self.assertFalse(app.reset_prediction_to_idle(old))
            self.assertEqual(app.choice_score(app.last_prediction[1]["prediction"]), 3)
            display.assert_not_called()

    def test_background_is_brighter_without_changing_blue_or_yellow(self):
        image = Image.new("RGB", (3, 1))
        image.putdata([(13, 17, 23), (76, 186, 240), (222, 240, 76)])
        with patch.dict("os.environ", {"DISPLAY_BACKGROUND_RGB": "48,54,64"}):
            result = brighten_background(image)
        self.assertEqual(list(result.get_flattened_data()), [
            (48, 54, 64), (76, 186, 240), (222, 240, 76),
        ])
        self.assertFalse(BGR)


class WebSocketIdleTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_seconds_of_silence_sends_choice_zero_without_closing(self):
        class FakeUpstream:
            def __init__(self):
                self.messages = asyncio.Queue()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            def __aiter__(self):
                return self

            async def __anext__(self):
                return await self.messages.get()

        class FakeWebSocket:
            client_state = WebSocketState.CONNECTED

            def __init__(self):
                self.events = asyncio.Queue()
                self.closed = False

            async def accept(self):
                pass

            async def receive(self):
                await asyncio.Future()

            async def send_json(self, event):
                await self.events.put(event)

            async def close(self, **_):
                self.closed = True

        upstream = FakeUpstream()
        websocket = FakeWebSocket()
        app.last_prediction = None
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test"}), \
             patch.object(app.websockets, "connect", return_value=upstream), \
             patch.object(app, "predict_toxicity", return_value={"score": "7"}), \
             patch.object(app.display_player, "set_score") as display, \
             patch.object(app.audio_player, "play") as audio, \
             patch.object(app, "LAST_PREDICTION_SECONDS", 0.05):
            task = asyncio.create_task(app.deepgram_websocket(websocket))
            try:
                await upstream.messages.put(json.dumps({
                    "type": "TurnInfo", "event": "EndOfTurn", "transcript": "Hallo",
                }))
                events = [await asyncio.wait_for(websocket.events.get(), 1) for _ in range(3)]
                self.assertEqual([event["event"] for event in events], [
                    "end_of_turn", "jev_prediction", "choice_reset",
                ])
                self.assertEqual(events[-1]["score"], 0)
                self.assertFalse(websocket.closed)
                self.assertFalse(task.done())
                self.assertEqual(app.get_last_prediction(), app.IDLE_PREDICTION)
                self.assertEqual(display.call_args_list[-1].args, (0,))
                audio.assert_called_once()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                app.last_prediction = None


if __name__ == "__main__":
    unittest.main()
