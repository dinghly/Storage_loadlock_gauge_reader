"""Direct source launcher and complete serial-to-SPI meter behavior."""

from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

from storage_loadlock_gauge_reader.cli import parse_args, run_meter
from storage_loadlock_gauge_reader.display import Max7219Display
from test_display import FakeSpi, wire_pixels
from test_serial_integration import EmulatedTPG261


class LedLauncherTests(unittest.TestCase):
    def test_python_script_works_without_installed_project_or_site_packages(self):
        script = Path(__file__).resolve().parents[1] / 'led_pressure_meter.py'
        result = subprocess.run(
            [sys.executable, '-S', str(script), '--demo', '--console', '--frames', '2', '--interval', '.001'],
            capture_output=True, text=True, timeout=10, cwd='/tmp',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1.5e-08 Torr; 100.0%', result.stdout)
        self.assertIn('|' + '#' * 32 + '|', result.stdout)

    def test_actual_padded_serial_reading_drives_twenty_right_anchored_columns(self):
        spi = FakeSpi()
        with EmulatedTPG261(unit=b'1', pressure=b'0, 2.9300E-07') as peer:
            args = parse_args(['--port', peer.port, '--frames', '1'])
            with patch.dict(sys.modules, {'spidev': types.SimpleNamespace(SpiDev=lambda: spi)}):
                with Max7219Display(intensity=args.brightness, direction=args.direction,
                                    reverse_modules=args.reverse_modules) as display:
                    self.assertEqual(run_meter(args, display), 0)
                    visible = {(8 * (3 - x // 8) + x % 8, y)
                               for x, y in wire_pixels(spi.frames[-8:])}
                    self.assertEqual(visible, {(x, y) for x in range(12, 32) for y in range(8)})
                    self.assertIn([10, 0] * 4, spi.frames)  # Minimum brightness.

    def test_overrange_serial_status_displays_steady_four_Xs(self):
        spi, sleep = FakeSpi(), Mock()
        with EmulatedTPG261(unit=b'1', pressure=b'2, 9.9000E+99') as peer:
            args = parse_args(['--port', peer.port, '--frames', '2'])
            with patch.dict(sys.modules, {'spidev': types.SimpleNamespace(SpiDev=lambda: spi)}):
                with Max7219Display() as display, self.assertLogs(level='WARNING'):
                    before = len(spi.frames)
                    self.assertEqual(run_meter(args, display, sleep=sleep), 1)
                    frames = spi.frames[before:]
                    self.assertEqual(len(frames), 16)  # Exactly two static images, no blank phases.
                    expected = {(m * 8 + x, y) for m in range(4) for x in range(8)
                                for y in range(8) if x == y or x + y == 7}
                    self.assertEqual(wire_pixels(frames[:8]), expected)
                    self.assertEqual(wire_pixels(frames[8:]), expected)
                    sleep.assert_called_once_with(.25)


if __name__ == '__main__':
    unittest.main()
