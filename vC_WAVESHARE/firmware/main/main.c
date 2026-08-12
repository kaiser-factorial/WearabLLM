#include <stdbool.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#include "cJSON.h"
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "nvs_flash.h"
#include "wearabllm_audio.h"
#include "model_path.h"
#include "wearabllm_display.h"
#include "wearabllm_openai.h"
#include "wearabllm_transcript_log.h"

static const char *TAG = "wearabllm";

#define WIFI_CONNECTED_BIT BIT0
#define QUERY_RESPONSE_MAX 1024
#define ACTION_RESPONSE_MAX (64 * 1024)
#define TTS_CHUNK_TEXT_MAX 160
#define ACTION_URL_MAX 384
#define ACTION_ID_MAX 37

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

#ifndef CONFIG_WEARABLLM_REMOTE_ACTIONS_ENABLED
#define CONFIG_WEARABLLM_REMOTE_ACTIONS_ENABLED 0
#endif

#ifndef CONFIG_WEARABLLM_ACTION_POLL_INTERVAL_MS
#define CONFIG_WEARABLLM_ACTION_POLL_INTERVAL_MS 2000
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

#if CONFIG_WEARABLLM_REMOTE_ACTIONS_ENABLED
typedef struct {
    char text[TTS_CHUNK_TEXT_MAX + 1];
    uint8_t *wav;
    size_t wav_len;
    esp_err_t result;
    SemaphoreHandle_t done;
} tts_prefetch_job_t;
#endif
#endif

static EventGroupHandle_t s_wifi_events;
static led_strip_handle_t s_led_strip;
static srmodel_list_t *s_sr_models;
static const esp_wn_iface_t *s_wakenet;
static model_iface_data_t *s_wakenet_data;
static int16_t *s_wakenet_samples;
static int s_wakenet_chunk_frames;
static SemaphoreHandle_t s_response_lock;
static bool s_response_active;

static const rgb_t COLOR_IDLE = {0, 0, 36};
static const rgb_t COLOR_LISTENING = {24, 24, 24};
static const rgb_t COLOR_THINKING = {80, 80, 80};
static const rgb_t COLOR_ERROR = {90, 0, 0};
static const rgb_t COLOR_GREEN = {0, 80, 0};
static const rgb_t COLOR_RED = {90, 0, 0};

static esp_err_t wake_word_init(void)
{
    s_sr_models = esp_srmodel_init("model");
    ESP_RETURN_ON_FALSE(s_sr_models, ESP_FAIL, TAG, "wake model partition unavailable");
    char *model_name = esp_srmodel_filter(s_sr_models, ESP_WN_PREFIX, "hiesp");
    ESP_RETURN_ON_FALSE(model_name, ESP_ERR_NOT_FOUND, TAG, "Hi ESP model unavailable");
    s_wakenet = esp_wn_handle_from_name(model_name);
    ESP_RETURN_ON_FALSE(s_wakenet, ESP_FAIL, TAG, "WakeNet interface unavailable");
    s_wakenet_data = s_wakenet->create(model_name, DET_MODE_90);
    ESP_RETURN_ON_FALSE(s_wakenet_data, ESP_FAIL, TAG, "WakeNet model creation failed");
    s_wakenet_chunk_frames = s_wakenet->get_samp_chunksize(s_wakenet_data);
    ESP_RETURN_ON_FALSE(s_wakenet_chunk_frames > 0, ESP_FAIL, TAG, "invalid WakeNet chunk size");
    s_wakenet_samples = calloc((size_t)s_wakenet_chunk_frames, sizeof(int16_t));
    ESP_RETURN_ON_FALSE(s_wakenet_samples, ESP_ERR_NO_MEM, TAG, "WakeNet audio allocation failed");
    ESP_LOGI(TAG, "wake word ready: Hi ESP, chunk_frames=%d", s_wakenet_chunk_frames);
    return ESP_OK;
}

