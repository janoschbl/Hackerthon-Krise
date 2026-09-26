"""Play the pre-rendered SVG avatar on the Pi's ST7735 SPI display.

The SVG and tools in assets/ and tools/ are source material; the Pi plays the
pre-rendered PNGs so no browser or SVG renderer is needed at runtime.
"""

from pathlib import Path
import logging
import random
import threading
import time

ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "frames"
NUM_POSES = 10
TRANSITION_FPS = 15
IDLE_FPS = 20
IDLE_EVENT_CHANCE = 0.3

# BCM GPIO pins; SPI0 CE0, MOSI and SCLK use the Pi hardware SPI pins.
SPI_PORT = 0
SPI_DEVICE = 0
GPIO_DC = 24
GPIO_RST = 25
DISPLAY_WIDTH = 160
DISPLAY_HEIGHT = 128
ROTATE = 0
BGR = True
H_OFFSET = 0
V_OFFSET = 0
DISPLAY_STARTUP_DELAY = 5.0
RESET_HOLD_TIME = 0.1
RESET_RELEASE_TIME = 0.2
DISPLAY_WARMUP_FRAME_SECONDS = 1.0

logger = logging.getLogger(__name__)


def init_display():
    from luma.core.interface.serial import spi
    from luma.lcd.device import st7735

    serial = spi(
        port=SPI_PORT, device=SPI_DEVICE, gpio_DC=GPIO_DC, gpio_RST=GPIO_RST,
        reset_hold_time=RESET_HOLD_TIME,
        reset_release_time=RESET_RELEASE_TIME,
    )
    return st7735(
        serial, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT, rotate=ROTATE,
        bgr=BGR, h_offset=H_OFFSET, v_offset=V_OFFSET,
    )


def load_frames(display):
    from PIL import Image

    size = (display.width, display.height)

    def load(path):
        with Image.open(path) as image:
            return image.convert("RGB").resize(size)

    def sequence(directory):
        paths = sorted(directory.glob("frame_*.png"))
        if not paths:
            raise FileNotFoundError(f"Keine Animationsframes in {directory}")
        return [load(path) for path in paths]

    poses = [load(ASSETS_DIR / f"pose_{i:02d}.png") for i in range(NUM_POSES)]
    idle = [sequence(ASSETS_DIR / f"idle_{i:02d}") for i in range(NUM_POSES)]
    events = [
        [sequence(path) for path in sorted(ASSETS_DIR.glob(f"idle_{i:02d}_event_*"))]
        for i in range(NUM_POSES)
    ]
    transitions = [sequence(ASSETS_DIR / f"transition_{i:02d}") for i in range(NUM_POSES)]
    return poses, idle, events, transitions


class AnimationPlayer:
    """Owns the display in one thread; set_score can be called from FastAPI."""

    def __init__(self):
        self._target = 0
        self._current = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.error = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="avatar-display", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def set_score(self, score: int):
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score < NUM_POSES:
            raise ValueError("score muss eine ganze Zahl von 0 bis 9 sein")
        with self._lock:
            self._target = score

    def _target_score(self):
        with self._lock:
            return self._target

    def _show_sequence(self, display, frames, fps):
        for frame in frames:
            if self._stop.is_set():
                return
            display.display(frame)
            self._stop.wait(1 / fps)

    def _run(self):
        try:
            # Give the display power time to settle after a Pi reboot.
            if self._stop.wait(DISPLAY_STARTUP_DELAY):
                return
            display = init_display()
            from PIL import Image
            size = (display.width, display.height)
            for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
                display.display(Image.new("RGB", size, color))
                if self._stop.wait(DISPLAY_WARMUP_FRAME_SECONDS):
                    return
            poses, idle, events, transitions = load_frames(display)
            display.display(poses[0])
            idle_index = 0
            event_frames = None
            event_index = 0
            while not self._stop.is_set():
                target = self._target_score()
                if target != self._current:
                    forward = (target - self._current) % NUM_POSES
                    backward = (self._current - target) % NUM_POSES
                    if forward <= backward:
                        index = self._current
                        self._show_sequence(display, transitions[index], TRANSITION_FPS)
                        self._current = (index + 1) % NUM_POSES
                    else:
                        index = (self._current - 1) % NUM_POSES
                        self._show_sequence(display, reversed(transitions[index]), TRANSITION_FPS)
                        self._current = index
                    idle_index = 0
                    event_frames = None
                    continue
                if event_frames is not None:
                    frame = event_frames[event_index]
                    event_index += 1
                    if event_index == len(event_frames):
                        event_frames = None
                        idle_index = 0
                else:
                    frames = idle[self._current]
                    frame = frames[idle_index]
                    idle_index += 1
                    if idle_index == len(frames):
                        idle_index = 0
                        options = events[self._current]
                        if options and random.random() < IDLE_EVENT_CHANCE:
                            event_frames = random.choice(options)
                            event_index = 0
                display.display(frame)
                self._stop.wait(1 / IDLE_FPS)
        except Exception as exc:
            self.error = exc
            logger.exception("Display-Animation konnte nicht gestartet werden")
