# TPG261 → Raspberry Pi 3B/4B → MAX7219 LED pressure meter

Read a Pfeiffer TPG261 through a **USB-to-RS232 adapter** and display its pressure
as a horizontal bar on the **[SMG] SZH-EKAD-115 MAX7219 8×32 dot matrix**.
The four 8×8 tiles provide a 256-LED **particle bar**: more lit LEDs mean higher
pressure. The default bar is anchored at the left. As pressure falls, its right
edge moves **right to left** until all LEDs are off at or below 1.5×10⁻⁸ Torr.
Each step switches a whole column; the LEDs keep their fixed hardware color.
At overrange (`oL`), the matrix displays **VENTED**. A pumpdown time estimate is
shown only while a sufficiently reliable falling-pressure trend is available.

Requires Raspberry Pi OS with Python **3.9 or newer**. Both 32-bit and 64-bit Pi OS
can use the Python implementation; actual Pi hardware operation still needs to be
verified. No Arduino or external graphics library is needed.

**Wire the hardware using [the wiring guide](hardware.md) before running the
LED display.** In particular, use 5 V module power, buffered 3.3 V → 5 V SPI
signals, and a real RS232 adapter with the correct crossover wiring.

## 1. Install on the Raspberry Pi

Copy this project folder to the Pi and open a terminal in it. The project files
must be present locally; cloud edits are not automatically pushed to GitHub.
For example, extract the provided project ZIP, then:

```sh
cd Storage_loadlock_gauge_reader-main
sudo apt update
sudo apt install -y python3-venv python3-dev build-essential
python3 -m venv .venv
.venv/bin/python -m pip install --editable '.[pi]'
```

The `pi` extra installs `spidev`; `pyserial` is installed automatically. Build tools
allow the SPI extension to be built on the Pi if a wheel is unavailable. Use a
virtual environment; do not install into the OS-managed Python with `sudo pip`.

Enable SPI using `sudo raspi-config` → Interface Options → SPI, and grant your
login user access to serial and SPI devices:

```sh
sudo usermod -aG dialout,spi "$USER"
sudo reboot
```

After reboot, return to the project directory. SPI0/CE0 should appear as
`/dev/spidev0.0`. Check the serial adapter's device name:

```sh
.venv/bin/gauge-reader --list-ports
ls -l /dev/serial/by-id/
```

Use a stable `/dev/serial/by-id/...` path with `--port` if available. Otherwise,
`/dev/ttyUSB0` is a common name; your adapter might use a different name.

## 2. Test the display without the gauge

With the matrix wired and powered:

```sh
.venv/bin/gauge-reader --demo --brightness 0
```

The bar sweeps from empty to full and back. **Demo data is synthetic**; this mode
does not open the serial port. Stop it with Ctrl+C.

PCB layouts differ. If the bar is broken into sideways strips, rotate each tile:

```sh
.venv/bin/gauge-reader --demo --rotate 90
```

Try `--rotate 0`, `90`, `180`, or `270` until the bar is continuous. The default
`--direction left-to-right --reverse-modules` keeps the DIN-nearest tile on the
right while anchoring the particle bar at the left. As pressure falls, columns
turn off from right to left. `--direction right-to-left` anchors the bar at the
opposite edge. If your board instead has the DIN-nearest tile on the
left, use `--no-reverse-modules`. `--mirror` is an additional orientation flip,
so leave it off for the filmed setup. Keep any rotation correction your board
requires. The supplied schematic does not establish the orientation of the assembled
four-tile board, so this is a required first-use check.

## 3. Read the gauge

Match the controller's baud setting (default **9600**, 8 data bits, no parity,
1 stop bit, no flow control). First check the pressure in the terminal:

```sh
.venv/bin/gauge-reader --port /dev/ttyUSB0 --console
```

Compare it with the front-panel reading and units. The default vacuum profile prints pressures in **Torr**, converting the reported
controller unit automatically. `100 Pa = 1 mbar`, `1 Torr ≈ 1.333224 mbar`.
Thresholds in this profile are specified in Torr; the optional original generic
profile still prints mbar.
The manual labels unit code 0 as “mbar/bar”; for an unambiguous initial check,
select **Pascal** on the controller's front panel and compare the converted
reading. The program queries the selected unit every sample and does not change
unit, baud, sensor, or relay settings. Avoid changing units while it is sampling.