static bool wake_word_poll(void)
{
    if (!s_wakenet_data || !s_wakenet_samples) return false;
    esp_err_t err = wearabllm_audio_read_mono(s_wakenet_samples, (size_t)s_wakenet_chunk_frames);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "wake audio read failed: %s", esp_err_to_name(err));
        return false;
    }
    return s_wakenet->detect(s_wakenet_data, s_wakenet_samples) == WAKENET_DETECTED;
}
static const rgb_t COLOR_YELLOW = {90, 55, 0};
static const rgb_t COLOR_BLUE = {0, 25, 90};
static const rgb_t COLOR_PURPLE = {55, 0, 80};

typedef struct {
    char id[ACTION_ID_MAX];
    char command[3];
    char *transcript;
    char *reply;
} remote_action_t;

static char *remote_action_strdup(const char *value)
{
    const char *source = value ? value : "";
    size_t length = strlen(source) + 1;
    char *copy = heap_caps_malloc(length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!copy) copy = malloc(length);
    if (copy) memcpy(copy, source, length);
    return copy;
}

static void remote_action_free(remote_action_t *action)
{
    if (!action) return;
    free(action->transcript);
    free(action->reply);
    action->transcript = NULL;
    action->reply = NULL;
}

#if CONFIG_WEARABLLM_TTS_ENABLED && CONFIG_WEARABLLM_REMOTE_ACTIONS_ENABLED
static const char *next_tts_chunk(const char *cursor, char *chunk, size_t capacity)
{
    if (!cursor || !chunk || capacity < 2) return NULL;
    while (*cursor && isspace((unsigned char)*cursor)) cursor++;
    if (!*cursor) return NULL;

    size_t scan = 0;
    size_t last_space = 0;
    size_t chosen = 0;
    while (cursor[scan] && scan < capacity - 1) {
        unsigned char current = (unsigned char)cursor[scan];
        scan++;
        if (isspace(current)) last_space = scan - 1;
        if ((current == '.' || current == '!' || current == '?' || current == '\n') && scan >= 24) {
            chosen = scan;
            break;
        }
    }
    if (!chosen) {
        if (!cursor[scan]) {
            chosen = scan;
        } else if (last_space) {
            chosen = last_space;
        } else {
            chosen = scan;
            while (chosen > 0 && (((unsigned char)cursor[chosen] & 0xc0) == 0x80)) chosen--;
        }
    }
    if (!chosen) return NULL;

    memcpy(chunk, cursor, chosen);
    chunk[chosen] = '\0';
    while (chosen > 0 && isspace((unsigned char)chunk[chosen - 1])) chunk[--chosen] = '\0';
    return cursor + (last_space == chosen && cursor[chosen] ? chosen + 1 : chosen);
}
#endif

static bool response_try_claim(void)
{
    if (!s_response_lock) return false;
    if (xSemaphoreTake(s_response_lock, pdMS_TO_TICKS(50)) != pdTRUE) return false;
    bool claimed = !s_response_active;
    if (claimed) s_response_active = true;
    xSemaphoreGive(s_response_lock);
    return claimed;
}

static void response_release(void)
{
    if (!s_response_lock) return;
    if (xSemaphoreTake(s_response_lock, pdMS_TO_TICKS(50)) == pdTRUE) {
        s_response_active = false;
        xSemaphoreGive(s_response_lock);
    }
}

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
        // led_strip 2.5.x packs GRB internally; this board's LEDs expect RGB bytes.
        ESP_ERROR_CHECK(led_strip_set_pixel(s_led_strip, i, color.g, color.r, color.b));
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
    ESP_ERROR_CHECK(led_strip_set_pixel(s_led_strip, index, color.g, color.r, color.b));
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

