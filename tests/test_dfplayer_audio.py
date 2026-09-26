import unittest
from unittest.mock import patch

from dfplayer_audio import AUDIO_TOX, DFPlayer, command_packet, select_track


class DFPlayerProtocolTests(unittest.TestCase):
    def test_packets_match_dfplayer_checksum_and_folder_command(self):
        for command, first, second in ((0x06, 0, 30), (0x0F, 6, 4)):
            packet = command_packet(command, first, second)
            self.assertEqual((packet[0], packet[-1], len(packet)), (0x7E, 0xEF, 10))
            self.assertEqual(packet[4], 0)  # no ACK on software TX
            self.assertEqual((sum(packet[1:7]) + (packet[7] << 8 | packet[8])) & 0xFFFF, 0)
            self.assertEqual(packet[3:7], bytes((command, 0, first, second)))

    def test_track_mapping_covers_every_score_and_volume_edge(self):
        with patch('dfplayer_audio.random.choice', side_effect=lambda values: values[0]):
            for score in range(2, 10):
                self.assertEqual(select_track(score, None), AUDIO_TOX[score][0])
            self.assertIsNone(select_track(0, None))
            self.assertIsNone(select_track(1, 0))
            self.assertEqual(select_track(0, 0.33), 3)
            self.assertEqual(select_track(1, 1), 4)

    def test_software_uart_sends_8n1_lsb_first(self):
        class FakeGPIO:
            TX_WAVE = 1
            def __init__(self):
                self.pulses = None
            def pulse(self, bit, mask, delay):
                return bit, mask, delay
            def tx_wave(self, handle, pin, pulses):
                self.pulses = pulses
            def tx_busy(self, handle, pin, kind):
                return False
        gpio = FakeGPIO()
        player = DFPlayer.__new__(DFPlayer)
        player.gpio, player.handle, player.tx_pin = gpio, 1, 5
        with patch('dfplayer_audio.time.sleep'):
            player.sendcmd(0x0F, 6, 4)
        packet = command_packet(0x0F, 6, 4)
        self.assertEqual(len(gpio.pulses), 100)
        for byte, offset in zip(packet, range(0, 100, 10)):
            bits = [pulse[0] for pulse in gpio.pulses[offset:offset + 10]]
            self.assertEqual(bits, [0, *(byte >> index & 1 for index in range(8)), 1])
            self.assertTrue(all(pulse[1:] == (1, 104) for pulse in gpio.pulses[offset:offset + 10]))


if __name__ == '__main__':
    unittest.main()
