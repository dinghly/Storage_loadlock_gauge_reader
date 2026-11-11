"""Protocol tests with an in-memory serial peer, not attached hardware."""

import math
import unittest

from storage_loadlock_gauge_reader.tpg261 import (
    CommunicationError,
    GaugeStatus,
    InvalidReadingError,
    PressureUnit,
    ProtocolError,
    Reading,
    TPG261,
    parse_pressure,
)


class FakeSerial:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.writes = []
        self.closed = False
        self.reset_count = 0
        self.settings = None

    def factory(self, **kwargs):
        self.settings = kwargs
        return self

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass

    def read_until(self, expected, size):
        return self.replies.pop(0) if self.replies else b""

    def reset_input_buffer(self):
        self.reset_count += 1

    def close(self):
        self.closed = True


def response(pressure=b"0,1.0000E-03", unit=b"0"):
    return [b"\x06\r\n", unit + b"\r\n", b"\x06\r\n", pressure + b"\r\n"]


class TPG261ProtocolTests(unittest.TestCase):
    def test_pressure_field_padding_is_accepted_but_malformed_content_is_not(self):
        for reply in ("0, 4.0600E-08", " 0 ,\t4.0600E-08 "):
            with self.subTest(reply=reply):
                reading = parse_pressure(reply, PressureUnit.TORR)
                self.assertEqual(reading.pressure_torr, 4.0600e-8)
        for reply in ("0, 4.0600E -08", "0, 4.0600E-08,extra", "0, "):
            with self.subTest(reply=reply), self.assertRaises(ProtocolError):
                parse_pressure(reply, PressureUnit.TORR)
        fault = parse_pressure("5, 2.0000E-02", PressureUnit.MBAR)
        with self.assertRaises(InvalidReadingError):
            _ = fault.pressure_torr

    def client(self, fake):
        return TPG261("/dev/test-gauge", serial_factory=fake.factory)

    def test_reads_with_exact_ack_enq_sequence_and_8n1(self):
        fake = FakeSerial(response())
        with self.client(fake) as gauge:
            reading = gauge.read_pressure()
        self.assertEqual(reading.status, GaugeStatus.OK)
        self.assertAlmostEqual(reading.pressure_mbar, 0.001)
        self.assertEqual(
            fake.writes, [b"\x03", b"UNI\r\n", b"\x05", b"PR1\r\n", b"\x05"]
        )
        self.assertEqual(fake.settings["baudrate"], 9600)
        self.assertEqual(fake.settings["bytesize"], 8)
        self.assertEqual(fake.settings["parity"], "N")
        self.assertEqual(fake.settings["stopbits"], 1)
        self.assertFalse(fake.settings["xonxoff"])
        self.assertFalse(fake.settings["rtscts"])
        self.assertFalse(fake.settings["dsrdtr"])
        self.assertEqual(fake.reset_count, 1)
        self.assertTrue(fake.closed)

    def test_units_are_requeried_every_sample(self):
        fake = FakeSerial(response(unit=b"0") + response(unit=b"2"))
        with self.client(fake) as gauge:
            self.assertAlmostEqual(gauge.read_pressure().pressure_mbar, 0.001)
            self.assertAlmostEqual(gauge.read_pressure().pressure_mbar, 0.00001)
        self.assertEqual(fake.writes.count(b"UNI\r\n"), 2)

    def test_nak_does_not_send_enq_or_read_pressure(self):
        fake = FakeSerial([b"\x15\r\n"])
        with self.client(fake) as gauge:
            with self.assertRaisesRegex(ProtocolError, "NAK"):
                gauge.read_pressure()
        self.assertEqual(fake.writes, [b"\x03", b"UNI\r\n"])

    def test_missing_ack_is_an_error(self):
        fake = FakeSerial([b"0,1.0000E-03\r\n"] * 4)
        with self.client(fake) as gauge:
            with self.assertRaisesRegex(ProtocolError, "Expected ACK"):
                gauge.read_pressure()

    def test_in_flight_startup_stream_is_discarded_before_ack(self):
        fake = FakeSerial([b"0,9.0000E+02\r\n", b"trailing-part-of-frame\r\n"] + response())
        with self.client(fake) as gauge:
            self.assertAlmostEqual(gauge.read_pressure().pressure_mbar, 0.001)
        self.assertEqual(fake.writes.count(b"UNI\r\n"), 1)

    def test_unexpected_frames_after_startup_are_not_accepted(self):
        fake = FakeSerial(response() + [b"0,1.0000E-03\r\n"])
        with self.client(fake) as gauge:
            gauge.read_pressure()
            with self.assertRaisesRegex(ProtocolError, "Expected ACK"):
                gauge.read_pressure()

    def test_open_and_close_are_idempotent(self):
        fake = FakeSerial(response())
        gauge = self.client(fake)
        self.assertIs(gauge.open(), gauge)
        self.assertIs(gauge.open(), gauge)
        self.assertEqual(fake.writes, [b"\x03"])
        gauge.close()
        gauge.close()
        self.assertTrue(fake.closed)

    def test_empty_and_partial_ack_are_timeouts(self):
        for acknowledgement in (b"", b"\x06", b"\x06\n"):
            with self.subTest(acknowledgement=acknowledgement):
                fake = FakeSerial([acknowledgement])
                with self.client(fake) as gauge:
                    with self.assertRaises(CommunicationError):
                        gauge.read_pressure()

    def test_unknown_unit_does_not_read_pressure(self):
        for value in (b"3", b"00", b"+1", b"NaN"):
            with self.subTest(unit=value):
                fake = FakeSerial(response(unit=value))
                with self.client(fake) as gauge:
                    with self.assertRaisesRegex(ProtocolError, "pressure unit"):
                        gauge.read_pressure()
                self.assertNotIn(b"PR1\r\n", fake.writes)

    def test_non_ascii_and_oversize_replies_fail(self):
        for reply in (b"\xff\r\n", b"x" * 128):
            with self.subTest(reply=reply):
                fake = FakeSerial([b"\x06\r\n", reply])
                with self.client(fake) as gauge:
                    with self.assertRaises(ProtocolError):
                        gauge.read_pressure()

    def test_closed_port_is_an_error(self):
        with self.assertRaisesRegex(CommunicationError, "not open"):
            self.client(FakeSerial()).read_pressure()

    def test_serial_io_failure_closes_context(self):
        fake = FakeSerial()

        def broken_read(*args):
            raise OSError("USB removed")

        fake.read_until = broken_read
        with self.assertRaisesRegex(CommunicationError, "USB removed"):
            with self.client(fake) as gauge:
                gauge.read_pressure()
        self.assertTrue(fake.closed)

    def test_invalid_serial_configuration(self):
        for baud in (0, 115200):
            with self.assertRaises(ValueError):
                TPG261("/dev/test", baudrate=baud)
        for timeout in (0, -1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                TPG261("/dev/test", timeout=timeout)


class PressureParsingTests(unittest.TestCase):
    def test_all_documented_statuses_are_preserved(self):
        for status in GaugeStatus:
            with self.subTest(status=status):
                reading = parse_pressure("{},2.0000E-02".format(status.value), PressureUnit.MBAR)
                self.assertEqual(reading.status, status)
                if status != GaugeStatus.OK:
                    with self.assertRaises(InvalidReadingError):
                        _ = reading.pressure_mbar

    def test_dummy_no_sensor_value_is_never_a_valid_pressure(self):
        reading = parse_pressure("5,2.0000E-02", PressureUnit.MBAR)
        self.assertEqual(reading.status, GaugeStatus.NO_SENSOR)
        with self.assertRaises(InvalidReadingError):
            _ = reading.pressure_mbar

    def test_torr_and_pascal_conversion(self):
        torr = parse_pressure("0,1.0000E+00", PressureUnit.TORR)
        self.assertAlmostEqual(torr.pressure_mbar, 1.3332236842105263)
        pascal = parse_pressure("0,1.0000E+02", PressureUnit.PASCAL)
        self.assertAlmostEqual(pascal.pressure_mbar, 1.0)

    def test_rejects_malformed_and_non_finite_measurements(self):
        for reply in (
            "", "7,1.0000E-03", "0,nan", "0,inf", "0,1.0000E999",
            "0,-1.0000E-03", "0,1.0", "0,1.0000E-03,0", "ERROR",
        ):
            with self.subTest(reply=reply):
                with self.assertRaises(ProtocolError):
                    parse_pressure(reply, PressureUnit.MBAR)

    def test_zero_ok_measurement_is_preserved_for_display_policy(self):
        self.assertEqual(parse_pressure("0,0.0000E+00", PressureUnit.MBAR).pressure_mbar, 0)

    def test_direct_non_finite_reading_is_rejected(self):
        with self.assertRaises(InvalidReadingError):
            _ = Reading(GaugeStatus.OK, math.nan, PressureUnit.MBAR).pressure_mbar


if __name__ == "__main__":
    unittest.main()
