"""Save timestamped pressure in CSV. Run from this project: python3 log_pressure.py."""

from datetime import datetime
from pathlib import Path
import sys

PORT = "/dev/ttyUSB0"
BAUD_RATE = 9600
INTERVAL_SECONDS = 1.0


def main(argv=None):
    folder = Path(__file__).resolve().parent
    if not (folder / "src" / "storage_loadlock_gauge_reader").is_dir():
        print("Keep log_pressure.py beside the project's src folder.", file=sys.stderr)
        return 2
    sys.path.insert(0, str(folder / "src"))
    from storage_loadlock_gauge_reader.cli import main as run

    filename = folder / datetime.now().strftime("pressure_log_%Y%m%d_%H%M%S_%f.csv")
    options = ["--port", PORT, "--baud", str(BAUD_RATE), "--interval", str(INTERVAL_SECONDS),
               "--log-pressure", str(filename), "--log-only", "--no-eta"]
    options.extend(sys.argv[1:] if argv is None else argv)
    return run(options)


if __name__ == "__main__":
    raise SystemExit(main())
