# Raspberry Pi 3B/4B hardware connections

The program reads a TPG261 through a USB-to-RS232 adapter and drives the four
MAX7219 controllers in the 8×32 SZH-EKAD-115 display through SPI. The controller
and display use different electrical interfaces. Assemble and check the wiring
with power disconnected.

## TPG261 to Raspberry Pi USB

Use a genuine **USB-to-RS232 adapter**, connected to a USB-A port on the Pi.
An adapter advertised as USB-to-TTL/UART or USB-to-RS485 is a different interface
and cannot replace it. Do not connect the TPG261 RS232 signals to Pi GPIO pins.

The supplied TPG261 operating manual, section 3.7, printed page 23, identifies the
controller's male nine-pin D-sub connector as follows:

| TPG261 DB9 pin | Signal | Connection to the adapter |
| --- | --- | --- |
| 2 | RXD, controller receive | Adapter TXD |
| 3 | TXD, controller transmit | Adapter RXD |
| 5 | GND, signal ground | Adapter signal GND |

For a standard PC-style/DTE USB-to-RS232 adapter, its pin 3 is TXD, pin 2 is RXD,
and pin 5 is GND. That adapter therefore needs a **crossed/null-modem connection**:
adapter 3 → TPG261 2, adapter 2 ← TPG261 3, and adapter 5 ↔ TPG261 5. Check your
adapter's actual pinout before choosing the cable: some adapters already provide
a crossed connection. A connector gender changer alone does not cross signals.
Identify D-sub pin numbers from the connector's markings or the adapter manual;
viewing the connector from opposite sides reverses its apparent layout.

Use these communication settings:

| Setting | Value |
| --- | --- |
| Baud rate | 9600 by default; 19200 or 38400 if selected on the TPG261 |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |
| Hardware/software flow control | None |

Match the program's baud-rate option to the controller. The software does not
change the controller's baud rate, pressure units, sensor settings, or setpoints.
It resets the serial interface's input buffer with ETX and uses the documented
UNI and PR1 queries with ACK/ENQ handshaking. A first received character stops
the controller's automatic power-on streaming, as described in the communication
manual, section 1.

Prefer the adapter's stable `/dev/serial/by-id/...` path when it is available.
Depending on its chipset and driver, an adapter may otherwise appear as
`/dev/ttyUSB0` or `/dev/ttyACM0`. Check which device appears when you plug it in;
the example device name in the README is not a promise about your adapter.

### Check the pressure unit on first use

The supplied communication manual, section 2.2.4, lists UNI codes 0 = mbar/bar,
1 = Torr, and 2 = Pascal. PR1 returns pressure in the selected unit. This program
interprets code 0 as mbar, consistent with the manual's serial examples and
measurement-unit specifications, and converts Torr and Pascal to mbar internally.
The supplied text does not explicitly resolve serial scaling for the bar-range
display of code 0.

Compare the program's first pressure reading against the controller's front-panel
reading and units before relying on the level meter. Selecting **Pascal on the
controller's front panel** provides an unambiguous initial check: 100 Pa is
1 mbar. This selection is a manual user action; the program never sends a unit
configuration command. A sensor fault or missing-sensor status is displayed as
an error rather than treating the controller's numeric placeholder as pressure.

## MAX7219 display power and logic levels

Power the display from a regulated **5 V** supply. A separate supply rated at
least **2 A** is a practical starting recommendation for four MAX7219 modules;
actual demand depends on the board's current-setting resistors, brightness, and
number of lit LEDs. Start at low brightness and check the supply and wiring
under the intended display load. Use short power leads of adequate gauge.

Power the Pi through its normal suitable supply. Connect the display supply's
ground to Pi ground and buffer ground, but do not join the separate supply's
positive output to the Pi's 5 V header or otherwise backfeed the Pi. The display
must not be powered from the Pi's 3.3 V pin.

