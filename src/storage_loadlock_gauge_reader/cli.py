"""Run the TPG261 pressure meter, or exercise it without attached hardware."""

import argparse
from importlib.metadata import PackageNotFoundError, version
import logging
import math
import signal
import time

from .display import Max7219Display, level_fraction, lit_columns, vacuum_fraction
from .eta import PumpdownEstimator, format_eta
from .tpg261 import GaugeStatus, InvalidReadingError, TPG261, TPG261Error


LOG = logging.getLogger(__name__)


def positive_float(text):
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return value


def nonnegative_float(text):
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return value


def nonnegative_int(text):
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="gauge-reader",
        description="TPG261 pressure level meter on a four-module MAX7219 32x8 matrix.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    try:
        package_version = version('storage-loadlock-gauge-reader')
    except PackageNotFoundError:
        package_version = "source checkout"
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version}")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="USB RS232 serial device")
    parser.add_argument("--baud", type=int, choices=(9600, 19200, 38400), default=9600,
                        help="must match the TPG261 front-panel setting")
    parser.add_argument("--timeout", type=positive_float, default=1.0, help="serial timeout in seconds")
    parser.add_argument("--interval", type=positive_float, default=0.25, help="pause between samples in seconds")
    parser.add_argument("--retry-delay", type=positive_float, default=2.0, help="pause before reconnecting after serial failure")
    parser.add_argument("--profile", choices=("vacuum", "generic"), default="vacuum",
                        help="vacuum: agreed Torr bands; generic: original configurable mbar bar")
    parser.add_argument("--atmospheric-torr", type=positive_float, default=760.0,
                        help="full-bar endpoint in Torr for the vacuum particle profile")
    parser.add_argument("--no-eta", action="store_true", help="disable the pumpdown time estimate")
    parser.add_argument("--eta-window", type=positive_float, default=300.0,
                        help="recent seconds used to fit log pressure versus time")
    parser.add_argument("--eta-min-span", type=positive_float, default=60.0,
                        help="minimum seconds of fresh falling-pressure history before an ETA")
    parser.add_argument("--min-pressure", type=nonnegative_float, default=1e-6, help="empty-bar threshold in mbar (before --invert)")
    parser.add_argument("--max-pressure", type=positive_float, default=1000.0, help="full-bar threshold in mbar (before --invert)")
    parser.add_argument("--scale", choices=("log", "linear"), default="log", help="pressure scale")
    parser.add_argument("--invert", action="store_true", help="fill the bar as vacuum improves (pressure decreases)")
    parser.add_argument("--spi-bus", type=nonnegative_int, default=0, help="Linux SPI bus")
    parser.add_argument("--spi-device", type=nonnegative_int, default=0, help="Linux SPI chip select")
    parser.add_argument("--brightness", type=int, choices=range(16), default=0, help="MAX7219 intensity, 0 is minimum brightness, not off")
    parser.add_argument("--direction", choices=("right-to-left", "left-to-right"), default="left-to-right",
                        help="edge from which the particle bar grows as pressure rises")
    parser.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=0, help="clockwise rotation of each 8x8 tile")
    order = parser.add_mutually_exclusive_group()
    order.add_argument("--reverse-modules", dest="reverse_modules", action="store_true",
                       help="DIN-nearest tile is the rightmost tile (default; matches the supplied video)")
    order.add_argument("--no-reverse-modules", dest="reverse_modules", action="store_false",
                       help="DIN-nearest tile is the leftmost tile")
    parser.set_defaults(reverse_modules=True)
    parser.add_argument("--mirror", action="store_true", help="mirror the logical image horizontally")
    parser.add_argument("--console", action="store_true", help="print a terminal bar instead of using SPI")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--demo", action="store_true", help="use a synthetic pressure sweep instead of a serial gauge")
    source.add_argument("--demo-overrange", action="store_true", help="simulate oL and show VENTED; no gauge is read")
    source.add_argument("--demo-pumpdown", action="store_true",
                        help="synthetic pumpdown/vent cycle, with 10 virtual seconds per sample")
    parser.add_argument("--frames", type=nonnegative_int, default=0, help="stop after this many samples; 0 runs until Ctrl+C")
    parser.add_argument("--list-ports", action="store_true", help="list serial devices and exit")
    parser.add_argument("--verbose", action="store_true", help="log each reading and diagnostic details")
    args = parser.parse_args(argv)
    if args.atmospheric_torr <= 1e-6:
        parser.error("--atmospheric-torr must exceed 1e-6 Torr")
    if args.eta_window < args.eta_min_span:
        parser.error("--eta-window must be at least --eta-min-span")
    if args.profile == "vacuum" and (args.invert or args.scale != "log" or
                                    args.min_pressure != 1e-6 or args.max_pressure != 1000.0):
        parser.error("--invert/--scale/--min-pressure/--max-pressure require --profile generic")
    if args.max_pressure <= args.min_pressure:
        parser.error("--max-pressure must exceed --min-pressure")
    if args.scale == "log" and args.min_pressure == 0:
        parser.error("--min-pressure must be greater than zero for logarithmic scaling")
    return args


