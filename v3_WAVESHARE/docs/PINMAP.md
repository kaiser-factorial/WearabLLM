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
| Volume up (K1) | TCA9555 `EXIO9` | active low; +10 volume, short earcon, NVS save |
| Mute toggle (K2) | TCA9555 `EXIO10` | active low; mute/unmute, amber ring while muted |
| Volume down (K3) | TCA9555 `EXIO11` | active low; -10 volume, short earcon, NVS save |
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

## SPI TFT Adapter

For the separate SPI TFT breakout on the perfboard adapter, use the mapping already discussed:

| TFT pin | Waveshare header signal | GPIO |
|---|---|---:|
| `VCC` | `3V3` | power |
| `GND` | `GND` | ground |
| `SCL` / `SCK` | `IO4` | `GPIO4` |
| `SDA` / `MOSI` | `IO9` | `GPIO9` |
| `CS` | `IO3` | `GPIO3` |
| `DC` | `IO7` | `GPIO7` |
| `RES` / `RST` | `IO6` | `GPIO6` |
| `BLK` / `BL` | `3V3` first, optionally `IO5` later | `GPIO5` if PWM |

Important: the TFT's `SCL`/`SDA` labels are SPI clock/data labels. Do not wire them to the Waveshare header's `SCL`/`SDA` I2C pins.

## FPC Display Connector

The onboard 18-pin `DISPLAY` FPC is for Waveshare-compatible display modules. Do not assume the separate SPI TFT breakout can plug directly into it or directly into the 2x10 header.

The perfboard adapter diagram is:

```text
v3_WAVESHARE/tft_perfboard_adapter.svg
```

## Pins To Avoid For New Controls

- `GPIO10` / `GPIO11`: board I2C
- `GPIO12` to `GPIO16`: audio codec I2S path
- `GPIO38`: RGB ring
- `GPIO40` to `GPIO42`: onboard TF card
- `GPIO19` / `GPIO20`: commonly used for native USB on ESP32-S3 designs
- `EX1` / `EX2` / `EX3`: expander pins, not direct fast GPIO