static esp_err_t capture_audio_wav(uint8_t **out_data, size_t *out_len, bool *used_silent_fallback)
{
    ESP_RETURN_ON_FALSE(used_silent_fallback, ESP_ERR_INVALID_ARG, TAG, "capture fallback flag is required");
    *used_silent_fallback = false;
    esp_err_t err = wearabllm_audio_capture_wav(
        keep_recording_while_ptt_held,
        NULL,
        CONFIG_WEARABLLM_AUDIO_MAX_SECONDS,
        CONFIG_WEARABLLM_AUDIO_MIN_CAPTURE_MS,
        out_data,
        out_len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "real audio capture failed (%s); using silent WAV fallback", esp_err_to_name(err));
        *used_silent_fallback = true;
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

static char *make_tts_json(const char *reply)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return NULL;
    if (!cJSON_AddStringToObject(root, "text", reply ? reply : "")) {
        cJSON_Delete(root);
        return NULL;
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return json;
}
#endif

static esp_err_t fetch_tts_wav(const char *reply, uint8_t **out_data, size_t *out_len);

#if CONFIG_WEARABLLM_TTS_ENABLED && CONFIG_WEARABLLM_REMOTE_ACTIONS_ENABLED
static esp_err_t request_tts_wav(const char *text, uint8_t **out_data, size_t *out_len)
{
#if CONFIG_WEARABLLM_DIRECT_OPENAI
    return wearabllm_openai_tts(text, out_data, out_len);
#else
    return fetch_tts_wav(text, out_data, out_len);
#endif
}

static void tts_prefetch_task(void *arg)
{
    tts_prefetch_job_t *job = (tts_prefetch_job_t *)arg;
    job->result = request_tts_wav(job->text, &job->wav, &job->wav_len);
    xSemaphoreGive(job->done);
    vTaskDelete(NULL);
}

static bool tts_prefetch_start(tts_prefetch_job_t *job, const char *text)
{
    if (!job || !text || !text[0]) return false;
    memset(job, 0, sizeof(*job));
    snprintf(job->text, sizeof(job->text), "%s", text);
    job->result = ESP_FAIL;
    job->done = xSemaphoreCreateBinary();
    if (!job->done) {
        ESP_LOGW(TAG, "TTS prefetch semaphore allocation failed");
        return false;
    }
    BaseType_t task_result = xTaskCreate(tts_prefetch_task, "tts_prefetch", 8192, job, 4, NULL);
    if (task_result != pdPASS) {
        ESP_LOGW(TAG, "TTS prefetch task creation failed; using sequential fallback");
        vSemaphoreDelete(job->done);
        job->done = NULL;
        return false;
    }
    ESP_LOGI(TAG, "TTS prefetch started: chars=%u", (unsigned)strlen(job->text));
    return true;
}

static esp_err_t tts_prefetch_await(tts_prefetch_job_t *job, uint8_t **out_data, size_t *out_len)
{
    ESP_RETURN_ON_FALSE(job && job->done && out_data && out_len,
                        ESP_ERR_INVALID_ARG,
                        TAG,
                        "invalid TTS prefetch result");
    xSemaphoreTake(job->done, portMAX_DELAY);
    vSemaphoreDelete(job->done);
    job->done = NULL;
    *out_data = job->wav;
    *out_len = job->wav_len;
    job->wav = NULL;
    job->wav_len = 0;
    ESP_LOGI(TAG,
             "TTS prefetch finished: result=%s bytes=%u",
             esp_err_to_name(job->result),
             (unsigned)*out_len);
    return job->result;
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
    char response_buf[QUERY_RESPONSE_MAX] = {0};
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
        .crt_bundle_attach = esp_crt_bundle_attach,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        return ESP_FAIL;
    }

    esp_http_client_set_header(client, "Content-Type", "audio/wav");
    if (CONFIG_WEARABLLM_BRIDGE_AUTH_TOKEN[0] != '\0') {
        esp_http_client_set_header(client, "X-WearabLLM-Device-Token", CONFIG_WEARABLLM_BRIDGE_AUTH_TOKEN);
    }
    esp_http_client_set_header(client, "X-WearabLLM-Device-Id", CONFIG_WEARABLLM_DEVICE_ID);
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

#if CONFIG_WEARABLLM_REMOTE_ACTIONS_ENABLED
static bool build_action_url(char *out, size_t capacity, const char *action_id)
{
    const char *query_url = CONFIG_WEARABLLM_BRIDGE_URL;
    const char *suffix = "/v1/query";
    size_t query_len = strlen(query_url);
    size_t suffix_len = strlen(suffix);
    if (query_len <= suffix_len || strcmp(query_url + query_len - suffix_len, suffix) != 0) {
        ESP_LOGE(TAG, "bridge URL must end with /v1/query for remote actions");
        return false;
    }
    int written = action_id && action_id[0]
        ? snprintf(out, capacity, "%.*s/v1/devices/%s/actions/%s/ack",
                   (int)(query_len - suffix_len), query_url, CONFIG_WEARABLLM_DEVICE_ID, action_id)
        : snprintf(out, capacity, "%.*s/v1/devices/%s/actions",
                   (int)(query_len - suffix_len), query_url, CONFIG_WEARABLLM_DEVICE_ID);
    return written > 0 && (size_t)written < capacity;
}

static void action_request_headers(esp_http_client_handle_t client)
{
    if (CONFIG_WEARABLLM_BRIDGE_AUTH_TOKEN[0] != '\0') {
        esp_http_client_set_header(client, "X-WearabLLM-Device-Token", CONFIG_WEARABLLM_BRIDGE_AUTH_TOKEN);
    }
    esp_http_client_set_header(client, "X-WearabLLM-Device-Id", CONFIG_WEARABLLM_DEVICE_ID);
}

static esp_err_t fetch_remote_action(remote_action_t *out_action)
{
    char url[ACTION_URL_MAX] = {0};
    if (!build_action_url(url, sizeof(url), NULL)) return ESP_ERR_INVALID_ARG;

    char *response_buf = heap_caps_calloc(1, ACTION_RESPONSE_MAX, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!response_buf) response_buf = calloc(1, ACTION_RESPONSE_MAX);
    ESP_RETURN_ON_FALSE(response_buf, ESP_ERR_NO_MEM, TAG, "remote action response allocation failed");
    http_response_t response = {
        .data = response_buf,
        .len = 0,
        .capacity = ACTION_RESPONSE_MAX,
        .overflow = false,
    };
    esp_http_client_config_t config = {
        .url = url,
        .method = HTTP_METHOD_GET,
        .event_handler = http_event_handler,
        .user_data = &response,
        .timeout_ms = 15000,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        free(response_buf);
        return ESP_FAIL;
    }
    action_request_headers(client);
    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK || status < 200 || status >= 300 || response.overflow) {
        ESP_LOGW(TAG, "remote action poll failed: err=%s status=%d", esp_err_to_name(err), status);
        free(response_buf);
        return err == ESP_OK ? ESP_FAIL : err;
    }

    cJSON *root = cJSON_Parse(response_buf);
    free(response_buf);
    cJSON *action = root ? cJSON_GetObjectItemCaseSensitive(root, "action") : NULL;
    if (!action || cJSON_IsNull(action)) {
        cJSON_Delete(root);
        return ESP_ERR_NOT_FOUND;
    }
    cJSON *id = cJSON_GetObjectItemCaseSensitive(action, "id");
    cJSON *command = cJSON_GetObjectItemCaseSensitive(action, "command");
    cJSON *transcript = cJSON_GetObjectItemCaseSensitive(action, "transcript");
    cJSON *reply = cJSON_GetObjectItemCaseSensitive(action, "reply");
    if (!cJSON_IsString(id) || strlen(id->valuestring) != ACTION_ID_MAX - 1 ||
        !cJSON_IsString(command) || strlen(command->valuestring) != 2 ||
        !cJSON_IsString(reply)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    char normalized_command[3] = {
        (char)toupper((unsigned char)command->valuestring[0]),
        (char)toupper((unsigned char)command->valuestring[1]),
        '\0',
    };
    if (!is_valid_led_command(normalized_command)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    snprintf(out_action->id, sizeof(out_action->id), "%s", id->valuestring);
    snprintf(out_action->command, sizeof(out_action->command), "%s", normalized_command);
    out_action->transcript = remote_action_strdup(cJSON_IsString(transcript) ? transcript->valuestring : "");
    out_action->reply = remote_action_strdup(reply->valuestring);
    if (!out_action->transcript || !out_action->reply) {
        remote_action_free(out_action);
        cJSON_Delete(root);
        return ESP_ERR_NO_MEM;
    }
    cJSON_Delete(root);
    return ESP_OK;
}

static esp_err_t acknowledge_remote_action(const remote_action_t *action, const char *status, const char *error)
{
    char url[ACTION_URL_MAX] = {0};
    if (!build_action_url(url, sizeof(url), action->id)) return ESP_ERR_INVALID_ARG;
    cJSON *root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(root, "status", status);
    if (error && error[0]) cJSON_AddStringToObject(root, "error", error);
    char *body = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!body) return ESP_ERR_NO_MEM;

    esp_http_client_config_t config = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 15000,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        free(body);
        return ESP_FAIL;
    }
    esp_http_client_set_header(client, "Content-Type", "application/json");
    action_request_headers(client);
    esp_http_client_set_post_field(client, body, strlen(body));
    esp_err_t err = esp_http_client_perform(client);
    int http_status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    free(body);
    return (err == ESP_OK && http_status >= 200 && http_status < 300) ? ESP_OK : ESP_FAIL;
}

