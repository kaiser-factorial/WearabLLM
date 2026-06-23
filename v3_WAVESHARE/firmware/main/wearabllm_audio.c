#include "wearabllm_audio.h"

#include <inttypes.h>
#include <stdlib.h>
#include <string.h>

#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_codec_dev_os.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "wearabllm_audio";

#define I2C_PORT 0
#define GPIO_I2C_SCL GPIO_NUM_10
#define GPIO_I2C_SDA GPIO_NUM_11

#define I2S_PORT I2S_NUM_1
#define GPIO_I2S_MCLK GPIO_NUM_12
#define GPIO_I2S_BCLK GPIO_NUM_13
#define GPIO_I2S_WS GPIO_NUM_14
#define GPIO_I2S_DIN GPIO_NUM_15
#define GPIO_I2S_DOUT GPIO_NUM_16

#define RECORD_VOLUME 30.0
#define PLAYBACK_CHANNELS 2
#define PLAYBACK_BITS_PER_SAMPLE 32
#define PLAYBACK_PA_PIN GPIO_NUM_NC
#define TCA9555_I2C_ADDRESS 0x20
#define TCA9555_INPUT_PORT_1_REG 0x01
#define TCA9555_OUTPUT_PORT_1_REG 0x03
#define TCA9555_CONFIG_PORT_1_REG 0x07
#define TCA9555_SPEAKER_PA_MASK (1U << 0) /* EXIO8 */
#define TCA9555_VOLUME_UP_MASK (1U << 1) /* EXIO9 / K1 */
#define TCA9555_VOLUME_DOWN_MASK (1U << 3) /* EXIO11 / K3 */
#define TCA9555_VOLUME_BUTTON_MASK (TCA9555_VOLUME_UP_MASK | TCA9555_VOLUME_DOWN_MASK)
#define I2C_TIMEOUT_MS 100
#define WAV_HEADER_BYTES 44
#define I2S_READ_FRAMES 256
#define ES7210_TDM_MIC_LANES 4
#define WAV_PCM_FORMAT 1
#define PLAYBACK_CHUNK_FRAMES 256
#define SILENCE_PEAK_THRESHOLD 128

static i2c_master_bus_handle_t s_i2c_bus;
static i2s_chan_handle_t s_i2s_rx;
static i2s_chan_handle_t s_i2s_tx;
static const audio_codec_data_if_t *s_record_data_if;
static const audio_codec_ctrl_if_t *s_record_ctrl_if;
static const audio_codec_if_t *s_record_codec_if;
static esp_codec_dev_handle_t s_record_dev;
#if CONFIG_WEARABLLM_AUDIO_OUT_ENABLED
static const audio_codec_data_if_t *s_play_data_if;
static const audio_codec_ctrl_if_t *s_play_ctrl_if;
static const audio_codec_gpio_if_t *s_play_gpio_if;
static const audio_codec_if_t *s_play_codec_if;
static esp_codec_dev_handle_t s_play_dev;
static i2c_master_dev_handle_t s_tca9555_dev;
static bool s_play_ready;
static bool s_volume_buttons_ready;
static uint8_t s_output_volume = CONFIG_WEARABLLM_AUDIO_OUT_VOLUME;
#endif
static bool s_audio_ready;
static int32_t *s_mono_read_buffer;
static size_t s_mono_read_capacity_frames;

static void write_wav_header(uint8_t *buf, uint32_t pcm_bytes)
{
    uint32_t chunk_size = 36 + pcm_bytes;
    uint32_t sample_rate = WEARABLLM_AUDIO_SAMPLE_RATE;
    uint32_t byte_rate = WEARABLLM_AUDIO_SAMPLE_RATE * 1 * 16 / 8;
    uint16_t block_align = 1 * 16 / 8;
    uint16_t audio_format = 1;
    uint16_t channels = 1;
    uint16_t bits_per_sample = 16;
    uint32_t subchunk1_size = 16;

    memcpy(buf + 0, "RIFF", 4);
    memcpy(buf + 4, &chunk_size, 4);
    memcpy(buf + 8, "WAVEfmt ", 8);
    memcpy(buf + 16, &subchunk1_size, 4);
    memcpy(buf + 20, &audio_format, 2);
    memcpy(buf + 22, &channels, 2);
    memcpy(buf + 24, &sample_rate, 4);
    memcpy(buf + 28, &byte_rate, 4);
    memcpy(buf + 32, &block_align, 2);
    memcpy(buf + 34, &bits_per_sample, 2);
    memcpy(buf + 36, "data", 4);
    memcpy(buf + 40, &pcm_bytes, 4);
}

