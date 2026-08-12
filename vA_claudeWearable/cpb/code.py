"""
Wearable LLM — Circuit Playground Bluefruit (Phase 2: BLE + Animations)
Receives a 2-byte command over BLE UART and responds with a NeoPixel
animation + speaker earcon.

Button A = toggle voice: first press sends "VS" (start listening),
second press sends "VP" (stop & send to Claude).

Slide switch = sensor mode: when ON (toward board center), sensor data
is sent along with VS so Claude has environmental context.

Commands (phone → CPB):
    GS — green solid   (yes, confident)
    GP — green pulse   (yes, gentle)
    GC — green chase   (yes, enthusiastic)
    RS — red solid     (no, firm)
    RF — red flicker   (warning / urgent)
    YP — yellow pulse  (uncertain)
    BS — blue solid    (neutral info)

Signals (CPB → phone):
    VS        — voice start (no sensor data)
    VS:t,l,x,y,z — voice start + sensor snapshot
    VP        — voice stop
"""

import array
import math
import random
import time
import board
import digitalio
import neopixel
import audiopwmio
import audiocore
import analogio
import adafruit_ble
import adafruit_lis3dh
import adafruit_thermistor
from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
from adafruit_ble.services.nordic import UARTService

# ── Config ─────────────────────────────────────────────────────────────────────

NUM_PIXELS  = 10
SAMPLE_RATE = 8000
FRAME_RATE  = 0.04   # seconds per animation frame (~25fps)

COLORS = {
    "G": (0, 200, 0),
    "R": (200, 0, 0),
    "Y": (200, 140, 0),
    "B": (0, 80, 200),
    "P": (140, 0, 200),
}

OFF = (0, 0, 0)

