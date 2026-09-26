"""Runtime player for a 10-pose animated avatar on a 160x128 RGB SPI display
(ST7735) wired to a Raspberry Pi 5, driven via luma.lcd.

Frames must be pre-rendered first with tools/render_frames.py (they can't be
generated on the Pi since the source SVG uses SMIL/CSS animations that need
a browser engine). This script only loads PNGs and blits them to the panel.

Controls (via keyboard, e.g. over SSH): Up/Down arrow = next/previous pose,
q / Ctrl+C = quit. Each press plays the morph animation between the current
and the target pose before settling on the new one.
"""

import glob
import os
import queue
import random
import select
import sys
import termios
import threading
import time
import tty

from luma.core.interface.serial import spi
from luma.lcd.device import st7735
from PIL import Image

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "frames")
NUM_POSES = 10
TRANSITION_FPS = 15  # playback speed of the morph frames
IDLE_FPS = 20  # playback speed of the per-pose idle (blink/bob) loop

# Display wiring/config - matches the confirmed-working test script.
SPI_PORT = 0
SPI_DEVICE = 0
GPIO_DC = 24
GPIO_RST = 25
DISPLAY_WIDTH = 160
DISPLAY_HEIGHT = 128
ROTATE = 0
BGR = True
H_OFFSET = 0  # shift the image left/right if it's not centered on the panel
V_OFFSET = 0  # shift the image up/down if it's not centered on the panel

# Odds of playing a rare "personality" event instead of looping idle again,
# checked each time the idle loop for the current pose completes a cycle.
IDLE_EVENT_CHANCE = 0.3


# --------------------------------------------------------------------------
# Display setup
# --------------------------------------------------------------------------
def init_display():
    serial = spi(port=SPI_PORT, device=SPI_DEVICE, gpio_DC=GPIO_DC, gpio_RST=GPIO_RST)
    return st7735(
        serial,
        width=DISPLAY_WIDTH,
        height=DISPLAY_HEIGHT,
        rotate=ROTATE,
        bgr=BGR,
        h_offset=H_OFFSET,
        v_offset=V_OFFSET,
    )


# --------------------------------------------------------------------------
# Frame loading
# --------------------------------------------------------------------------
def load_frames(display):
    """Load all pose, idle, and transition PNGs, pre-scaled to the panel size."""
    size = (display.width, display.height)

    def load(path):
        return Image.open(path).convert("RGB").resize(size)

    def load_sequence(pattern):
        frame_paths = sorted(glob.glob(pattern))
        if not frame_paths:
            raise FileNotFoundError(
                f"No frames found at {pattern}. Run tools/render_frames.py first."
            )
        return [load(p) for p in frame_paths]

    poses = [load(os.path.join(ASSETS_DIR, f"pose_{i:02d}.png")) for i in range(NUM_POSES)]

    idle_frames = [
        load_sequence(os.path.join(ASSETS_DIR, f"idle_{i:02d}", "frame_*.png"))
        for i in range(NUM_POSES)
    ]

    # Rare one-off "personality" animations (wink, look-around, ...) that
    # only exist for some poses - empty list if a pose has none rendered.
    idle_events = [
        [
            load_sequence(os.path.join(event_dir, "frame_*.png"))
            for event_dir in sorted(glob.glob(os.path.join(ASSETS_DIR, f"idle_{i:02d}_event_*")))
        ]
        for i in range(NUM_POSES)
    ]

    transitions = [
        load_sequence(os.path.join(ASSETS_DIR, f"transition_{i:02d}", "frame_*.png"))
        for i in range(NUM_POSES)
    ]

    return poses, idle_frames, idle_events, transitions


# --------------------------------------------------------------------------
# Keyboard input (Up/Down arrows over a terminal, e.g. via SSH)
# --------------------------------------------------------------------------
class KeyListener(threading.Thread):
    """Reads raw stdin and posts 'up' / 'down' / 'quit' events to a queue."""

    def __init__(self, event_queue):
        super().__init__(daemon=True)
        self.queue = event_queue
        self._stop = threading.Event()
        self._fd = sys.stdin.fileno()
        self._original_settings = termios.tcgetattr(self._fd)

    def run(self):
        tty.setcbreak(self._fd)
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                if not ready:
                    continue
                ch = sys.stdin.read(1)
                if ch == "\x1b":  # arrow keys arrive as ESC [ A/B
                    ch2 = sys.stdin.read(1)
                    ch3 = sys.stdin.read(1)
                    if ch2 == "[" and ch3 == "A":
                        self.queue.put("up")
                    elif ch2 == "[" and ch3 == "B":
                        self.queue.put("down")
                elif ch in ("q", "Q", "\x03"):
                    self.queue.put("quit")
        finally:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._original_settings)

    def stop(self):
        self._stop.set()


# --------------------------------------------------------------------------
# Playback
# --------------------------------------------------------------------------
class AnimationPlayer:
    def __init__(self, display, poses, idle_frames, idle_events, transitions):
        self.display = display
        self.poses = poses
        self.idle_frames = idle_frames
        self.idle_events = idle_events
        self.transitions = transitions
        self.current_index = 0

    def play_transition(self, index, reverse):
        frames = self.transitions[index]
        if reverse:
            frames = list(reversed(frames))
        delay = 1.0 / TRANSITION_FPS
        for frame in frames:
            self.display.display(frame)
            time.sleep(delay)

    def go_up(self):
        self.play_transition(self.current_index, reverse=False)
        self.current_index = (self.current_index + 1) % NUM_POSES

    def go_down(self):
        previous_index = (self.current_index - 1) % NUM_POSES
        self.play_transition(previous_index, reverse=True)
        self.current_index = previous_index


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    display = init_display()
    poses, idle_frames, idle_events, transitions = load_frames(display)
    player = AnimationPlayer(display, poses, idle_frames, idle_events, transitions)

    events = queue.Queue()
    listener = KeyListener(events)
    listener.start()

    idle_delay = 1.0 / IDLE_FPS
    idle_frame_index = 0
    active_event_frames = None
    active_event_index = 0

    print("Up/Down = switch animation, q = quit.")
    try:
        while True:
            if active_event_frames is not None:
                frame = active_event_frames[active_event_index]
                active_event_index += 1
                if active_event_index >= len(active_event_frames):
                    active_event_frames = None
                    idle_frame_index = 0
            else:
                frames = player.idle_frames[player.current_index]
                frame = frames[idle_frame_index]
                idle_frame_index += 1
                if idle_frame_index >= len(frames):
                    idle_frame_index = 0
                    pose_events = player.idle_events[player.current_index]
                    if pose_events and random.random() < IDLE_EVENT_CHANCE:
                        active_event_frames = random.choice(pose_events)
                        active_event_index = 0
            display.display(frame)
            try:
                event = events.get(timeout=idle_delay)
            except queue.Empty:
                continue
            if event == "up":
                player.go_up()
                idle_frame_index = 0
                active_event_frames = None
            elif event == "down":
                player.go_down()
                idle_frame_index = 0
                active_event_frames = None
            elif event == "quit":
                break
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
        listener.join(timeout=1)


if __name__ == "__main__":
    main()