The supplied MAX7219 datasheet specifies a minimum input-high voltage of
**3.5 V** with its normal 5 V supply. Pi GPIO outputs are 3.3 V, so direct
connection is not guaranteed to work reliably. Use a **74AHCT125 or 74HCT125
buffer powered at 5 V** to translate MOSI, clock, and chip select to 5 V logic.
These TTL-input HCT/AHCT families accept Pi 3.3 V signals at their inputs; an
ordinary 5 V 74HC125 is not an equivalent choice for that input threshold.

For three buffer channels, connect the Pi signal to channel input A and its
corresponding output Y to the display. Tie the selected channels' active-low
output-enable inputs (`/OE`) low. Connect buffer VCC to the display's 5 V supply,
ground to the common ground, and place a 100 nF decoupling capacitor close to
its supply pins. Follow the buffer manufacturer's pinout for your exact package;
keep unused inputs at defined levels and disable unused outputs as its
datasheet recommends.

## SPI wiring table

The supplied module schematic labels **J1 as the input** and J2 as the cascade
output. J1 is numbered 1 VCC, 2 GND, 3 DIN, 4 CS, 5 CLK. Check the printed signal
labels on your actual assembled 8×32 board before applying power; do not infer
connector orientation from this table alone.

| Raspberry Pi 3B/4B source | Through the buffer | Display input J1 |
| --- | --- | --- |
| Physical pin 19, GPIO10 / SPI0 MOSI | A → Y, channel 1 | Pin 3, DIN |
| Physical pin 23, GPIO11 / SPI0 SCLK | A → Y, channel 2 | Pin 5, CLK |
| Physical pin 24, GPIO8 / SPI0 CE0 | A → Y, channel 3 | Pin 4, CS / LOAD |
| Physical pin 6, GND | Common ground, including buffer GND | Pin 2, GND |
| Separate regulated 5 V supply positive | Also supplies buffer VCC | Pin 1, VCC |

The numbers in the first column are **physical pins on the 40-pin header**;
GPIO numbers are shown separately. Physical pin 24 is CE0, not GPIO24.

Leave J2/DOUT unconnected unless cascading another display. The 8×32 board
already contains its four cascaded MAX7219 devices. No Pi MISO connection is
needed. **Do not connect the display's 5 V DOUT to Pi GPIO/MISO.**

## Enable SPI and device permissions

On Raspberry Pi OS, run `sudo raspi-config`, select **Interface Options → SPI**,
and enable SPI. Reboot if requested. The default wiring uses SPI bus 0, device 0,
which should provide `/dev/spidev0.0`.

Add the user who runs the program to the SPI and serial device groups:

```sh
sudo usermod -aG spi,dialout "$USER"
```

Log out and log back in, or reboot, so the group change takes effect. Check access
to `/dev/spidev0.0` and the selected USB serial device. Running the application
as root is not normally necessary after the device permissions are correct.
Installation, simulation, display checks, and normal run commands are in the
[usage guide](usage.md).

## Source documents and limits of validation

The wiring and protocol above use the supplied documents as technical reference:

- `TPG261-singlegauge-1.pdf`: BG 805 195 BE/A, section 3.7, printed page 23,
  RS232 connector; technical data and general parameters for serial settings.
- `tpg 261-262 comm protocol bg5198be.pdf`: BG 805 198 BE, sections 1.2, 2.1,
  and 2.2.4 for framing, ACK/ENQ, pressure status, and unit codes.
- Supplier's `SZH-EKAD-115-2` schematic PDF: J1 input, J2 cascade output,
  and MAX7219 connections.
- Supplier's `SZH-EKAD-115` MAX7219/MAX7221 datasheet PDF: electrical
  characteristics, including the 3.5 V minimum logic-high input voltage.
- Supplier's `SZH-EKAD-115-1` LED matrix specifications: individual matrix
  dimensions and LED characteristics; these are not a Pi wiring diagram.

Cloud tests exercise the software and an emulated serial peer. They do not verify
your adapter wiring, gauge hardware, display orientation, power supply, or signal
levels. Perform the first hardware check on the Pi using the documented wiring
and compare the reported pressure with the TPG261 display.
