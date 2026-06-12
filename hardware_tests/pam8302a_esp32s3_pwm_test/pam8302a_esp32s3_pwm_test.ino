/*
  PAM8302A ESP32-S3 PWM Tone Test

  Purpose:
    Verifies that an Adafruit PAM8302A analog Class-D amp and speaker work.
    This is not high-quality audio playback; it is a square-wave tone test.

  Wiring:
    ESP32-S3 GND  -> PAM8302A GND
    ESP32-S3 3V3 or 5V -> PAM8302A VIN
    ESP32-S3 AUDIO_PIN -> 1k resistor -> PAM8302A A+
    PAM8302A A- -> GND
    Speaker + / - -> PAM8302A speaker output terminals

  Important:
    - Do not connect either speaker output terminal to GND.
    - Start with the amp trim pot turned low.
    - If it gets hot or buzzes harshly, unplug and recheck wiring.
*/

#include <Arduino.h>

#if __has_include(<esp_arduino_version.h>)
#include <esp_arduino_version.h>
#endif

// Change this to any safe, exposed GPIO on your ESP32-S3.
// Avoid pins already used by USB, boot strapping, the TFT, SD, NeoPixels, or I2S.
static const int AUDIO_PIN = 17;

static const int PWM_CHANNEL = 0;
static const int PWM_RESOLUTION_BITS = 8;
static const int DUTY_50_PERCENT = 128;
static uint32_t loopCount = 0;

static void printDivider() {
  Serial.println("----------------------------------------");
}

static void printWiringGuide() {
  printDivider();
  Serial.println("Wiring checklist:");
  Serial.println("  ESP32-S3 GND        -> PAM8302A GND");
  Serial.println("  ESP32-S3 3V3 or 5V  -> PAM8302A VIN");
  Serial.println("  ESP32-S3 GPIO17     -> 1k resistor -> PAM8302A A+");
  Serial.println("  PAM8302A A-         -> GND");
  Serial.println("  Speaker             -> PAM8302A output terminals only");
  Serial.println();
  Serial.println("Safety:");
  Serial.println("  Do NOT connect either speaker terminal to GND.");
  Serial.println("  Start the amp trim pot low, then raise slowly.");
  Serial.println("  Unplug if the chip gets hot or power drops out.");
  printDivider();
}

static void printTroubleshootingHint() {
  Serial.println("If you hear nothing:");
  Serial.println("  1. Confirm Serial Monitor shows tones advancing.");
  Serial.println("  2. Confirm AUDIO_PIN matches your actual wired GPIO.");
  Serial.println("  3. Confirm PAM8302A VIN/GND power.");
  Serial.println("  4. Confirm A- is tied to GND.");
  Serial.println("  5. Turn the tiny trim pot slowly.");
  Serial.println("  6. Try another 4-8 ohm speaker if available.");
  Serial.println("  7. Move AUDIO_PIN to another free GPIO and re-upload.");
}

static void toneOn(uint32_t frequencyHz) {
  Serial.print("  PWM on: frequency=");
  Serial.print(frequencyHz);
  Serial.print(" Hz, resolution=");
  Serial.print(PWM_RESOLUTION_BITS);
  Serial.print(" bits, duty=");
  Serial.print(DUTY_50_PERCENT);
  Serial.println("/255");

#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(AUDIO_PIN, frequencyHz, PWM_RESOLUTION_BITS);
  ledcWrite(AUDIO_PIN, DUTY_50_PERCENT);
#else
  ledcSetup(PWM_CHANNEL, frequencyHz, PWM_RESOLUTION_BITS);
  ledcAttachPin(AUDIO_PIN, PWM_CHANNEL);
  ledcWrite(PWM_CHANNEL, DUTY_50_PERCENT);
#endif
}

static void toneOff() {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(AUDIO_PIN, 0);
  ledcDetach(AUDIO_PIN);
#else
  ledcWrite(PWM_CHANNEL, 0);
  ledcDetachPin(AUDIO_PIN);
#endif
  pinMode(AUDIO_PIN, OUTPUT);
  digitalWrite(AUDIO_PIN, LOW);
  Serial.println("  PWM off, pin driven LOW");
}

static void playTone(uint32_t frequencyHz, uint32_t durationMs) {
  Serial.print("Playing tone: ");
  Serial.print(frequencyHz);
  Serial.print(" Hz for ");
  Serial.print(durationMs);
  Serial.println(" ms");
  toneOn(frequencyHz);
  delay(durationMs);
  toneOff();
  delay(120);
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("PAM8302A ESP32-S3 PWM tone test");
  printDivider();
  Serial.print("Build date: ");
  Serial.print(__DATE__);
  Serial.print(" ");
  Serial.println(__TIME__);
  Serial.print("AUDIO_PIN = GPIO");
  Serial.println(AUDIO_PIN);
  Serial.print("PWM channel = ");
  Serial.println(PWM_CHANNEL);
  Serial.print("PWM resolution bits = ");
  Serial.println(PWM_RESOLUTION_BITS);
#if defined(ESP_ARDUINO_VERSION_MAJOR)
  Serial.print("Arduino ESP32 core = ");
  Serial.print(ESP_ARDUINO_VERSION_MAJOR);
  Serial.print(".");
  Serial.print(ESP_ARDUINO_VERSION_MINOR);
  Serial.print(".");
  Serial.println(ESP_ARDUINO_VERSION_PATCH);
#else
  Serial.println("Arduino ESP32 core version macro not available.");
#endif
  Serial.println("Start amp volume low, then raise slowly.");
  printWiringGuide();

  pinMode(AUDIO_PIN, OUTPUT);
  digitalWrite(AUDIO_PIN, LOW);
  Serial.println("AUDIO_PIN initialized LOW.");
}

void loop() {
  loopCount++;
  printDivider();
  Serial.print("Starting test loop #");
  Serial.println(loopCount);
  Serial.println("Expected result: three chirps, pause, then an ascending note sweep.");

  // Slow startup chirp.
  playTone(440, 250);
  playTone(660, 250);
  playTone(880, 350);

  delay(700);

  // Sweep through several easy-to-hear frequencies.
  const uint16_t notes[] = {262, 330, 392, 523, 659, 784, 1047};
  for (size_t i = 0; i < sizeof(notes) / sizeof(notes[0]); i++) {
    playTone(notes[i], 180);
  }

  delay(1800);

  if (loopCount % 3 == 0) {
    printDivider();
    printTroubleshootingHint();
  }
}