static void log_pcm16_capture_stats(const int16_t *pcm, uint32_t sample_count)
{
    if (!pcm || sample_count == 0) {
        ESP_LOGW(TAG, "capture stats unavailable: no PCM samples");
        return;
    }

    uint32_t peak_abs = 0;
    uint64_t sum_squares = 0;
    for (uint32_t i = 0; i < sample_count; i++) {
        int32_t sample = pcm[i];
        uint32_t abs_sample = (uint32_t)(sample < 0 ? -sample : sample);
        if (abs_sample > peak_abs) {
            peak_abs = abs_sample;
        }
        sum_squares += (uint64_t)sample * (uint64_t)sample;
    }

    uint32_t mean_square = (uint32_t)(sum_squares / sample_count);
    uint32_t rms = 0;
    while ((rms + 1) <= 32767 && (uint32_t)((rms + 1) * (rms + 1)) <= mean_square) {
        rms++;
    }

    uint32_t duration_ms = (sample_count * 1000U) / WEARABLLM_AUDIO_SAMPLE_RATE;
    ESP_LOGI(TAG,
             "capture stats: duration=%" PRIu32 " ms samples=%" PRIu32 " peak=%" PRIu32 " rms=%" PRIu32 " appears_silent=%s",
             duration_ms,
             sample_count,
             peak_abs,
             rms,
             peak_abs < SILENCE_PEAK_THRESHOLD ? "yes" : "no");
}

static void log_tdm_lane_stats(
    const uint32_t lane_peaks[ES7210_TDM_MIC_LANES],
    const uint64_t lane_sum_squares[ES7210_TDM_MIC_LANES],
    uint32_t frame_count)
{
    if (frame_count == 0) {
        return;
    }

    for (int lane = 0; lane < ES7210_TDM_MIC_LANES; lane++) {
        uint32_t mean_square = (uint32_t)(lane_sum_squares[lane] / frame_count);
        uint32_t rms = 0;
        while ((rms + 1) <= 32767 && (uint32_t)((rms + 1) * (rms + 1)) <= mean_square) {
            rms++;
        }
        ESP_LOGI(TAG,
                 "ES7210 packed lane %d: peak=%" PRIu32 " rms=%" PRIu32 " appears_silent=%s",
                 lane,
                 lane_peaks[lane],
                 rms,
                 lane_peaks[lane] < SILENCE_PEAK_THRESHOLD ? "yes" : "no");
    }
}

#if CONFIG_WEARABLLM_AUDIO_OUT_ENABLED
static esp_err_t tca9555_init(void)
{
    if (s_tca9555_dev) {
        return ESP_OK;
    }

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = TCA9555_I2C_ADDRESS,
        .scl_speed_hz = 400000,
    };
    return i2c_master_bus_add_device(s_i2c_bus, &dev_cfg, &s_tca9555_dev);
}

static esp_err_t tca9555_read_reg(uint8_t reg, uint8_t *value)
{
    ESP_RETURN_ON_FALSE(value, ESP_ERR_INVALID_ARG, TAG, "TCA9555 read value is required");
    ESP_RETURN_ON_ERROR(tca9555_init(), TAG, "TCA9555 init failed");
    return i2c_master_transmit_receive(
        s_tca9555_dev, &reg, sizeof(reg), value, sizeof(*value), I2C_TIMEOUT_MS);
}

static esp_err_t tca9555_write_reg(uint8_t reg, uint8_t value)
{
    ESP_RETURN_ON_ERROR(tca9555_init(), TAG, "TCA9555 init failed");
    uint8_t command[] = {reg, value};
    return i2c_master_transmit(s_tca9555_dev, command, sizeof(command), I2C_TIMEOUT_MS);
}

static esp_err_t set_speaker_pa_enabled(bool enabled)
{
    uint8_t output = 0;
    ESP_RETURN_ON_ERROR(
        tca9555_read_reg(TCA9555_OUTPUT_PORT_1_REG, &output), TAG, "TCA9555 output read failed");
    if (enabled) {
        output |= TCA9555_SPEAKER_PA_MASK;
    } else {
        output &= (uint8_t)~TCA9555_SPEAKER_PA_MASK;
    }
    ESP_RETURN_ON_ERROR(
        tca9555_write_reg(TCA9555_OUTPUT_PORT_1_REG, output), TAG, "TCA9555 output write failed");

    uint8_t config = 0;
    ESP_RETURN_ON_ERROR(
        tca9555_read_reg(TCA9555_CONFIG_PORT_1_REG, &config), TAG, "TCA9555 config read failed");
    config &= (uint8_t)~TCA9555_SPEAKER_PA_MASK;
    ESP_RETURN_ON_ERROR(
        tca9555_write_reg(TCA9555_CONFIG_PORT_1_REG, config), TAG, "TCA9555 config write failed");

    vTaskDelay(pdMS_TO_TICKS(50));
    ESP_LOGI(TAG, "speaker power amplifier %s via TCA9555 EXIO8", enabled ? "enabled" : "disabled");
    return ESP_OK;
}

