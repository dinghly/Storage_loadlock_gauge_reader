"""Tests of pressure mapping and MAX7219 wire frames without Raspberry Pi IO."""

import math
import sys
import types
import unittest
from unittest.mock import patch

from storage_loadlock_gauge_reader.display import Max7219Display, level_fraction


class FakeSpi:
    def __init__(self):
        self.frames = []
        self.opened = None
        self.closed = False

    def open(self, bus, device):
        self.opened = (bus, device)

    def xfer2(self, data):
        self.frames.append(list(data))
        return [0] * len(data)

    def close(self):
        self.closed = True


def wire_pixels(frames):
    """Decode eight physical row writes; module zero is nearest the DIN input."""
    pixels = set()
    for frame in frames:
        for offset in range(0, 8, 2):
            module = 3 - offset // 2
            row, data = frame[offset:offset + 2]
            for x in range(8):
                if data & (1 << (7 - x)):
                    pixels.add((8 * module + x, row - 1))
    return pixels


class LevelFractionTests(unittest.TestCase):
    def test_logarithmic_decades_are_evenly_spaced(self):
        for decade in range(-6, 4):
            with self.subTest(decade=decade):
                self.assertAlmostEqual(level_fraction(10.0 ** decade), (decade + 6) / 9)

    def test_clamps_and_inverts(self):
        self.assertEqual(level_fraction(1e-10), 0)
        self.assertEqual(level_fraction(1e6), 1)
        self.assertEqual(level_fraction(1e-10, invert=True), 1)
        self.assertEqual(level_fraction(1e6, invert=True), 0)
        self.assertAlmostEqual(level_fraction(1e-2, invert=True), 5 / 9)

    def test_linear_mapping_including_zero(self):
        self.assertEqual(level_fraction(0, low=0, high=1000, scale="linear"), 0)
        self.assertEqual(level_fraction(500, low=0, high=1000, scale="linear"), 0.5)

    def test_rejects_invalid_pressure_and_range(self):
        for pressure in (0, -1, math.nan, math.inf, "bad", None):
            with self.subTest(pressure=pressure), self.assertRaises(ValueError):
                level_fraction(pressure)
        for kwargs in (
            {"low": 1, "high": 1}, {"low": 2, "high": 1},
            {"low": 0}, {"low": -1}, {"high": math.inf}, {"scale": "other"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                level_fraction(1, **kwargs)


class Max7219Tests(unittest.TestCase):
    def make_display(self, **kwargs):
        self.spi = FakeSpi()
        fake = types.SimpleNamespace(SpiDev=lambda: self.spi)
        with patch.dict(sys.modules, {"spidev": fake}):
            display = Max7219Display(**kwargs)
        self.addCleanup(display.close)
        return display

    def test_initialization_configures_four_drivers_before_wakeup(self):
        self.make_display(bus=1, device=2, intensity=3)
        self.assertEqual(self.spi.opened, (1, 2))
        self.assertEqual(self.spi.mode, 0)
        self.assertEqual(self.spi.max_speed_hz, 1_000_000)
        expected = [[12, 0] * 4, [15, 0] * 4, [9, 0] * 4,
                    [11, 7] * 4, [10, 3] * 4]
        expected.extend([row, 0] * 4 for row in range(1, 9))
        expected.append([12, 1] * 4)
        self.assertEqual(self.spi.frames, expected)

    def test_bar_crosses_module_boundary_and_wire_order(self):
        display = self.make_display()
        display.show_level(9 / 32)
        self.assertEqual(self.spi.frames[-8:], [[row, 0, row, 0, row, 128, row, 255]
                                              for row in range(1, 9)])
        self.assertEqual(wire_pixels(self.spi.frames[-8:]),
                         {(x, y) for x in range(9) for y in range(8)})

    def test_empty_full_and_clipped_levels(self):
        display = self.make_display()
        for level, count in ((0, 0), (-0.1, 0), (0.5, 128), (1, 256), (2, 256)):
            display.show_level(level)
            self.assertEqual(len(wire_pixels(self.spi.frames[-8:])), count)

    def test_rotation_maps_one_column_to_expected_row_or_column(self):
        expected = {
            0: {(0, y) for y in range(8)},
            90: {(x, 0) for x in range(8)},
            180: {(7, y) for y in range(8)},
            270: {(x, 7) for x in range(8)},
        }
        for rotation, pixels in expected.items():
            with self.subTest(rotation=rotation):
                display = self.make_display(rotate=rotation)
                display.show_level(1 / 32)
                self.assertEqual(wire_pixels(self.spi.frames[-8:]), pixels)

    def test_module_order_and_horizontal_mirror(self):
        for kwargs, column in (({"reverse_modules": True}, 24), ({"mirror": True}, 31)):
            with self.subTest(kwargs=kwargs):
                display = self.make_display(**kwargs)
                display.show_level(1 / 32)
                self.assertEqual(wire_pixels(self.spi.frames[-8:]),
                                 {(column, y) for y in range(8)})

    def test_error_replaces_full_bar_with_four_crosses(self):
        display = self.make_display()
        display.show_level(1)
        display.show_error()
        pixels = wire_pixels(self.spi.frames[-8:])
        self.assertEqual(pixels, {(m * 8 + x, y) for m in range(4)
                                 for x in range(8) for y in range(8)
                                 if x == y or x + y == 7})
        self.assertEqual(len(pixels), 64)
        self.assertNotIn((0, 3), pixels)

    def test_clear_and_close_are_safe_and_close_is_idempotent(self):
        display = self.make_display()
        display.show_level(1)
        display.close()
        self.assertEqual(self.spi.frames[-1], [12, 0] * 4)
        self.assertEqual(wire_pixels(self.spi.frames[-9:-1]), set())
        self.assertTrue(self.spi.closed)
        writes = len(self.spi.frames)
        display.close()
        self.assertEqual(len(self.spi.frames), writes)
        with self.assertRaises(RuntimeError):
            display.show_level(0.5)

    def test_spi_is_closed_when_initialization_fails(self):
        spi = FakeSpi()
        spi.xfer2 = lambda data: (_ for _ in ()).throw(OSError("SPI fault"))
        with patch.dict(sys.modules, {"spidev": types.SimpleNamespace(SpiDev=lambda: spi)}):
            with self.assertRaises(OSError):
                Max7219Display()
        self.assertTrue(spi.closed)

    def test_shutdown_is_attempted_even_when_a_clear_row_fails(self):
        display = self.make_display()
        display.show_level(1)
        attempts = []

        def transient_failure(data):
            attempts.append(list(data))
            if len(attempts) == 1:
                raise OSError("row write failed")
            return [0] * len(data)

        self.spi.xfer2 = transient_failure
        with self.assertRaisesRegex(OSError, "row write failed"):
            display.close()
        self.assertEqual(attempts, [[1, 0] * 4, [12, 0] * 4])
        self.assertTrue(self.spi.closed)
        display.close()
        self.assertEqual(len(attempts), 2)

    def test_spi_is_released_when_shutdown_also_fails(self):
        display = self.make_display()
        attempts = []

        def persistent_failure(data):
            attempts.append(list(data))
            raise OSError("SPI disconnected")

        self.spi.xfer2 = persistent_failure
        with self.assertRaisesRegex(OSError, "SPI disconnected"):
            display.close()
        self.assertEqual(attempts, [[1, 0] * 4, [12, 0] * 4])
        self.assertTrue(self.spi.closed)

    def test_invalid_parameters_and_levels(self):
        for kwargs in ({"intensity": -1}, {"intensity": 16}, {"intensity": 1.5},
                       {"rotate": 45}, {"bus": -1}, {"device": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Max7219Display(**kwargs)
        display = self.make_display()
        for level in (math.inf, math.nan, None, "bad"):
            with self.subTest(level=level), self.assertRaises(ValueError):
                display.show_level(level)


if __name__ == "__main__":
    unittest.main()
