#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#include "cJSON.h"
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "nvs_flash.h"
#include "wearabllm_audio.h"
#include "wearabllm_display.h"

static const char *TAG = "wearabllm";

#define WIFI_CONNECTED_BIT BIT0
#define HTTP_RESPONSE_MAX 1024
#define TTS_JSON_MAX 384

#ifndef CONFIG_WEARABLLM_LED_SELF_TEST_ON_BOOT
#define CONFIG_WEARABLLM_LED_SELF_TEST_ON_BOOT 0
#endif

#ifndef CONFIG_WEARABLLM_AUDIO_MAX_SECONDS
#define CONFIG_WEARABLLM_AUDIO_MAX_SECONDS 6
#endif

#ifndef CONFIG_WEARABLLM_AUDIO_MIN_CAPTURE_MS
#define CONFIG_WEARABLLM_AUDIO_MIN_CAPTURE_MS 250
#endif

#ifndef CONFIG_WEARABLLM_PTT_DEBOUNCE_MS
#define CONFIG_WEARABLLM_PTT_DEBOUNCE_MS 35
#endif

typedef struct {
    uint8_t r;
    uint8_t g;
    uint8_t b;
} rgb_t;

typedef enum {
    LED_ANIM_SOLID,
    LED_ANIM_PULSE,
    LED_ANIM_CHASE,
    LED_ANIM_FLICKER,
} led_animation_t;

typedef struct {
    rgb_t color;
    led_animation_t animation;
} response_visual_t;

typedef struct {
    char *data;
    int len;
    int capacity;
    bool overflow;
} http_response_t;

#if CONFIG_WEARABLLM_TTS_ENABLED
typedef struct {
    uint8_t *data;
    int len;
    int capacity;
    bool overflow;
} http_binary_response_t;
#endif

static EventGroupHandle_t s_wifi_events;
static led_strip_handle_t s_led_strip;

static const rgb_t COLOR_IDLE = {0, 0, 36};
static const rgb_t COLOR_LISTENING = {70, 70, 70};
static const rgb_t COLOR_THINKING = {70, 45, 0};
static const rgb_t COLOR_ERROR = {90, 0, 0};
static const rgb_t COLOR_GREEN = {0, 80, 0};
static const rgb_t COLOR_RED = {90, 0, 0};
static const rgb_t COLOR_YELLOW = {90, 55, 0};
static const rgb_t COLOR_BLUE = {0, 25, 90};
static const rgb_t COLOR_PURPLE = {55, 0, 80};

static rgb_t led_scale(rgb_t color, uint8_t numerator, uint8_t denominator)
{
    if (denominator == 0) {
        return color;
    }
    return (rgb_t){
        .r = (uint8_t)(((uint16_t)color.r * numerator) / denominator),
        .g = (uint8_t)(((uint16_t)color.g * numerator) / denominator),
        .b = (uint8_t)(((uint16_t)color.b * numerator) / denominator),
    };
}

static void led_set_all(rgb_t color)
{
    if (!s_led_strip) {
        return;
    }

    for (int i = 0; i < CONFIG_WEARABLLM_LED_COUNT; i++) {
        ESP_ERROR_CHECK(led_strip_set_pixel(s_led_strip, i, color.r, color.g, color.b));
    }
    ESP_ERROR_CHECK(led_strip_refresh(s_led_strip));
}

static void led_clear_pixels(void)
{
    if (!s_led_strip) {
        return;
    }
    ESP_ERROR_CHECK(led_strip_clear(s_led_strip));
}

static void led_set_pixel(int index, rgb_t color)
{
    if (!s_led_strip || index < 0 || index >= CONFIG_WEARABLLM_LED_COUNT) {
        return;
    }
    ESP_ERROR_CHECK(led_strip_set_pixel(s_led_strip, index, color.r, color.g, color.b));
}

static void led_refresh(void)
{
    if (!s_led_strip) {
        return;
    }
    ESP_ERROR_CHECK(led_strip_refresh(s_led_strip));
}