static esp_err_t init_volume_buttons(void)
{
    if (s_volume_buttons_ready) {
        return ESP_OK;
    }

    uint8_t config = 0;
    ESP_RETURN_ON_ERROR(
        tca9555_read_reg(TCA9555_CONFIG_PORT_1_REG, &config), TAG, "TCA9555 config read failed");
    config |= TCA9555_VOLUME_BUTTON_MASK;
    ESP_RETURN_ON_ERROR(
        tca9555_write_reg(TCA9555_CONFIG_PORT_1_REG, config), TAG, "TCA9555 button config failed");
    s_volume_buttons_ready = true;
    ESP_LOGI(TAG, "volume buttons ready: K1/+ and K3/-");
    return ESP_OK;
}

static uint16_t read_le16(const uint8_t *data)
{
    return (uint16_t)data[0] | ((uint16_t)data[1] << 8);
}

static uint32_t read_le32(const uint8_t *data)
{
    return (uint32_t)data[0] |
           ((uint32_t)data[1] << 8) |
           ((uint32_t)data[2] << 16) |
           ((uint32_t)data[3] << 24);
}

static esp_err_t parse_wav_pcm16_mono(
    const uint8_t *wav_data,
    size_t wav_len,
    const uint8_t **pcm_data,
    size_t *pcm_len,
    uint32_t *sample_rate)
{
    ESP_RETURN_ON_FALSE(wav_data && pcm_data && pcm_len && sample_rate,
                        ESP_ERR_INVALID_ARG,
                        TAG,
                        "invalid wav parser args");
    ESP_RETURN_ON_FALSE(wav_len >= WAV_HEADER_BYTES, ESP_ERR_INVALID_SIZE, TAG, "wav too small");
    ESP_RETURN_ON_FALSE(memcmp(wav_data, "RIFF", 4) == 0 && memcmp(wav_data + 8, "WAVE", 4) == 0,
                        ESP_ERR_INVALID_RESPONSE,
                        TAG,
                        "not a RIFF/WAVE file");

    bool saw_fmt = false;
    bool saw_data = false;
    uint16_t audio_format = 0;
    uint16_t channels = 0;
    uint16_t bits_per_sample = 0;
    uint32_t rate = 0;
    const uint8_t *data_start = NULL;
    size_t data_len = 0;

    size_t offset = 12;
    while (offset + 8 <= wav_len) {
        const uint8_t *chunk = wav_data + offset;
        uint32_t chunk_size = read_le32(chunk + 4);
        size_t payload_offset = offset + 8;
        if (payload_offset + chunk_size > wav_len) {
            return ESP_ERR_INVALID_SIZE;
        }

        if (memcmp(chunk, "fmt ", 4) == 0) {
            ESP_RETURN_ON_FALSE(chunk_size >= 16, ESP_ERR_INVALID_SIZE, TAG, "fmt chunk too small");
            audio_format = read_le16(wav_data + payload_offset + 0);
            channels = read_le16(wav_data + payload_offset + 2);
            rate = read_le32(wav_data + payload_offset + 4);
            bits_per_sample = read_le16(wav_data + payload_offset + 14);
            saw_fmt = true;
        } else if (memcmp(chunk, "data", 4) == 0) {
            data_start = wav_data + payload_offset;
            data_len = chunk_size;
            saw_data = true;
        }

        offset = payload_offset + chunk_size + (chunk_size & 1U);
    }

    ESP_RETURN_ON_FALSE(saw_fmt && saw_data, ESP_ERR_INVALID_RESPONSE, TAG, "wav missing fmt/data");
    ESP_RETURN_ON_FALSE(audio_format == WAV_PCM_FORMAT, ESP_ERR_NOT_SUPPORTED, TAG, "wav is not PCM");
    ESP_RETURN_ON_FALSE(channels == 1, ESP_ERR_NOT_SUPPORTED, TAG, "wav is not mono");
    ESP_RETURN_ON_FALSE(bits_per_sample == 16, ESP_ERR_NOT_SUPPORTED, TAG, "wav is not 16-bit");
    ESP_RETURN_ON_FALSE(rate == WEARABLLM_AUDIO_SAMPLE_RATE,
                        ESP_ERR_NOT_SUPPORTED,
                        TAG,
                        "wav sample rate is %" PRIu32 ", expected %d",
                        rate,
                        WEARABLLM_AUDIO_SAMPLE_RATE);

    *pcm_data = data_start;
    *pcm_len = data_len;
    *sample_rate = rate;
    return ESP_OK;
}
#endif

