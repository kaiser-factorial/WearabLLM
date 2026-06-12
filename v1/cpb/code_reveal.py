"""
Wearable LLM — Circuit Playground Bluefruit
Reveal Panel variant (servo clamshell + BLE + NeoPixels + speaker)

When a response arrives:
  1. Servo sweeps panel open
  2. NeoPixels animate + earcon plays
  3. Panel holds open for HOLD_SECONDS
  4. Servo sweeps panel closed, LEDs off

While idle:      panel closed, single dim pixel as heartbeat
While thinking:  panel closed, pixels pulse softly behind panel edges

BLE commands (2 bytes):
    GS — green solid   (yes, confident)
    GP — green pulse   (yes, gentle)
    GC — green chase   (yes, enthusiastic)
    RS — red solid     (no, firm)
    RF — red flicker   (warning / urgent)
    YP — yellow pulse  (uncertain)
    BS — blue solid    (neutral info)

Wiring:
    Servo signal wire → CPB pad A1
    Servo power (red) → CPB 3.3V or VOUT (see note below)
    Servo ground      → CPB GND

    Note: SG90 micro servo works fine on 3.3V for light panel loads.
    If the panel is heavy or servo feels weak, power from VOUT (battery
    voltage ~3.7-4.2V) instead of the 3.3V regulated rail.

Servo angles:
    CLOSED_ANGLE — adjust until panel sits flush closed
    OPEN_ANGLE   — adjust until panel is fully open (typically 80-100°)
"""

import array
import math
import random
import time
import board
import digitalio
import neopixel
import pwmio
import audiopwmio
import audiocore
import adafruit_ble
from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
from adafruit_ble.services.nordic import UARTService

# ── Config ─────────────────────────────────────────────────────────────────────

NUM_PIXELS   = 10
SAMPLE_RATE  = 8000
HOLD_SECONDS = 3.5      # how long panel stays open after response
SWEEP_STEPS  = 25       # smoothness of servo sweep (more = smoother)
SWEEP_TIME   = 0.5      # seconds for a full open or close sweep

CLOSED_ANGLE = 0        # degrees — panel fully closed (tune to your print)
OPEN_ANGLE   = 90       # degrees — panel fully open   (tune to your print)

FRAME_RATE   = 0.04     # animation update interval (~25fps)

# ── Colors & responses ─────────────────────────────────────────────────────────

COLORS = {
    "G": (0, 200, 0),
    "R": (200, 0, 0),
    "Y": (200, 140, 0),
    "B": (0, 80, 200),
}

OFF = (0, 0, 0)

# (color_key, animation, [(freq_hz, duration_s), ...])
RESPONSES = {
    b"GS": ("G", "solid",   [(880, 0.1),  (1320, 0.15)]),
    b"GP": ("G", "pulse",   [(880, 0.25)]),
    b"GC": ("G", "chase",   [(880, 0.08), (1100, 0.08), (1320, 0.12)]),
    b"RS": ("R", "solid",   [(440, 0.15), (220, 0.2)]),
    b"RF": ("R", "flicker", [(300, 0.07), (300, 0.07), (300, 0.1)]),
    b"YP": ("Y", "pulse",   [(600, 0.1),  (550, 0.12)]),
    b"BS": ("B", "solid",   [(660, 0.2)]),
}

# ── Hardware setup ─────────────────────────────────────────────────────────────

pixels = neopixel.NeoPixel(
    board.NEOPIXEL, NUM_PIXELS,
    brightness=0.3,
    auto_write=False
)
pixels.fill(OFF)
pixels.show()

speaker_enable = digitalio.DigitalInOut(board.SPEAKER_ENABLE)
speaker_enable.direction = digitalio.Direction.OUTPUT
speaker_enable.value = True
audio = audiopwmio.PWMAudioOut(board.SPEAKER)

# Servo via raw PWM — no extra library needed
# 50Hz period = 20ms; pulse width 500µs (0°) to 2500µs (180°)
servo_pwm = pwmio.PWMOut(board.A1, frequency=50)


# ── Servo helpers ──────────────────────────────────────────────────────────────

def _angle_to_duty(angle: float) -> int:
    pulse_us = 500 + (angle / 180.0) * 2000
    return int(pulse_us / 20_000.0 * 65535)


def set_servo(angle: float) -> None:
    servo_pwm.duty_cycle = _angle_to_duty(max(0.0, min(180.0, angle)))


