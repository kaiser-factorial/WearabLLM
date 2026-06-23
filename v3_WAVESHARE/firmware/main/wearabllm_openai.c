#include "wearabllm_openai.h"

#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"

#define JSON_RESPONSE_MAX 8192
#define OPENAI_TIMEOUT_MS 60000

#ifndef CONFIG_WEARABLLM_TTS_MAX_BYTES
#define CONFIG_WEARABLLM_TTS_MAX_BYTES 131072
#endif

static const char *TAG = "wearabllm_openai";
static char s_previous_response_id[96];

static esp_err_t set_auth(esp_http_client_handle_t client)
{
    size_t length = strlen(CONFIG_WEARABLLM_OPENAI_API_KEY) + 8;
    char *authorization = malloc(length);
    if (!authorization) return ESP_ERR_NO_MEM;
    snprintf(authorization, length, "Bearer %s", CONFIG_WEARABLLM_OPENAI_API_KEY);
    esp_err_t err = esp_http_client_set_header(client, "Authorization", authorization);
    free(authorization);
    return err;
}

static esp_err_t read_response(esp_http_client_handle_t client, uint8_t *buffer, size_t capacity, size_t *length)
{
    int content_length = esp_http_client_fetch_headers(client);
    if (content_length > (int)capacity) return ESP_ERR_INVALID_SIZE;
    size_t offset = 0;
    while (offset < capacity) {
        int read = esp_http_client_read(client, (char *)buffer + offset, capacity - offset);
        if (read < 0) return ESP_FAIL;
        if (read == 0) break;
        offset += read;
    }
    *length = offset;
    int status = esp_http_client_get_status_code(client);
    if (status < 200 || status >= 300) {
        ESP_LOGE(TAG, "OpenAI HTTP status=%d body=%.*s", status, (int)offset, (char *)buffer);
        return ESP_FAIL;
    }
    return ESP_OK;
}

static esp_err_t post_json(const char *url, const char *json, uint8_t *buffer, size_t capacity, size_t *length)
{
    esp_http_client_config_t config = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = OPENAI_TIMEOUT_MS,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) return ESP_FAIL;
    esp_err_t err = set_auth(client);
    if (err == ESP_OK) err = esp_http_client_set_header(client, "Content-Type", "application/json");
    if (err == ESP_OK) err = esp_http_client_open(client, strlen(json));
    if (err == ESP_OK && esp_http_client_write(client, json, strlen(json)) != (int)strlen(json)) err = ESP_FAIL;
    if (err == ESP_OK) err = read_response(client, buffer, capacity, length);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return err;
}

static esp_err_t transcribe(const uint8_t *wav, size_t wav_len, char *out, size_t out_len)
{
    char boundary[48];
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(boundary, sizeof(boundary), "----wearabllm%02x%02x%02x%02x%02x%02x", MAC2STR(mac));
    char prefix[384];
    int prefix_len = snprintf(prefix, sizeof(prefix),
        "--%s\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n%s\r\n"
        "--%s\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n"
        "--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"capture.wav\"\r\n"
        "Content-Type: audio/wav\r\n\r\n",
        boundary, CONFIG_WEARABLLM_OPENAI_STT_MODEL, boundary, boundary);
    char suffix[64];
    int suffix_len = snprintf(suffix, sizeof(suffix), "\r\n--%s--\r\n", boundary);
    if (prefix_len <= 0 || suffix_len <= 0) return ESP_FAIL;

    esp_http_client_config_t config = {
        .url = "https://api.openai.com/v1/audio/transcriptions",
        .method = HTTP_METHOD_POST,
        .timeout_ms = OPENAI_TIMEOUT_MS,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) return ESP_FAIL;
    char content_type[96];
    snprintf(content_type, sizeof(content_type), "multipart/form-data; boundary=%s", boundary);
    esp_err_t err = set_auth(client);
    if (err == ESP_OK) err = esp_http_client_set_header(client, "Content-Type", content_type);
    if (err == ESP_OK) err = esp_http_client_open(client, prefix_len + wav_len + suffix_len);
    if (err == ESP_OK && esp_http_client_write(client, prefix, prefix_len) != prefix_len) err = ESP_FAIL;
    if (err == ESP_OK && esp_http_client_write(client, (const char *)wav, wav_len) != (int)wav_len) err = ESP_FAIL;
    if (err == ESP_OK && esp_http_client_write(client, suffix, suffix_len) != suffix_len) err = ESP_FAIL;

    char response[1024] = {0};
    size_t response_len = 0;
    if (err == ESP_OK) err = read_response(client, (uint8_t *)response, sizeof(response) - 1, &response_len);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK) return err;
    cJSON *root = cJSON_Parse(response);
    cJSON *text = root ? cJSON_GetObjectItemCaseSensitive(root, "text") : NULL;
    if (!cJSON_IsString(text)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    snprintf(out, out_len, "%s", text->valuestring);
    cJSON_Delete(root);
    return ESP_OK;
}

