"""Application behavior across gauge faults, recovery, and simulated input."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import math
import unittest
from unittest.mock import Mock, patch

from storage_loadlock_gauge_reader.cli import ConsoleDisplay, demo_pressure, main, parse_args, run_meter
from storage_loadlock_gauge_reader.tpg261 import (
    CommunicationError, GaugeStatus, PressureUnit, Reading,
)


class FakeDisplay:
    def __init__(self):
        self.events = []

    def show_level(self, level):
        self.events.append(("level", level))

    def show_error(self):
        self.events.append(("error",))

    def show_vented(self):
        self.events.append(("vented",))

    def show_eta(self, fraction, text):
        self.events.append(("eta", fraction, text))


class MeterTests(unittest.TestCase):
    def args(self, frames=1, *extra):
        return parse_args(["--profile", "generic", "--frames", str(frames), *extra])

    def test_demo_never_opens_serial_and_reaches_both_endpoints(self):
        display = FakeDisplay()
        factory = Mock(side_effect=AssertionError("must not open a serial port"))
        result = run_meter(self.args(65, "--demo"), display, factory, sleep=lambda _: None)
        self.assertEqual(result, 0)
        self.assertEqual(display.events[0], ("level", 0))
        self.assertEqual(display.events[32], ("level", 1))
        self.assertEqual(display.events[64], ("level", 0))
        factory.assert_not_called()

    def test_pressure_is_converted_from_pascal_before_log_mapping(self):
        reader = Mock()
        reader.read_pressure.return_value = Reading(GaugeStatus.OK, 100, PressureUnit.PASCAL)
        display = FakeDisplay()
        run_meter(self.args(), display, Mock(return_value=reader))
        self.assertAlmostEqual(display.events[0][1], 6 / 9)
        reader.open.assert_called_once()
        reader.close.assert_called_once()

    def test_every_fault_status_replaces_a_previous_valid_bar(self):
        for status in list(GaugeStatus)[1:]:
            if status == GaugeStatus.OVERRANGE:
                continue  # oL is covered separately below.
            with self.subTest(status=status):
                reader = Mock()
                reader.read_pressure.side_effect = [
                    Reading(GaugeStatus.OK, 1000, PressureUnit.MBAR),
                    Reading(status, 0.02, PressureUnit.MBAR),
                ]
                display = FakeDisplay()
                with self.assertLogs(level="WARNING"):
                    result = run_meter(self.args(2), display, Mock(return_value=reader), sleep=lambda _: None)
                self.assertEqual(result, 1)
                self.assertEqual(display.events, [("level", 1), ("error",)])
                reader.open.assert_called_once()

    def test_status_recovers_without_reopening_serial_port(self):
        reader = Mock()
        reader.read_pressure.side_effect = [
            Reading(GaugeStatus.NO_SENSOR, 0.02, PressureUnit.MBAR),
            Reading(GaugeStatus.OK, 1000, PressureUnit.MBAR),
        ]
        display = FakeDisplay()
        with self.assertLogs(level="INFO"):
            result = run_meter(self.args(2), display, Mock(return_value=reader), sleep=lambda _: None)
        self.assertEqual(result, 0)
        self.assertEqual(display.events, [("error",), ("level", 1)])
        reader.open.assert_called_once()

    def test_serial_timeout_clears_bar_and_reopens_after_delay(self):
        first, second = Mock(), Mock()
        first.read_pressure.side_effect = [
            Reading(GaugeStatus.OK, 1000, PressureUnit.MBAR),
            CommunicationError("unplugged"),
        ]
        second.read_pressure.return_value = Reading(GaugeStatus.OK, 1e-6, PressureUnit.MBAR)
        factory = Mock(side_effect=[first, second])
        display, sleep = FakeDisplay(), Mock()
        with self.assertLogs(level="INFO"):
            result = run_meter(self.args(3), display, factory, sleep)
        self.assertEqual(result, 0)
        self.assertEqual(display.events, [("level", 1), ("error",), ("level", 0)])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.25, 2.0])
        first.close.assert_called_once()
        second.open.assert_called_once()
        second.close.assert_called_once()

    def test_open_failure_shows_error_and_closes_failed_reader(self):
        reader = Mock()
        reader.open.side_effect = CommunicationError("device not found")
        display = FakeDisplay()
        with self.assertLogs(level="WARNING"):
            result = run_meter(self.args(), display, Mock(return_value=reader))
        self.assertEqual(result, 1)
        self.assertEqual(display.events, [("error",)])
        reader.close.assert_called_once()

    def test_zero_pressure_is_not_shown_as_valid_on_log_scale(self):
        reader = Mock()
        reader.read_pressure.return_value = Reading(GaugeStatus.OK, 0, PressureUnit.MBAR)
        display = FakeDisplay()
        with self.assertLogs(level="WARNING"):
            result = run_meter(self.args(), display, Mock(return_value=reader))
        self.assertEqual(result, 1)
        self.assertEqual(display.events, [("error",)])

    def test_spi_failure_is_fatal_and_closes_gauge(self):
        reader = Mock()
        reader.read_pressure.return_value = Reading(GaugeStatus.OK, 1, PressureUnit.MBAR)
        display = Mock()
        display.show_level.side_effect = OSError("SPI failed")
        with self.assertRaisesRegex(OSError, "SPI failed"):
            run_meter(self.args(), display, Mock(return_value=reader))
        reader.close.assert_called_once()
        display.show_error.assert_not_called()

    def test_keyboard_interrupt_closes_gauge(self):
        reader = Mock()
        reader.read_pressure.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            run_meter(self.args(), FakeDisplay(), Mock(return_value=reader))
        reader.close.assert_called_once()

    def test_inverted_demo_and_linear_zero_threshold(self):
        display = FakeDisplay()
        run_meter(self.args(1, "--demo", "--invert"), display)
        self.assertEqual(display.events, [("level", 1)])
        self.assertEqual(demo_pressure(0, 0, 1000, "linear"), 0)
        self.assertEqual(demo_pressure(16, 0, 1000, "linear"), 500)
        self.assertEqual(demo_pressure(32, 0, 1000, "linear"), 1000)

    def test_invalid_cli_configuration_is_rejected_before_opening_hardware(self):
        for extra in (
            ["--min-pressure", "0"], ["--min-pressure", "1000"],
            ["--interval", "0"], ["--timeout", "nan"], ["--frames", "-1"],
            ["--max-pressure", "inf"], ["--rotate", "45"], ["--brightness", "16"],
        ):
            with self.subTest(extra=extra), redirect_stderr(StringIO()), self.assertRaises(SystemExit) as exc:
                parse_args(extra)
            self.assertEqual(exc.exception.code, 2)

    def test_console_demo_main_does_not_construct_spi_display(self):
        stdout = StringIO()
        with patch("storage_loadlock_gauge_reader.cli.Max7219Display") as hardware, redirect_stdout(stdout):
            self.assertEqual(main(["--demo", "--console", "--frames", "1"]), 0)
        hardware.assert_not_called()
        self.assertIn("DEMO:", stdout.getvalue())
        self.assertIn("|................................|", stdout.getvalue())

    def test_vacuum_profile_is_default_and_uses_native_torr_bands(self):
        for pressure, expected in ((1.5e-8, 0), (1e-8, 0), (1e-7, .25),
                                   (1e-6, .5), (760, 1)):
            with self.subTest(pressure=pressure):
                reader = Mock()
                reader.read_pressure.return_value = Reading(GaugeStatus.OK, pressure, PressureUnit.TORR)
                display = FakeDisplay()
                args = parse_args(["--frames", "1"])
                self.assertEqual(args.profile, "vacuum")
                self.assertEqual(run_meter(args, display, Mock(return_value=reader)), 0)
                self.assertAlmostEqual(display.events[0][1], expected)

    def test_oL_shows_VENTED_then_recovers_without_reopening(self):
        reader = Mock()
        reader.read_pressure.side_effect = [
            Reading(GaugeStatus.OVERRANGE, 0, PressureUnit.TORR),
            Reading(GaugeStatus.OVERRANGE, 9999, PressureUnit.TORR),
            Reading(GaugeStatus.OK, 1.5e-8, PressureUnit.TORR),
        ]
        display, sleep = FakeDisplay(), Mock()
        with self.assertLogs(level="INFO"):
            result = run_meter(parse_args(["--frames", "3"]), display, Mock(return_value=reader), sleep)
        self.assertEqual(result, 0)
        self.assertEqual(display.events, [("vented",), ("vented",), ("level", 0)])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [.25, .25])
        reader.open.assert_called_once()
        reader.close.assert_called_once()

    def test_oL_followed_by_sensor_fault_changes_VENTED_to_Xs(self):
        reader = Mock()
        reader.read_pressure.side_effect = [
            Reading(GaugeStatus.OVERRANGE, 0, PressureUnit.TORR),
            Reading(GaugeStatus.NO_SENSOR, .02, PressureUnit.MBAR),
        ]
        display = FakeDisplay()
        with self.assertLogs(level="WARNING"):
            result = run_meter(parse_args(["--frames", "2"]), display,
                               Mock(return_value=reader), sleep=lambda _: None)
        self.assertEqual(result, 1)
        self.assertEqual(display.events, [("vented",), ("error",)])

    def test_overrange_demo_does_not_open_serial(self):
        display, factory = FakeDisplay(), Mock()
        with self.assertLogs(level="WARNING"):
            result = run_meter(parse_args(["--demo-overrange", "--frames", "2"]),
                               display, factory, sleep=lambda _: None)
        factory.assert_not_called()
        self.assertEqual(result, 1)  # Overrange is not a valid numeric pressure.
        self.assertEqual(display.events, [("vented",), ("vented",)])

    def test_vacuum_profile_rejects_incompatible_generic_controls(self):
        for extra in (["--invert"], ["--scale", "linear"], ["--min-pressure", "1e-8"],
                      ["--atmospheric-torr", "1e-6"], ["--atmospheric-torr", "nan"],
                      ["--demo", "--demo-overrange"]):
            with self.subTest(extra=extra), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                parse_args(extra)

    def test_new_defaults_and_orientation_overrides(self):
        args = parse_args([])
        self.assertEqual(args.brightness, 0)
        self.assertEqual(args.direction, "left-to-right")
        self.assertTrue(args.reverse_modules)
        args = parse_args(["--brightness", "5", "--direction", "left-to-right", "--no-reverse-modules"])
        self.assertEqual(args.brightness, 5)
        self.assertEqual(args.direction, "left-to-right")
        self.assertFalse(args.reverse_modules)

    def test_console_uses_whole_columns_and_the_selected_direction(self):
        for direction, expected in (("right-to-left", "." * 23 + "#" * 9),
                                    ("left-to-right", "#" * 9 + "." * 23)):
            output = StringIO()
            with redirect_stdout(output):
                ConsoleDisplay(direction).show_level(9 / 32 + .001)
            self.assertEqual(output.getvalue().strip(), "|" + expected + "|")

    def test_running_from_source_does_not_require_installed_package_metadata(self):
        from importlib.metadata import PackageNotFoundError
        with patch("storage_loadlock_gauge_reader.cli.version", side_effect=PackageNotFoundError):
            self.assertEqual(parse_args([]).brightness, 0)

    def test_eta_is_removed_on_pressure_rise_and_after_goal_is_reached(self):
        # Exact exponential decline permits an ETA after 60 seconds. Then an
        # upward jump must remove it immediately and discard the old model.
        pressures = [1e-5 * math.exp(-t / 30) for t in range(71)]
        pressures += [pressures[-1] * 1.02, pressures[-1] * 1.03, 5e-8, 4e-8]
        reader = Mock()
        reader.read_pressure.side_effect = [Reading(GaugeStatus.OK, p, PressureUnit.TORR) for p in pressures]
        display = FakeDisplay()
        timestamps = iter(range(len(pressures)))
        args = parse_args(["--frames", str(len(pressures))])
        run_meter(args, display, Mock(return_value=reader), sleep=lambda _: None,
                  clock=lambda: next(timestamps))
        self.assertEqual(display.events[59][0], "level")
        self.assertEqual(display.events[60][0], "eta")
        self.assertTrue(all(event[0] == "level" for event in display.events[71:]))

    def test_serial_fault_or_overrange_clears_eta_history(self):
        for fault in (CommunicationError("unplugged"), Reading(GaugeStatus.OVERRANGE, 9999, PressureUnit.TORR)):
            readings = [Reading(GaugeStatus.OK, 1e-5 * math.exp(-t / 30), PressureUnit.TORR) for t in range(65)]
            readings += [fault, Reading(GaugeStatus.OK, 1e-6, PressureUnit.TORR)]
            reader = Mock()
            reader.read_pressure.side_effect = readings
            display = FakeDisplay()
            timestamps = iter(range(100))
            with self.assertLogs(level="INFO"):
                run_meter(parse_args(["--frames", "67"]), display, Mock(return_value=reader),
                          sleep=lambda _: None, clock=lambda: next(timestamps))
            self.assertEqual(display.events[64][0], "eta")
            self.assertIn(display.events[65][0], ("error", "vented"))
            self.assertEqual(display.events[66][0], "level")

    def test_demo_pumpdown_exercises_eta_then_venting_without_serial(self):
        display, factory = FakeDisplay(), Mock()
        with self.assertLogs(level="INFO"):
            result = run_meter(parse_args(["--demo-pumpdown", "--frames", "130"]),
                               display, factory, sleep=lambda _: None)
        factory.assert_not_called()
        self.assertEqual(result, 1)
        self.assertTrue(any(event[0] == "eta" for event in display.events[:81]))
        self.assertTrue(all(event[0] == "level" for event in display.events[81:120]))
        self.assertTrue(all(event == ("vented",) for event in display.events[120:]))

    def test_eta_can_be_disabled_and_history_options_are_validated(self):
        display = FakeDisplay()
        run_meter(parse_args(["--demo-pumpdown", "--no-eta", "--frames", "70"]),
                  display, sleep=lambda _: None)
        self.assertTrue(all(event[0] == "level" for event in display.events))
        for options in (["--eta-window", "10"], ["--eta-min-span", "0"],
                        ["--demo", "--demo-pumpdown"]):
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                parse_args(options)


if __name__ == "__main__":
    unittest.main()
