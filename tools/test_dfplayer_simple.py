"""Try the checksum-free DFPlayer packet used by SimpleDFPlayerMini.

Run on the Pi while krise-backend.service is stopped:
    .venv/bin/python tools/test_dfplayer_simple.py

This keeps the existing /06/004.mp3 test unchanged. BCM GPIO 5 sends the
commands; GPIO 26 reports the player's active-low BUSY output.
"""

import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from picodfplayer import DFPlayer  # noqa: E402


def send_short(player: DFPlayer, command: int, high: int = 0, low: int = 0):
    """Send the eight-byte, no-checksum format from the linked project."""
    packet = bytes((0x7E, 0xFF, 0x06, command, 0, high, low, 0xEF))
    pulses = []
    for byte in packet:
        for bit in (0, *(byte >> offset & 1 for offset in range(8)), 1):
            pulses.append(player.gpio.pulse(bit, 1, 104))
    player.gpio.tx_wave(player.handle, player.tx_pin, pulses)
    while player.gpio.tx_busy(player.handle, player.tx_pin, player.gpio.TX_WAVE):
        time.sleep(0.001)
    time.sleep(0.2)
    print(f"TX: {packet.hex(' ')}", flush=True)


def wait_busy(player: DFPlayer) -> bool:
    busy_seen = False
    for _ in range(200):
        busy_seen |= player.queryBusy()
        time.sleep(0.025)
    print("BUSY wurde LOW: Wiedergabe gestartet." if busy_seen
          else "BUSY blieb HIGH: Wiedergabe nicht bestätigt.")
    return busy_seen


def main():
    player = DFPlayer(0, 5, 22, 26)
    try:
        time.sleep(1)
        # Match the linked project's command format, selecting the TF card.
        send_short(player, 0x09, low=2)
        send_short(player, 0x06, low=30)
        print("Spiele /06/004.mp3 mit 8-Byte-Befehlen …", flush=True)
        send_short(player, 0x0F, high=6, low=4)
        if not wait_busy(player):
            print("Versuche globalen Track 4 (kann eine andere Datei sein) …", flush=True)
            send_short(player, 0x03, low=4)
            wait_busy(player)
    finally:
        player.close()


if __name__ == "__main__":
    main()
