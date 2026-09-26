"""DFPlayer audio on Raspberry Pi 5 UART0 (GPIO14 TX, GPIO15 RX)."""

import logging
import queue
import random
import threading
import time

logger = logging.getLogger(__name__)

AUDIO_TOX = {
    2: [1, 12, 6, 7],
    3: [1, 10, 6, 12, 7],
    4: [1, 7, 10, 2],
    5: [7, 9, 11],
    6: [6, 9, 11],
    7: [5, 6],
    8: [2, 5, 8],
    9: [2, 8],
}
AUDIO_VOL = {1: [3], 2: [4]}


def track_options(score: int, volume: float | None) -> list[int]:
    """Return the tracks allowed for this score and RMS."""
    if not 0 <= score <= 9:
        raise ValueError("score muss zwischen 0 und 9 liegen")
    if score >= 2:
        return AUDIO_TOX[score]
    if volume is None:
        return []
    # The original volume rule rounds RMS percentages into levels. Clamp the
    # top level to 2 because the supplied table has no level 3.
    level = min(2, max(0, round(max(0.0, min(1.0, volume)) * 100 / 33)))
    return AUDIO_VOL.get(level, [])


def select_track(score: int, volume: float | None, previous_track: int | None = None) -> int | None:
    """Pick a track in SD-card folder 06 without repeating the last one."""
    candidates = [track for track in track_options(score, volume) if track != previous_track]
    return random.choice(candidates) if candidates else None


def command_packet(command: int, parameter1: int, parameter2: int) -> bytes:
    """DFPlayer 10-byte command with checksum and feedback request."""
    if any(not 0 <= value <= 255 for value in (command, parameter1, parameter2)):
        raise ValueError("DFPlayer-Parameter außerhalb 0..255")
    payload = (0xFF, 0x06, command, 0x01, parameter1, parameter2)
    checksum = (-sum(payload)) & 0xFFFF
    return bytes((0x7E, *payload, checksum >> 8, checksum & 0xFF, 0xEF))


class DFPlayer:
    """UART0 to DFPlayer and GPIO26 for its active-low BUSY signal."""

    def __init__(self, uart_instance=0, tx_pin=14, rx_pin=15, busy_pin=26):
        if (uart_instance, tx_pin, rx_pin) != (0, 14, 15):
            raise ValueError("Pi-5-UART0 nutzt GPIO14 (TX) und GPIO15 (RX)")
        import lgpio
        import serial

        self.gpio = lgpio
        self.port = serial.Serial("/dev/ttyAMA0", 9600, timeout=0, write_timeout=1)
        self.handle = None
        self.tx_pin, self.rx_pin, self.busy_pin = tx_pin, rx_pin, busy_pin
        try:
            self.handle = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_input(self.handle, busy_pin, lgpio.SET_PULL_UP)
        except Exception:
            self.close()
            raise

    def sendcmd(self, command: int, parameter1: int, parameter2: int):
        packet = command_packet(command, parameter1, parameter2)
        self.port.write(packet)
        self.port.flush()

    def setPlaybackSource(self, source: int):
        self.sendcmd(0x09, 0, source)

    def setVolume(self, volume: int):
        if not 0 <= volume <= 30:
            raise ValueError("DFPlayer-Lautstärke muss 0..30 sein")
        self.sendcmd(0x06, 0, volume)

    def playTrack(self, folder: int, track: int):
        if not 1 <= folder <= 99 or not 1 <= track <= 255:
            raise ValueError("Ungültiger DFPlayer-Ordner oder Track")
        self.sendcmd(0x0F, folder, track)

    def queryBusy(self) -> bool:
        return self.gpio.gpio_read(self.handle, self.busy_pin) == 0

    def close(self):
        self.port.close()
        if self.handle is not None:
            self.gpio.gpiochip_close(self.handle)
            self.handle = None


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
        if not track_options(score, volume):
            return
        event = (time.monotonic(), score, volume)
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
            player = DFPlayer(0, 14, 15, 26)
            player.setPlaybackSource(2)
            if self._stop.wait(0.5):
                return
            player.setVolume(30)
            if self._stop.wait(0.5):
                return
            while not self._stop.is_set():
                try:
                    created, score, volume = self._events.get(timeout=0.2)
                except queue.Empty:
                    continue
                while not self._stop.is_set() and time.monotonic() - created <= 3:
                    if not player.queryBusy():
                        track = select_track(score, volume, self.last_track)
                        if track is None:
                            break
                        player.playTrack(6, track)
                        self.last_track = track
                        self.last_played_at = time.time()
                        self.last_busy_seen = False
                        deadline = time.monotonic() + 1.5
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