class ConsoleDisplay:
    """A terminal backend that needs neither SPI nor an attached matrix."""

    def __init__(self, direction="left-to-right"):
        self.direction = direction

    def show_level(self, fraction):
        columns = lit_columns(fraction)
        bar = "#" * columns + "." * (32 - columns)
        if self.direction == "right-to-left":
            bar = bar[::-1]
        print("|" + bar + "|", flush=True)

    def show_error(self):
        print("|XXXXXXXX  ERROR / NO VALID DATA  XXXXXXXX|", flush=True)

    def show_vented(self):
        print("|" + "VENTED".center(32) + "|", flush=True)

    def show_eta(self, fraction, text):
        columns = int(max(0, min(1, fraction)) * 16 + .5)
        bar = "#" * columns + "." * (16 - columns)
        if self.direction == "right-to-left":
            bar = bar[::-1]
        print("|" + bar + "|" + text.center(16) + "|", flush=True)

    def close(self):
        pass


def demo_pressure(frame, low, high, scale):
    """Sweep through the range and back once per 64 sample intervals."""
    phase = frame % 64
    fraction = phase / 32 if phase <= 32 else (64 - phase) / 32
    if scale == "log":
        return 10 ** ((1 - fraction) * math.log10(low) + fraction * math.log10(high))
    return (1 - fraction) * low + fraction * high


def demo_pumpdown(frame):
    """Synthetic fall, rise, then overrange; every frame advances ten seconds."""
    phase = frame % 130
    if phase <= 80:
        return max(1.5e-8, 1e-5 * math.exp(-phase * 10 / 120))
    if phase < 120:
        return min(760.0, 1.5e-8 * math.exp((phase - 80) * 10 / 20))
    return None  # Simulated oL, not a numeric measurement.


