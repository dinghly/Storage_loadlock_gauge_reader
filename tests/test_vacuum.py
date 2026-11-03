"""Agreed Torr boundaries, continuity, unit independence, and fine pixel fill."""

import math
import unittest
from unittest.mock import patch
import types

from storage_loadlock_gauge_reader.display import Max7219Display, vacuum_fraction
from storage_loadlock_gauge_reader.tpg261 import Reading, PressureUnit, GaugeStatus, InvalidReadingError
from test_display import FakeSpi, wire_pixels


class VacuumBandsTests(unittest.TestCase):
    def test_agreed_boundaries(self):
        for pressure, expected in ((2.1e-8, 1), (5e-8, 1), (1e-7, .75),
                                   (1e-6, .5), (760, 0), (1000, 0)):
            with self.subTest(pressure=pressure):
                self.assertAlmostEqual(vacuum_fraction(pressure), expected)

    def test_geometric_midpoints_give_half_of_each_log_segment(self):
        self.assertAlmostEqual(vacuum_fraction(math.sqrt(5e-8 * 1e-7)), .875)
        self.assertAlmostEqual(vacuum_fraction(math.sqrt(1e-7 * 1e-6)), .625)
        self.assertAlmostEqual(vacuum_fraction(math.sqrt(1e-6 * 760)), .25)

    def test_continuous_at_both_tile_boundaries_and_full_threshold(self):
        for pressure in (5e-8, 1e-7, 1e-6):
            self.assertAlmostEqual(vacuum_fraction(pressure * (1 - 1e-10)),
                                   vacuum_fraction(pressure * (1 + 1e-10)), places=8)

    def test_fill_never_increases_as_pressure_rises(self):
        pressures = [10 ** (-10 + i * .01) for i in range(1401)]
        levels = [vacuum_fraction(p) for p in pressures]
        self.assertTrue(all(0 <= x <= 1 for x in levels))
        self.assertTrue(all(a >= b for a, b in zip(levels, levels[1:])))

    def test_invalid_pressure_and_upper_bound(self):
        for p in (0, -1, math.nan, math.inf):
            with self.subTest(p=p), self.assertRaises(ValueError):
                vacuum_fraction(p)
        for high in (0, 1e-6, math.nan, math.inf):
            with self.subTest(high=high), self.assertRaises(ValueError):
                vacuum_fraction(1e-7, high)

    def test_equivalent_readings_in_all_three_units(self):
        torr = 5e-7
        mbar = torr * 1.3332236842105263
        for raw, unit in ((torr, PressureUnit.TORR), (mbar, PressureUnit.MBAR),
                          (mbar * 100, PressureUnit.PASCAL)):
            reading = Reading(GaugeStatus.OK, raw, unit)
            self.assertAlmostEqual(reading.pressure_torr, torr)
            self.assertAlmostEqual(vacuum_fraction(reading.pressure_torr), vacuum_fraction(torr))
        with self.assertRaises(InvalidReadingError):
            _ = Reading(GaugeStatus.NO_SENSOR, .02, PressureUnit.MBAR).pressure_torr

    def test_every_lit_column_is_full_height(self):
        spi = FakeSpi()
        with patch.dict('sys.modules', {'spidev': types.SimpleNamespace(SpiDev=lambda: spi)}):
            with Max7219Display() as display:
                display.show_level(3 / 256)
                self.assertEqual(wire_pixels(spi.frames[-8:]), set())
                display.show_level(5 / 256)
                self.assertEqual(wire_pixels(spi.frames[-8:]), {(0, y) for y in range(8)})
                display.show_level(193 / 256)
                pixels = wire_pixels(spi.frames[-8:])
                self.assertEqual(pixels, {(x, y) for x in range(24) for y in range(8)})


if __name__ == '__main__':
    unittest.main()