Then drive the LED bar (include any orientation flags found above):

```sh
.venv/bin/gauge-reader --port /dev/ttyUSB0 --baud 9600 --brightness 0
```

The default **vacuum** profile follows these pressure bands, all in **Torr**.
The bar fills from the left as pressure rises and shrinks from the right as it
falls. The following table describes the full-width bar when no ETA is shown:

| Valid pressure | Illuminated portion |
| --- | --- |
| At/below **1.5×10⁻⁸** | All LEDs off, matching the chamber's approximate lowest pressure. |
| **1.5×10⁻⁸ to 1×10⁻⁷** | Logarithmic rise from 0 to 8 lit columns as pressure rises. |
| **1×10⁻⁷ to 1×10⁻⁶** | Logarithmic rise from 8 to 16 lit columns as pressure rises. |
| **1×10⁻⁶ to 760** | Logarithmic rise from 16 to 32 lit columns toward atmosphere. |
| **oL / OVERRANGE** (serial status 2) | **VENTED**, with no ETA. Numeric placeholder values are ignored. |
| Other invalid statuses or serial errors | **XXXX**, with no ETA; terminal warnings identify the fault. |

The mathematical fill is continuous at every pressure-band boundary: 0 columns
at 1.5×10⁻⁸ Torr, 8 at 1×10⁻⁷, 16 at 1×10⁻⁶, and 32 at 760 Torr. The hardware
rounds to the nearest whole column, giving **33 fill levels**, including empty.
Every lit column of the full-width bar contains all eight LEDs. In terminal
output, `#` is a full column and `.` is an unlit column. An OK-status zero pressure
is invalid for this log scale and displays Xs. An underrange status is also an
error, not a guarantee that the chamber has reached the blank-bar pressure.

Words use compact six-row characters: the logical top and bottom rows remain
blank when text is rendered. At fully vented **oL**, the display holds **VENTED**
steadily. Other sensor or communication faults show XXXX, with their reason in
the terminal. Either type of fault clears the ETA and its pressure history; a
valid reading restores the bar, while estimation needs a fresh falling trend.
A finite `--frames` run exits and attempts blanking after its last sample; use
continuous operation to keep the indication visible.

Use `--atmospheric-torr` to change the upper full-bar endpoint if necessary
(default 760 Torr). This is a visualization endpoint, not the gauge's maximum
measurable pressure. The agreed 1.5×10⁻⁸, 1×10⁻⁷, and 1×10⁻⁶ thresholds are fixed
in the vacuum profile. This display indicates the selected pressure bands; it
neither grants valve-open permission nor controls the gate valve.

Examples:

```sh
# Check the agreed pressure bands without a physical gauge.
.venv/bin/gauge-reader --demo

# Check VENTED for oL without a physical gauge. Ctrl+C to stop.
.venv/bin/gauge-reader --demo-overrange

# Print Torr pressures without driving SPI LEDs.
.venv/bin/gauge-reader --port /dev/ttyUSB0 --console

# Original configurable bar remains available under the generic profile.
# These thresholds are in mbar; --invert fills as pressure decreases.
.venv/bin/gauge-reader --profile generic --port /dev/ttyUSB0 \
  --min-pressure 1e-6 --max-pressure 1000 --invert

.venv/bin/gauge-reader --help
```

`--scale`, `--min-pressure`, `--max-pressure`, and `--invert` belong to the
**generic** profile. Select `--profile generic` before using these original
controls. VENTED for oL applies to both profiles. No RGB hardware or additional
color indicators are required.

Serial errors trigger a close/reopen after `--retry-delay` (default 2 seconds).
Other sensor-status faults are re-polled on the same connection. `--interval`
is the pause after a normal sample, not a guaranteed sample rate. A serial
timeout can take several timeout intervals during startup synchronization; an
old bar is replaced once the failure is detected. Ctrl+C or SIGTERM attempts to
blank/shut down the matrix and close the ports; SPI failure can prevent physical
blanking.

### Choose brightness before starting

The new default is **`--brightness 0`**, the dimmest hardware setting. It applies
to demo, normal readings, VENTED, ETA text, and fault patterns. This is not off.
Choose a register level from **0 to 15** before starting either mode:

```sh
# Dimmest demo (the new default).
.venv/bin/gauge-reader --demo --brightness 0

# Brighter demo, or the same chosen brightness during real measurement.
.venv/bin/gauge-reader --demo --brightness 3
.venv/bin/gauge-reader --port /dev/ttyUSB0 --brightness 3

# Optional: anchor the particle bar at the opposite edge instead.
.venv/bin/gauge-reader --demo --direction right-to-left
```

The MAX7219 uses 16 intensity steps, not a calibrated visual percentage. For
this MAX7219, level 0 uses 1/32 LED on-time and the previous default level 2
uses 5/32: level 0 is about **20% of the previous on-time**. Perceived brightness
and camera exposure do not necessarily change by that same percentage. Level
15 is the brightest hardware setting (31/32 on-time).

A finite `--frames` run returns 1 if its last sample is faulty or overrange, 0
if valid; configuration/display-startup failures return 2. Without `--frames`,
the program runs until stopped. Demo inputs are synthetic; remove `--demo`,
`--demo-pumpdown`, or `--demo-overrange` for real measurements. Do not change controller units while
sampling.

## Pumpdown time estimate

The estimate answers the next pressure target, in **Torr**:

- Above 1×10⁻⁷, estimate the time to reach 1×10⁻⁷ (entry into the E−8 decade).
- At/below 1×10⁻⁷ but above 5×10⁻⁸, estimate the time to reach 5×10⁻⁸.
- At/below 5×10⁻⁸, remove the ETA entirely.

The displayed duration is approximate and rounded up: `5M` means about 5 minutes,
`60M` about one hour, and `2H` about two hours. A missing ETA means the program
cannot currently make a suitable estimate; it does not mean zero time remains.

While a valid ETA is available, the **rightmost two tiles** display the duration
and the **leftmost two tiles** display the particle bar, rescaled to 16 columns.
When no ETA is available, the pressure bar uses all 32 columns again. Because
the available bar width changes, compare the fraction filled rather than the
absolute number of lit columns across a layout change. In the combined text/bar
layout, both areas leave the top and bottom rows blank; full-width bar-only
frames continue to use all eight rows. VENTED uses the entire display width.

The default fit uses up to the most recent **300 seconds**, at least **10 samples**
spanning **60 seconds**, an observed decline of at least **2%**, and fit quality
**R² ≥ 0.9**. There is no estimate during initial data collection, a plateau,
an unreliable trend, or pressure rise. Any pressure reading above the previous
reading hides the ETA for that sample. The estimator also discards its history
on a larger or sustained rise: more than 1% above the previous reading or the
minimum in the last 15 seconds, three consecutive increases, or a positive
recent trend with more than 0.2% net rise. This prevents the preceding pumpdown
from continuing to supply an ETA while venting starts.

Faults, overrange, invalid readings/times, and reaching 5×10⁻⁸ Torr clear the fit.
A gap longer than 30 seconds starts a fresh history. After a reset, new falling
data must satisfy the fit requirements again. Estimates beyond 24 hours are
hidden rather than displaying a long extrapolation. The display rounds up to
whole minutes through `99M`, then whole hours through `24H`.

You can disable the ETA or change its observation window:

```sh
python3 led_pressure_meter.py --no-eta
python3 led_pressure_meter.py --eta-window 600 --eta-min-span 120

# Synthetic declining pressure with a demonstration time base; no gauge is read.
python3 led_pressure_meter.py --demo-pumpdown
```

A larger window smooths noise but reacts more slowly to changed pumpdown
conditions. Shortening the minimum span can make predictions appear sooner but
provides less evidence that the trend will continue. Ordinary `--demo` sweeps
pressure up and down and is primarily a bar/direction check; use the pumpdown
demo to exercise the ETA.

The model fits a straight line to **ln(pressure) versus elapsed time** over a
recent rolling window. This is a local exponential pumpdown model, not a linear
pressure-versus-time extrapolation. For a chamber with volume V and constant
effective pumping speed S, ideal pumpdown behaves approximately as
`P(t) = P0 exp(-S t / V)`. Real load locks depart from this model: outgassing,
conductance changes, leaks, pump sequencing, and the ultimate-pressure floor can
change the trend. Late pumpdown may behave more like a power law or approach a
nonzero floor. A single exponential fit across the entire pumpdown is therefore
unlikely to remain reliable. The rolling fit follows the recent observed trend;
its quality checks can hide the ETA, but cannot guarantee the future pumpdown.