static esp_err_t generate_reply(const char *transcript, char *command, size_t command_len, char *reply, size_t reply_len)
{
    cJSON *request = cJSON_CreateObject();
    cJSON_AddStringToObject(request, "model", CONFIG_WEARABLLM_OPENAI_LLM_MODEL);
    cJSON_AddStringToObject(request, "instructions",
        "You are WearabLLM. Return exactly two lines. Line 1 is one LED code from GS, GP, GC, RS, RF, YP, BS, PS, PP. Line 2 is a concise spoken answer. No markdown or labels.");
    cJSON_AddStringToObject(request, "input", transcript);
    cJSON_AddNumberToObject(request, "max_output_tokens", 180);
    cJSON_AddBoolToObject(request, "store", true);
    if (s_previous_response_id[0]) cJSON_AddStringToObject(request, "previous_response_id", s_previous_response_id);
    char *json = cJSON_PrintUnformatted(request);
    cJSON_Delete(request);
    if (!json) return ESP_ERR_NO_MEM;

    char *response = calloc(1, JSON_RESPONSE_MAX);
    if (!response) {
        free(json);
        return ESP_ERR_NO_MEM;
    }
    size_t response_len = 0;
    esp_err_t err = post_json("https://api.openai.com/v1/responses", json, (uint8_t *)response,
                              JSON_RESPONSE_MAX - 1, &response_len);
    free(json);
    if (err != ESP_OK) {
        free(response);
        return err;
    }

    cJSON *root = cJSON_Parse(response);
    free(response);
    cJSON *id = root ? cJSON_GetObjectItemCaseSensitive(root, "id") : NULL;
    if (cJSON_IsString(id)) snprintf(s_previous_response_id, sizeof(s_previous_response_id), "%s", id->valuestring);
    const char *output_text = NULL;
    cJSON *output = root ? cJSON_GetObjectItemCaseSensitive(root, "output") : NULL;
    cJSON *item = NULL;
    cJSON_ArrayForEach(item, output) {
        cJSON *content = cJSON_GetObjectItemCaseSensitive(item, "content");
        cJSON *part = NULL;
        cJSON_ArrayForEach(part, content) {
            cJSON *type = cJSON_GetObjectItemCaseSensitive(part, "type");
            cJSON *text = cJSON_GetObjectItemCaseSensitive(part, "text");
            if (cJSON_IsString(type) && strcmp(type->valuestring, "output_text") == 0 && cJSON_IsString(text)) {
                output_text = text->valuestring;
                break;
            }
        }
        if (output_text) break;
    }
    if (!output_text || strlen(output_text) < 3 || output_text[2] != '\n') {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }
    snprintf(command, command_len, "%.2s", output_text);
    snprintf(reply, reply_len, "%s", output_text + 3);
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t wearabllm_openai_query(const uint8_t *wav, size_t wav_len, char *command_out,
    size_t command_len, char *transcript_out, size_t transcript_len, char *reply_out, size_t reply_len)
{
    if (!CONFIG_WEARABLLM_OPENAI_API_KEY[0]) return ESP_ERR_INVALID_STATE;
    esp_err_t err = transcribe(wav, wav_len, transcript_out, transcript_len);
    if (err != ESP_OK) return err;
    return generate_reply(transcript_out, command_out, command_len, reply_out, reply_len);
}

esp_err_t wearabllm_openai_tts(const char *reply, uint8_t **out_data, size_t *out_len)
{
    if (!reply || !reply[0]) return ESP_ERR_INVALID_ARG;
    cJSON *request = cJSON_CreateObject();
    cJSON_AddStringToObject(request, "model", CONFIG_WEARABLLM_OPENAI_TTS_MODEL);
    cJSON_AddStringToObject(request, "voice", CONFIG_WEARABLLM_OPENAI_TTS_VOICE);
    cJSON_AddStringToObject(request, "input", reply);
    cJSON_AddStringToObject(request, "response_format", "wav");
    cJSON_AddStringToObject(request, "instructions", "A mysterious noir detective: cool, deliberate, reassuring, and concise.");
    char *json = cJSON_PrintUnformatted(request);
    cJSON_Delete(request);
    if (!json) return ESP_ERR_NO_MEM;
    uint8_t *response = malloc(CONFIG_WEARABLLM_TTS_MAX_BYTES);
    if (!response) {
        free(json);
        return ESP_ERR_NO_MEM;
    }
    size_t response_len = 0;
    esp_err_t err = post_json("https://api.openai.com/v1/audio/speech", json, response,
                              CONFIG_WEARABLLM_TTS_MAX_BYTES, &response_len);
    free(json);
    if (err != ESP_OK) {
        free(response);
        return err;
    }
    *out_data = response;
    *out_len = response_len;
    return ESP_OK;
}
