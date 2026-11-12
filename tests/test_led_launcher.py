"""Direct source launcher and complete serial-to-SPI meter behavior."""

from pathlib import Path
import math
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

from storage_loadlock_gauge_reader.cli import parse_args, run_meter
from storage_loadlock_gauge_reader.display import Max7219Display, text_pixels
from storage_loadlock_gauge_reader.tpg261 import GaugeStatus, PressureUnit, Reading
from test_display import FakeSpi, wire_pixels
from test_serial_integration import EmulatedTPG261


class LedLauncherTests(unittest.TestCase):
    def test_estimate_reaches_the_right_physical_tiles_with_blank_outer_rows(self):
        spi, reader = FakeSpi(), Mock()
        reader.read_pressure.side_effect = [
            Reading(GaugeStatus.OK, 1e-5 * math.exp(-second / 30), PressureUnit.TORR)
            for second in range(62)
        ]
        args = parse_args(['--frames', '62'])
        timestamps = iter(range(62))
        with patch.dict(sys.modules, {'spidev': types.SimpleNamespace(SpiDev=lambda: spi)}):
            with Max7219Display(direction=args.direction, reverse_modules=args.reverse_modules) as display:
                self.assertEqual(run_meter(args, display, Mock(return_value=reader),
                                           sleep=lambda _: None, clock=lambda: next(timestamps)), 0)
                visible = {(8 * (3 - x // 8) + x % 8, y)
                           for x, y in wire_pixels(spi.frames[-8:])}
                self.assertEqual({(x, y) for x, y in visible if x >= 16}, text_pixels('2M', 16, 16))
                self.assertEqual({(x, y) for x, y in visible if x < 16},
                                 {(x, y) for x in range(8) for y in range(1, 7)})
                self.assertTrue(all(1 <= y <= 6 for _, y in visible))

    def test_python_script_works_without_installed_project_or_site_packages(self):
        script = Path(__file__).resolve().parents[1] / 'led_pressure_meter.py'
        result = subprocess.run(
            [sys.executable, '-S', str(script), '--demo', '--console', '--frames', '2', '--interval', '.001'],
            capture_output=True, text=True, timeout=10, cwd='/tmp',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1.5e-08 Torr; 0.0%', result.stdout)
        self.assertIn('|' + '.' * 32 + '|', result.stdout)

    def test_actual_padded_serial_reading_drives_twelve_left_anchored_columns(self):
        spi = FakeSpi()
        with EmulatedTPG261(unit=b'1', pressure=b'0, 2.9300E-07') as peer:
            args = parse_args(['--port', peer.port, '--frames', '1'])
            with patch.dict(sys.modules, {'spidev': types.SimpleNamespace(SpiDev=lambda: spi)}):
                with Max7219Display(intensity=args.brightness, direction=args.direction,
                                    reverse_modules=args.reverse_modules) as display:
                    self.assertEqual(run_meter(args, display), 0)
                    visible = {(8 * (3 - x // 8) + x % 8, y)
                               for x, y in wire_pixels(spi.frames[-8:])}
                    self.assertEqual(visible, {(x, y) for x in range(12) for y in range(8)})
                    self.assertIn([10, 0] * 4, spi.frames)  # Minimum brightness.

    def test_overrange_serial_status_displays_VENTED_on_middle_six_rows(self):
        spi, sleep = FakeSpi(), Mock()
        with EmulatedTPG261(unit=b'1', pressure=b'2, 9.9000E+99') as peer:
            args = parse_args(['--port', peer.port, '--frames', '2'])
            with patch.dict(sys.modules, {'spidev': types.SimpleNamespace(SpiDev=lambda: spi)}):
                with Max7219Display(direction=args.direction, reverse_modules=args.reverse_modules) as display, self.assertLogs(level='WARNING'):
                    before = len(spi.frames)
                    self.assertEqual(run_meter(args, display, sleep=sleep), 1)
                    frames = spi.frames[before:]
                    self.assertEqual(len(frames), 16)  # Exactly two static images, no blank phases.
                    expected = text_pixels('VENTED')
                    for frame in (frames[:8], frames[8:]):
                        visible = {(8 * (3 - x // 8) + x % 8, y)
                                   for x, y in wire_pixels(frame)}
                        self.assertEqual(visible, expected)
                        self.assertTrue(all(1 <= y <= 6 for _, y in visible))
                    sleep.assert_called_once_with(.25)


if __name__ == '__main__':
    unittest.main()
