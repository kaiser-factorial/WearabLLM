"""
Wearable Bridge — Phase 2 (BLE + Laptop Mic)
Records your voice, transcribes with Whisper, sends to Claude,
and sends an LED command to the Circuit Playground Bluefruit over BLE.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python bridge.py

Interaction:
    Press Enter          → start recording, speak, press Enter again to stop
    Type a question      → skip mic, send text directly (useful for testing)
    sensor:val | [Enter] → attach sensor data, then record voice question
    'quit'               → exit

LED commands sent to CPB over BLE:
    G = green  (yes / positive / safe)
    R = red    (no / negative / unsafe)
    Y = yellow (uncertain / maybe)
    B = blue   (neutral / informational)

Requirements:
    pip install anthropic bleak openai-whisper sounddevice soundfile numpy
    brew install ffmpeg          # macOS — required by Whisper
    export ANTHROPIC_API_KEY="your-key-here"
"""

import asyncio
import os
import re
import sys
import tempfile

import anthropic
import numpy as np
import sounddevice as sd
import soundfile as sf
import whisper
from bleak import BleakClient, BleakScanner

# ── Config ────────────────────────────────────────────────────────────────────

DEVICE_NAME   = "Wearable LLM"
UART_RX_UUID  = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # laptop writes here

CLAUDE_MODEL  = "claude-opus-4-6"
WHISPER_MODEL = "base"
SAMPLE_RATE   = 16_000
CHANNELS      = 1

SYSTEM_PROMPT = """\
You are an AI assistant embedded in a wearable device. You communicate back to \
the user through LED animations and sounds on the device. Always end your reply \
with exactly one LED command on its own line, chosen from the options below.

  LED:green-solid    — yes, confident answer
  LED:green-pulse    — yes, gentle or soft agreement
  LED:green-chase    — yes, enthusiastic or exciting news
  LED:red-solid      — no, firm answer
  LED:red-flicker    — warning, urgent, danger, stop immediately
  LED:yellow-pulse   — uncertain, depends, maybe, need more info
  LED:blue-solid     — neutral information, not a yes/no question

Pick the option that best fits both the content AND the tone of your answer. \
Reply conversationally first (1–3 sentences), then on a new line write the LED \
command. Example:

  That temperature is perfect for a long run — go for it!
  LED:green-chase

Never omit the LED command. Never include more than one LED command.\
"""

LED_COMMANDS = {
    "green-solid":   b"GS",
    "green-pulse":   b"GP",
    "green-chase":   b"GC",
    "red-solid":     b"RS",
    "red-flicker":   b"RF",
    "yellow-pulse":  b"YP",
    "blue-solid":    b"BS",
}

# ── Audio / transcription ─────────────────────────────────────────────────────

def record_until_enter() -> np.ndarray | None:
    frames: list[np.ndarray] = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                        dtype="float32", callback=callback):
        input()

    if not frames:
        return None
    return np.concatenate(frames, axis=0)


def transcribe(audio: np.ndarray, model: whisper.Whisper) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name
    try:
        sf.write(tmp_path, audio, SAMPLE_RATE)
        result = model.transcribe(tmp_path, language="en", fp16=False)
        return result["text"].strip()
    finally:
        os.unlink(tmp_path)


# ── Claude helpers ────────────────────────────────────────────────────────────

def build_prompt(question: str, sensor_data: str | None) -> str:
    if sensor_data:
        return f"[Sensor data: {sensor_data}]\n\n{question}"
    return question


def parse_led_command(text: str) -> str:
    match = re.search(
        r"LED:(green-solid|green-pulse|green-chase|red-solid|red-flicker|yellow-pulse|blue-solid)",
        text, re.IGNORECASE
    )
    return match.group(1).lower() if match else "blue-solid"


def strip_led_line(text: str) -> str:
    return re.sub(
        r"\s*LED:(green-solid|green-pulse|green-chase|red-solid|red-flicker|yellow-pulse|blue-solid)\s*$",
        "", text, flags=re.IGNORECASE
    ).strip()


