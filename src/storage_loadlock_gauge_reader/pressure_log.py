"""Small, flushed CSV pressure log suitable for later pumpdown fitting."""

import csv
from datetime import datetime
import math
from pathlib import Path
import time


class PressureLog:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        # A new file per session: never truncate an existing pressure record.
        self._file = self.path.open("x", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(("timestamp", "elapsed_seconds", "pressure_torr", "status"))
        self._file.flush()
        self._started = time.monotonic()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def write(self, pressure_torr, status):
        value = ""
        if pressure_torr is not None:
            pressure_torr = float(pressure_torr)
            if not math.isfinite(pressure_torr) or pressure_torr < 0:
                raise ValueError("Logged pressure must be finite and nonnegative")
            value = f"{pressure_torr:.9e}"
        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self._writer.writerow((timestamp, f"{time.monotonic() - self._started:.3f}", value, status))
        self._file.flush()

    def close(self):
        self._file.close()