static response_visual_t visual_for_command(const char *command)
{
    if (strcmp(command, "GS") == 0) {
        return (response_visual_t){COLOR_GREEN, LED_ANIM_SOLID};
    }
    if (strcmp(command, "GP") == 0) {
        return (response_visual_t){COLOR_GREEN, LED_ANIM_PULSE};
    }
    if (strcmp(command, "GC") == 0) {
        return (response_visual_t){COLOR_GREEN, LED_ANIM_CHASE};
    }
    if (strcmp(command, "RS") == 0) {
        return (response_visual_t){COLOR_RED, LED_ANIM_SOLID};
    }
    if (strcmp(command, "RF") == 0) {
        return (response_visual_t){COLOR_RED, LED_ANIM_FLICKER};
    }
    if (strcmp(command, "YP") == 0) {
        return (response_visual_t){COLOR_YELLOW, LED_ANIM_PULSE};
    }
    if (strcmp(command, "PS") == 0) {
        return (response_visual_t){COLOR_PURPLE, LED_ANIM_SOLID};
    }
    if (strcmp(command, "PP") == 0) {
        return (response_visual_t){COLOR_PURPLE, LED_ANIM_PULSE};
    }
    return (response_visual_t){COLOR_BLUE, LED_ANIM_SOLID};
}

static bool is_valid_led_command(const char *command)
{
    if (!command || strlen(command) != 2) {
        return false;
    }
    return strcmp(command, "GS") == 0 ||
           strcmp(command, "GP") == 0 ||
           strcmp(command, "GC") == 0 ||
           strcmp(command, "RS") == 0 ||
           strcmp(command, "RF") == 0 ||
           strcmp(command, "YP") == 0 ||
           strcmp(command, "BS") == 0 ||
           strcmp(command, "PS") == 0 ||
           strcmp(command, "PP") == 0;
}

static void led_run_pulse(rgb_t color)
{
    const uint8_t levels[] = {2, 4, 7, 10, 7, 4, 2, 1};
    for (int cycle = 0; cycle < 2; cycle++) {
        for (size_t i = 0; i < sizeof(levels); i++) {
            led_set_all(led_scale(color, levels[i], 10));
            vTaskDelay(pdMS_TO_TICKS(65));
        }
    }
}

static void led_run_chase(rgb_t color)
{
    for (int frame = 0; frame < CONFIG_WEARABLLM_LED_COUNT * 3; frame++) {
        led_clear_pixels();
        int head = frame % CONFIG_WEARABLLM_LED_COUNT;
        led_set_pixel(head, color);
        led_set_pixel((head + CONFIG_WEARABLLM_LED_COUNT - 1) % CONFIG_WEARABLLM_LED_COUNT,
                      led_scale(color, 1, 3));
        led_set_pixel((head + CONFIG_WEARABLLM_LED_COUNT - 2) % CONFIG_WEARABLLM_LED_COUNT,
                      led_scale(color, 1, 9));
        led_refresh();
        vTaskDelay(pdMS_TO_TICKS(55));
    }
}

static void led_run_flicker(rgb_t color)
{
    const uint8_t levels[] = {10, 2, 8, 0, 10, 3, 6, 1, 10, 0, 7, 10};
    for (size_t i = 0; i < sizeof(levels); i++) {
        led_set_all(led_scale(color, levels[i], 10));
        vTaskDelay(pdMS_TO_TICKS(55));
    }
}

static void led_apply_command(const char *command)
{
    response_visual_t visual = visual_for_command(command);

    switch (visual.animation) {
    case LED_ANIM_PULSE:
        led_run_pulse(visual.color);
        break;
    case LED_ANIM_CHASE:
        led_run_chase(visual.color);
        break;
    case LED_ANIM_FLICKER:
        led_run_flicker(visual.color);
        break;
    case LED_ANIM_SOLID:
    default:
        break;
    }

    led_set_all(visual.color);
}

