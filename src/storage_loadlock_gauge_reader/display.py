"""A 32 by 8 level meter using four cascaded MAX7219 drivers.

Logical module zero is the driver nearest DIN. Its digit registers are rows
top to bottom and bit 7 is the leftmost column before orientation corrections.
``reverse_modules``, ``rotate``, and ``mirror`` accommodate different PCB layouts.
Only construction imports spidev, keeping simulation usable on other computers.
"""

import math
from typing import Iterable, Tuple


def vacuum_fraction(pressure_torr: float, atmospheric_torr: float = 760.0) -> float:
    """Continuous, piecewise-log particle bar, using Torr throughout.

    1.5e-8 -> 0 columns, 1e-7 -> 8, 1e-6 -> 16, atmosphere -> 32.
    Fewer lit LEDs mean lower pressure. Fault/status handling is separate.
    """
    pressure_torr, atmospheric_torr = float(pressure_torr), float(atmospheric_torr)
    if not math.isfinite(pressure_torr) or pressure_torr <= 0:
        raise ValueError("vacuum pressure must be finite and positive")
    if not math.isfinite(atmospheric_torr) or atmospheric_torr <= 1e-6:
        raise ValueError("atmospheric pressure must be finite and above 1e-6 Torr")
    if pressure_torr <= 1.5e-8:
        return 0.0
    for low, high, start, end in (
        (1.5e-8, 1e-7, 0.0, 0.25),
        (1e-7, 1e-6, 0.25, 0.5),
        (1e-6, atmospheric_torr, 0.5, 1.0),
    ):
        if pressure_torr <= high:
            progress = (math.log10(pressure_torr) - math.log10(low)) / (
                math.log10(high) - math.log10(low)
            )
            return start + progress * (end - start)
    return 1.0


# Four-pixel-wide, six-row glyphs fit VENTED in 29 columns and 60M in 14.
# Text is placed on logical rows 1..6, leaving rows 0 and 7 unused.
FONT_4X6 = {
    "0": ("0110", "1001", "1001", "1001", "1001", "0110"),
    "1": ("0010", "0110", "0010", "0010", "0010", "0111"),
    "2": ("0110", "1001", "0001", "0010", "0100", "1111"),
    "3": ("1110", "0001", "0110", "0001", "1001", "0110"),
    "4": ("0010", "0110", "1010", "1111", "0010", "0010"),
    "5": ("1111", "1000", "1110", "0001", "1001", "0110"),
    "6": ("0110", "1000", "1110", "1001", "1001", "0110"),
    "7": ("1111", "0001", "0010", "0010", "0100", "0100"),
    "8": ("0110", "1001", "0110", "1001", "1001", "0110"),
    "9": ("0110", "1001", "1001", "0111", "0001", "0110"),
    "V": ("1001", "1001", "1001", "1001", "0110", "0110"),
    "E": ("1111", "1000", "1110", "1000", "1000", "1111"),
    "N": ("1001", "1101", "1101", "1011", "1011", "1001"),
    "T": ("1111", "0110", "0110", "0110", "0110", "0110"),
    "D": ("1110", "1001", "1001", "1001", "1001", "1110"),
    "M": ("1001", "1111", "1111", "1001", "1001", "1001"),
    "H": ("1001", "1001", "1111", "1001", "1001", "1001"),
    "-": ("0000", "0000", "1111", "0000", "0000", "0000"),
    " ": ("0000", "0000", "0000", "0000", "0000", "0000"),
}


def text_pixels(text: str, width: int = 32, x_offset: int = 0):
    """Return centered text pixels on rows 1..6 inside a logical display region.

    Letters are case-insensitive. Unsupported or oversized text raises instead
    of clipping a time value into a different, potentially misleading value.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if not isinstance(width, int) or not isinstance(x_offset, int):
        raise ValueError("text region must use integer coordinates")
    if width < 1 or x_offset < 0 or x_offset + width > 32:
        raise ValueError("text region must fit within the 32-column display")
    text = text.upper()
    if any(character not in FONT_4X6 for character in text):
        raise ValueError("text contains an unsupported character")
    text_width = 5 * len(text) - 1 if text else 0
    if text_width > width:
        raise ValueError("text does not fit the display region")
    start = x_offset + (width - text_width) // 2
    return {
        (start + index * 5 + column, row + 1)
        for index, character in enumerate(text)
        for row, bits in enumerate(FONT_4X6[character])
        for column, bit in enumerate(bits)
        if bit == "1"
    }


def lit_columns(fraction: float) -> int:
    """Round a clipped level to 0..32 complete columns (no partial columns)."""
    fraction = float(fraction)
    if not math.isfinite(fraction):
        raise ValueError("level must be a finite number")
    return int(max(0.0, min(1.0, fraction)) * 32 + 0.5)


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
        intensity: int = 0,
        rotate: int = 0,
        reverse_modules: bool = False,
        mirror: bool = False,
        direction: str = "left-to-right",
    ) -> None:
        if not isinstance(intensity, int) or not 0 <= intensity <= 15:
            raise ValueError("intensity must be an integer from 0 to 15")
        if rotate not in (0, 90, 180, 270):
            raise ValueError("rotate must be 0, 90, 180, or 270 degrees")
        if direction not in ("left-to-right", "right-to-left"):
            raise ValueError("direction must be left-to-right or right-to-left")
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
        self.direction = direction
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
        """Grow/shrink a bar from the chosen edge, using full-height columns."""
        try:
            fraction = float(fraction)
        except (TypeError, ValueError) as exc:
            raise ValueError("level must be a finite number") from exc
        if not math.isfinite(fraction):
            raise ValueError("level must be a finite number")
        columns = lit_columns(fraction)
        start = self.width - columns if self.direction == "right-to-left" else 0
        self._show_pixels((x, y) for x in range(start, start + columns) for y in range(8))

    def show_error(self) -> None:
        """Replace the reading with four X symbols, never a plausible level bar."""
        self._show_pixels(
            (module * 8 + x, y)
            for module in range(self.modules)
            for y in range(self.height)
            for x in range(8)
            if x == y or x + y == 7
        )

    def show_text(self, text: str) -> None:
        """Replace the display with centered text, keeping outer rows blank."""
        self._show_pixels(text_pixels(text))

    def show_vented(self) -> None:
        """Show VENTED for the controller's overrange indication."""
        self.show_text("VENTED")

    def show_eta(self, fraction: float, text: str) -> None:
        """Show a scaled bar on the left two tiles and ETA on the right two.

        All pixels use rows 1..6, including the reduced-width pressure bar.
        Normal show_level() returns to the full 32-column, eight-row bar.
        """
        try:
            fraction = float(fraction)
        except (TypeError, ValueError) as exc:
            raise ValueError("level must be a finite number") from exc
        if not math.isfinite(fraction):
            raise ValueError("level must be a finite number")
        columns = int(max(0.0, min(1.0, fraction)) * 16 + 0.5)
        start = 16 - columns if self.direction == "right-to-left" else 0
        pixels = text_pixels(text, width=16, x_offset=16)
        pixels.update((x, y) for x in range(start, start + columns) for y in range(1, 7))
        self._show_pixels(pixels)

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