static esp_err_t init_i2c(void)
{
    if (s_i2c_bus) {
        return ESP_OK;
    }

    i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_PORT,
        .sda_io_num = GPIO_I2C_SDA,
        .scl_io_num = GPIO_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
    };
    return i2c_new_master_bus(&bus_config, &s_i2c_bus);
}

static esp_err_t init_i2s(void)
{
    if (s_i2s_rx) {
        return ESP_OK;
    }

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_PORT, I2S_ROLE_MASTER);
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_i2s_tx, &s_i2s_rx), TAG, "i2s_new_channel failed");

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(WEARABLLM_AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(32, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = GPIO_I2S_MCLK,
            .bclk = GPIO_I2S_BCLK,
            .ws = GPIO_I2S_WS,
            .dout = GPIO_I2S_DOUT,
            .din = GPIO_I2S_DIN,
        },
    };

    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_tx, &std_cfg), TAG, "i2s tx init failed");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_rx, &std_cfg), TAG, "i2s rx init failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_tx), TAG, "i2s tx enable failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_rx), TAG, "i2s rx enable failed");
    return ESP_OK;
}

#if CONFIG_WEARABLLM_AUDIO_OUT_ENABLED
static esp_err_t write_playback_silence(void)
{
    enum { SILENCE_FRAMES = WEARABLLM_AUDIO_SAMPLE_RATE / 50 };
    int32_t silence[SILENCE_FRAMES * PLAYBACK_CHANNELS] = {0};
    return esp_codec_dev_write(s_play_dev, silence, sizeof(silence));
}

static esp_err_t begin_playback(void)
{
    ESP_RETURN_ON_FALSE(esp_codec_dev_set_out_mute(s_play_dev, false) == ESP_CODEC_DEV_OK,
                        ESP_FAIL,
                        TAG,
                        "speaker unmute failed");
    ESP_RETURN_ON_ERROR(set_speaker_pa_enabled(true), TAG, "speaker amplifier enable failed");
    vTaskDelay(pdMS_TO_TICKS(10));
    return ESP_OK;
}

static esp_err_t end_playback(void)
{
    esp_err_t ret = ESP_OK;
    if (esp_codec_dev_set_out_mute(s_play_dev, true) != ESP_CODEC_DEV_OK) {
        ESP_LOGW(TAG, "speaker mute failed");
        ret = ESP_FAIL;
    }
    esp_err_t pa_ret = set_speaker_pa_enabled(false);
    return ret != ESP_OK ? ret : pa_ret;
}

static esp_err_t init_playback(void)
{
    if (s_play_ready) {
        return ESP_OK;
    }

    ESP_RETURN_ON_ERROR(init_i2c(), TAG, "i2c init failed");
    ESP_RETURN_ON_ERROR(init_i2s(), TAG, "i2s init failed");

    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = I2S_PORT,
        .rx_handle = NULL,
        .tx_handle = s_i2s_tx,
    };
    s_play_data_if = audio_codec_new_i2s_data(&i2s_cfg);
    ESP_RETURN_ON_FALSE(s_play_data_if, ESP_FAIL, TAG, "playback i2s data init failed");

    audio_codec_i2c_cfg_t i2c_cfg = {
        .addr = ES8311_CODEC_DEFAULT_ADDR,
        .bus_handle = s_i2c_bus,
    };
    s_play_ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    ESP_RETURN_ON_FALSE(s_play_ctrl_if, ESP_FAIL, TAG, "playback i2c ctrl init failed");

    s_play_gpio_if = audio_codec_new_gpio();
    ESP_RETURN_ON_FALSE(s_play_gpio_if, ESP_FAIL, TAG, "playback gpio init failed");

    es8311_codec_cfg_t es8311_cfg = {
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .ctrl_if = s_play_ctrl_if,
        .gpio_if = s_play_gpio_if,
        .pa_pin = PLAYBACK_PA_PIN,
        .use_mclk = false,
    };
    s_play_codec_if = es8311_codec_new(&es8311_cfg);
    ESP_RETURN_ON_FALSE(s_play_codec_if, ESP_FAIL, TAG, "ES8311 codec init failed");

    esp_codec_dev_cfg_t dev_cfg = {
        .codec_if = s_play_codec_if,
        .data_if = s_play_data_if,
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
    };
    s_play_dev = esp_codec_dev_new(&dev_cfg);
    ESP_RETURN_ON_FALSE(s_play_dev, ESP_FAIL, TAG, "playback device init failed");

    esp_codec_dev_sample_info_t fs = {
        .sample_rate = WEARABLLM_AUDIO_SAMPLE_RATE,
        .channel = PLAYBACK_CHANNELS,
        .bits_per_sample = PLAYBACK_BITS_PER_SAMPLE,
    };
    ESP_RETURN_ON_ERROR(esp_codec_dev_open(s_play_dev, &fs), TAG, "playback device open failed");
    ESP_RETURN_ON_ERROR(esp_codec_dev_set_out_vol(s_play_dev, s_output_volume),
                        TAG,
                        "playback volume set failed");
    ESP_RETURN_ON_FALSE(esp_codec_dev_set_out_mute(s_play_dev, true) == ESP_CODEC_DEV_OK,
                        ESP_FAIL,
                        TAG,
                        "initial speaker mute failed");
    ESP_RETURN_ON_ERROR(set_speaker_pa_enabled(false), TAG, "initial speaker amplifier disable failed");

    s_play_ready = true;
    ESP_LOGI(TAG, "ES8311 speaker output ready");
    return ESP_OK;
}
#endif