static void led_run_self_test(void)
{
    if (!CONFIG_WEARABLLM_LED_SELF_TEST_ON_BOOT) {
        return;
    }

    static const char *const commands[] = {"GS", "GP", "GC", "RS", "RF", "YP", "BS", "PS", "PP"};
    ESP_LOGI(TAG, "running RGB ring command self-test");
    for (size_t i = 0; i < sizeof(commands) / sizeof(commands[0]); i++) {
        ESP_LOGI(TAG, "LED self-test command: %s", commands[i]);
        led_apply_command(commands[i]);
        vTaskDelay(pdMS_TO_TICKS(250));
    }
    led_set_all(COLOR_IDLE);
}

static void led_init(void)
{
    led_strip_config_t strip_config = {
        .strip_gpio_num = CONFIG_WEARABLLM_LED_GPIO,
        .max_leds = CONFIG_WEARABLLM_LED_COUNT,
        .led_model = LED_MODEL_WS2812,
        .led_pixel_format = LED_PIXEL_FORMAT_GRB,
        .flags = {
            .invert_out = false,
        },
    };
    led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .mem_block_symbols = 64,
        .flags = {
            .with_dma = false,
        },
    };

    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config, &s_led_strip));
    ESP_ERROR_CHECK(led_strip_clear(s_led_strip));
    led_set_all(COLOR_IDLE);
    led_run_self_test();
}

static void ptt_button_init(void)
{
    gpio_config_t io_conf = {
        .pin_bit_mask = 1ULL << CONFIG_WEARABLLM_PTT_GPIO,
        .mode = GPIO_MODE_INPUT,
#if CONFIG_WEARABLLM_PTT_PULL_UP
        .pull_up_en = GPIO_PULLUP_ENABLE,
#else
        .pull_up_en = GPIO_PULLUP_DISABLE,
#endif
#if CONFIG_WEARABLLM_PTT_PULL_DOWN
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
#else
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
#endif
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));
}

static bool ptt_is_held(void)
{
    return gpio_get_level(CONFIG_WEARABLLM_PTT_GPIO) == CONFIG_WEARABLLM_PTT_ACTIVE_LEVEL;
}

static bool ptt_is_held_debounced(void)
{
    bool initial = ptt_is_held();
    if (!initial || CONFIG_WEARABLLM_PTT_DEBOUNCE_MS <= 0) {
        return initial;
    }

    vTaskDelay(pdMS_TO_TICKS(CONFIG_WEARABLLM_PTT_DEBOUNCE_MS));
    return initial && ptt_is_held();
}

static bool ptt_is_released_debounced(void)
{
    bool initial = !ptt_is_held();
    if (!initial || CONFIG_WEARABLLM_PTT_DEBOUNCE_MS <= 0) {
        return initial;
    }

    vTaskDelay(pdMS_TO_TICKS(CONFIG_WEARABLLM_PTT_DEBOUNCE_MS));
    return initial && !ptt_is_held();
}

static const char *ptt_pull_mode_name(void)
{
#if CONFIG_WEARABLLM_PTT_PULL_UP
    return "pull-up";
#elif CONFIG_WEARABLLM_PTT_PULL_DOWN
    return "pull-down";
#else
    return "none";
#endif
}

static bool keep_recording_while_ptt_held(void *ctx)
{
    (void)ctx;
    return !ptt_is_released_debounced();
}

static int hex_nibble(char ch)
{
    if (ch >= '0' && ch <= '9') {
        return ch - '0';
    }
    if (ch >= 'a' && ch <= 'f') {
        return ch - 'a' + 10;
    }
    if (ch >= 'A' && ch <= 'F') {
        return ch - 'A' + 10;
    }
    return -1;
}

static bool parse_bssid(const char *text, uint8_t out[6])
{
    if (!text || strlen(text) != 17) {
        return false;
    }

    for (int i = 0; i < 6; i++) {
        int offset = i * 3;
        int high = hex_nibble(text[offset]);
        int low = hex_nibble(text[offset + 1]);
        if (high < 0 || low < 0) {
            return false;
        }
        out[i] = (uint8_t)((high << 4) | low);
        if (i < 5 && text[offset + 2] != ':') {
            return false;
        }
    }
    return true;
}