A time estimate is a trend indicator, not permission to open the chamber gate
valve. It does not operate the pumps or valve. Recorded pressure/time data from
several actual pumpdowns can later be used to evaluate the local model against
your chamber and refine its window or fit model.

## Run the LED meter using a Python filename

Keep `led_pressure_meter.py` in the complete project folder, alongside the `src`
directory. To run it with the system `python3`, install its serial and SPI
libraries once through Raspberry Pi OS:

```sh
sudo apt update
sudo apt install -y python3-serial python3-spidev
```

From the project folder, run:

```sh
python3 led_pressure_meter.py
```

It uses the same pressure bands, minimum brightness, whole-column fill, and
left-anchored particle-bar direction described above. Edit the serial port, controller baud,
and brightness settings near the top of the launcher when needed. Its default
pause is one second after each sample; communication time adds to that interval.
Stop other programs using the gauge's serial port before starting it.

Options can be supplied after the filename:

```sh
python3 led_pressure_meter.py --demo
python3 led_pressure_meter.py --demo-overrange
python3 led_pressure_meter.py --demo --console --frames 5
```

The first command tests the bar without the gauge; the second shows VENTED
without the gauge; the third needs neither the gauge nor the LED board. Press
Ctrl+C to stop a continuous run. If you already installed the project's virtual
environment, you can instead use `.venv/bin/python led_pressure_meter.py`.
The launcher loads the adjacent source directory, so keep that directory with it;
copying only the launcher is insufficient.

## Read pressure using a single Python command

`read_pressure.py` is self-contained: it needs only Python and pyserial, not the
LED modules or an installed project package. On Raspberry Pi OS, install the
system Python serial library once:

```sh
sudo apt update
sudo apt install -y python3-serial
```

Then run from the folder containing the file:

```sh
python3 read_pressure.py
```

It prints a valid pressure in Torr every second, for example `2.100e-08 Torr`.
Press Ctrl+C to stop. Edit `PORT` and `BAUD_RATE` near the top of the file to
match the USB adapter and controller. `INTERVAL_SECONDS` defaults to 1.0.
Read time is accounted for in scheduling; a communication timeout can delay
sampling beyond one second. Errors and non-OK sensor statuses go to stderr,
and no numeric placeholder is printed as a pressure. The connection is retried.
A working RS232 connection is required to obtain readings.

For the controller's **raw pressure reply**, without any numeric parsing, status
interpretation, or unit conversion, use the separate standalone script:

```sh
python3 read_raw_pressure.py
```

This sends TPG261 `PR1`, waits for ACK, then sends ENQ. It prints the received
bytes with `repr`, for example `b'0, 4.0600E-08\r\n'`, preserving whitespace,
status, and terminators. An empty data reply prints `b''`. A missing or invalid
ACK is reported separately to stderr. The value uses the controller's selected
unit. It targets a one-second interval; timeouts can make this longer. Stop other
programs using the serial port first. This script uses the same system pyserial
dependency as `read_pressure.py`. The uploaded example's `#000F` command belongs
to a different controller protocol and is not sent to the TPG261.

## Save pressure readings for pumpdown analysis

To record pressure without using the LED display, run from the complete project
folder (keep the `src` folder alongside the script):

```sh
python3 log_pressure.py
```

It creates a new `pressure_log_YYYYMMDD_HHMMSS_microseconds.csv` beside the script
and records at a nominal one-second interval. The actual interval includes serial
read time. Press Ctrl+C to stop; the CSV is flushed after each row and closed on
exit. Copy or upload that CSV for later analysis. Existing log files are never
overwritten, and automatically named pressure logs are excluded from Git.

The four columns are:

```text
timestamp,elapsed_seconds,pressure_torr,status
```

