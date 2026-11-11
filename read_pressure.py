#!/usr/bin/env python3
"""Print TPG261 pressure in Torr every second. Run: python3 read_pressure.py

Requires only pyserial: sudo apt install python3-serial
No LED hardware, project installation, or virtual environment is required.
Protocol reference: Pfeiffer BG 805 198 BE, sections 1.2, 2.1, 2.2.4.
"""

import math
import re
import sys
import time

try:
    import serial
except ImportError:
    raise SystemExit("Install the serial library first: sudo apt install python3-serial")


# Edit these settings to match your controller.
PORT = "/dev/ttyUSB0"
BAUD_RATE = 9600
INTERVAL_SECONDS = 1.0
TIMEOUT_SECONDS = 1.0

STATUS_NAMES = {
    1: "UNDERRANGE", 2: "oL / OVERRANGE", 3: "SENSOR ERROR",
    4: "SENSOR OFF", 5: "NO SENSOR", 6: "IDENTIFICATION ERROR",
}
# Some controllers pad the pressure field with spaces after the comma.
PRESSURE_PATTERN = re.compile(r"([0-6])[ \t]*,[ \t]*([+-]?\d+\.\d+[Ee][+-]?\d+)")


class GaugeError(Exception):
    """An invalid or missing controller reply."""


def read_line(port):
    reply = port.read_until(b"\n", 128)
    if not reply:
        raise GaugeError("No reply; check the controller baud rate and RS232 cable")
    if len(reply) >= 128 or not reply.endswith(b"\r\n"):
        raise GaugeError("Incomplete or oversized controller reply: {!r}".format(reply))
    return reply[:-2]


def write(port, data):
    if port.write(data) != len(data):
        raise GaugeError("Incomplete serial write")
    port.flush()


def query(port, command, synchronizing=False):
    write(port, command + b"\r\n")
    # A power-on streaming measurement may still be in flight when we connect.
    for _ in range(4 if synchronizing else 1):
        ack = read_line(port)
        if ack == b"\x15":
            raise GaugeError("Controller rejected {} (NAK)".format(command.decode()))
        if ack == b"\x06":
            break
    else:
        raise GaugeError("Expected ACK for {}, received {!r}".format(command.decode(), ack))
    write(port, b"\x05")
    return read_line(port)


def read_pressure(port, synchronizing=False):
    """Return a validated pressure in Torr, or raise for all non-OK statuses."""
    unit = query(port, b"UNI", synchronizing)
    if unit not in (b"0", b"1", b"2"):
        raise GaugeError("Unknown pressure unit: {!r}".format(unit))
    raw = query(port, b"PR1")
    match = PRESSURE_PATTERN.fullmatch(raw.decode("ascii", errors="replace").strip())
    if match is None:
        raise GaugeError("Invalid pressure reply: {!r}".format(raw))
    status = int(match.group(1))
    if status != 0:
        raise GaugeError(STATUS_NAMES[status])
    pressure = float(match.group(2))
    # UNI 0 is interpreted as mbar, UNI 1 Torr, UNI 2 Pa.
    # The manual calls UNI 0 mbar/bar; verify scaling against the front panel.
    factor = {b"0": 1 / 1.3332236842105263, b"1": 1.0,
              b"2": 0.01 / 1.3332236842105263}[unit]
    torr = pressure * factor
    if not math.isfinite(torr) or torr < 0:
        raise GaugeError("Invalid numeric pressure")
    return torr


def main():
    port = None
    next_sample = time.monotonic()
    try:
        while True:
            try:
                synchronizing = port is None
                if port is None:
                    port = serial.Serial(
                        PORT, baudrate=BAUD_RATE, timeout=TIMEOUT_SECONDS,
                        write_timeout=TIMEOUT_SECONDS, bytesize=8, parity="N",
                        stopbits=1, xonxoff=False, rtscts=False, dsrdtr=False,
                    )
                    write(port, b"\x03")  # Reset interface buffer, stop startup stream.
                    port.reset_input_buffer()
                pressure = read_pressure(port, synchronizing)
                print(f"{pressure:.3e} Torr", flush=True)
            except (GaugeError, OSError) as error:
                print(f"ERROR: {error}", file=sys.stderr, flush=True)
                if port is not None:
                    port.close()
                    port = None

            next_sample += INTERVAL_SECONDS
            now = time.monotonic()
            if next_sample < now:
                # A timeout may exceed one interval; never burst stale samples.
                next_sample = now + INTERVAL_SECONDS
            time.sleep(max(0.0, next_sample - now))
    except KeyboardInterrupt:
        pass
    finally:
        if port is not None:
            port.close()


if __name__ == "__main__":
    main()