static esp_err_t wait_for_wifi_ready(void)
{
    if (CONFIG_WEARABLLM_WIFI_SSID[0] == '\0') {
        ESP_LOGE(TAG, "Wi-Fi SSID is not configured; run scripts/configure_firmware.py or idf.py menuconfig");
        return ESP_ERR_INVALID_STATE;
    }

    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_events,
        WIFI_CONNECTED_BIT,
        pdFALSE,
        pdTRUE,
        pdMS_TO_TICKS(CONFIG_WEARABLLM_WIFI_CONNECT_TIMEOUT_MS));
    if ((bits & WIFI_CONNECTED_BIT) == 0) {
        ESP_LOGE(TAG, "Wi-Fi not connected after %d ms", CONFIG_WEARABLLM_WIFI_CONNECT_TIMEOUT_MS);
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

static esp_err_t capture_audio_wav(uint8_t **out_data, size_t *out_len)
{
    esp_err_t err = wearabllm_audio_capture_wav(
        keep_recording_while_ptt_held,
        NULL,
        CONFIG_WEARABLLM_AUDIO_MAX_SECONDS,
        CONFIG_WEARABLLM_AUDIO_MIN_CAPTURE_MS,
        out_data,
        out_len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "real audio capture failed (%s); using silent WAV fallback", esp_err_to_name(err));
        return wearabllm_audio_make_silent_wav(500, out_data, out_len);
    }
    return ESP_OK;
}

static esp_err_t http_event_handler(esp_http_client_event_t *evt)
{
    http_response_t *response = (http_response_t *)evt->user_data;

    if (evt->event_id == HTTP_EVENT_ON_DATA && response && evt->data_len > 0) {
        int copy_len = evt->data_len;
        if (response->len + copy_len >= response->capacity) {
            copy_len = response->capacity - response->len - 1;
            response->overflow = true;
        }
        if (copy_len > 0) {
            memcpy(response->data + response->len, evt->data, copy_len);
            response->len += copy_len;
            response->data[response->len] = '\0';
        }
    }

    return ESP_OK;
}

#if CONFIG_WEARABLLM_TTS_ENABLED
static esp_err_t http_binary_event_handler(esp_http_client_event_t *evt)
{
    http_binary_response_t *response = (http_binary_response_t *)evt->user_data;

    if (evt->event_id == HTTP_EVENT_ON_DATA && response && evt->data_len > 0) {
        int copy_len = evt->data_len;
        if (response->len + copy_len > response->capacity) {
            copy_len = response->capacity - response->len;
            response->overflow = true;
        }
        if (copy_len > 0) {
            memcpy(response->data + response->len, evt->data, copy_len);
            response->len += copy_len;
        }
    }

    return ESP_OK;
}

static bool append_json_string(char *buf, size_t capacity, size_t *offset, const char *value)
{
    if (*offset >= capacity) {
        return false;
    }

    for (const char *p = value; *p; p++) {
        const char *escaped = NULL;
        char escaped_buf[7] = {0};
        switch (*p) {
        case '"':
            escaped = "\\\"";
            break;
        case '\\':
            escaped = "\\\\";
            break;
        case '\n':
            escaped = "\\n";
            break;
        case '\r':
            escaped = "\\r";
            break;
        case '\t':
            escaped = "\\t";
            break;
        default:
            if ((unsigned char)*p < 0x20) {
                snprintf(escaped_buf, sizeof(escaped_buf), "\\u%04x", (unsigned char)*p);
                escaped = escaped_buf;
            }
            break;
        }

        if (escaped) {
            size_t len = strlen(escaped);
            if (*offset + len >= capacity) {
                return false;
            }
            memcpy(buf + *offset, escaped, len);
            *offset += len;
        } else {
            if (*offset + 1 >= capacity) {
                return false;
            }
            buf[(*offset)++] = *p;
        }
    }
    buf[*offset] = '\0';
    return true;
}

