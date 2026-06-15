#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define WEARABLLM_AUDIO_SAMPLE_RATE 16000

typedef bool (*wearabllm_audio_keep_recording_fn)(void *ctx);

esp_err_t wearabllm_audio_init(void);

esp_err_t wearabllm_audio_capture_wav(
    wearabllm_audio_keep_recording_fn keep_recording,
    void *ctx,
    uint32_t max_seconds,
    uint32_t min_capture_ms,
    uint8_t **out_data,
    size_t *out_len);

esp_err_t wearabllm_audio_make_silent_wav(uint32_t milliseconds, uint8_t **out_data, size_t *out_len);

esp_err_t wearabllm_audio_play_tone(uint32_t frequency_hz, uint32_t milliseconds);

esp_err_t wearabllm_audio_play_wav(const uint8_t *wav_data, size_t wav_len);

#ifdef __cplusplus
}
#endif
