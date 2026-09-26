"""Test the DFPlayer connected directly to Raspberry Pi GPIO 5/6/26.

Run on the Pi with this project's virtual environment, for example:
    .venv/bin/python tools/test_dfplayer.py --track 1

The script temporarily stops krise-backend.service so it can own the GPIOs.
"""

import argparse
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dfplayer_audio import DFPlayer  # noqa: E402

SERVICE = "krise-backend.service"


def service_active() -> bool:
    return subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", SERVICE],
        check=False,
    ).returncode == 0


def main():
    parser = argparse.ArgumentParser(description="DFPlayer-Audiotest am Raspberry Pi 5")
    parser.add_argument("--folder", type=int, default=6, help="SD-Karten-Ordner, Standard: 6")
    parser.add_argument("--track", type=int, default=1, help="Tracknummer, Standard: 1")
    parser.add_argument("--volume", type=int, default=25, help="Lautstärke 0..30, Standard: 25")
    parser.add_argument("--duration", type=float, default=8, help="BUSY-Beobachtung in Sekunden")
    args = parser.parse_args()
    if not 1 <= args.folder <= 99 or not 1 <= args.track <= 255:
        parser.error("Ordner muss 1..99 und Track 1..255 sein")
    if not 0 <= args.volume <= 30 or args.duration <= 0:
        parser.error("Lautstärke muss 0..30 und Dauer > 0 sein")

    was_running = service_active()
    if was_running:
        print(f"Stoppe {SERVICE} vorübergehend …", flush=True)
        subprocess.run(["systemctl", "--user", "stop", SERVICE], check=True)

    player = None
    try:
        print("Pins: GPIO 5 → DFPlayer RX, GPIO 6 ← DFPlayer TX, GPIO 26 ← BUSY")
        print(f"Erwartete Datei auf der DFPlayer-SD-Karte: /{args.folder:02d}/{args.track:03d}.mp3")
        player = DFPlayer(0, 5, 6, 26)
        time.sleep(1)
        print(f"Setze Lautstärke auf {args.volume} …", flush=True)
        player.setVolume(args.volume)
        print("BUSY vor dem Start:", "LOW (spielt)" if player.queryBusy() else "HIGH (frei)")
        print(f"Sende Play-Befehl für Ordner {args.folder:02d}, Track {args.track:03d} …", flush=True)
        player.playTrack(args.folder, args.track)

        saw_busy = False
        previous = None
        started = time.monotonic()
        while time.monotonic() - started < args.duration:
            busy = player.queryBusy()
            saw_busy |= busy
            if busy != previous:
                print(f"{time.monotonic() - started:.2f}s: BUSY {'LOW (spielt)' if busy else 'HIGH (frei)'}", flush=True)
                previous = busy
            time.sleep(0.025)
        print("Ergebnis:", "BUSY wurde LOW; Modul hat Wiedergabe gestartet." if saw_busy
              else "BUSY blieb HIGH; keine Wiedergabe vom Modul bestätigt.")
    finally:
        if player is not None:
            player.close()
        if was_running:
            print(f"Starte {SERVICE} wieder …", flush=True)
            subprocess.run(["systemctl", "--user", "start", SERVICE], check=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nTest abgebrochen.")