# Each response: (color_key, animation, [(freq_hz, duration_s), ...])
RESPONSES = {
    b"GS": ("G", "solid",   [(880, 0.1), (1320, 0.15)]),
    b"GP": ("G", "pulse",   [(880, 0.25)]),
    b"GC": ("G", "chase",   [(880, 0.08), (1100, 0.08), (1320, 0.12)]),
    b"RS": ("R", "solid",   [(440, 0.15), (220, 0.2)]),
    b"RF": ("R", "flicker", [(300, 0.07), (300, 0.07), (300, 0.1)]),
    b"YP": ("Y", "pulse",   [(600, 0.1), (550, 0.12)]),
    b"BS": ("B", "solid",   [(660, 0.2)]),
    b"PS": ("P", "solid",   [(520, 0.12), (780, 0.15)]),
    b"PP": ("P", "pulse",   [(520, 0.2), (620, 0.15)]),
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

# Push-to-talk button (Button A on CPB)
button_a = digitalio.DigitalInOut(board.BUTTON_A)
button_a.direction = digitalio.Direction.INPUT
button_a.pull = digitalio.Pull.DOWN
button_a_prev = False  # track previous state for edge detection

# Slide switch — ON = sensor mode enabled
slide_switch = digitalio.DigitalInOut(board.SLIDE_SWITCH)
slide_switch.direction = digitalio.Direction.INPUT
slide_switch.pull = digitalio.Pull.UP

# Sensors
thermistor = adafruit_thermistor.Thermistor(
    board.TEMPERATURE, 10000, 10000, 25, 3950
)
light_sensor = analogio.AnalogIn(board.LIGHT)

import busio
i2c = busio.I2C(board.ACCELEROMETER_SCL, board.ACCELEROMETER_SDA)
accelerometer = adafruit_lis3dh.LIS3DH_I2C(i2c, address=0x19)
accelerometer.range = adafruit_lis3dh.RANGE_2_G

TEMP_OFFSET = -5.0  # CPB thermistor reads high due to processor heat

def read_sensors():
    """Read all sensors and return as a compact string: temp,light,x,y,z"""
    temp_c = thermistor.temperature + TEMP_OFFSET
    # Light sensor: raw 0-65535 but indoor range is typically 0-5000
    light_raw = light_sensor.value
    light_pct = min(100, (light_raw * 100) // 5000)
    x, y, z = accelerometer.acceleration
    return "{:.1f},{},{:.1f},{:.1f},{:.1f}".format(temp_c, light_pct, x, y, z)

# ── Sound ──────────────────────────────────────────────────────────────────────

def play_earcon(notes):
    for freq, dur in notes:
        n = max(2, SAMPLE_RATE // freq)
        wave = array.array("H", [
            int((math.sin(2 * math.pi * i / n) + 1) * 32767)
            for i in range(n)
        ])
        audio.play(audiocore.RawSample(wave, sample_rate=SAMPLE_RATE), loop=True)
        time.sleep(dur)
    audio.stop()

# ── Animation ──────────────────────────────────────────────────────────────────

def update_animation(frame, color, anim):
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
            pixels[i] = color if random.random() > 0.35 else tuple(c // 5 for c in color)

    pixels.show()

# ── BLE setup ──────────────────────────────────────────────────────────────────

ble = adafruit_ble.BLERadio()
ble.name = "Wearable LLM"
uart = UARTService()
advertisement = ProvideServicesAdvertisement(uart)

# ── Main loop ──────────────────────────────────────────────────────────────────

while True:
    # Dim blue pixel while advertising
    pixels.fill(OFF)
    pixels[0] = (0, 0, 60)
    pixels.show()

    print("Advertising...")
    ble.start_advertising(advertisement)
    while not ble.connected:
        pass
    ble.stop_advertising()
    print("Connected!")

    # Solid blue = connected, idle
    pixels.fill(OFF)
    pixels[0] = (0, 0, 180)
    pixels.show()

    current_color = COLORS["B"]
    current_anim  = "solid"
    frame = 0
    buf   = b""
    last_frame_time = time.monotonic()
    button_a_prev = False
    listening = False  # toggle state for voice
    # Send initial slide switch state immediately on connect
    last_switch_state = slide_switch.value
    if last_switch_state:
        uart.write(b"S1\n")
        print("Initial slide switch → S1")
    else:
        uart.write(b"S0\n")
        print("Initial slide switch → S0")
    last_switch_check = time.monotonic()

    while ble.connected:
        # ── Slide switch: notify phone of sensor mode changes ─────────
        now_mono = time.monotonic()
        if now_mono - last_switch_check >= 0.5:  # check every 500ms
            last_switch_check = now_mono
            sw = slide_switch.value
            if sw != last_switch_state:
                last_switch_state = sw
                if sw:
                    uart.write(b"S1\n")  # sensors on
                else:
                    uart.write(b"S0\n")  # sensors off
                print("Slide switch →", "S1" if sw else "S0")

        # ── Button A: toggle voice ────────────────────────────────────
        button_now = button_a.value
        if button_now and not button_a_prev:
            # Button just pressed → toggle listening
            listening = not listening
            if listening:
                if slide_switch.value:
                    # Sensor mode ON — attach sensor data
                    sensor_str = read_sensors()
                    msg = "VS:" + sensor_str + "\n"
                    uart.write(msg.encode())
                    print("Button A → " + msg.strip())
                else:
                    uart.write(b"VS\n")
                    print("Button A → VS (start)")
                pixels.fill((80, 80, 80))
                pixels.show()
            else:
                uart.write(b"VP\n")
                pixels.fill(OFF)
                pixels[0] = (0, 0, 180)
                pixels.show()
                print("Button A → VP (stop)")
        button_a_prev = button_now

        # White breathing effect while listening
        if listening:
            b = 0.1 + 0.3 * (0.5 + 0.5 * math.sin(frame * 0.2))
            pixels.brightness = b
            pixels.fill((100, 100, 120))
            pixels.show()

        # ── Read incoming bytes from phone ────────────────────────────
        if uart.in_waiting:
            buf += uart.read(uart.in_waiting)

        # Process complete 2-byte commands
        while len(buf) >= 2:
            cmd = buf[:2]
            buf = buf[2:]
            if cmd == b"SR":
                # Sensor request from phone — reply with sensor data
                if slide_switch.value:
                    sensor_str = read_sensors()
                    uart.write(("SD:" + sensor_str + "\n").encode())
                    print("Sensor request → SD:" + sensor_str)
            elif cmd in RESPONSES:
                color_key, anim, notes = RESPONSES[cmd]
                current_color = COLORS[color_key]
                current_anim  = anim
                frame = 0
                listening = False
                play_earcon(notes)

        # Advance animation frame
        now = time.monotonic()
        if now - last_frame_time >= FRAME_RATE:
            if not listening:
                update_animation(frame, current_color, current_anim)
            frame += 1
            last_frame_time = now

    print("Disconnected.")
    pixels.fill(OFF)
    pixels.show()