esp_err_t wearabllm_audio_init(void)
{
    if (s_audio_ready) {
        return ESP_OK;
    }

    ESP_RETURN_ON_ERROR(init_i2c(), TAG, "i2c init failed");
    ESP_RETURN_ON_ERROR(init_i2s(), TAG, "i2s init failed");

    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = I2S_PORT,
        .rx_handle = s_i2s_rx,
        .tx_handle = NULL,
    };
    s_record_data_if = audio_codec_new_i2s_data(&i2s_cfg);
    ESP_RETURN_ON_FALSE(s_record_data_if, ESP_FAIL, TAG, "audio_codec_new_i2s_data failed");

    audio_codec_i2c_cfg_t i2c_cfg = {
        .addr = ES7210_CODEC_DEFAULT_ADDR,
        .bus_handle = s_i2c_bus,
    };
    s_record_ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    ESP_RETURN_ON_FALSE(s_record_ctrl_if, ESP_FAIL, TAG, "audio_codec_new_i2c_ctrl failed");

    es7210_codec_cfg_t es7210_cfg = {
        .ctrl_if = s_record_ctrl_if,
        .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2 | ES7210_SEL_MIC3 | ES7210_SEL_MIC4,
    };
    s_record_codec_if = es7210_codec_new(&es7210_cfg);
    ESP_RETURN_ON_FALSE(s_record_codec_if, ESP_FAIL, TAG, "es7210 codec init failed");

    esp_codec_dev_cfg_t dev_cfg = {
        .codec_if = s_record_codec_if,
        .data_if = s_record_data_if,
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
    };
    s_record_dev = esp_codec_dev_new(&dev_cfg);
    ESP_RETURN_ON_FALSE(s_record_dev, ESP_FAIL, TAG, "esp_codec_dev_new failed");

    esp_codec_dev_sample_info_t fs = {
        .sample_rate = WEARABLLM_AUDIO_SAMPLE_RATE,
        .channel = 2,
        .bits_per_sample = 32,
    };
    ESP_RETURN_ON_ERROR(esp_codec_dev_open(s_record_dev, &fs), TAG, "esp_codec_dev_open failed");

    for (int i = 0; i < ES7210_TDM_MIC_LANES; i++) {
        ESP_RETURN_ON_ERROR(
            esp_codec_dev_set_in_channel_gain(
                s_record_dev, ESP_CODEC_DEV_MAKE_CHANNEL_MASK(i), RECORD_VOLUME),
            TAG,
            "microphone lane %d gain set failed",
            i);
    }

    s_audio_ready = true;
    ESP_LOGI(TAG,
             "ES7210 microphone capture ready: %d x 16-bit TDM lanes packed into 2 x 32-bit I2S slots",
             ES7210_TDM_MIC_LANES);
#if CONFIG_WEARABLLM_AUDIO_OUT_ENABLED
    esp_err_t play_err = init_playback();
    if (play_err != ESP_OK) {
        ESP_LOGW(TAG, "speaker output init failed: %s", esp_err_to_name(play_err));
    }
#endif
    return ESP_OK;
}

