"""Run the LED meter from this project folder: python3 led_pressure_meter.py.

Keep this file beside the project's src folder. Install serial/SPI dependencies
with sudo apt install python3-serial python3-spidev, or use the existing .venv.
"""

from pathlib import Path
import sys

# Editable startup settings. Command-line options can override these defaults.
PORT = "/dev/ttyUSB0"
BAUD_RATE = 9600
BRIGHTNESS = 0  # Minimum MAX7219 brightness (not off).
INTERVAL_SECONDS = 1.0


def main(argv=None):
    source = Path(__file__).resolve().parent / "src"
    if not (source / "storage_loadlock_gauge_reader").is_dir():
        print("Keep led_pressure_meter.py beside the project's src folder.", file=sys.stderr)
        return 2
    sys.path.insert(0, str(source))
    from storage_loadlock_gauge_reader.cli import main as run_meter

    options = [
        "--port", PORT, "--baud", str(BAUD_RATE),
        "--brightness", str(BRIGHTNESS), "--interval", str(INTERVAL_SECONDS),
    ]
    options.extend(sys.argv[1:] if argv is None else argv)
    return run_meter(options)


if __name__ == "__main__":
    raise SystemExit(main())
