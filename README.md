# TPG261 → Raspberry Pi 3B/4B → MAX7219 LED pressure meter

Read a Pfeiffer TPG261 through a **USB-to-RS232 adapter** and display its pressure
as a horizontal bar on the **[SMG] SZH-EKAD-115 MAX7219 8×32 dot matrix**.
The four 8×8 tiles provide a 256-LED bar that fills **right to left** as pressure
falls, and empties **left to right** as pressure rises. Each step switches an
entire column of eight LEDs; there is no gradual filling within a column. The LEDs keep their
fixed hardware color; software does not change their color.

Requires Raspberry Pi OS with Python **3.9 or newer**. Both 32-bit and 64-bit Pi OS
can use the Python implementation; actual Pi hardware operation still needs to be
verified. No Arduino or external graphics library is needed.

**Wire the hardware using [the wiring guide](docs/hardware.md) before running the
LED display.** In particular, use 5 V module power, buffered 3.3 V → 5 V SPI
signals, and a real RS232 adapter with the correct crossover wiring.

## 1. Install on the Raspberry Pi

Copy this project folder to the Pi and open a terminal in it. The project files
must be present locally; cloud edits are not automatically pushed to GitHub.
For example, extract the provided project ZIP, then:

```sh
cd Storage_loadlock_gauge_reader
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
`--direction right-to-left --reverse-modules` matches the supplied hardware video:
the DIN-nearest tile is on the right. `--direction left-to-right` changes which
edge the bar grows from. If your board instead has the DIN-nearest tile on the
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
Tiles are counted from the right edge (the edge that lights first):

| Valid pressure | Illuminated portion |
| --- | --- |
| At/below **5×10⁻⁸** | All four tiles full (256 LEDs). Your 2.1×10⁻⁸ reading gives a full bar. |
| **5×10⁻⁸ to 1×10⁻⁷** | First three tiles full; fourth shrinks logarithmically as pressure rises. |
| **1×10⁻⁷ to 1×10⁻⁶** | First two tiles full; third shrinks logarithmically as pressure rises. |
| **1×10⁻⁶ to 760** | First two tiles shrink logarithmically toward an empty bar at atmosphere. |
| **oL / OVERRANGE** (serial status 2) | All 256 LEDs blink on/off. Numeric placeholder values are ignored. |
| Other invalid statuses or serial errors | Four X symbols, distinct from the overrange blink. |

The mathematical fill is continuous at every pressure-band boundary: 256 LEDs
at 5×10⁻⁸ Torr, 192 at 1×10⁻⁷, and 128 at 1×10⁻⁶. The hardware rounds to the
nearest whole column, giving **33 fill levels**, including empty. Every lit
column contains all eight LEDs. In terminal output, `#` is a full column and
`.` is an unlit column; the default bar is anchored at the right. An OK-status zero pressure is invalid for this log scale
and displays Xs. An underrange status is also an error, not a full-bar guarantee.

Default oL blinking is approximately **one complete on/off cycle per second**.
`--blink-period` sets the cycle length; it is at least `--interval`. A new serial
sample is read between cycles, so serial transaction time adds to that period.
If a subsequent sample is a sensor/communication fault, blinking ends and the
X pattern is shown after that failure is detected. A valid sample restores the
normal bar automatically. The first blink phase is on. A finite `--frames` run
exits and attempts blanking after its last sample; use continuous operation to
observe the alarm.

Use `--atmospheric-torr` to change the upper empty-bar endpoint if necessary
(default 760 Torr). This is a visualization endpoint, not the gauge's maximum
measurable pressure. The agreed 5×10⁻⁸, 1×10⁻⁷, and 1×10⁻⁶ thresholds are fixed
in the vacuum profile. This display indicates the selected pressure bands; it
neither grants valve-open permission nor controls the gate valve.

Examples:

```sh
# Check the agreed pressure bands without a physical gauge.
.venv/bin/gauge-reader --demo

# Check the all-LED oL blink without a physical gauge. Ctrl+C to stop.
.venv/bin/gauge-reader --demo-overrange

# Print Torr pressures without driving SPI LEDs.
.venv/bin/gauge-reader --port /dev/ttyUSB0 --console

# Change the oL blink cycle to 2 seconds; keep the agreed pressure mapping.
.venv/bin/gauge-reader --port /dev/ttyUSB0 --blink-period 2

# Original configurable bar remains available under the generic profile.
# These thresholds are in mbar; --invert fills as pressure decreases.
.venv/bin/gauge-reader --profile generic --port /dev/ttyUSB0 \
  --min-pressure 1e-6 --max-pressure 1000 --invert

.venv/bin/gauge-reader --help
```

`--scale`, `--min-pressure`, `--max-pressure`, and `--invert` belong to the
**generic** profile. Select `--profile generic` before using these original
controls. oL blinking applies to both profiles. No RGB hardware or additional
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
to demo, normal readings, oL blinking, and fault patterns. This is not off.
Choose a register level from **0 to 15** before starting either mode:

```sh
# Dimmest demo (the new default).
.venv/bin/gauge-reader --demo --brightness 0

# Brighter demo, or the same chosen brightness during real measurement.
.venv/bin/gauge-reader --demo --brightness 3
.venv/bin/gauge-reader --port /dev/ttyUSB0 --brightness 3

# Optional: fill from the opposite edge instead.
.venv/bin/gauge-reader --demo --direction left-to-right
```

The MAX7219 uses 16 intensity steps, not a calibrated visual percentage. For
this MAX7219, level 0 uses 1/32 LED on-time and the previous default level 2
uses 5/32: level 0 is about **20% of the previous on-time**. Perceived brightness
and camera exposure do not necessarily change by that same percentage. Level
15 is the brightest hardware setting (31/32 on-time).

A finite `--frames` run returns 1 if its last sample is faulty or overrange, 0
if valid; configuration/display-startup failures return 2. Without `--frames`,
the program runs until stopped. Demo inputs are synthetic; remove `--demo` or
`--demo-overrange` for real measurements. Do not change controller units while
sampling.

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
The existing RS232 communication issue must still be resolved to obtain readings.

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
error display policy, and MAX7219 frames/orientation using a fake SPI device.
They do **not** verify electrical wiring, actual gauge firmware, physical matrix
orientation, or operation on a Raspberry Pi CPU.

## Source files

- `src/storage_loadlock_gauge_reader/tpg261.py`: TPG261 protocol and pressure units.
- `src/storage_loadlock_gauge_reader/display.py`: scaling and four-chip SPI display.
- `src/storage_loadlock_gauge_reader/cli.py`: polling, demo, options, and recovery.
- `docs/hardware.md`: wiring, power, serial settings, and manual references.
- `tests/`: hardware-independent protocol, display, and application tests.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Four Xs | Read terminal warnings; check gauge status, baud, device path, RS232 wiring, and USB adapter. |
| All LEDs blink | The controller reported oL/overrange, or you selected `--demo-overrange`. |
| Timeout / no ACK | Confirm a real RS232 adapter and crossed TX/RX, not a TTL cable or a second null-modem crossover. Close other programs using the controller. |
| Permission denied | Add your user to `dialout` and `spi`, then log out/in or reboot. |
| No `/dev/spidev0.0` | Enable SPI and reboot. |
| Matrix blank | Check 5 V power, common ground, level-shifter enable pins, and connection to the module's **input** header. |
| Sideways strips, gaps, or scrambled order | Use demo mode to choose `--rotate` and module order (`--reverse-modules` or `--no-reverse-modules`). |
| Pressure differs by a factor of 100 or 1000 | Compare the controller's units; use Pascal for the initial verification. |
