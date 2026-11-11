"""Print the TPG261's raw pressure reply every second. Ctrl+C stops it."""

import sys
import time

try:
    import serial
except ImportError:
    raise SystemExit("Install pyserial: sudo apt install python3-serial")

PORT = "/dev/ttyUSB0"
BAUD_RATE = 9600
INTERVAL_SECONDS = 1.0


def query_pressure(port):
    port.write(b"PR1\r\n")  # Ask for gauge 1 pressure.
    port.flush()
    ack = port.read_until(b"\n", size=128)
    if ack != b"\x06\r\n":
        raise RuntimeError(f"Expected ACK, received {ack!r}")

    port.write(b"\x05")  # ENQ: request the pressure data after ACK.
    port.flush()
    return port.read_until(b"\n", size=128)


def main():
    try:
        with serial.Serial(
            PORT, baudrate=BAUD_RATE, timeout=2, write_timeout=2,
            bytesize=8, parity="N", stopbits=1,
            xonxoff=False, rtscts=False, dsrdtr=False,
        ) as port:
            port.write(b"\x03")  # Reset serial interface; stop power-on streaming.
            port.flush()
            time.sleep(0.2)
            port.reset_input_buffer()

            while True:
                started = time.monotonic()
                try:
                    reply = query_pressure(port)
                    print(repr(reply), flush=True)  # No parsing or conversion.
                except RuntimeError as error:
                    print(f"ERROR: {error}", file=sys.stderr, flush=True)
                time.sleep(max(0.0, INTERVAL_SECONDS - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    except OSError as error:
        print(f"Serial error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