The timestamp includes the Pi's local UTC offset; elapsed seconds use a monotonic
clock. Only valid pressure is recorded numerically, always converted to Torr.
Vented/sensor/communication errors have an empty pressure field and a status
describing the problem, so their numeric placeholders cannot distort a fit.
Demo records are explicitly marked `DEMO_...` and are not real measurements.

You can also record while watching the LED meter, using a new filename each run:

```sh
python3 led_pressure_meter.py --log-pressure pressure_log_1.csv --verbose
```

Use this combined command instead of starting `log_pressure.py` at the same time:
only one process should read the gauge's serial port. An existing CSV destination
is rejected to protect the previous record. Stop with Ctrl+C before copying the
file. An unmodified `python3 led_pressure_meter.py` does not create a CSV.

### Why an ETA can be missing at 1.7e-7 Torr

That pressure is eligible for an ETA to 1e-7 Torr, but a single value cannot
establish the falling-pressure trend. The checks described above still apply:
fresh history of at least 60 seconds/10 samples, at least 2% net fall in the
rolling window, R² at least 0.9, recent decline rather than a plateau, no upward
reading, and a prediction no longer than 24 hours. Quantized/stair-step sensor
readings can look flat between changes. Restarting the program resets history.

Use `--verbose` to see the actual reason for the current frame, such as
`ETA hidden: collecting ...`, a pressure rise, recent plateau, insufficient
decline, or a poor fit. This diagnostic does not relax the estimator's checks.
The recorded CSV can be used to evaluate those checks and calibrate a model
against a real pumpdown rather than guessing from one pressure value.

## Development / cloud testing

Cloud testing does not require the Pi, serial adapter, or LED board:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --editable .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/gauge-reader --demo --console --frames 5 --interval 0.01
```

`--console` replaces SPI output; `--demo` replaces the gauge input. Use both for
hardware-free testing, or just one to test the other hardware component.
You can also run `.venv/bin/python -m storage_loadlock_gauge_reader` with the same
arguments. Use the existing checkout in each isolated cloud task; no extra Git
worktree is needed. Publishing an environment saves its setup/snapshot; a separate
commit and push is needed to put source changes on GitHub.

Tests cover ACK/ENQ commands, pressure/status parsing, unit conversions, real
pyserial transport through an emulated POSIX serial port, reconnect behavior,
fault/VENTED display policy, six-row text and ETA layout, pumpdown trend/ETA
reset behavior, and MAX7219 frames/orientation using a fake SPI device.
They do **not** verify electrical wiring, actual gauge firmware, physical matrix
orientation, or operation on a Raspberry Pi CPU.

## Source files

- `led_pressure_meter.py`: direct `python3` launcher for the LED meter.
- `log_pressure.py`: one-second CSV logging without the LED display.
- `src/storage_loadlock_gauge_reader/tpg261.py`: TPG261 protocol and pressure units.
- `src/storage_loadlock_gauge_reader/display.py`: scaling, text, and four-chip SPI display.
- `src/storage_loadlock_gauge_reader/eta.py`: rolling pumpdown trend and time estimate.
- `src/storage_loadlock_gauge_reader/pressure_log.py`: flushed, timestamped CSV records.
- `src/storage_loadlock_gauge_reader/cli.py`: polling, demo, options, and recovery.
- `docs/hardware.md`: wiring, power, serial settings, and manual references.
- `tests/`: hardware-independent protocol, display, and application tests.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| VENTED | The controller reported oL/overrange, or `--demo-overrange` is selected. |
| Four Xs | A sensor fault or communication error occurred. Read terminal warnings for the reason. |
| Timeout / no ACK | Confirm a real RS232 adapter and crossed TX/RX, not a TTL cable or a second null-modem crossover. Close other programs using the controller. |
| Permission denied | Add your user to `dialout` and `spi`, then log out/in or reboot. |
| No `/dev/spidev0.0` | Enable SPI and reboot. |
| Matrix blank | A valid pressure at/below 1.5×10⁻⁸ Torr intentionally gives a blank bar. Otherwise check 5 V power, common ground, level-shifter enable pins, and the module's **input** header. |
| Sideways strips, gaps, or scrambled order | Use demo mode to choose `--rotate` and module order (`--reverse-modules` or `--no-reverse-modules`). |
| Pressure differs by a factor of 100 or 1000 | Compare the controller's units; use Pascal for the initial verification. |
