"""Exercise actual pyserial against a POSIX pseudo-terminal controller emulator.

These tests cover the host serial transport without physical TPG261 hardware.
The peer thread is event-driven and stopped through a wake-up pipe, without
timing sleeps or an indefinitely blocking thread.
"""

import os
import select
import threading
import unittest

try:
    import serial
except ImportError:
    serial = None

if os.name == "posix":
    import pty
    import tty

from storage_loadlock_gauge_reader.tpg261 import (
    CommunicationError,
    GaugeStatus,
    PressureUnit,
    TPG261,
)


class EmulatedTPG261:
    """A minimal ACK/ENQ peer connected to a real PTY serial port."""

    def __init__(self, unit=b"2", pressure=b"0,1.0000E+02", silent_data=False):
        self.unit = unit
        self.pressure = pressure
        self.silent_data = silent_data
        self.messages = []
        self.failures = []
        self.started = threading.Event()
        self.enquiry_seen = threading.Event()
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        self.port = os.ttyname(self.slave)
        self.wake_read, self.wake_write = os.pipe()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        if not self.started.wait(2):
            self.__exit__(None, None, None)
            raise AssertionError("Serial emulator did not start")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            os.write(self.wake_write, b"x")
            self.thread.join(timeout=2)
        finally:
            for fd in (self.master, self.slave, self.wake_read, self.wake_write):
                os.close(fd)
        if self.thread.is_alive():
            raise AssertionError("Serial emulator did not stop")
        if self.failures and exc_type is None:
            raise AssertionError("Serial emulator failed: {}".format(self.failures[0]))

    def _send(self, reply):
        remaining = memoryview(reply)
        while remaining:
            remaining = remaining[os.write(self.master, remaining):]

    def _run(self):
        pending_reply = None
        command = bytearray()
        self.started.set()
        try:
            while True:
                readable, _, _ = select.select([self.master, self.wake_read], [], [])
                if self.wake_read in readable:
                    return
                incoming = os.read(self.master, 128)
                if not incoming:
                    return
                for value in incoming:
                    if value == 0x03:
                        self.messages.append(b"\x03")
                        command.clear()
                        pending_reply = None
                    elif value == 0x05:
                        self.messages.append(b"\x05")
                        self.enquiry_seen.set()
                        if pending_reply is None:
                            raise AssertionError("ENQ arrived before a valid command")
                        if not self.silent_data:
                            self._send(pending_reply + b"\r\n")
                    else:
                        command.append(value)
                        if value == 0x0A:
                            message = bytes(command)
                            self.messages.append(message)
                            command.clear()
                            if message == b"UNI\r\n":
                                pending_reply = self.unit
                            elif message == b"PR1\r\n":
                                pending_reply = self.pressure
                            else:
                                raise AssertionError("Unexpected command: {!r}".format(message))
                            self._send(b"\x06\r\n")
        except BaseException as exc:
            self.failures.append(exc)


@unittest.skipUnless(os.name == "posix" and serial is not None, "requires POSIX PTY and pyserial")
class RealSerialIntegrationTests(unittest.TestCase):
    def test_actual_padded_reply_reported_by_user(self):
        with EmulatedTPG261(unit=b"1", pressure=b"0, 4.0600E-08") as peer:
            with TPG261(peer.port, timeout=1.0) as gauge:
                reading = gauge.read_pressure()
            self.assertEqual(reading.status, GaugeStatus.OK)
            self.assertEqual(reading.pressure_torr, 4.0600e-8)

    def test_real_serial_transport_and_pressure_conversion(self):
        with EmulatedTPG261() as peer:
            with TPG261(peer.port, timeout=1.0) as gauge:
                reading = gauge.read_pressure()
            self.assertEqual(reading.status, GaugeStatus.OK)
            self.assertEqual(reading.unit, PressureUnit.PASCAL)
            self.assertEqual(reading.pressure, 100.0)
            self.assertAlmostEqual(reading.pressure_mbar, 1.0)
            self.assertEqual(
                peer.messages,
                [b"\x03", b"UNI\r\n", b"\x05", b"PR1\r\n", b"\x05"],
            )

    def test_real_serial_timeout_after_ack_without_data(self):
        with EmulatedTPG261(silent_data=True) as peer:
            with TPG261(peer.port, timeout=0.5) as gauge:
                with self.assertRaisesRegex(CommunicationError, "timed out"):
                    gauge.read_pressure()
            self.assertTrue(peer.enquiry_seen.wait(1), "Expected ENQ after the emulated ACK")
            self.assertEqual(peer.messages, [b"\x03", b"UNI\r\n", b"\x05"])


if __name__ == "__main__":
    unittest.main()
