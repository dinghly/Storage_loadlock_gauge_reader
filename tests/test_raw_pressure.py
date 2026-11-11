"""Raw pressure replies must be preserved, not interpreted as measurements."""

from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import Mock, patch

import read_raw_pressure as reader
from test_serial_integration import EmulatedTPG261


class RawPressureTests(unittest.TestCase):
    def test_real_serial_reply_is_unchanged_including_padding_and_terminator(self):
        with EmulatedTPG261(pressure=b"0, 4.0600E-08") as peer:
            with reader.serial.Serial(peer.port, 9600, timeout=1) as port:
                self.assertEqual(reader.query_pressure(port), b"0, 4.0600E-08\r\n")
            self.assertEqual(peer.messages, [b"PR1\r\n", b"\x05"])

    def test_fault_and_non_numeric_payloads_are_preserved_as_raw_bytes(self):
        for raw in (b"5, 2.0000E-02\r\n", b"unexpected\r\n", b"", b"partial"):
            port = Mock()
            port.read_until.side_effect = [b"\x06\r\n", raw]
            self.assertEqual(reader.query_pressure(port), raw)

    def test_no_enq_is_sent_without_ack(self):
        for ack in (b"", b"\x15\r\n", b"garbage\r\n"):
            port = Mock()
            port.read_until.return_value = ack
            with self.assertRaises(RuntimeError):
                reader.query_pressure(port)
            port.write.assert_called_once_with(b"PR1\r\n")

    def test_main_prints_bytes_representation_and_closes_on_ctrl_c(self):
        port = Mock()
        port.__enter__ = Mock(return_value=port)
        port.__exit__ = Mock(return_value=False)
        output = StringIO()
        with patch.object(reader.serial, "Serial", return_value=port), \
                patch.object(reader, "query_pressure", side_effect=[b"0, 4.0600E-08\r\n", KeyboardInterrupt]), \
                patch.object(reader.time, "sleep"), redirect_stdout(output):
            self.assertEqual(reader.main(), 0)
        self.assertEqual(output.getvalue(), "b'0, 4.0600E-08\\r\\n'\n")
        port.__exit__.assert_called_once()
