"""Standalone reader: real emulated serial IO and one-second scheduling."""

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import unittest
from unittest.mock import Mock, patch

import read_pressure as reader
from test_serial_integration import EmulatedTPG261


class StandaloneReaderTests(unittest.TestCase):
    def test_reads_real_emulated_serial_controller_and_converts_pascal(self):
        with EmulatedTPG261(unit=b"2", pressure=b"0,1.3332E-06") as peer:
            with reader.serial.Serial(peer.port, 9600, timeout=1, write_timeout=1) as port:
                reader.write(port, b"\x03")
                pressure = reader.read_pressure(port, synchronizing=True)
            self.assertAlmostEqual(pressure, 1.3332e-8 / 1.3332236842105263)
            self.assertEqual(peer.messages, [b"\x03", b"UNI\r\n", b"\x05", b"PR1\r\n", b"\x05"])

    def test_unit_conversion_and_rejection_of_fault_placeholders(self):
        for unit, expected in ((b"0", 1 / 1.3332236842105263), (b"1", 1),
                               (b"2", .01 / 1.3332236842105263)):
            with patch.object(reader, "query", side_effect=[unit, b"0,1.0000E+00"]):
                self.assertAlmostEqual(reader.read_pressure(Mock()), expected)
        for status in range(1, 7):
            with self.subTest(status=status), patch.object(reader, "query", side_effect=[
                b"1", f"{status},2.0000E-02".encode()
            ]), self.assertRaises(reader.GaugeError):
                reader.read_pressure(Mock())

    def test_invalid_and_nonfinite_pressure_is_rejected(self):
        for raw in (b"garbage", b"0,1.0000E+999", b"0,-1.0000E-01"):
            with self.subTest(raw=raw), patch.object(reader, "query", side_effect=[b"1", raw]), self.assertRaises(reader.GaugeError):
                reader.read_pressure(Mock())

    def test_no_reply_is_reported(self):
        port = Mock()
        port.read_until.return_value = b""
        with self.assertRaisesRegex(reader.GaugeError, "No reply"):
            reader.read_line(port)

    def test_controller_reply_with_padding_after_comma(self):
        with EmulatedTPG261(unit=b"1", pressure=b"0, 4.0600E-08") as peer:
            with reader.serial.Serial(peer.port, 9600, timeout=1, write_timeout=1) as port:
                self.assertEqual(reader.read_pressure(port, synchronizing=True), 4.0600e-8)

    def test_padding_does_not_hide_a_sensor_fault_or_invalid_number(self):
        for raw in (b"5, 2.0000E-02", b"0, -4.0600E-08", b"0, 4.0600E -08",
                    b"0, 4.0600E-08,extra"):
            with self.subTest(raw=raw), patch.object(reader, "query", side_effect=[b"1", raw]), self.assertRaises(reader.GaugeError):
                reader.read_pressure(Mock())

    def test_one_second_schedule_accounts_for_read_time_and_closes_on_ctrl_c(self):
        clock = {"now": 0.0}
        starts = []
        port = Mock()
        port.write.side_effect = len

        def sample(*args):
            starts.append(clock["now"])
            if len(starts) == 3:
                raise KeyboardInterrupt
            clock["now"] += .2
            return 2.1e-8

        def sleep(seconds):
            clock["now"] += seconds

        output = StringIO()
        with patch.object(reader.serial, "Serial", return_value=port), \
                patch.object(reader, "read_pressure", side_effect=sample), \
                patch.object(reader.time, "monotonic", side_effect=lambda: clock["now"]), \
                patch.object(reader.time, "sleep", side_effect=sleep), redirect_stdout(output):
            reader.main()
        self.assertEqual(starts, [0, 1, 2])
        self.assertEqual(output.getvalue(), "2.100e-08 Torr\n" * 2)
        port.close.assert_called_once()

    def test_error_prints_no_fake_pressure_and_reconnects(self):
        port = Mock()
        port.write.side_effect = len
        output, errors = StringIO(), StringIO()
        with patch.object(reader.serial, "Serial", return_value=port) as connect, \
                patch.object(reader, "read_pressure", side_effect=[reader.GaugeError("No reply"), 2.1e-8, KeyboardInterrupt]), \
                patch.object(reader.time, "sleep"), redirect_stdout(output), redirect_stderr(errors):
            reader.main()
        self.assertEqual(output.getvalue(), "2.100e-08 Torr\n")
        self.assertIn("ERROR: No reply", errors.getvalue())
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(port.close.call_count, 2)


if __name__ == '__main__':
    unittest.main()
