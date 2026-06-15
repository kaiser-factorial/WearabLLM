#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    WEARABLLM_DISPLAY_IDLE = 0,
    WEARABLLM_DISPLAY_LISTENING,
    WEARABLLM_DISPLAY_THINKING,
    WEARABLLM_DISPLAY_RESPONSE,
    WEARABLLM_DISPLAY_ERROR,
} wearabllm_display_state_t;

esp_err_t wearabllm_display_init(void);
void wearabllm_display_show_state(wearabllm_display_state_t state);
void wearabllm_display_show_response(const char *command, const char *transcript, const char *reply);
void wearabllm_display_show_error(const char *message);

#ifdef __cplusplus
}
#endif