def ask_claude(client: anthropic.Anthropic, prompt: str) -> tuple[str, str]:
    full_text = ""
    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for delta in stream.text_stream:
            full_text += delta

    return strip_led_line(full_text), parse_led_command(full_text)


# ── Input handling ────────────────────────────────────────────────────────────

def get_question(whisper_model: whisper.Whisper) -> tuple[str, str | None] | None:
    print("\nPress Enter to speak  |  or type a question  |  'quit' to exit")
    try:
        raw = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if raw.lower() in ("quit", "exit", "q"):
        return None

    sensor_data = None
    typed_question = raw
    if "|" in raw:
        parts = raw.split("|", 1)
        sensor_data = parts[0].strip() or None
        typed_question = parts[1].strip()

    if typed_question:
        return typed_question, sensor_data

    if sensor_data:
        print(f"  Sensor data attached: {sensor_data}")

    print("  Recording... speak now, then press Enter to stop.")
    audio = record_until_enter()

    if audio is None or len(audio) < SAMPLE_RATE * 0.3:
        print("  (no audio captured — try again)")
        return get_question(whisper_model)

    print("  Transcribing...", end="", flush=True)
    question = transcribe(audio, whisper_model)
    print(f"\r  You said: \"{question}\"")

    if not question:
        print("  (couldn't understand audio — try again)")
        return get_question(whisper_model)

    return question, sensor_data


# ── BLE helpers ───────────────────────────────────────────────────────────────

async def find_cpb() -> object | None:
    print(f"Scanning for '{DEVICE_NAME}' over BLE...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=15.0)
    if device:
        print(f"Found: {device.name} [{device.address}]")
    else:
        print(f"'{DEVICE_NAME}' not found — running in dry-run mode.")
    return device


async def send_ble(client: BleakClient, cmd: bytes) -> None:
    await client.write_gatt_char(UART_RX_UUID, cmd)


# ── Main loop ─────────────────────────────────────────────────────────────────

async def run(claude: anthropic.Anthropic, whisper_model: whisper.Whisper,
              ble_client: BleakClient | None) -> None:

    mode = f"BLE ({DEVICE_NAME})" if ble_client else "DRY RUN (no BLE)"
    print(f"\n── Wearable Bridge ready [{mode}] ──")
    print("Sensor data tip:  temp:89 humidity:95 | [then speak or type]\n")

    while True:
        result = await asyncio.to_thread(get_question, whisper_model)
        if result is None:
            print("Exiting.")
            break

        question, sensor_data = result
        prompt = build_prompt(question, sensor_data)

        print("  Asking Claude...", end="", flush=True)
        try:
            reply, color = await asyncio.to_thread(ask_claude, claude, prompt)
        except anthropic.APIError as exc:
            print(f"\r  API error: {exc}")
            continue

        print(f"\r  Claude: {reply}")
        print(f"  LED   : {color}")

        cmd = LED_COMMANDS.get(color, b"BS")

        if ble_client and ble_client.is_connected:
            try:
                await send_ble(ble_client, cmd)
                print(f"  Sent  : {cmd.decode()} → CPB (BLE)")
            except Exception as exc:
                print(f"  BLE write failed: {exc}")
        else:
            print(f"  [dry-run] would send: {cmd.decode()}")


async def main() -> None:
    claude = anthropic.Anthropic()

    print(f"Loading Whisper '{WHISPER_MODEL}' model...", end="", flush=True)
    whisper_model = await asyncio.to_thread(whisper.load_model, WHISPER_MODEL)
    print(" ready.")

    device = await find_cpb()

    if device:
        async with BleakClient(device) as ble_client:
            await run(claude, whisper_model, ble_client)
    else:
        await run(claude, whisper_model, ble_client=None)


if __name__ == "__main__":
    asyncio.run(main())
