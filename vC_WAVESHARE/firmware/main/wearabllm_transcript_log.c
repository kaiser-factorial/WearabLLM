#include "wearabllm_transcript_log.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#define TRANSCRIPT_QUEUE_DEPTH 4
#define TRANSCRIPT_HTTP_TIMEOUT_MS 15000

typedef struct {
    uint32_t interaction_id;
    char command[3];
    char transcript[192];
    char reply[256];
    char capture_source[24];
} transcript_event_t;

static const char *TAG = "transcript_log";
static QueueHandle_t s_queue;

static esp_err_t upload_event(const transcript_event_t *event)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(root, "device_id", CONFIG_WEARABLLM_TRANSCRIPT_DEVICE_ID);
    cJSON_AddNumberToObject(root, "interaction_id", event->interaction_id);
    cJSON_AddStringToObject(root, "command", event->command);
    cJSON_AddStringToObject(root, "transcript", event->transcript);
    cJSON_AddStringToObject(root, "reply", event->reply);
    cJSON_AddStringToObject(root, "capture_source", event->capture_source);
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!json) return ESP_ERR_NO_MEM;

    esp_http_client_config_t config = {
        .url = CONFIG_WEARABLLM_TRANSCRIPT_LOG_URL,
        .method = HTTP_METHOD_POST,
        .timeout_ms = TRANSCRIPT_HTTP_TIMEOUT_MS,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        free(json);
        return ESP_FAIL;
    }
    esp_err_t err = esp_http_client_set_header(client, "Content-Type", "application/json");
    if (err == ESP_OK) {
        err = esp_http_client_set_header(
            client, "X-WearabLLM-Device-Token", CONFIG_WEARABLLM_TRANSCRIPT_DEVICE_TOKEN);
    }
    if (err == ESP_OK) err = esp_http_client_set_post_field(client, json, strlen(json));
    if (err == ESP_OK) err = esp_http_client_perform(client);
    int status = err == ESP_OK ? esp_http_client_get_status_code(client) : 0;
    if (err == ESP_OK && (status < 200 || status >= 300)) err = ESP_FAIL;
    esp_http_client_cleanup(client);
    free(json);

    if (err == ESP_OK) {
        ESP_LOGI(TAG, "uploaded interaction #" PRIu32, event->interaction_id);
    } else {
        ESP_LOGW(TAG, "upload failed for interaction #" PRIu32 " status=%d err=%s",
                 event->interaction_id, status, esp_err_to_name(err));
    }
    return err;
}

static void transcript_log_task(void *arg)
{
    transcript_event_t event;
    while (true) {
        if (xQueueReceive(s_queue, &event, portMAX_DELAY) == pdTRUE) upload_event(&event);
    }
}

esp_err_t wearabllm_transcript_log_init(void)
{
    if (!CONFIG_WEARABLLM_TRANSCRIPT_LOG_URL[0] || !CONFIG_WEARABLLM_TRANSCRIPT_DEVICE_TOKEN[0]) {
        ESP_LOGE(TAG, "enabled without URL or device token");
        return ESP_ERR_INVALID_STATE;
    }
    s_queue = xQueueCreate(TRANSCRIPT_QUEUE_DEPTH, sizeof(transcript_event_t));
    if (!s_queue) return ESP_ERR_NO_MEM;
    BaseType_t created = xTaskCreate(transcript_log_task, "transcript_log", 6144, NULL, 4, NULL);
    if (created != pdPASS) {
        vQueueDelete(s_queue);
        s_queue = NULL;
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "background transcript logging enabled for device=%s",
             CONFIG_WEARABLLM_TRANSCRIPT_DEVICE_ID);
    return ESP_OK;
}

void wearabllm_transcript_log_enqueue(uint32_t interaction_id, const char *command,
    const char *transcript, const char *reply, const char *capture_source)
{
    if (!s_queue || !transcript || !transcript[0]) return;
    transcript_event_t event = {.interaction_id = interaction_id};
    snprintf(event.command, sizeof(event.command), "%s", command ? command : "");
    snprintf(event.transcript, sizeof(event.transcript), "%s", transcript);
    snprintf(event.reply, sizeof(event.reply), "%s", reply ? reply : "");
    snprintf(event.capture_source, sizeof(event.capture_source), "%s",
             capture_source ? capture_source : "unknown");
    if (xQueueSend(s_queue, &event, 0) != pdTRUE) {
        ESP_LOGW(TAG, "queue full; dropped interaction #" PRIu32, interaction_id);
    }
}
