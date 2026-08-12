# v3 Pin Map

## Waveshare Board Constants

These are from the Waveshare docs and demo bundle inspection.

| Function | GPIO / interface | Notes |
|---|---:|---|
| RGB ring data | `GPIO38` | 7 WS2812 LEDs |
| I2C SCL | `GPIO10` | shared board I2C |
| I2C SDA | `GPIO11` | shared board I2C |
| I2S MCLK | `GPIO12` | audio codec clock |
| I2S BCLK/SCLK | `GPIO13` | audio codec bit clock |
| I2S LRCK/WS | `GPIO14` | audio codec word select |
| I2S SDIN | `GPIO15` | mic/audio input to ESP32 |
| I2S DOUT | `GPIO16` | audio output from ESP32 |
| ES8311 speaker codec | board I2C + I2S | optional firmware path, no extra header wiring |
| Speaker amplifier enable | TCA9555 `EXIO8` | active high; firmware preserves other expander outputs |
| microSD CLK | `GPIO40` | onboard TF slot |
| microSD D0 | `GPIO41` | onboard TF slot |
| microSD CMD | `GPIO42` | onboard TF slot |

## Push-To-Talk

Current firmware default:

| Function | GPIO | Reason |
|---|---:|---|
| PTT button | `GPIO0` | BOOT button is easy to test at runtime |

Default electrical config:

| Config | Value | Meaning |
|---|---:|---|
| `CONFIG_WEARABLLM_PTT_ACTIVE_LEVEL` | `0` | pressed/held reads low |
| `CONFIG_WEARABLLM_PTT_PULL_UP` | `y` | internal pull-up enabled |

This matches the BOOT button and any simple external button wired from the PTT
GPIO to `GND`.

Caution: `GPIO0` is also a bootstrapping pin. It is acceptable for early runtime
testing, but not ideal as the final push-to-talk control.

External button options:

| Button wiring | Active level | Pull mode |
|---|---:|---|
| GPIO to `GND` when pressed | `0` | internal pull-up |
| GPIO to `3V3` when pressed | `1` | internal pull-down |
| circuit has its own resistor | match circuit | no internal pull |

Better later options:

- use one of Waveshare's user buttons through the TCA9555 expander
- add a dedicated external pushbutton on a confirmed-free direct GPIO
- reserve direct GPIOs needed by the TFT before choosing this

## Vendor References

- [Waveshare ESP32-S3-AUDIO-Board wiki](https://www.waveshare.com/wiki/ESP32-S3-AUDIO-Board)
- [Waveshare board demo archive](https://files.waveshare.com/wiki/ESP32-S3-AUDIO-Board/ESP32-S3-AUDIO-Board-Demo.zip)
- [Waveshare board schematic, revision 1.1](https://files.waveshare.com/wiki/ESP32-S3-AUDIO-Board/ESP32-S3-AUDIO-Board_1.1.pdf)

The speaker amplifier mapping is taken from the vendor demo's
`Audio_ES8311.cpp`, where `Audio_PA_EN()` drives TCA9555 `EXIO8` high.

## TFT200C 240x320 SPI Display

The current display is the 2.0-inch `TFT200C 240*320 V1.3` module with the
physical header order `BLK | RES | SDA | CLK | CS | DC | VCC | GND`. The
firmware uses the ST7789 driver: that is the strongest match for this size,
resolution, and eight-pin interface, but the bare `TFT200C` marking does not
uniquely identify the controller. If the self-test remains blank or garbled,
verify the controller printed on the seller listing before changing wiring.

| TFT pin | Waveshare header signal | GPIO |
|---|---|---:|
| `VCC` | `3V3` | power |
| `GND` | `GND` | ground |
| `CLK` / `SCLK` | `IO6` | `GPIO6` |
| `SDA` / `MOSI` | `IO7` | `GPIO7` |
| `CS` | `IO9` | `GPIO9` |
| `DC` | `IO4` | `GPIO4` |
| `RES` / `RST` | `IO3` | `GPIO3` |
| `BLK` / `BL` | `IO5` | `GPIO5` |

Important: the TFT's `SCL`/`SDA` labels are SPI clock/data labels. Do not wire them to the Waveshare header's `SCL`/`SDA` I2C pins.

### Position-for-position harness remap

Compared with the removed module's old
`BLK | CS | DC | RES | SDA | SCL | VCC | GND` order, reusing the harness in
the same physical positions produces this software pin remap:

| New TFT signal | Existing harness GPIO |
|---|---:|
| `BLK` | `GPIO5` |
| `RES` | `GPIO3` |
| `SDA` / `MOSI` | `GPIO7` |
| `CLK` / `SCLK` | `GPIO6` |
| `CS` | `GPIO9` |
| `DC` | `GPIO4` |
| `VCC` | `3V3` |
| `GND` | `GND` |

All six signals are ESP32 outputs and the ESP32-S3 routes SPI through its GPIO
matrix. `VCC` remains 3.3 V and `GND` remains ground; never compensate for a
power-pin mismatch in firmware.

## FPC Display Connector

The onboard 18-pin `DISPLAY` FPC is for Waveshare-compatible display modules. Do not assume the separate SPI TFT breakout can plug directly into it or directly into the 2x10 header.

The perfboard adapter diagram is:

```text
vC_WAVESHARE/tft_perfboard_adapter.svg
```

## Pins To Avoid For New Controls

- `GPIO10` / `GPIO11`: board I2C
- `GPIO12` to `GPIO16`: audio codec I2S path
- `GPIO38`: RGB ring
- `GPIO40` to `GPIO42`: onboard TF card
- `GPIO19` / `GPIO20`: commonly used for native USB on ESP32-S3 designs
- `EX1` / `EX2` / `EX3`: expander pins, not direct fast GPIO
