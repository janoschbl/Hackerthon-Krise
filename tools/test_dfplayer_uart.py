"""Test a DFPlayer Mini on Raspberry Pi 5 UART0 (GPIO14 TX, GPIO15 RX).

Run on the Pi while krise-backend.service is stopped:
    .venv/bin/python tools/test_dfplayer_uart.py

The DFPlayer RX is connected to physical header pin 8 (GPIO14), its TX to
pin 10 (GPIO15), and both devices share ground. Plays /06/004.mp3.
"""

import argparse
import time

import lgpio
import serial


def packet(command: int, high: int = 0, low: int = 0) -> bytes:
    payload = (0xFF, 0x06, command, 0x01, high, low)
    checksum = (-sum(payload)) & 0xFFFF
    return bytes((0x7E, *payload, checksum >> 8, checksum & 0xFF, 0xEF))


def send(port: serial.Serial, command: int, high: int = 0, low: int = 0) -> None:
    data = packet(command, high, low)
    port.write(data)
    port.flush()
    print(f"TX 0x{command:02X}: {data.hex(' ')}", flush=True)


def send_short(port: serial.Serial, command: int, high: int = 0, low: int = 0) -> None:
    data = bytes((0x7E, 0xFF, 0x06, command, 0, high, low, 0xEF))
    port.write(data)
    port.flush()
    print(f"TX kurz 0x{command:02X}: {data.hex(' ')}", flush=True)


def listen(port: serial.Serial, gpio: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    received = bytearray()
    busy_seen = False
    while time.monotonic() < deadline:
        received.extend(port.read(32))
        busy_seen |= lgpio.gpio_read(gpio, 26) == 0
    if received:
        print(f"RX: {received.hex(' ')}", flush=True)
    else:
        print("RX: keine Antwort", flush=True)
    return busy_seen


def play_fast(port: serial.Serial, gpio: int) -> None:
    started = time.monotonic()
    send(port, 0x09, low=2)  # TF card needs time to rebuild its file index
    time.sleep(0.5)
    send(port, 0x06, low=20)
    time.sleep(0.5)
    send(port, 0x0F, high=6, low=4)
    sent_at = time.monotonic()
    received = bytearray()
    deadline = sent_at + 2
    while time.monotonic() < deadline:
        received.extend(port.read(32))
        if lgpio.gpio_read(gpio, 26) == 0:
            print(f"BUSY LOW nach {time.monotonic() - started:.3f} s ab Start "
                  f"({time.monotonic() - sent_at:.3f} s nach Play).", flush=True)
            break
    else:
        print("BUSY blieb HIGH: Wiedergabe nicht bestätigt.", flush=True)
    print(f"RX: {received.hex(' ') if received else 'keine Antwort'}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnose", action="store_true", help="Reset und ausführliche Abfragen")
    args = parser.parse_args()
    gpio = lgpio.gpiochip_open(0)
    try:
        lgpio.gpio_claim_input(gpio, 26, lgpio.SET_PULL_UP)
        with serial.Serial("/dev/ttyAMA0", 9600, timeout=0.025) as port:
            port.reset_input_buffer()
            if not args.diagnose:
                play_fast(port, gpio)
                return
            print("Setze DFPlayer zurück und warte auf Startmeldung …", flush=True)
            send(port, 0x0C)
            listen(port, gpio, 4)
            send(port, 0x42)  # playback status
            listen(port, gpio, 1)
            send(port, 0x47)  # number of files on TF card
            listen(port, gpio, 1)
            send(port, 0x09, low=2)  # select TF card
            listen(port, gpio, 1)
            send(port, 0x06, low=20)  # volume
            listen(port, gpio, 0.5)
            print("Spiele /06/004.mp3 …", flush=True)
            send(port, 0x0F, high=6, low=4)
            busy_seen = listen(port, gpio, 5)
            print("BUSY wurde LOW: Wiedergabe gestartet." if busy_seen
                  else "BUSY blieb HIGH: Wiedergabe nicht bestätigt.", flush=True)
            if not busy_seen:
                print("Teste kurzes 8-Byte-Format über Hardware-UART …", flush=True)
                send_short(port, 0x09, low=2)
                listen(port, gpio, 0.5)
                send_short(port, 0x06, low=20)
                listen(port, gpio, 0.5)
                send_short(port, 0x0F, high=6, low=4)
                busy_seen = listen(port, gpio, 5)
                if not busy_seen:
                    print("Teste globalen Track 4 …", flush=True)
                    send_short(port, 0x03, low=4)
                    busy_seen = listen(port, gpio, 5)
                print("BUSY wurde LOW: Wiedergabe gestartet." if busy_seen
                      else "BUSY blieb HIGH: Wiedergabe nicht bestätigt.", flush=True)
    finally:
        lgpio.gpiochip_close(gpio)


if __name__ == "__main__":
    main()