def run_meter(args, display, reader_factory=TPG261, sleep=time.sleep, clock=time.monotonic):
    """Poll, replace invalid/stale data with Xs, and reconnect after serial faults."""
    reader = None
    frame = 0
    last_fault = None
    healthy = False
    estimator = PumpdownEstimator(window_seconds=args.eta_window,
                                  min_span_seconds=args.eta_min_span)

    def disconnect():
        nonlocal reader
        if reader is not None:
            try:
                reader.close()
            except OSError:
                LOG.debug("Serial close failed", exc_info=True)
            finally:
                reader = None

    try:
        while args.frames == 0 or frame < args.frames:
            reconnect = False
            fault = None
            overrange = False
            estimate = None
            try:
                if args.demo_overrange:
                    overrange = True
                elif args.demo_pumpdown:
                    pressure_torr = demo_pumpdown(frame)
                    overrange = pressure_torr is None
                    if not overrange and args.profile == "generic":
                        pressure = pressure_torr * 1.3332236842105263
                elif args.demo:
                    if args.profile == "vacuum":
                        pressure_torr = demo_pressure(frame, 1.5e-8, args.atmospheric_torr, "log")
                    else:
                        pressure = demo_pressure(frame, args.min_pressure, args.max_pressure, args.scale)
                else:
                    if reader is None:
                        reader = reader_factory(args.port, baudrate=args.baud, timeout=args.timeout)
                        reader.open()
                    reading = reader.read_pressure()
                    if reading.status == GaugeStatus.OVERRANGE:
                        overrange = True
                    elif reading.status != GaugeStatus.OK:
                        raise InvalidReadingError(reading.status.name.replace("_", " "))
                    elif args.profile == "vacuum":
                        pressure_torr = reading.pressure_torr
                    else:
                        pressure = reading.pressure_mbar
                if not overrange:
                    if args.profile == "vacuum":
                        fraction = vacuum_fraction(pressure_torr, args.atmospheric_torr)
                        if not args.no_eta:
                            sample_time = frame * 10.0 if args.demo_pumpdown else clock()
                            estimate = estimator.update(sample_time, pressure_torr)
                    else:
                        fraction = level_fraction(pressure, args.min_pressure, args.max_pressure,
                                                  args.scale, args.invert)
            except InvalidReadingError as exc:
                fault = str(exc)
            except (TPG261Error, OSError) as exc:
                fault = str(exc)
                reconnect = True
                disconnect()
            except ValueError as exc:
                # Zero is not usable on a logarithmic scale; never silently
                # draw an invalid reading as excellent vacuum.
                fault = str(exc)

            healthy = fault is None and not overrange
            if overrange:
                estimator.reset()
                display.show_vented()
                if last_fault != "oL / OVERRANGE":
                    LOG.warning("oL / OVERRANGE: displaying VENTED")
                last_fault = "oL / OVERRANGE"
            elif fault is not None:
                estimator.reset()
                # SPI errors are fatal, not a reason to reconnect the gauge.
                display.show_error()
                if fault != last_fault:
                    LOG.warning("No valid pressure: %s", fault)
                last_fault = fault
            else:
                if estimate is not None:
                    display.show_eta(fraction, format_eta(estimate.seconds))
                    LOG.debug("ETA %.0fs to %.3g Torr; fit R² %.3f", estimate.seconds,
                              estimate.target_torr, estimate.r_squared)
                else:
                    display.show_level(fraction)
                if last_fault is not None:
                    LOG.info("Valid pressure restored")
                last_fault = None
                value, unit = (pressure_torr, "Torr") if args.profile == "vacuum" else (pressure, "mbar")
                LOG.debug("%.6g %s; bar %.1f%%", value, unit, fraction * 100)
                if args.console:
                    label = "DEMO" if args.demo or args.demo_pumpdown else "TPG261"
                    print(f"{label}: {value:.6g} {unit}; {fraction:.1%}", flush=True)
            frame += 1
            if args.frames == 0 or frame < args.frames:
                sleep(args.retry_delay if reconnect else args.interval)
    finally:
        disconnect()
    return 0 if healthy else 1


def _stop(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s: %(message)s")
    if args.list_ports:
        try:
            from serial.tools import list_ports
        except ImportError:
            LOG.error("Install this project with pip to provide pyserial")
            return 2
        for port in list_ports.comports():
            print(f"{port.device}: {port.description}")
        return 0

    display = None
    previous_sigterm = signal.signal(signal.SIGTERM, _stop)
    try:
        display = ConsoleDisplay(direction=args.direction) if args.console else Max7219Display(
            bus=args.spi_bus, device=args.spi_device, intensity=args.brightness,
            rotate=args.rotate, reverse_modules=args.reverse_modules, mirror=args.mirror,
            direction=args.direction,
        )
        if args.demo or args.demo_overrange or args.demo_pumpdown:
            LOG.info("DEMO: synthetic pressures; no gauge is connected or read")
            if args.demo_pumpdown:
                LOG.info("DEMO: 10 virtual seconds per sample; estimates are simulated")
        else:
            LOG.info("Reading TPG261 at %s, %d baud", args.port, args.baud)
        return run_meter(args, display)
    except KeyboardInterrupt:
        LOG.info("Stopped")
        return 0
    except (OSError, RuntimeError) as exc:
        LOG.error("%s", exc)
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        if display is not None:
            try:
                display.close()
            except OSError as exc:
                LOG.error("Could not blank/close the display: %s", exc)