static esp_err_t make_tts_json(const char *reply, char *json, size_t json_len)
{
    size_t offset = 0;
    const char *prefix = "{\"text\":\"";
    const char *suffix = "\"}";
    size_t prefix_len = strlen(prefix);
    size_t suffix_len = strlen(suffix);
    if (prefix_len + suffix_len + 1 > json_len) {
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(json, prefix, prefix_len);
    offset = prefix_len;
    if (!append_json_string(json, json_len, &offset, reply)) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (offset + suffix_len >= json_len) {
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(json + offset, suffix, suffix_len);
    offset += suffix_len;
    json[offset] = '\0';
    return ESP_OK;
}
#endif

static esp_err_t send_audio_to_bridge(
    const uint8_t *wav,
    size_t wav_len,
    char *command_out,
    size_t command_len,
    char *transcript_out,
    size_t transcript_len,
    char *reply_out,
    size_t reply_len)
{
    char response_buf[HTTP_RESPONSE_MAX] = {0};
    http_response_t response = {
        .data = response_buf,
        .len = 0,
        .capacity = sizeof(response_buf),
        .overflow = false,
    };

    esp_http_client_config_t config = {
        .url = CONFIG_WEARABLLM_BRIDGE_URL,
        .method = HTTP_METHOD_POST,
        .event_handler = http_event_handler,
        .user_data = &response,
        .timeout_ms = 30000,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        return ESP_FAIL;
    }

    esp_http_client_set_header(client, "Content-Type", "audio/wav");
    esp_http_client_set_post_field(client, (const char *)wav, wav_len);

    ESP_LOGI(TAG, "posting WAV to bridge: %u bytes -> %s", (unsigned)wav_len, CONFIG_WEARABLLM_BRIDGE_URL);
    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    ESP_LOGI(TAG, "bridge HTTP result: err=%s status=%d response_bytes=%d",
             esp_err_to_name(err),
             status,
             response.len);

    if (response.overflow) {
        ESP_LOGE(TAG, "bridge response exceeded %u bytes; ignoring truncated JSON", (unsigned)sizeof(response_buf));
        return ESP_ERR_INVALID_SIZE;
    }

    if (err != ESP_OK || status < 200 || status >= 300) {
        ESP_LOGE(TAG, "bridge request failed: err=%s status=%d body=%s", esp_err_to_name(err), status, response_buf);
        return err == ESP_OK ? ESP_FAIL : err;
    }

    cJSON *root = cJSON_Parse(response_buf);
    cJSON *command = root ? cJSON_GetObjectItemCaseSensitive(root, "command") : NULL;
    if (!cJSON_IsString(command) || strlen(command->valuestring) != 2) {
        ESP_LOGE(TAG, "bridge response missing 2-char command: %s", response_buf);
        cJSON_Delete(root);
        return ESP_FAIL;
    }

    char normalized_command[3] = {
        (char)toupper((unsigned char)command->valuestring[0]),
        (char)toupper((unsigned char)command->valuestring[1]),
        '\0',
    };
    if (!is_valid_led_command(normalized_command)) {
        ESP_LOGE(TAG, "bridge response has unknown LED command '%s': %s", command->valuestring, response_buf);
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }

    snprintf(command_out, command_len, "%s", normalized_command);
    cJSON *transcript = cJSON_GetObjectItemCaseSensitive(root, "transcript");
    if (cJSON_IsString(transcript)) {
        ESP_LOGI(TAG, "transcript: %s", transcript->valuestring);
        snprintf(transcript_out, transcript_len, "%s", transcript->valuestring);
    } else if (transcript_len > 0) {
        transcript_out[0] = '\0';
    }

    cJSON *reply = cJSON_GetObjectItemCaseSensitive(root, "reply");
    if (cJSON_IsString(reply)) {
        ESP_LOGI(TAG, "bridge command=%s reply_len=%u", normalized_command, (unsigned)strlen(reply->valuestring));
        ESP_LOGI(TAG, "reply: %s", reply->valuestring);
        snprintf(reply_out, reply_len, "%s", reply->valuestring);
    } else if (reply_len > 0) {
        ESP_LOGI(TAG, "bridge command=%s with no reply string", normalized_command);
        reply_out[0] = '\0';
    }
    cJSON_Delete(root);
    return ESP_OK;
}

static esp_err_t fetch_tts_wav(const char *reply, uint8_t **out_data, size_t *out_len)
{
#if CONFIG_WEARABLLM_TTS_ENABLED
    if (!reply || reply[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }

    char request_json[TTS_JSON_MAX] = {0};
    ESP_RETURN_ON_ERROR(make_tts_json(reply, request_json, sizeof(request_json)), TAG, "tts json too large");

    uint8_t *response_buf = calloc(1, CONFIG_WEARABLLM_TTS_MAX_BYTES);
    ESP_RETURN_ON_FALSE(response_buf, ESP_ERR_NO_MEM, TAG, "tts response allocation failed");

    http_binary_response_t response = {
        .data = response_buf,
        .len = 0,
        .capacity = CONFIG_WEARABLLM_TTS_MAX_BYTES,
        .overflow = false,
    };

    esp_http_client_config_t config = {
        .url = CONFIG_WEARABLLM_TTS_URL,
        .method = HTTP_METHOD_POST,
        .event_handler = http_binary_event_handler,
        .user_data = &response,
        .timeout_ms = 30000,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        free(response_buf);
        return ESP_FAIL;
    }

    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, request_json, strlen(request_json));

    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK || status < 200 || status >= 300 || response.overflow || response.len <= 0) {
        ESP_LOGE(TAG,
                 "tts request failed: err=%s status=%d bytes=%d overflow=%d",
                 esp_err_to_name(err),
                 status,
                 response.len,
                 response.overflow);
        free(response_buf);
        return err == ESP_OK ? ESP_FAIL : err;
    }

    *out_data = response_buf;
    *out_len = response.len;
    return ESP_OK;
#else
    (void)reply;
    (void)out_data;
    (void)out_len;
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

static void wifi_log_ap_info(void)
{
    wifi_ap_record_t ap_info = {0};
    esp_err_t err = esp_wifi_sta_get_ap_info(&ap_info);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Wi-Fi AP info unavailable: %s", esp_err_to_name(err));
        return;
    }

    ESP_LOGI(TAG,
             "Wi-Fi AP: ssid=%s bssid=%02x:%02x:%02x:%02x:%02x:%02x channel=%u rssi=%d auth=%d",
             (const char *)ap_info.ssid,
             ap_info.bssid[0],
             ap_info.bssid[1],
             ap_info.bssid[2],
             ap_info.bssid[3],
             ap_info.bssid[4],
             ap_info.bssid[5],
             ap_info.primary,
             ap_info.rssi,
             ap_info.authmode);
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "Wi-Fi disconnected; reconnecting");
        esp_wifi_connect();
        xEventGroupClearBits(s_wifi_events, WIFI_CONNECTED_BIT);
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Wi-Fi connected: " IPSTR, IP2STR(&event->ip_info.ip));
        wifi_log_ap_info();
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
    }
}

