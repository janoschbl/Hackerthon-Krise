import unittest
from unittest.mock import patch

from dfplayer_audio import AUDIO_TOX, DFPlayer, command_packet, select_track


class DFPlayerProtocolTests(unittest.TestCase):
    def test_packets_match_dfplayer_checksum_and_folder_command(self):
        for command, first, second in ((0x06, 0, 30), (0x0F, 6, 4)):
            packet = command_packet(command, first, second)
            self.assertEqual((packet[0], packet[-1], len(packet)), (0x7E, 0xEF, 10))
            self.assertEqual(packet[4], 1)  # request DFPlayer feedback on UART RX
            self.assertEqual((sum(packet[1:7]) + (packet[7] << 8 | packet[8])) & 0xFFFF, 0)
            self.assertEqual(packet[3:7], bytes((command, 1, first, second)))

    def test_track_mapping_covers_every_score_and_volume_edge(self):
        with patch('dfplayer_audio.random.choice', side_effect=lambda values: values[0]):
            for score in range(2, 10):
                self.assertEqual(select_track(score, None), AUDIO_TOX[score][0])
            self.assertIsNone(select_track(0, None))
            self.assertIsNone(select_track(1, 0))
            self.assertEqual(select_track(0, 0.33), 3)
            self.assertEqual(select_track(1, 1), 4)

    def test_hardware_uart_writes_complete_packet(self):
        class FakePort:
            def __init__(self):
                self.written = None
                self.flushed = False
            def write(self, data):
                self.written = data
            def flush(self):
                self.flushed = True
        port = FakePort()
        player = DFPlayer.__new__(DFPlayer)
        player.port = port
        player.sendcmd(0x0F, 6, 4)
        self.assertEqual(port.written, command_packet(0x0F, 6, 4))
        self.assertTrue(port.flushed)


if __name__ == '__main__':
    unittest.main()
