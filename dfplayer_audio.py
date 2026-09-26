"""Linux Raspberry Pi adapter for the PicoDFPlayer command protocol.

PicoDFPlayer uses MicroPython's machine.UART, which is unavailable on a Pi 5
running Linux. GPIO 5/6 are not a TX/RX hardware UART pair on that board, so
lgpio sends the same DFPlayer packets as a timed 9600-baud wave on GPIO 5.
Feedback is disabled; GPIO 6 is reserved as an RX input and GPIO 26 reads BUSY.
"""

import logging
import queue
import random
import threading
import time

logger = logging.getLogger(__name__)

AUDIO_TOX = {
    2: [1],
    3: [1, 10],
    4: [1, 7, 10],
    5: [7, 9, 11],
    6: [6, 9, 11],
    7: [5, 6],
    8: [2, 5, 8],
    9: [2, 8],
}
AUDIO_VOL = {1: [3], 2: [4]}


def select_track(score: int, volume: float | None) -> int | None:
    """Pick a track in SD-card folder 06 using the supplied score and RMS."""
    if not 0 <= score <= 9:
        raise ValueError("score muss zwischen 0 und 9 liegen")
    if score >= 2:
        return random.choice(AUDIO_TOX[score])
    if volume is None:
        return None
    # The original volume rule rounds RMS percentages into levels. Clamp the
    # top level to 2 because the supplied table has no level 3.
    level = min(2, max(0, round(max(0.0, min(1.0, volume)) * 100 / 33)))
    return random.choice(AUDIO_VOL[level]) if level in AUDIO_VOL else None


def command_packet(command: int, parameter1: int, parameter2: int) -> bytes:
    """DFPlayer 10-byte command, as in PicoDFPlayer but without an ACK."""
    if any(not 0 <= value <= 255 for value in (command, parameter1, parameter2)):
        raise ValueError("DFPlayer-Parameter außerhalb 0..255")
    payload = (0xFF, 0x06, command, 0x00, parameter1, parameter2)
    checksum = (-sum(payload)) & 0xFFFF
    return bytes((0x7E, *payload, checksum >> 8, checksum & 0xFF, 0xEF))


class DFPlayer:
    """The user's DFPlayer(0, 5, 6, 26) pinout on Raspberry Pi 5 Linux."""

    def __init__(self, uart_instance=0, tx_pin=5, rx_pin=6, busy_pin=26):
        if uart_instance != 0:
            raise ValueError("Nur DFPlayer-UART 0 ist konfiguriert")
        import lgpio

        self.gpio = lgpio
        self.handle = lgpio.gpiochip_open(0)
        self.tx_pin, self.rx_pin, self.busy_pin = tx_pin, rx_pin, busy_pin
        try:
            lgpio.group_claim_output(self.handle, [tx_pin], [1])
            lgpio.gpio_claim_input(self.handle, rx_pin)
            lgpio.gpio_claim_input(self.handle, busy_pin, lgpio.SET_PULL_UP)
        except Exception:
            lgpio.gpiochip_close(self.handle)
            raise

    def sendcmd(self, command: int, parameter1: int, parameter2: int, latency: float = 0.5):
        packet = command_packet(command, parameter1, parameter2)
        # 8N1, LSB first. Each bit lasts about 104 us (9600 baud).
        pulses = []
        for byte in packet:
            for bit in (0, *(byte >> offset & 1 for offset in range(8)), 1):
                pulses.append(self.gpio.pulse(bit, 1, 104))
        self.gpio.tx_wave(self.handle, self.tx_pin, pulses)
        while self.gpio.tx_busy(self.handle, self.tx_pin, self.gpio.TX_WAVE):
            time.sleep(0.001)
        time.sleep(latency)

    def setVolume(self, volume: int):
        if not 0 <= volume <= 30:
            raise ValueError("DFPlayer-Lautstärke muss 0..30 sein")
        self.sendcmd(0x06, 0, volume)

    def playTrack(self, folder: int, track: int):
        if not 1 <= folder <= 99 or not 1 <= track <= 255:
            raise ValueError("Ungültiger DFPlayer-Ordner oder Track")
        self.sendcmd(0x0F, folder, track, latency=0.05)

    def queryBusy(self) -> bool:
        return self.gpio.gpio_read(self.handle, self.busy_pin) == 0

    def close(self):
        self.gpio.gpiochip_close(self.handle)


class AudioPlayer:
    """Runs hardware I/O off the request path and plays each prediction once."""

    def __init__(self):
        self._events = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = None
        self.error = None
        self.last_track = None
        self.last_played_at = None
        self.last_busy_seen = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="dfplayer-audio", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def play(self, score: int, volume: float | None):
        track = select_track(score, volume)
        if track is None:
            return
        event = (time.monotonic(), track)
        try:
            self._events.put_nowait(event)
        except queue.Full:
            try:
                self._events.get_nowait()
            except queue.Empty:
                pass
            try:
                self._events.put_nowait(event)
            except queue.Full:
                pass

    def _run(self):
        player = None
        try:
            player = DFPlayer(0, 5, 6, 26)
            if self._stop.wait(1):
                return
            player.setVolume(30)
            while not self._stop.is_set():
                try:
                    created, track = self._events.get(timeout=0.2)
                except queue.Empty:
                    continue
                while not self._stop.is_set() and time.monotonic() - created <= 3:
                    if not player.queryBusy():
                        player.playTrack(6, track)
                        self.last_track = track
                        self.last_played_at = time.time()
                        self.last_busy_seen = False
                        deadline = time.monotonic() + 1
                        while time.monotonic() < deadline and not self._stop.is_set():
                            if player.queryBusy():
                                self.last_busy_seen = True
                                break
                            self._stop.wait(0.025)
                        if self.last_busy_seen:
                            logger.info("DFPlayer BUSY bestätigt Ordner 06, Track %02d", track)
                        else:
                            logger.warning("DFPlayer-Befehl gesendet, aber BUSY blieb hoch (Track %02d)", track)
                        break
                    self._stop.wait(0.1)
        except Exception as exc:
            self.error = exc
            logger.exception("DFPlayer-Audio konnte nicht gestartet werden")
        finally:
            if player is not None:
                player.close()