esp_err_t wearabllm_audio_play_tone(uint32_t frequency_hz, uint32_t milliseconds)
{
#if CONFIG_WEARABLLM_AUDIO_OUT_ENABLED
    if (frequency_hz == 0 || milliseconds == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    ESP_RETURN_ON_ERROR(init_playback(), TAG, "speaker output init failed");
    ESP_RETURN_ON_ERROR(begin_playback(), TAG, "speaker playback start failed");

    const uint32_t frame_count = (WEARABLLM_AUDIO_SAMPLE_RATE * milliseconds) / 1000;
    const uint32_t period_frames = WEARABLLM_AUDIO_SAMPLE_RATE / frequency_hz;
    ESP_RETURN_ON_FALSE(period_frames >= 2, ESP_ERR_INVALID_ARG, TAG, "tone frequency too high");

    int32_t *pcm = calloc(frame_count * PLAYBACK_CHANNELS, sizeof(int32_t));
    ESP_RETURN_ON_FALSE(pcm, ESP_ERR_NO_MEM, TAG, "tone allocation failed");

    const int32_t amplitude = INT32_MAX / 12;
    for (uint32_t i = 0; i < frame_count; i++) {
        int32_t sample = ((i % period_frames) < (period_frames / 2)) ? amplitude : -amplitude;
        pcm[i * 2 + 0] = sample;
        pcm[i * 2 + 1] = sample;
    }

    esp_err_t ret = esp_codec_dev_write(s_play_dev, pcm, frame_count * PLAYBACK_CHANNELS * sizeof(int32_t));
    free(pcm);
    if (ret == ESP_OK) {
        ret = write_playback_silence();
    }
    vTaskDelay(pdMS_TO_TICKS(milliseconds + 25));
    esp_err_t end_ret = end_playback();
    return ret != ESP_OK ? ret : end_ret;
#else
    (void)frequency_hz;
    (void)milliseconds;
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

esp_err_t wearabllm_audio_play_wav(const uint8_t *wav_data, size_t wav_len)
{
#if CONFIG_WEARABLLM_AUDIO_OUT_ENABLED
    const uint8_t *pcm_data = NULL;
    size_t pcm_len = 0;
    uint32_t sample_rate = 0;
    ESP_RETURN_ON_ERROR(parse_wav_pcm16_mono(wav_data, wav_len, &pcm_data, &pcm_len, &sample_rate),
                        TAG,
                        "unsupported wav");
    ESP_RETURN_ON_ERROR(init_playback(), TAG, "speaker output init failed");

    int32_t *playback = calloc(PLAYBACK_CHUNK_FRAMES * PLAYBACK_CHANNELS, sizeof(int32_t));
    ESP_RETURN_ON_FALSE(playback, ESP_ERR_NO_MEM, TAG, "wav playback allocation failed");
    esp_err_t start_ret = begin_playback();
    if (start_ret != ESP_OK) {
        free(playback);
        return start_ret;
    }

    size_t offset = 0;
    while (offset < pcm_len) {
        size_t pcm_bytes = pcm_len - offset;
        if (pcm_bytes > PLAYBACK_CHUNK_FRAMES * sizeof(int16_t)) {
            pcm_bytes = PLAYBACK_CHUNK_FRAMES * sizeof(int16_t);
        }
        size_t frames = pcm_bytes / sizeof(int16_t);
        const int16_t *pcm = (const int16_t *)(pcm_data + offset);

        for (size_t i = 0; i < frames; i++) {
            int32_t sample = ((int32_t)pcm[i]) << 16;
            playback[i * 2 + 0] = sample;
            playback[i * 2 + 1] = sample;
        }

        esp_err_t ret = esp_codec_dev_write(s_play_dev, playback, frames * PLAYBACK_CHANNELS * sizeof(int32_t));
        if (ret != ESP_OK) {
            free(playback);
            end_playback();
            return ret;
        }
        offset += frames * sizeof(int16_t);
    }

    free(playback);
    esp_err_t ret = write_playback_silence();
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_err_t end_ret = end_playback();
    ESP_RETURN_ON_ERROR(ret, TAG, "wav silence tail failed");
    ESP_RETURN_ON_ERROR(end_ret, TAG, "speaker playback stop failed");
    ESP_LOGI(TAG, "played %" PRIu32 " Hz mono WAV, %u bytes PCM", sample_rate, (unsigned)pcm_len);
    return ESP_OK;
#else
    (void)wav_data;
    (void)wav_len;
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

esp_err_t wearabllm_audio_set_output_volume(uint8_t volume)
{
#if CONFIG_WEARABLLM_AUDIO_OUT_ENABLED
    ESP_RETURN_ON_FALSE(volume <= 100, ESP_ERR_INVALID_ARG, TAG, "volume must be 0..100");
    ESP_RETURN_ON_ERROR(init_playback(), TAG, "speaker output init failed");
    ESP_RETURN_ON_ERROR(esp_codec_dev_set_out_vol(s_play_dev, volume), TAG, "playback volume set failed");
    s_output_volume = volume;
    ESP_LOGI(TAG, "speaker volume=%u", (unsigned)volume);
    return ESP_OK;
#else
    (void)volume;
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

uint8_t wearabllm_audio_get_output_volume(void)
{
#if CONFIG_WEARABLLM_AUDIO_OUT_ENABLED
    return s_output_volume;
#else
    return 0;
#endif
}

esp_err_t wearabllm_audio_read_volume_buttons(uint8_t *pressed_mask)
{
#if CONFIG_WEARABLLM_AUDIO_OUT_ENABLED
    ESP_RETURN_ON_FALSE(pressed_mask, ESP_ERR_INVALID_ARG, TAG, "button mask is required");
    ESP_RETURN_ON_ERROR(init_volume_buttons(), TAG, "volume button init failed");

    uint8_t levels = 0;
    ESP_RETURN_ON_ERROR(
        tca9555_read_reg(TCA9555_INPUT_PORT_1_REG, &levels), TAG, "TCA9555 button read failed");
    *pressed_mask = 0;
    if ((levels & TCA9555_VOLUME_UP_MASK) == 0) {
        *pressed_mask |= WEARABLLM_AUDIO_BUTTON_VOLUME_UP;
    }
    if ((levels & TCA9555_VOLUME_DOWN_MASK) == 0) {
        *pressed_mask |= WEARABLLM_AUDIO_BUTTON_VOLUME_DOWN;
    }
    return ESP_OK;
#else
    (void)pressed_mask;
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

esp_err_t wearabllm_audio_make_silent_wav(uint32_t milliseconds, uint8_t **out_data, size_t *out_len)
{
    uint32_t pcm_bytes = (WEARABLLM_AUDIO_SAMPLE_RATE * 2 * milliseconds) / 1000;
    uint8_t *wav = calloc(1, WAV_HEADER_BYTES + pcm_bytes);
    ESP_RETURN_ON_FALSE(wav, ESP_ERR_NO_MEM, TAG, "silent wav allocation failed");

    write_wav_header(wav, pcm_bytes);
    *out_data = wav;
    *out_len = WAV_HEADER_BYTES + pcm_bytes;
    return ESP_OK;
}

esp_err_t wearabllm_audio_capture_wav(
    wearabllm_audio_keep_recording_fn keep_recording,
    void *ctx,
    uint32_t max_seconds,
    uint32_t min_capture_ms,
    uint8_t **out_data,
    size_t *out_len)
{
    ESP_RETURN_ON_FALSE(keep_recording, ESP_ERR_INVALID_ARG, TAG, "keep_recording is required");
    ESP_RETURN_ON_FALSE(max_seconds > 0, ESP_ERR_INVALID_ARG, TAG, "max_seconds must be > 0");
    ESP_RETURN_ON_ERROR(wearabllm_audio_init(), TAG, "audio init failed");

    uint32_t max_pcm_bytes = WEARABLLM_AUDIO_SAMPLE_RATE * 2 * max_seconds;
    uint8_t *wav = calloc(1, WAV_HEADER_BYTES + max_pcm_bytes);
    ESP_RETURN_ON_FALSE(wav, ESP_ERR_NO_MEM, TAG, "capture wav allocation failed");

    int32_t *i2s_samples = calloc(I2S_READ_FRAMES * 2, sizeof(int32_t));
    if (!i2s_samples) {
        free(wav);
        return ESP_ERR_NO_MEM;
    }

    uint32_t pcm_bytes = 0;
    uint32_t tdm_frame_count = 0;
    uint32_t lane_peaks[ES7210_TDM_MIC_LANES] = {0};
    uint64_t lane_sum_squares[ES7210_TDM_MIC_LANES] = {0};
    int64_t start_us = esp_timer_get_time();
    bool min_duration_met = false;
    ESP_LOGI(TAG, "capture limits: min=%" PRIu32 " ms max=%" PRIu32 " s", min_capture_ms, max_seconds);

    while (pcm_bytes + (I2S_READ_FRAMES * sizeof(int16_t)) <= max_pcm_bytes) {
        min_duration_met = (esp_timer_get_time() - start_us) >= ((int64_t)min_capture_ms * 1000);
        if (min_duration_met && !keep_recording(ctx)) {
            break;
        }

        int in_bytes = I2S_READ_FRAMES * 2 * sizeof(int32_t);
        esp_err_t ret = esp_codec_dev_read(s_record_dev, i2s_samples, in_bytes);
        if (ret != ESP_OK) {
            free(i2s_samples);
            free(wav);
            return ret;
        }

        const int16_t *packed_tdm = (const int16_t *)i2s_samples;
        int16_t *pcm_out = (int16_t *)(wav + WAV_HEADER_BYTES + pcm_bytes);
        for (int i = 0; i < I2S_READ_FRAMES; i++) {
            int32_t mono = 0;
            for (int lane = 0; lane < ES7210_TDM_MIC_LANES; lane++) {
                int32_t sample = packed_tdm[i * ES7210_TDM_MIC_LANES + lane];
                uint32_t abs_sample = (uint32_t)(sample < 0 ? -sample : sample);
                if (abs_sample > lane_peaks[lane]) {
                    lane_peaks[lane] = abs_sample;
                }
                lane_sum_squares[lane] += (uint64_t)sample * (uint64_t)sample;
                mono += sample;
            }
            pcm_out[i] = (int16_t)(mono / ES7210_TDM_MIC_LANES);
        }
        tdm_frame_count += I2S_READ_FRAMES;
        pcm_bytes += I2S_READ_FRAMES * sizeof(int16_t);
    }

    free(i2s_samples);

    if (pcm_bytes == 0) {
        free(wav);
        return ESP_ERR_INVALID_SIZE;
    }

    write_wav_header(wav, pcm_bytes);
    log_tdm_lane_stats(lane_peaks, lane_sum_squares, tdm_frame_count);
    log_pcm16_capture_stats((const int16_t *)(wav + WAV_HEADER_BYTES), pcm_bytes / sizeof(int16_t));
    *out_data = wav;
    *out_len = WAV_HEADER_BYTES + pcm_bytes;
    ESP_LOGI(TAG, "captured %u PCM bytes, WAV bytes=%u", (unsigned)pcm_bytes, (unsigned)*out_len);
    return ESP_OK;
}

esp_err_t wearabllm_audio_read_mono(int16_t *samples, size_t frame_count)
{
    ESP_RETURN_ON_FALSE(samples && frame_count, ESP_ERR_INVALID_ARG, TAG, "mono output is required");
    ESP_RETURN_ON_ERROR(wearabllm_audio_init(), TAG, "audio init failed");

    if (frame_count > s_mono_read_capacity_frames) {
        int32_t *resized = realloc(s_mono_read_buffer, frame_count * 2 * sizeof(int32_t));
        ESP_RETURN_ON_FALSE(resized, ESP_ERR_NO_MEM, TAG, "mono read allocation failed");
        s_mono_read_buffer = resized;
        s_mono_read_capacity_frames = frame_count;
    }
    esp_err_t ret = esp_codec_dev_read(
        s_record_dev, s_mono_read_buffer, frame_count * 2 * sizeof(int32_t));
    if (ret == ESP_OK) {
        const int16_t *lanes = (const int16_t *)s_mono_read_buffer;
        for (size_t frame = 0; frame < frame_count; frame++) {
            int32_t mono = 0;
            for (int lane = 0; lane < ES7210_TDM_MIC_LANES; lane++) {
                mono += lanes[frame * ES7210_TDM_MIC_LANES + lane];
            }
            samples[frame] = (int16_t)(mono / ES7210_TDM_MIC_LANES);
        }
    }
    return ret;
}

esp_err_t wearabllm_audio_capture_wav_until_silence(
    uint32_t max_seconds,
    uint32_t silence_ms,
    uint8_t **out_data,
    size_t *out_len)
{
    enum { VOICE_PEAK_THRESHOLD = 160 };
    ESP_RETURN_ON_FALSE(max_seconds && silence_ms && out_data && out_len,
                        ESP_ERR_INVALID_ARG, TAG, "silence capture arguments are required");
    uint32_t max_frames = WEARABLLM_AUDIO_SAMPLE_RATE * max_seconds;
    uint8_t *wav = calloc(1, WAV_HEADER_BYTES + max_frames * sizeof(int16_t));
    int16_t *chunk = calloc(I2S_READ_FRAMES, sizeof(int16_t));
    if (!wav || !chunk) {
        free(wav);
        free(chunk);
        return ESP_ERR_NO_MEM;
    }

    uint32_t frames = 0;
    uint32_t silent_frames = 0;
    bool heard_voice = false;
    while (frames + I2S_READ_FRAMES <= max_frames) {
        esp_err_t ret = wearabllm_audio_read_mono(chunk, I2S_READ_FRAMES);
        if (ret != ESP_OK) {
            free(chunk);
            free(wav);
            return ret;
        }
        uint32_t peak = 0;
        for (int i = 0; i < I2S_READ_FRAMES; i++) {
            uint32_t value = (uint32_t)(chunk[i] < 0 ? -chunk[i] : chunk[i]);
            if (value > peak) peak = value;
        }
        memcpy(wav + WAV_HEADER_BYTES + frames * sizeof(int16_t), chunk,
               I2S_READ_FRAMES * sizeof(int16_t));
        frames += I2S_READ_FRAMES;
        if (peak >= VOICE_PEAK_THRESHOLD) {
            heard_voice = true;
            silent_frames = 0;
        } else if (heard_voice) {
            silent_frames += I2S_READ_FRAMES;
            if ((silent_frames * 1000U) / WEARABLLM_AUDIO_SAMPLE_RATE >= silence_ms) break;
        }
    }
    free(chunk);
    if (!heard_voice) {
        free(wav);
        return ESP_ERR_INVALID_STATE;
    }
    uint32_t pcm_bytes = frames * sizeof(int16_t);
    write_wav_header(wav, pcm_bytes);
    *out_data = wav;
    *out_len = WAV_HEADER_BYTES + pcm_bytes;
    ESP_LOGI(TAG, "wake capture: frames=%" PRIu32 " silence_ms=%" PRIu32, frames, silence_ms);
    return ESP_OK;
}
