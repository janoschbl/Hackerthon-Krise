"""PicoDFPlayer API adapted for Raspberry Pi 5 Linux GPIO.

Based on https://github.com/mannbro/PicoDFPlayer/blob/main/picodfplayer.py
(MIT license in PICODFPLAYER_LICENSE). GPIO 5 and 22 are not a hardware UART
pair on the Pi 5, so lgpio generates the 9600-baud transmit waveform on GPIO 5.
GPIO 22 is reserved as input; GPIO 26 reads the DFPlayer's active-low BUSY pin.
"""

import time


def command_packet(command: int, parameter1: int, parameter2: int) -> bytes:
    """Build a 10-byte DFPlayer command with its two's-complement checksum."""
    if any(not 0 <= value <= 255 for value in (command, parameter1, parameter2)):
        raise ValueError("DFPlayer-Parameter außerhalb 0..255")
    # Match the original PicoDFPlayer packet, including the ACK request.
    payload = (0xFF, 0x06, command, 0x01, parameter1, parameter2)
    checksum = (-sum(payload)) & 0xFFFF
    return bytes((0x7E, *payload, checksum >> 8, checksum & 0xFF, 0xEF))


class DFPlayer:
    """DFPlayer control on Pi 5 BCM GPIO 5 (TX), 22 (RX), 26 (BUSY)."""

    UART_BAUD_RATE = 9600
    COMMAND_LATENCY = 0.5

    def __init__(self, uartInstance=0, txPin=5, rxPin=22, busyPin=26):
        if uartInstance != 0:
            raise ValueError("Nur DFPlayer-UART 0 ist konfiguriert")
        if len({txPin, rxPin, busyPin}) != 3:
            raise ValueError("DFPlayer-Pins müssen verschieden sein")
        import lgpio

        self.gpio = lgpio
        self.handle = lgpio.gpiochip_open(0)
        self.tx_pin, self.rx_pin, self.busy_pin = txPin, rxPin, busyPin
        try:
            lgpio.group_claim_output(self.handle, [txPin], [1])
            lgpio.gpio_claim_input(self.handle, rxPin)
            lgpio.gpio_claim_input(self.handle, busyPin, lgpio.SET_PULL_UP)
        except Exception:
            lgpio.gpiochip_close(self.handle)
            raise

    def sendcmd(self, command, parameter1, parameter2, latency=None):
        packet = command_packet(command, parameter1, parameter2)
        # lgpio.pulse(bits, mask, delay): one GPIO in the claimed group.
        # UART 8N1 sends a LOW start bit, eight LSB-first data bits, HIGH stop.
        pulses = []
        for byte in packet:
            for bit in (0, *(byte >> offset & 1 for offset in range(8)), 1):
                pulses.append(self.gpio.pulse(bit, 1, 104))
        self.gpio.tx_wave(self.handle, self.tx_pin, pulses)
        while self.gpio.tx_busy(self.handle, self.tx_pin, self.gpio.TX_WAVE):
            time.sleep(0.001)
        time.sleep(self.COMMAND_LATENCY if latency is None else latency)

    def queryBusy(self):
        return self.gpio.gpio_read(self.handle, self.busy_pin) == 0

    def nextTrack(self):
        self.sendcmd(0x01, 0, 0)

    def prevTrack(self):
        self.sendcmd(0x02, 0, 0)

    def increaseVolume(self):
        self.sendcmd(0x04, 0, 0)

    def decreaseVolume(self):
        self.sendcmd(0x05, 0, 0)

    def setVolume(self, volume):
        if not 0 <= volume <= 30:
            raise ValueError("DFPlayer-Lautstärke muss 0..30 sein")
        self.sendcmd(0x06, 0, volume)

    def setEQ(self, eq):
        self.sendcmd(0x07, 0, eq)

    def setPlaybackMode(self, mode):
        self.sendcmd(0x08, 0, mode)

    def setPlaybackSource(self, source):
        self.sendcmd(0x09, 0, source)

    def standby(self):
        self.sendcmd(0x0A, 0, 0)

    def normalWorking(self):
        self.sendcmd(0x0B, 0, 0)

    def reset(self):
        self.sendcmd(0x0C, 0, 0)

    def resume(self):
        self.sendcmd(0x0D, 0, 0)

    def pause(self):
        self.sendcmd(0x0E, 0, 0)

    def playTrack(self, folder, file):
        if not 1 <= folder <= 99 or not 1 <= file <= 255:
            raise ValueError("Ungültiger DFPlayer-Ordner oder Track")
        self.sendcmd(0x0F, folder, file, latency=0.05)

    def playMP3(self, filenum):
        if not 1 <= filenum <= 65535:
            raise ValueError("Ungültige DFPlayer-Dateinummer")
        return self.sendcmd(0x12, filenum >> 8, filenum & 0xFF)

    def init(self, params):
        self.sendcmd(0x3F, 0, params)

    def close(self):
        if self.handle is not None:
            self.gpio.gpiochip_close(self.handle)
            self.handle = None
