"""Play the fixed file /06/004.mp3 on the Pi's DFPlayer, without network input.

Run on the Pi: .venv/bin/python tools/test_dfplayer.py
"""

from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dfplayer_audio import DFPlayer  # noqa: E402

SERVICE = "krise-backend.service"


def main():
    was_running = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", SERVICE], check=False
    ).returncode == 0
    if was_running:
        subprocess.run(["systemctl", "--user", "stop", SERVICE], check=True)

    player = None
    try:
        player = DFPlayer(0, 5, 22, 26)
        time.sleep(1)
        player.setVolume(30)
        print("Spiele /06/004.mp3 über GPIO 5 (TX), 22 (RX), 26 (BUSY) …", flush=True)
        player.playTrack(6, 4)
        busy_seen = False
        for _ in range(200):
            busy_seen |= player.queryBusy()
            time.sleep(0.025)
        print("BUSY wurde LOW: Wiedergabe gestartet." if busy_seen
              else "BUSY blieb HIGH: Wiedergabe nicht bestätigt.")
    finally:
        if player is not None:
            player.close()
        if was_running:
            subprocess.run(["systemctl", "--user", "start", SERVICE], check=True)


if __name__ == "__main__":
    main()
