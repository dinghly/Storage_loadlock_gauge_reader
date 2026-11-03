"""A 32 by 8 level meter using four cascaded MAX7219 drivers.

Logical module zero is the driver nearest DIN. Its digit registers are rows
top to bottom and bit 7 is the leftmost column before orientation corrections.
``reverse_modules``, ``rotate``, and ``mirror`` accommodate different PCB layouts.
Only construction imports spidev, keeping simulation usable on other computers.
"""

import math
from typing import Iterable, Tuple


def vacuum_fraction(pressure_torr: float, atmospheric_torr: float = 760.0) -> float:
    """Continuous, piecewise-log bar for the load-lock, using Torr throughout.

    5e-8 -> 32 columns, 1e-7 -> 24, 1e-6 -> 16, atmosphere -> 0.
    Lower positive pressures saturate at full. Fault/status handling is separate.
    """
    pressure_torr, atmospheric_torr = float(pressure_torr), float(atmospheric_torr)
    if not math.isfinite(pressure_torr) or pressure_torr <= 0:
        raise ValueError("vacuum pressure must be finite and positive")
    if not math.isfinite(atmospheric_torr) or atmospheric_torr <= 1e-6:
        raise ValueError("atmospheric pressure must be finite and above 1e-6 Torr")
    if pressure_torr <= 5e-8:
        return 1.0
    for low, high, start, end in (
        (5e-8, 1e-7, 1.0, 0.75),
        (1e-7, 1e-6, 0.75, 0.5),
        (1e-6, atmospheric_torr, 0.5, 0.0),
    ):
        if pressure_torr <= high:
            progress = (math.log10(pressure_torr) - math.log10(low)) / (
                math.log10(high) - math.log10(low)
            )
            return start + progress * (end - start)
    return 0.0


def lit_pixels(fraction: float) -> int:
    """Round a clipped level to one of 257 possible pixel counts."""
    fraction = float(fraction)
    if not math.isfinite(fraction):
        raise ValueError("level must be a finite number")
    return int(max(0.0, min(1.0, fraction)) * 256 + 0.5)


def level_fraction(
    pressure_mbar: float,
    low: float = 1e-6,
    high: float = 1000.0,
    scale: str = "log",
    invert: bool = False,
) -> float:
    """Map pressure to [0, 1], clipping outside a configurable pressure range.

    By default, high pressure fills the bar. Logarithmic scaling allocates equal
    display width to each pressure decade. ``invert`` makes better vacuum fill it.
    """
    if scale not in ("log", "linear"):
        raise ValueError("scale must be 'log' or 'linear'")
    try:
        pressure_mbar, low, high = map(float, (pressure_mbar, low, high))
    except (TypeError, ValueError) as exc:
        raise ValueError("pressure and bounds must be finite numbers") from exc
    if not all(math.isfinite(value) for value in (pressure_mbar, low, high)):
        raise ValueError("pressure and bounds must be finite numbers")
    if low < 0 or pressure_mbar < 0 or high <= low:
        raise ValueError("pressure must be nonnegative and bounds require 0 <= low < high")
    if scale == "log" and (pressure_mbar <= 0 or low <= 0):
        raise ValueError("logarithmic scaling requires positive pressure and bounds")
    if pressure_mbar <= low:
        fraction = 0.0
    elif pressure_mbar >= high:
        fraction = 1.0
    elif scale == "log":
        fraction = (math.log10(pressure_mbar) - math.log10(low)) / (
            math.log10(high) - math.log10(low)
        )
    else:
        fraction = (pressure_mbar - low) / (high - low)
    return 1.0 - fraction if invert else fraction


