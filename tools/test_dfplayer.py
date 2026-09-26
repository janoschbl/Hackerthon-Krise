"""Play the fixed file /06/004.mp3 on the Pi's DFPlayer, without network input.

Run on the Pi: .venv/bin/python tools/test_dfplayer.py
"""

from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dfplayer_audio import DFPlayer  # noqa: E402

def main():
    player = None
    try:
        player = DFPlayer(0, 14, 15, 26)
        player.setPlaybackSource(2)
        time.sleep(0.5)
        player.setVolume(30)
        time.sleep(0.5)
        print("Spiele /06/004.mp3 über GPIO 14 (TX), 15 (RX), 26 (BUSY) …", flush=True)
        started = time.monotonic()
        player.playTrack(6, 4)
        busy_seen = False
        for _ in range(200):
            if player.queryBusy():
                busy_seen = True
                break
            time.sleep(0.025)
        print("BUSY wurde LOW: Wiedergabe gestartet." if busy_seen
              else "BUSY blieb HIGH: Wiedergabe nicht bestätigt.")
        if busy_seen:
            print(f"Zeit ab Play-Befehl: {time.monotonic() - started:.3f} s")
    finally:
        if player is not None:
            player.close()

if __name__ == "__main__":
    main()
