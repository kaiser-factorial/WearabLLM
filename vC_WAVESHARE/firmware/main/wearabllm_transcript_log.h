#pragma once

#include <stdint.h>

#include "esp_err.h"

esp_err_t wearabllm_transcript_log_init(void);

void wearabllm_transcript_log_enqueue(
    uint32_t interaction_id,
    const char *command,
    const char *transcript,
    const char *reply,
    const char *capture_source);
