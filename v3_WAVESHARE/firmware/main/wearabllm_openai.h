#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t wearabllm_openai_query(
    const uint8_t *wav,
    size_t wav_len,
    char *command_out,
    size_t command_len,
    char *transcript_out,
    size_t transcript_len,
    char *reply_out,
    size_t reply_len);

esp_err_t wearabllm_openai_tts(const char *reply, uint8_t **out_data, size_t *out_len);
