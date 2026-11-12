"""CSV records genuine values, preserves errors, and never overwrites sessions."""

from contextlib import redirect_stderr
import csv
from datetime import datetime
from io import StringIO
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from storage_loadlock_gauge_reader.cli import parse_args, run_meter
from storage_loadlock_gauge_reader.pressure_log import PressureLog
from storage_loadlock_gauge_reader.tpg261 import CommunicationError, GaugeStatus, PressureUnit, Reading
from test_cli import FakeDisplay


class PressureLogTests(unittest.TestCase):
    def test_rows_have_timestamp_elapsed_pressure_and_status_and_are_flushed(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'pressure.csv'
            with patch('storage_loadlock_gauge_reader.pressure_log.time.monotonic', side_effect=[100, 101, 103]):
                with PressureLog(path) as log:
                    log.write(1.7e-7, 'OK')
                    log.write(None, 'OVERRANGE')
                    # Read while the log remains open: each row is already flushed.
                    with path.open() as handle:
                        rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(float(rows[0]['pressure_torr']), 1.7e-7)
            self.assertEqual(rows[1]['pressure_torr'], '')
            self.assertEqual(rows[1]['status'], 'OVERRANGE')
            self.assertEqual([float(row['elapsed_seconds']) for row in rows], [1, 3])
            self.assertIsNotNone(datetime.fromisoformat(rows[0]['timestamp']).utcoffset())
            previous = path.read_bytes()
            with self.assertRaises(FileExistsError):
                PressureLog(path)
            self.assertEqual(path.read_bytes(), previous)

    def test_meter_logs_unit_conversion_and_no_numeric_fault_placeholders(self):
        gauge = Mock()
        gauge.read_pressure.side_effect = [Reading(GaugeStatus.OK, 100, PressureUnit.PASCAL),
                                           Reading(GaugeStatus.NO_SENSOR, .02, PressureUnit.MBAR),
                                           CommunicationError('unplugged')]
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'pressure.csv'
            with PressureLog(path) as log, self.assertLogs(level='WARNING'):
                run_meter(parse_args(['--frames', '3']), FakeDisplay(), Mock(return_value=gauge),
                          sleep=lambda _: None, pressure_log=log)
            with path.open() as handle:
                rows = list(csv.DictReader(handle))
        self.assertAlmostEqual(float(rows[0]['pressure_torr']), 1 / 1.3332236842105263, places=9)
        self.assertEqual(rows[0]['status'], 'OK')
        self.assertEqual(rows[1]['pressure_torr'], '')
        self.assertIn('NO SENSOR', rows[1]['status'])
        self.assertEqual(rows[2]['pressure_torr'], '')
        self.assertIn('unplugged', rows[2]['status'])

    def test_demo_records_are_explicitly_marked(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'pressure.csv'
            with PressureLog(path) as log:
                run_meter(parse_args(['--demo', '--frames', '1']), FakeDisplay(), pressure_log=log)
            with path.open() as handle:
                row = next(csv.DictReader(handle))
        self.assertEqual(row['status'], 'DEMO_OK')

    def test_log_only_launcher_needs_no_spi_or_installed_package(self):
        script = Path(__file__).resolve().parents[1] / 'log_pressure.py'
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'pressure.csv'
            result = subprocess.run([sys.executable, '-S', str(script), '--log-pressure', str(path),
                                     '--demo', '--frames', '2', '--interval', '.001'],
                                    capture_output=True, text=True, cwd=folder, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertIn(str(path), result.stderr)
            with path.open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row['status'] == 'DEMO_OK' for row in rows))

    def test_log_only_requires_a_destination(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_args(['--log-only'])

    def test_verbose_output_explains_why_eta_is_missing(self):
        gauge = Mock()
        gauge.read_pressure.return_value = Reading(GaugeStatus.OK, 1.7e-7, PressureUnit.TORR)
        with self.assertLogs(level='DEBUG') as messages:
            run_meter(parse_args(['--frames', '1']), FakeDisplay(), Mock(return_value=gauge), clock=lambda: 0)
        self.assertIn('ETA hidden:', '\n'.join(messages.output))
        self.assertIn('collect', '\n'.join(messages.output).lower())


if __name__ == '__main__':
    unittest.main()