static void wifi_init(void)
{
    s_wifi_events = xEventGroupCreate();
    if (CONFIG_WEARABLLM_WIFI_SSID[0] == '\0') {
        ESP_LOGW(TAG, "Wi-Fi disabled: WearabLLM v3 -> Wi-Fi SSID is empty");
        ESP_LOGW(TAG, "Set local credentials with scripts/configure_firmware.py before bridge tests");
        return;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = CONFIG_WEARABLLM_WIFI_SSID,
            .password = CONFIG_WEARABLLM_WIFI_PASSWORD,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    if (CONFIG_WEARABLLM_WIFI_BSSID[0] != '\0') {
        if (parse_bssid(CONFIG_WEARABLLM_WIFI_BSSID, wifi_config.sta.bssid)) {
            wifi_config.sta.bssid_set = true;
            ESP_LOGI(TAG, "Wi-Fi locked to BSSID %s", CONFIG_WEARABLLM_WIFI_BSSID);
        } else {
            ESP_LOGW(TAG, "Ignoring invalid Wi-Fi BSSID: %s", CONFIG_WEARABLLM_WIFI_BSSID);
        }
    }

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
}

static void interaction_task(void *arg)
{
    bool was_held = false;

    while (true) {
        bool held = ptt_is_held_debounced();
        if (held && !was_held) {
            ESP_LOGI(TAG, "push-to-talk held: listening");
            led_set_all(COLOR_LISTENING);
            wearabllm_display_show_state(WEARABLLM_DISPLAY_LISTENING);
            uint8_t *wav = NULL;
            size_t wav_len = 0;
            esp_err_t err = capture_audio_wav(&wav, &wav_len);

            led_set_all(COLOR_THINKING);
            wearabllm_display_show_state(WEARABLLM_DISPLAY_THINKING);
            if (err == ESP_OK) {
                char command[3] = "BS";
                char transcript[192] = {0};
                char reply[256] = {0};
                err = wait_for_wifi_ready();
                if (err == ESP_OK) {
                    err = send_audio_to_bridge(
                        wav,
                        wav_len,
                        command,
                        sizeof(command),
                        transcript,
                        sizeof(transcript),
                        reply,
                        sizeof(reply));
                }
                if (err == ESP_OK) {
                    ESP_LOGI(TAG, "LED command: %s", command);
                    led_apply_command(command);
                    wearabllm_display_show_response(command, transcript, reply);
                    esp_err_t tone_err = wearabllm_audio_play_tone(880, 80);
                    if (tone_err != ESP_OK && tone_err != ESP_ERR_NOT_SUPPORTED) {
                        ESP_LOGW(TAG, "speaker tone failed: %s", esp_err_to_name(tone_err));
                    }
                    uint8_t *tts_wav = NULL;
                    size_t tts_wav_len = 0;
                    esp_err_t tts_err = fetch_tts_wav(reply, &tts_wav, &tts_wav_len);
                    if (tts_err == ESP_OK) {
                        ESP_LOGI(TAG, "playing TTS WAV: %u bytes", (unsigned)tts_wav_len);
                        tts_err = wearabllm_audio_play_wav(tts_wav, tts_wav_len);
                    }
                    if (tts_err != ESP_OK && tts_err != ESP_ERR_NOT_SUPPORTED) {
                        ESP_LOGW(TAG, "tts playback failed: %s", esp_err_to_name(tts_err));
                    }
                    free(tts_wav);
                } else {
                    led_set_all(COLOR_ERROR);
                    wearabllm_display_show_error("bridge request failed");
                }
                free(wav);
            } else {
                ESP_LOGE(TAG, "audio capture failed: %s", esp_err_to_name(err));
                led_set_all(COLOR_ERROR);
                wearabllm_display_show_error("audio capture failed");
            }
        }
        was_held = held && !ptt_is_released_debounced();
        vTaskDelay(pdMS_TO_TICKS(25));
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    led_init();
    ptt_button_init();
    ESP_ERROR_CHECK(wearabllm_display_init());
    err = wearabllm_audio_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "audio init failed at boot (%s); capture will retry on demand", esp_err_to_name(err));
    }
    wifi_init();

    ESP_LOGI(TAG, "WearabLLM v3 Waveshare phase-1 scaffold");
    ESP_LOGI(TAG, "PTT GPIO=%d active_level=%d pull=%s debounce=%d ms LED GPIO=%d bridge=%s",
             CONFIG_WEARABLLM_PTT_GPIO,
             CONFIG_WEARABLLM_PTT_ACTIVE_LEVEL,
             ptt_pull_mode_name(),
             CONFIG_WEARABLLM_PTT_DEBOUNCE_MS,
             CONFIG_WEARABLLM_LED_GPIO,
             CONFIG_WEARABLLM_BRIDGE_URL);
    ESP_LOGI(TAG, "Audio capture min=%d ms max=%d s",
             CONFIG_WEARABLLM_AUDIO_MIN_CAPTURE_MS,
             CONFIG_WEARABLLM_AUDIO_MAX_SECONDS);
    ESP_LOGI(TAG, "Wi-Fi SSID configured=%s", CONFIG_WEARABLLM_WIFI_SSID[0] ? "yes" : "no");

    xTaskCreate(interaction_task, "interaction", 8192, NULL, 5, NULL);
}