static void remote_action_task(void *arg)
{
    while (true) {
        if (wait_for_wifi_ready() == ESP_OK && response_try_claim()) {
            remote_action_t action = {0};
            esp_err_t err = fetch_remote_action(&action);
            if (err == ESP_OK) {
                ESP_LOGI(TAG, "remote action %s command=%s", action.id, action.command);
                led_set_all(COLOR_THINKING);
                ESP_LOGI(TAG, "remote action display: thinking start");
                wearabllm_display_show_state(WEARABLLM_DISPLAY_THINKING);
                ESP_LOGI(TAG, "remote action display: thinking done");
                led_apply_command(action.command);

                bool tts_started = false;
#if CONFIG_WEARABLLM_TTS_ENABLED
                esp_err_t tts_err = ESP_OK;
                bool response_rendered = false;
                char current_chunk[TTS_CHUNK_TEXT_MAX + 1] = {0};
                const char *tts_cursor = next_tts_chunk(action.reply, current_chunk, sizeof(current_chunk));
                uint8_t *tts_wav = NULL;
                size_t tts_wav_len = 0;
                if (tts_cursor) {
                    ESP_LOGI(TAG,
                             "remote action first TTS chunk: chars=%u",
                             (unsigned)strlen(current_chunk));
                    tts_err = request_tts_wav(current_chunk, &tts_wav, &tts_wav_len);
                }
                while (tts_cursor && tts_err == ESP_OK) {
                    char next_chunk[TTS_CHUNK_TEXT_MAX + 1] = {0};
                    const char *next_cursor = next_tts_chunk(tts_cursor, next_chunk, sizeof(next_chunk));
                    tts_prefetch_job_t prefetch = {0};
                    bool prefetch_started = next_cursor && tts_prefetch_start(&prefetch, next_chunk);

                    // Do not put a sentence on screen while its TTS request is still pending.
                    // Once ready, show only that sentence while its audio plays. The following
                    // sentence is fetched concurrently but remains hidden until its own turn.
                    ESP_LOGI(TAG,
                             "remote action TTS playback: chars=%u bytes=%u next_prefetch=%d",
                             (unsigned)strlen(current_chunk),
                             (unsigned)tts_wav_len,
                             prefetch_started);
                    if (!response_rendered) {
                        err = acknowledge_remote_action(&action, "rendered", NULL);
                        if (err != ESP_OK) ESP_LOGW(TAG, "remote action render acknowledgement failed");
                        response_rendered = true;
                    }
                    if (tts_err == ESP_OK && !tts_started) {
                        err = acknowledge_remote_action(&action, "tts_started", NULL);
                        if (err != ESP_OK) ESP_LOGW(TAG, "remote action TTS-start acknowledgement failed");
                        tts_started = true;
                    }
                    // Finish first-card queue acknowledgements before switching the display.
                    // Once the sentence appears, playback begins without another network wait.
                    wearabllm_display_show_response(action.command, action.transcript, current_chunk);
                    tts_err = wearabllm_audio_play_wav(tts_wav, tts_wav_len);
                    free(tts_wav);
                    tts_wav = NULL;
                    tts_wav_len = 0;

                    if (prefetch_started) {
                        esp_err_t prefetch_err = tts_prefetch_await(&prefetch, &tts_wav, &tts_wav_len);
                        if (tts_err == ESP_OK) tts_err = prefetch_err;
                        if (tts_err != ESP_OK) {
                            free(tts_wav);
                            tts_wav = NULL;
                            break;
                        }
                    } else if (tts_err == ESP_OK && next_cursor) {
                        // Low-memory/task-creation failures retain the old sequential behavior.
                        tts_err = request_tts_wav(next_chunk, &tts_wav, &tts_wav_len);
                    }
                    if (tts_err != ESP_OK || !next_cursor) break;
                    snprintf(current_chunk, sizeof(current_chunk), "%s", next_chunk);
                    tts_cursor = next_cursor;
                }
#else
                esp_err_t tts_err = ESP_ERR_NOT_SUPPORTED;
                ESP_LOGI(TAG, "remote action display: response start");
                wearabllm_display_show_response(action.command, action.transcript, action.reply);
                ESP_LOGI(TAG, "remote action display: response done");
                err = acknowledge_remote_action(&action, "rendered", NULL);
                if (err != ESP_OK) ESP_LOGW(TAG, "remote action render acknowledgement failed");
#endif
                if (tts_err == ESP_OK && tts_started) {
                    wearabllm_display_show_response(action.command, action.transcript, action.reply);
                    err = acknowledge_remote_action(&action, "played", NULL);
                    if (err != ESP_OK) ESP_LOGW(TAG, "remote action playback acknowledgement failed");
                } else if (tts_err == ESP_ERR_NOT_SUPPORTED) {
                    ESP_LOGI(TAG, "remote action audio is disabled; render completion is the final verified state");
                } else if (tts_err != ESP_OK) {
                    ESP_LOGW(TAG, "remote action TTS failed: %s", esp_err_to_name(tts_err));
                    char error[80];
                    snprintf(error, sizeof(error), "TTS/playback failed: %s", esp_err_to_name(tts_err));
                    err = acknowledge_remote_action(&action, "failed", error);
                    if (err != ESP_OK) ESP_LOGW(TAG, "remote action failure acknowledgement failed");
                    wearabllm_display_show_error("AUDIO FAILED");
                } else {
                    ESP_LOGW(TAG, "remote action contained no playable TTS text");
                    err = acknowledge_remote_action(&action, "failed", "No playable TTS text");
                    if (err != ESP_OK) ESP_LOGW(TAG, "remote action failure acknowledgement failed");
                }
            } else if (err != ESP_ERR_NOT_FOUND) {
                ESP_LOGW(TAG, "remote action fetch error: %s", esp_err_to_name(err));
            }
            remote_action_free(&action);
            response_release();
        }
        vTaskDelay(pdMS_TO_TICKS(CONFIG_WEARABLLM_ACTION_POLL_INTERVAL_MS));
    }
}
#endif

