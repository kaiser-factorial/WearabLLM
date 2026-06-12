# Setup Guide

## Prerequisites

| Tool | Check | Install |
|---|---|---|
| Python 3.10+ | `python3 --version` | python.org |
| Homebrew | `brew --version` | brew.sh |
| ffmpeg | `ffmpeg -version` | `brew install ffmpeg` |
| Anthropic API key | console.anthropic.com | — |

---

## One-Time Installation

```bash
# 1. Install ffmpeg (required by Whisper for audio processing)
brew install ffmpeg

# 2. Install Python dependencies
pip3 install -r requirements.txt
```

This installs:
- `anthropic` — Claude API client
- `openai-whisper` — local speech-to-text (~140 MB model downloads on first run)
- `sounddevice` — laptop mic recording
- `soundfile` — WAV file I/O
- `pyserial` — USB serial communication with CPB
- `numpy` — audio array handling

---

## Running the Bridge

```bash
# Set your API key (do this in terminal — never paste the key into a chat)
export ANTHROPIC_API_KEY="sk-ant-..."

# Run
python3 "/Users/corinakaiser/Desktop/Claude Wearable/bridge.py"
```

The Whisper `base` model (~140 MB) downloads automatically on the first run.

To make the API key permanent (so you don't set it every session):
```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
source ~/.zshrc
```

---

## Using the Bridge

```
Press Enter to speak  |  or type a question  |  'quit' to exit
>
```

| Action | How |
|---|---|
| Ask a voice question | Press Enter, speak, press Enter again |
| Ask a typed question | Just type and press Enter |
| Add sensor context | Type `temp:72 light:400 \|` then press Enter to speak |
| List serial ports | Type `ports` |
| Exit | Type `quit` |

---

## CPB Setup (CircuitPython)

### Step 1 — Flash CircuitPython
1. Go to **circuitpython.org/board/circuitplayground_bluefruit**
2. Download the latest `.uf2` file
3. Double-click the reset button on the CPB — NeoPixels turn green, drive `CPLAYBOOT` appears
4. Drag the `.uf2` file onto `CPLAYBOOT`
5. Board reboots — drive is now called `CIRCUITPY`

### Step 2 — Install the neopixel library
1. Go to **github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/latest**
2. Download `adafruit-circuitpython-bundle-10.x-mpy-YYYYMMDD.zip`
3. Unzip it
4. Copy `lib/neopixel.mpy` into `/Volumes/CIRCUITPY/lib/`

### Step 3 — Upload the LED listener code
The bridge writes `boot.py` and `code.py` directly. They are already on the board.
If you need to re-upload manually, copy them from this project onto `CIRCUITPY`.

### Step 4 — Verify
- Plug CPB in via USB
- Run `bridge.py` — it should print `Found Circuit Playground Bluefruit at /dev/cu.usbmodem...`
- Speak a question — the NeoPixels should light up the correct color

---

## Whisper Model Sizes

The model is configured in `bridge.py` via `WHISPER_MODEL = "base"`.

| Model | Size | Speed | Accuracy |
|---|---|---|---|
| tiny | ~75 MB | fastest | lower |
| base | ~140 MB | fast | good (default) |
| small | ~460 MB | medium | better |
| medium | ~1.5 GB | slow | very good |

---

## Troubleshooting

**CPB not detected:**
- Try a different USB cable (some cables are charge-only)
- Run `ports` in the bridge to see what's connected
- Make sure CircuitPython is installed on the CPB

**Whisper not transcribing well:**
- Speak clearly and close to the mic
- Try switching to `WHISPER_MODEL = "small"` in bridge.py

**API key errors:**
- Make sure `ANTHROPIC_API_KEY` is exported in the same terminal session
- Never paste your API key into a chat or commit it to git
