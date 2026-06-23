#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define WEARABLLM_AUDIO_SAMPLE_RATE 16000
#define WEARABLLM_AUDIO_BUTTON_VOLUME_UP (1U << 0)
#define WEARABLLM_AUDIO_BUTTON_VOLUME_DOWN (1U << 1)

typedef bool (*wearabllm_audio_keep_recording_fn)(void *ctx);

esp_err_t wearabllm_audio_init(void);

esp_err_t wearabllm_audio_capture_wav(
    wearabllm_audio_keep_recording_fn keep_recording,
    void *ctx,
    uint32_t max_seconds,
    uint32_t min_capture_ms,
    uint8_t **out_data,
    size_t *out_len);

esp_err_t wearabllm_audio_read_mono(int16_t *samples, size_t frame_count);

esp_err_t wearabllm_audio_capture_wav_until_silence(
    uint32_t max_seconds,
    uint32_t silence_ms,
    uint8_t **out_data,
    size_t *out_len);

esp_err_t wearabllm_audio_make_silent_wav(uint32_t milliseconds, uint8_t **out_data, size_t *out_len);

esp_err_t wearabllm_audio_play_tone(uint32_t frequency_hz, uint32_t milliseconds);

esp_err_t wearabllm_audio_play_wav(const uint8_t *wav_data, size_t wav_len);

esp_err_t wearabllm_audio_set_output_volume(uint8_t volume);

uint8_t wearabllm_audio_get_output_volume(void);

esp_err_t wearabllm_audio_read_volume_buttons(uint8_t *pressed_mask);

#ifdef __cplusplus
}
#endif
