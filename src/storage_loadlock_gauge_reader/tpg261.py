"""Read-only Pfeiffer TPG261 RS232 interface (BG 805 198 BE).

The controller's reported pressure unit is queried rather than changed. Only
the interface-buffer reset (ETX), UNI query, PR1 query, and ENQ are transmitted.
"""

import math
import re
from dataclasses import dataclass
from enum import IntEnum


class TPG261Error(Exception):
    """Base class for controller, transport, and measurement errors."""


class CommunicationError(TPG261Error):
    """A serial device failed or did not provide a complete reply in time."""


class ProtocolError(TPG261Error):
    """The controller rejected a request or returned an invalid reply."""


class InvalidReadingError(TPG261Error):
    """A reading is not a valid pressure measurement."""


class GaugeStatus(IntEnum):
    OK = 0
    UNDERRANGE = 1
    OVERRANGE = 2
    SENSOR_ERROR = 3
    SENSOR_OFF = 4
    NO_SENSOR = 5
    IDENTIFICATION_ERROR = 6


class PressureUnit(IntEnum):
    MBAR = 0
    TORR = 1
    PASCAL = 2

    @property
    def symbol(self) -> str:
        return {self.MBAR: "mbar", self.TORR: "Torr", self.PASCAL: "Pa"}[self]

    def to_mbar(self, value: float) -> float:
        """Convert a finite pressure using the pressure unit selected by UNI."""
        factor = {self.MBAR: 1.0, self.TORR: 1.3332236842105263, self.PASCAL: 0.01}[self]
        converted = value * factor
        if not math.isfinite(converted):
            raise InvalidReadingError("Pressure must be finite")
        return converted


@dataclass(frozen=True)
class Reading:
    status: GaugeStatus
    pressure: float
    unit: PressureUnit

    @property
    def pressure_torr(self) -> float:
        """Validated pressure in Torr, preserving exact native Torr values."""
        mbar = self.pressure_mbar
        if self.unit == PressureUnit.TORR:
            return self.pressure
        return mbar / PressureUnit.TORR.to_mbar(1.0)

    @property
    def pressure_mbar(self) -> float:
        """Return pressure in mbar, rejecting status-placeholder values."""
        if self.status != GaugeStatus.OK:
            raise InvalidReadingError("Gauge status is {}".format(self.status.name))
        if self.pressure < 0:
            raise InvalidReadingError("Pressure must not be negative")
        return self.unit.to_mbar(self.pressure)


_PRESSURE_REPLY = re.compile(r"([0-6]),([+-]?\d+\.\d+[Ee][+-]?\d+)")


def parse_pressure(reply: str, unit: PressureUnit) -> Reading:
    """Validate one PR1 reply, including sensor-error placeholder readings."""
    match = _PRESSURE_REPLY.fullmatch(reply.strip())
    if match is None:
        raise ProtocolError("Invalid PR1 reply: {!r}".format(reply))
    status = GaugeStatus(int(match.group(1)))
    pressure = float(match.group(2))
    if not math.isfinite(pressure):
        raise ProtocolError("Non-finite pressure in PR1 reply")
    if status == GaugeStatus.OK and pressure < 0:
        raise ProtocolError("Negative pressure in an OK PR1 reply")
    return Reading(status=status, pressure=pressure, unit=unit)


class TPG261:
    """Poll gauge 1 using the ACK/ENQ protocol, with 8N1 and no flow control.

    Use as a context manager. Errors are explicit; callers may close/reopen this
    object after a timeout or USB disconnection. ``serial_factory`` is an optional
    dependency-injection hook for tests; normal use imports pyserial lazily.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: float = 1.0,
        serial_factory=None,
    ) -> None:
        if baudrate not in (9600, 19200, 38400):
            raise ValueError("TPG261 baud rate must be 9600, 19200, or 38400")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Serial timeout must be finite and positive")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial_factory = serial_factory
        self._serial = None
        self._startup_synchronizing = True

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def open(self):
        if self._serial is not None:
            return self
        factory = self._serial_factory
        if factory is None:
            try:
                import serial
            except ImportError as exc:
                raise CommunicationError("Install pyserial to use the TPG261 serial port") from exc
            factory = serial.Serial
        try:
            self._serial = factory(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=self.timeout,
                write_timeout=self.timeout,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            # BG 805 198 BE §1.2.3: ETX clears the controller input buffer.
            # Any received character also stops its power-on continuous output.
            self._write(b"\x03")
            self._serial.reset_input_buffer()
            self._startup_synchronizing = True
        except (OSError, ValueError) as exc:
            self.close()
            raise CommunicationError("Cannot open TPG261 at {}: {}".format(self.port, exc)) from exc
        except TPG261Error:
            self.close()
            raise
        return self

    def close(self) -> None:
        device, self._serial = self._serial, None
        if device is not None:
            try:
                device.close()
            except OSError:
                pass

    def _write(self, data: bytes) -> None:
        try:
            if self._serial.write(data) != len(data):
                raise CommunicationError("Incomplete serial write")
            self._serial.flush()
        except OSError as exc:
            raise CommunicationError("TPG261 write failed: {}".format(exc)) from exc

    def _read_line(self) -> bytes:
        try:
            line = self._serial.read_until(b"\n", 128)
        except OSError as exc:
            raise CommunicationError("TPG261 read failed: {}".format(exc)) from exc
        if not line:
            raise CommunicationError("TPG261 reply timed out; check cable and baud rate")
        if len(line) >= 128:
            raise ProtocolError("TPG261 reply exceeds the maximum line length")
        if not line.endswith(b"\r\n"):
            raise CommunicationError("Incomplete TPG261 reply (missing CR LF)")
        return line[:-2]

    def _query(self, mnemonic: bytes) -> str:
        if self._serial is None:
            raise CommunicationError("TPG261 serial port is not open")
        self._write(mnemonic + b"\r\n")
        # The controller streams measurements on power-up. A final streaming
        # frame may already be crossing the USB adapter when ETX is sent. Only
        # during startup, discard at most three such stale frames before ACK.
        # Both the line count and per-read timeout bound synchronization time.
        for _ in range(4 if self._startup_synchronizing else 1):
            acknowledgement = self._read_line()
            if acknowledgement == b"\x15":
                raise ProtocolError("TPG261 rejected {} (NAK)".format(mnemonic.decode("ascii")))
            if acknowledgement == b"\x06":
                self._startup_synchronizing = False
                break
        else:
            raise ProtocolError("Expected ACK, received {!r}".format(acknowledgement))
        self._write(b"\x05")
        reply = self._read_line()
        try:
            return reply.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ProtocolError("Non-ASCII TPG261 reply") from exc

    def read_pressure(self) -> Reading:
        """Query current units, then PR1, without changing controller settings."""
        unit_reply = self._query(b"UNI")
        try:
            # Reject arbitrary numeric spellings such as 00 or +1.
            if unit_reply not in ("0", "1", "2"):
                raise ValueError
            unit = PressureUnit(int(unit_reply))
        except ValueError as exc:
            raise ProtocolError("Unknown TPG261 pressure unit: {!r}".format(unit_reply)) from exc
        return parse_pressure(self._query(b"PR1"), unit)