static esp_err_t fetch_tts_wav(const char *reply, uint8_t **out_data, size_t *out_len)
{
#if CONFIG_WEARABLLM_TTS_ENABLED
    if (!reply || reply[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }

    char *request_json = make_tts_json(reply);
    ESP_RETURN_ON_FALSE(request_json, ESP_ERR_NO_MEM, TAG, "tts json allocation failed");

    uint8_t *response_buf = heap_caps_calloc(
        1,
        CONFIG_WEARABLLM_TTS_MAX_BYTES,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!response_buf) response_buf = calloc(1, CONFIG_WEARABLLM_TTS_MAX_BYTES);
    if (!response_buf) {
        free(request_json);
        ESP_LOGE(TAG, "tts response allocation failed");
        return ESP_ERR_NO_MEM;
    }

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
        .crt_bundle_attach = esp_crt_bundle_attach,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        free(request_json);
        free(response_buf);
        return ESP_FAIL;
    }

    esp_http_client_set_header(client, "Content-Type", "application/json");
    if (CONFIG_WEARABLLM_BRIDGE_AUTH_TOKEN[0] != '\0') {
        esp_http_client_set_header(client, "X-WearabLLM-Device-Token", CONFIG_WEARABLLM_BRIDGE_AUTH_TOKEN);
    }
    esp_http_client_set_header(client, "X-WearabLLM-Device-Id", CONFIG_WEARABLLM_DEVICE_ID);
    esp_http_client_set_post_field(client, request_json, strlen(request_json));

    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    free(request_json);

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
        xEventGroupClearBits(s_wifi_events, WIFI_CONNECTED_BIT);
        esp_wifi_connect();
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
    ESP_ERROR_CHECK(s_wifi_events ? ESP_OK : ESP_ERR_NO_MEM);
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
    uint32_t interaction_id = 0;
    uint8_t previous_volume_buttons = 0;
    int64_t next_volume_button_poll_us = 0;

    while (true) {
        int64_t now_us = esp_timer_get_time();
        if (now_us >= next_volume_button_poll_us) {
            uint8_t volume_buttons = 0;
            esp_err_t button_err = wearabllm_audio_read_volume_buttons(&volume_buttons);
            if (button_err == ESP_OK) {
                uint8_t released = previous_volume_buttons & (uint8_t)~volume_buttons;
                int volume = wearabllm_audio_get_output_volume();
                if (released & WEARABLLM_AUDIO_BUTTON_VOLUME_UP) {
                    volume = volume < 90 ? volume + 10 : 100;
                    button_err = wearabllm_audio_set_output_volume((uint8_t)volume);
                } else if (released & WEARABLLM_AUDIO_BUTTON_VOLUME_DOWN) {
                    volume = volume > 10 ? volume - 10 : 0;
                    button_err = wearabllm_audio_set_output_volume((uint8_t)volume);
                }
                previous_volume_buttons = volume_buttons;
            }
            if (button_err != ESP_OK && button_err != ESP_ERR_NOT_SUPPORTED) {
                ESP_LOGW(TAG, "volume button poll failed: %s", esp_err_to_name(button_err));
            }
            next_volume_button_poll_us = now_us + 100000;
        }

        bool held = ptt_is_held_debounced();
        bool wake_detected = !held && !was_held && wake_word_poll();
        if (((held && !was_held) || wake_detected) && response_try_claim()) {
            interaction_id++;
            int64_t interaction_start_us = esp_timer_get_time();
            ESP_LOGI(TAG, "interaction #%" PRIu32 " started", interaction_id);
            ESP_LOGI(TAG, "%s: listening", wake_detected ? "wake word detected" : "push-to-talk held");
            led_set_all(COLOR_LISTENING);
            wearabllm_display_show_state(WEARABLLM_DISPLAY_LISTENING);
            uint8_t *wav = NULL;
            size_t wav_len = 0;
            bool used_silent_fallback = false;
            esp_err_t err = wake_detected
                ? wearabllm_audio_capture_wav_until_silence(
                    CONFIG_WEARABLLM_AUDIO_MAX_SECONDS, 1400, &wav, &wav_len)
                : capture_audio_wav(&wav, &wav_len, &used_silent_fallback);
            uint32_t capture_elapsed_ms = (uint32_t)((esp_timer_get_time() - interaction_start_us) / 1000);
            ESP_LOGI(TAG,
                     "interaction #%" PRIu32 " capture source=%s elapsed_ms=%" PRIu32 " wav_bytes=%u",
                     interaction_id,
                     used_silent_fallback ? "silent-fallback" : "onboard-mic",
                     capture_elapsed_ms,
                     (unsigned)wav_len);

            if (err == ESP_OK) {
                char command[3] = "BS";
                char transcript[192] = {0};
                char reply[256] = {0};
                led_set_all(COLOR_PURPLE);
                wearabllm_display_show_state(WEARABLLM_DISPLAY_SENDING);
                err = wait_for_wifi_ready();
                if (err == ESP_OK) {
                    // Give the sending state a visible flash before the
                    // request blocks while the assistant generates a reply.
                    vTaskDelay(pdMS_TO_TICKS(180));
                    led_set_all(COLOR_THINKING);
                    wearabllm_display_show_state(WEARABLLM_DISPLAY_THINKING);
#if CONFIG_WEARABLLM_DIRECT_OPENAI
                    err = wearabllm_openai_query(
#else
                    err = send_audio_to_bridge(
#endif
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
#if CONFIG_WEARABLLM_TRANSCRIPT_LOG_ENABLED
                    wearabllm_transcript_log_enqueue(
                        interaction_id,
                        command,
                        transcript,
                        reply,
                        used_silent_fallback ? "silent-fallback" : "onboard-mic");
#endif
                    esp_err_t tone_err = wearabllm_audio_play_tone(880, 80);
                    if (tone_err != ESP_OK && tone_err != ESP_ERR_NOT_SUPPORTED) {
                        ESP_LOGW(TAG, "speaker tone failed: %s", esp_err_to_name(tone_err));
                    }
                    uint8_t *tts_wav = NULL;
                    size_t tts_wav_len = 0;
#if CONFIG_WEARABLLM_DIRECT_OPENAI
                    esp_err_t tts_err = wearabllm_openai_tts(reply, &tts_wav, &tts_wav_len);
#else
                    esp_err_t tts_err = fetch_tts_wav(reply, &tts_wav, &tts_wav_len);
#endif
                    if (tts_err == ESP_OK) {
                        ESP_LOGI(TAG, "playing TTS WAV: %u bytes", (unsigned)tts_wav_len);
                        tts_err = wearabllm_audio_play_wav(tts_wav, tts_wav_len);
                    }
                    if (tts_err != ESP_OK) {
                        ESP_LOGW(TAG, "tts playback failed: %s", esp_err_to_name(tts_err));
                    }
                    free(tts_wav);
                    ESP_LOGI(TAG,
                             "interaction #%" PRIu32 " complete result=ok total_ms=%" PRIu32 " command=%s capture_source=%s",
                             interaction_id,
                             (uint32_t)((esp_timer_get_time() - interaction_start_us) / 1000),
                             command,
                             used_silent_fallback ? "silent-fallback" : "onboard-mic");
                } else {
                    led_set_all(COLOR_ERROR);
                    wearabllm_display_show_error(
#if CONFIG_WEARABLLM_DIRECT_OPENAI
                        "OpenAI request failed"
#else
                        "bridge request failed"
#endif
                    );
                    ESP_LOGE(TAG,
                             "interaction #%" PRIu32 " complete result=error total_ms=%" PRIu32 " err=%s capture_source=%s",
                             interaction_id,
                             (uint32_t)((esp_timer_get_time() - interaction_start_us) / 1000),
                             esp_err_to_name(err),
                             used_silent_fallback ? "silent-fallback" : "onboard-mic");
                }
                free(wav);
            } else {
                ESP_LOGE(TAG, "audio capture failed: %s", esp_err_to_name(err));
                led_set_all(COLOR_ERROR);
                wearabllm_display_show_error("audio capture failed");
                ESP_LOGE(TAG,
                         "interaction #%" PRIu32 " complete result=error total_ms=%" PRIu32 " err=%s capture_source=%s",
                         interaction_id,
                         (uint32_t)((esp_timer_get_time() - interaction_start_us) / 1000),
                         esp_err_to_name(err),
                         used_silent_fallback ? "silent-fallback" : "onboard-mic");
            }
            response_release();
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
    esp_err_t wake_err = wake_word_init();
    if (wake_err != ESP_OK) {
        ESP_LOGE(TAG, "wake word disabled (%s); BOOT push-to-talk remains available", esp_err_to_name(wake_err));
    }
    wifi_init();
    s_response_lock = xSemaphoreCreateMutex();
    ESP_ERROR_CHECK(s_response_lock ? ESP_OK : ESP_ERR_NO_MEM);
#if CONFIG_WEARABLLM_TRANSCRIPT_LOG_ENABLED
    ESP_ERROR_CHECK(wearabllm_transcript_log_init());
#endif

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

    BaseType_t task_result = xTaskCreate(interaction_task, "interaction", 8192, NULL, 5, NULL);
    ESP_ERROR_CHECK(task_result == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
#if CONFIG_WEARABLLM_REMOTE_ACTIONS_ENABLED
    task_result = xTaskCreate(remote_action_task, "remote_action", 8192, NULL, 4, NULL);
    ESP_ERROR_CHECK(task_result == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
#endif
}