def sweep_servo(start: float, end: float,
                steps: int = SWEEP_STEPS,
                duration: float = SWEEP_TIME) -> None:
    """Smoothly move servo from start to end angle."""
    delay = duration / steps
    for i in range(steps + 1):
        t = i / steps
        # Ease in-out for a more organic feel
        t_eased = t * t * (3 - 2 * t)
        set_servo(start + (end - start) * t_eased)
        time.sleep(delay)


def open_panel() -> None:
    sweep_servo(CLOSED_ANGLE, OPEN_ANGLE)


def close_panel() -> None:
    sweep_servo(OPEN_ANGLE, CLOSED_ANGLE)
    # Release servo tension after closing (prevents buzzing/heat)
    servo_pwm.duty_cycle = 0


# ── Sound ──────────────────────────────────────────────────────────────────────

def play_earcon(notes: list) -> None:
    for freq, dur in notes:
        n = max(2, SAMPLE_RATE // freq)
        wave = array.array("H", [
            int((math.sin(2 * math.pi * i / n) + 1) * 32767)
            for i in range(n)
        ])
        audio.play(audiocore.RawSample(wave, sample_rate=SAMPLE_RATE), loop=True)
        time.sleep(dur)
    audio.stop()


# ── LED animations ─────────────────────────────────────────────────────────────

def update_animation(frame: int, color: tuple, anim: str) -> None:
    if anim == "solid":
        pixels.brightness = 0.3
        pixels.fill(color)

    elif anim == "pulse":
        b = 0.05 + 0.3 * (0.5 + 0.5 * math.sin(frame * 0.15))
        pixels.brightness = b
        pixels.fill(color)

    elif anim == "chase":
        pixels.fill(OFF)
        head = frame % NUM_PIXELS
        pixels.brightness = 0.6
        pixels[head] = color
        pixels[(head - 1) % NUM_PIXELS] = tuple(c // 3 for c in color)
        pixels[(head - 2) % NUM_PIXELS] = tuple(c // 9 for c in color)

    elif anim == "flicker":
        pixels.brightness = 0.25 + random.random() * 0.35
        for i in range(NUM_PIXELS):
            pixels[i] = (color if random.random() > 0.35
                         else tuple(c // 5 for c in color))

    pixels.show()


def idle_heartbeat(frame: int) -> None:
    """Single dim pixel, slow fade — visible as light leak around panel edge."""
    b = 0.02 + 0.04 * (0.5 + 0.5 * math.sin(frame * 0.05))
    pixels.brightness = b
    pixels.fill(OFF)
    pixels[0] = (0, 0, 180)   # dim blue
    pixels.show()


# ── BLE setup ──────────────────────────────────────────────────────────────────

ble       = adafruit_ble.BLERadio()
ble.name  = "Wearable LLM"
uart      = UARTService()
advertisement = ProvideServicesAdvertisement(uart)

# ── Main loop ──────────────────────────────────────────────────────────────────

# Start with panel closed
set_servo(CLOSED_ANGLE)
time.sleep(0.5)
servo_pwm.duty_cycle = 0

while True:
    pixels.fill(OFF)
    pixels.show()

    print("Advertising...")
    ble.start_advertising(advertisement)
    while not ble.connected:
        pass
    ble.stop_advertising()
    print("Connected!")

    frame          = 0
    buf            = b""
    last_frame     = time.monotonic()

    while ble.connected:

        # ── Receive BLE bytes ──────────────────────────────────────────────────
        if uart.in_waiting:
            buf += uart.read(uart.in_waiting)

        # ── Process complete 2-byte commands ──────────────────────────────────
        while len(buf) >= 2:
            cmd = buf[:2]
            buf = buf[2:]

            if cmd in RESPONSES:
                color_key, anim, notes = RESPONSES[cmd]
                color = COLORS[color_key]

                # 1. Open panel
                open_panel()

                # 2. Animate LEDs + play earcon
                frame = 0
                display_start = time.monotonic()

                while time.monotonic() - display_start < HOLD_SECONDS:
                    update_animation(frame, color, anim)
                    frame += 1

                    # Play earcon once at the start
                    if frame == 1:
                        play_earcon(notes)

                    time.sleep(FRAME_RATE)

                # 3. Close panel
                pixels.fill(OFF)
                pixels.show()
                close_panel()

                frame = 0   # reset heartbeat frame

        # ── Idle heartbeat (light leak while panel is closed) ─────────────────
        now = time.monotonic()
        if now - last_frame >= FRAME_RATE:
            idle_heartbeat(frame)
            frame      += 1
            last_frame  = now

    print("Disconnected.")
    pixels.fill(OFF)
    pixels.show()
    servo_pwm.duty_cycle = 0