class Max7219Display:
    """Drive one four-module MAX7219 matrix through Linux SPI.

    Use a 5 V logic buffer between the Pi's outputs and DIN/CLK/CS: the MAX7219
    specifies a 3.5 V minimum logic-high input, above the Pi's 3.3 V output.
    ``rotate`` rotates each 8 by 8 module clockwise before sending data.
    ``mirror`` mirrors the entire logical image horizontally before rotation.
    """

    width = 32
    height = 8
    modules = 4

    def __init__(
        self,
        bus: int = 0,
        device: int = 0,
        intensity: int = 2,
        rotate: int = 0,
        reverse_modules: bool = False,
        mirror: bool = False,
    ) -> None:
        if not isinstance(intensity, int) or not 0 <= intensity <= 15:
            raise ValueError("intensity must be an integer from 0 to 15")
        if rotate not in (0, 90, 180, 270):
            raise ValueError("rotate must be 0, 90, 180, or 270 degrees")
        if not isinstance(bus, int) or not isinstance(device, int) or min(bus, device) < 0:
            raise ValueError("SPI bus and device must be nonnegative integers")
        try:
            import spidev
        except ImportError as exc:
            raise RuntimeError(
                "The LED display requires spidev; install this project's [pi] extra "
                "on the Raspberry Pi and enable SPI."
            ) from exc
        self.rotate = rotate
        self.reverse_modules = reverse_modules
        self.mirror = mirror
        self._closed = False
        self._spi = spidev.SpiDev()
        try:
            self._spi.open(bus, device)
            self._spi.max_speed_hz = 1_000_000
            self._spi.mode = 0
            self._spi.lsbfirst = False
            self._spi.cshigh = False
            # Blank and initialize all four drivers before displaying anything.
            self._broadcast(0x0C, 0)  # Shutdown while configuring.
            self._broadcast(0x0F, 0)  # Disable all-LED test mode.
            self._broadcast(0x09, 0)  # No BCD decoding: use all eight pixel bits.
            self._broadcast(0x0B, 7)  # Scan all eight rows.
            self._broadcast(0x0A, intensity)
            self.clear()
            self._broadcast(0x0C, 1)  # Normal operation.
        except BaseException:
            self._closed = True
            self._spi.close()
            raise

    def __enter__(self) -> "Max7219Display":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _broadcast(self, register: int, value: int) -> None:
        if self._closed:
            raise RuntimeError("display is closed")
        # Each xfer2 holds LOAD/CS low for the complete 64-bit cascade frame.
        self._spi.xfer2([register, value] * self.modules)

    def _show_pixels(self, pixels: Iterable[Tuple[int, int]]) -> None:
        if self._closed:
            raise RuntimeError("display is closed")
        rows = [[0] * self.height for _ in range(self.modules)]
        for x, y in pixels:
            if self.mirror:
                x = self.width - 1 - x
            module, x = divmod(x, 8)
            if self.reverse_modules:
                module = self.modules - 1 - module
            for _ in range(self.rotate // 90):
                x, y = 7 - y, x
            rows[module][y] |= 1 << (7 - x)
        for row in range(self.height):
            frame = []
            # The first word shifts furthest; the last reaches the DIN-nearest IC.
            for module in reversed(range(self.modules)):
                frame.extend((row + 1, rows[module][row]))
            self._spi.xfer2(frame)

    def show_level(self, fraction: float) -> None:
        """Fill columns left to right, filling a partial column bottom to top."""
        try:
            fraction = float(fraction)
        except (TypeError, ValueError) as exc:
            raise ValueError("level must be a finite number") from exc
        if not math.isfinite(fraction):
            raise ValueError("level must be a finite number")
        count = lit_pixels(fraction)
        self._show_pixels((index // 8, 7 - index % 8) for index in range(count))

    def show_error(self) -> None:
        """Replace the reading with four X symbols, never a plausible level bar."""
        self._show_pixels(
            (module * 8 + x, y)
            for module in range(self.modules)
            for y in range(self.height)
            for x in range(8)
            if x == y or x + y == 7
        )

    def clear(self) -> None:
        """Blank the display without changing its brightness or orientation."""
        self._show_pixels(())

    def close(self) -> None:
        """Attempt blanking and shutdown, then always release the SPI device.

        Hardware blanking cannot be guaranteed if SPI communication fails.
        Even if a row clear fails, still attempt the independent shutdown write.
        """
        if self._closed:
            return
        try:
            try:
                self.clear()
            finally:
                self._broadcast(0x0C, 0)
        finally:
            self._closed = True
            self._spi.close()
