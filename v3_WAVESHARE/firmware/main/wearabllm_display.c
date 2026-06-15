#include "wearabllm_display.h"

#include <ctype.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_lcd_io_spi.h"
#include "esp_lcd_panel_io.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "wearabllm_display";

#if CONFIG_WEARABLLM_DISPLAY_ENABLED

#define LCD_HOST SPI2_HOST
#define LCD_CMD_SWRESET 0x01
#define LCD_CMD_SLPOUT 0x11
#define LCD_CMD_NORON 0x13
#define LCD_CMD_INVOFF 0x20
#define LCD_CMD_INVON 0x21
#define LCD_CMD_DISPON 0x29
#define LCD_CMD_CASET 0x2A
#define LCD_CMD_RASET 0x2B
#define LCD_CMD_RAMWR 0x2C
#define LCD_CMD_MADCTL 0x36
#define LCD_CMD_COLMOD 0x3A

#define COLOR_BG 0x0000
#define COLOR_PANEL 0x18E3
#define COLOR_TEXT 0xFFFF
#define COLOR_MUTED 0xBDF7
#define COLOR_LISTENING 0xFFFF
#define COLOR_THINKING 0xFD20
#define COLOR_ERROR 0xF800

static esp_lcd_panel_io_handle_t s_lcd_io;
static uint16_t s_line_buf[CONFIG_WEARABLLM_TFT_WIDTH];
static bool s_ready;

static uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b)
{
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

static uint16_t color_for_command(const char *command)
{
    if (!command) {
        return rgb565(0, 40, 130);
    }
    if (strcmp(command, "GS") == 0 || strcmp(command, "GP") == 0 || strcmp(command, "GC") == 0) {
        return rgb565(0, 150, 50);
    }
    if (strcmp(command, "RS") == 0 || strcmp(command, "RF") == 0) {
        return rgb565(190, 20, 20);
    }
    if (strcmp(command, "YP") == 0) {
        return rgb565(220, 150, 0);
    }
    if (strcmp(command, "PS") == 0 || strcmp(command, "PP") == 0) {
        return rgb565(145, 70, 210);
    }
    return rgb565(0, 75, 190);
}

static esp_err_t lcd_cmd(uint8_t cmd, const void *data, size_t len)
{
    return esp_lcd_panel_io_tx_param(s_lcd_io, cmd, data, len);
}

static esp_err_t lcd_set_window(int x0, int y0, int x1, int y1)
{
    uint16_t xs = x0 + CONFIG_WEARABLLM_TFT_X_OFFSET;
    uint16_t xe = x1 + CONFIG_WEARABLLM_TFT_X_OFFSET;
    uint16_t ys = y0 + CONFIG_WEARABLLM_TFT_Y_OFFSET;
    uint16_t ye = y1 + CONFIG_WEARABLLM_TFT_Y_OFFSET;
    uint8_t cols[] = {xs >> 8, xs & 0xFF, xe >> 8, xe & 0xFF};
    uint8_t rows[] = {ys >> 8, ys & 0xFF, ye >> 8, ye & 0xFF};

    ESP_RETURN_ON_ERROR(lcd_cmd(LCD_CMD_CASET, cols, sizeof(cols)), TAG, "CASET failed");
    ESP_RETURN_ON_ERROR(lcd_cmd(LCD_CMD_RASET, rows, sizeof(rows)), TAG, "RASET failed");
    return ESP_OK;
}

static esp_err_t lcd_fill_rect(int x, int y, int w, int h, uint16_t color)
{
    if (!s_ready || w <= 0 || h <= 0) {
        return ESP_OK;
    }
    if (x < 0) {
        w += x;
        x = 0;
    }
    if (y < 0) {
        h += y;
        y = 0;
    }
    if (x + w > CONFIG_WEARABLLM_TFT_WIDTH) {
        w = CONFIG_WEARABLLM_TFT_WIDTH - x;
    }
    if (y + h > CONFIG_WEARABLLM_TFT_HEIGHT) {
        h = CONFIG_WEARABLLM_TFT_HEIGHT - y;
    }
    if (w <= 0 || h <= 0) {
        return ESP_OK;
    }

    ESP_RETURN_ON_ERROR(lcd_set_window(x, y, x + w - 1, y + h - 1), TAG, "set window failed");

    for (int i = 0; i < w; i++) {
        s_line_buf[i] = __builtin_bswap16(color);
    }

    for (int row = 0; row < h; row++) {
        ESP_RETURN_ON_ERROR(esp_lcd_panel_io_tx_color(s_lcd_io, row == 0 ? LCD_CMD_RAMWR : -1, s_line_buf, w * sizeof(uint16_t)),
                            TAG, "fill row failed");
    }
    return ESP_OK;
}

static const uint8_t *glyph5x7(char ch)
{
    static const uint8_t blank[5] = {0, 0, 0, 0, 0};
    static const uint8_t question[5] = {0x02, 0x01, 0x51, 0x09, 0x06};
    static const uint8_t digits[10][5] = {
        {0x3E, 0x51, 0x49, 0x45, 0x3E}, {0x00, 0x42, 0x7F, 0x40, 0x00},
        {0x42, 0x61, 0x51, 0x49, 0x46}, {0x21, 0x41, 0x45, 0x4B, 0x31},
        {0x18, 0x14, 0x12, 0x7F, 0x10}, {0x27, 0x45, 0x45, 0x45, 0x39},
        {0x3C, 0x4A, 0x49, 0x49, 0x30}, {0x01, 0x71, 0x09, 0x05, 0x03},
        {0x36, 0x49, 0x49, 0x49, 0x36}, {0x06, 0x49, 0x49, 0x29, 0x1E},
    };
    static const uint8_t letters[26][5] = {
        {0x7E, 0x11, 0x11, 0x11, 0x7E}, {0x7F, 0x49, 0x49, 0x49, 0x36},
        {0x3E, 0x41, 0x41, 0x41, 0x22}, {0x7F, 0x41, 0x41, 0x22, 0x1C},
        {0x7F, 0x49, 0x49, 0x49, 0x41}, {0x7F, 0x09, 0x09, 0x09, 0x01},
        {0x3E, 0x41, 0x49, 0x49, 0x7A}, {0x7F, 0x08, 0x08, 0x08, 0x7F},
        {0x00, 0x41, 0x7F, 0x41, 0x00}, {0x20, 0x40, 0x41, 0x3F, 0x01},
        {0x7F, 0x08, 0x14, 0x22, 0x41}, {0x7F, 0x40, 0x40, 0x40, 0x40},
        {0x7F, 0x02, 0x0C, 0x02, 0x7F}, {0x7F, 0x04, 0x08, 0x10, 0x7F},
        {0x3E, 0x41, 0x41, 0x41, 0x3E}, {0x7F, 0x09, 0x09, 0x09, 0x06},
        {0x3E, 0x41, 0x51, 0x21, 0x5E}, {0x7F, 0x09, 0x19, 0x29, 0x46},
        {0x46, 0x49, 0x49, 0x49, 0x31}, {0x01, 0x01, 0x7F, 0x01, 0x01},
        {0x3F, 0x40, 0x40, 0x40, 0x3F}, {0x1F, 0x20, 0x40, 0x20, 0x1F},
        {0x3F, 0x40, 0x38, 0x40, 0x3F}, {0x63, 0x14, 0x08, 0x14, 0x63},
        {0x07, 0x08, 0x70, 0x08, 0x07}, {0x61, 0x51, 0x49, 0x45, 0x43},
    };
    static const uint8_t colon[5] = {0x00, 0x36, 0x36, 0x00, 0x00};
    static const uint8_t period[5] = {0x00, 0x60, 0x60, 0x00, 0x00};
    static const uint8_t comma[5] = {0x00, 0x80, 0x60, 0x00, 0x00};
    static const uint8_t dash[5] = {0x08, 0x08, 0x08, 0x08, 0x08};
    static const uint8_t bang[5] = {0x00, 0x00, 0x5F, 0x00, 0x00};
    static const uint8_t slash[5] = {0x20, 0x10, 0x08, 0x04, 0x02};

    if (ch == ' ') {
        return blank;
    }
    if (ch >= '0' && ch <= '9') {
        return digits[ch - '0'];
    }
    if (ch >= 'A' && ch <= 'Z') {
        return letters[ch - 'A'];
    }
    switch (ch) {
    case ':': return colon;
    case '.': return period;
    case ',': return comma;
    case '-': return dash;
    case '!': return bang;
    case '/': return slash;
    default: return question;
    }
}

static void lcd_draw_char(int x, int y, char ch, uint16_t fg, uint16_t bg, int scale)
{
    ch = (char)toupper((unsigned char)ch);
    const uint8_t *glyph = glyph5x7(ch);
    for (int col = 0; col < 6; col++) {
        uint8_t bits = col < 5 ? glyph[col] : 0;
        for (int row = 0; row < 8; row++) {
            uint16_t color = (bits & (1 << row)) ? fg : bg;
            lcd_fill_rect(x + col * scale, y + row * scale, scale, scale, color);
        }
    }
}

static int lcd_draw_wrapped_text(int x, int y, int max_w, int max_h, const char *text, uint16_t fg, uint16_t bg, int scale)
{
    if (!text) {
        return y;
    }
    int char_w = 6 * scale;
    int line_h = 9 * scale;
    int cx = x;
    int cy = y;

    for (const char *p = text; *p && cy + line_h <= y + max_h; p++) {
        char ch = *p;
        if (ch == '\n' || cx + char_w > x + max_w) {
            cx = x;
            cy += line_h;
            if (ch == '\n') {
                continue;
            }
        }
        lcd_draw_char(cx, cy, ch, fg, bg, scale);
        cx += char_w;
    }
    return cy + line_h;
}

static const char *state_name(wearabllm_display_state_t state)
{
    switch (state) {
    case WEARABLLM_DISPLAY_IDLE: return "READY";
    case WEARABLLM_DISPLAY_LISTENING: return "LISTENING";
    case WEARABLLM_DISPLAY_THINKING: return "THINKING";
    case WEARABLLM_DISPLAY_RESPONSE: return "RESPONSE";
    case WEARABLLM_DISPLAY_ERROR: return "ERROR";
    default: return "UNKNOWN";
    }
}

static uint16_t state_color(wearabllm_display_state_t state)
{
    switch (state) {
    case WEARABLLM_DISPLAY_LISTENING: return COLOR_LISTENING;
    case WEARABLLM_DISPLAY_THINKING: return COLOR_THINKING;
    case WEARABLLM_DISPLAY_ERROR: return COLOR_ERROR;
    default: return rgb565(50, 120, 220);
    }
}

static esp_err_t lcd_init_panel(void)
{
    spi_bus_config_t buscfg = {
        .mosi_io_num = CONFIG_WEARABLLM_TFT_MOSI_GPIO,
        .miso_io_num = -1,
        .sclk_io_num = CONFIG_WEARABLLM_TFT_SCLK_GPIO,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = CONFIG_WEARABLLM_TFT_WIDTH * sizeof(uint16_t),
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(LCD_HOST, &buscfg, SPI_DMA_CH_AUTO), TAG, "spi bus init failed");

    esp_lcd_panel_io_spi_config_t io_config = {
        .cs_gpio_num = CONFIG_WEARABLLM_TFT_CS_GPIO,
        .dc_gpio_num = CONFIG_WEARABLLM_TFT_DC_GPIO,
        .spi_mode = 0,
        .pclk_hz = SPI_MASTER_FREQ_20M,
        .trans_queue_depth = 10,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)LCD_HOST, &io_config, &s_lcd_io),
                        TAG, "panel io init failed");

    gpio_config_t out_conf = {
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << CONFIG_WEARABLLM_TFT_RST_GPIO) | (1ULL << CONFIG_WEARABLLM_TFT_BL_GPIO),
    };
    ESP_RETURN_ON_ERROR(gpio_config(&out_conf), TAG, "display gpio config failed");

    gpio_set_level(CONFIG_WEARABLLM_TFT_BL_GPIO, 0);
    gpio_set_level(CONFIG_WEARABLLM_TFT_RST_GPIO, 0);
    vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level(CONFIG_WEARABLLM_TFT_RST_GPIO, 1);
    vTaskDelay(pdMS_TO_TICKS(120));

    ESP_RETURN_ON_ERROR(lcd_cmd(LCD_CMD_SWRESET, NULL, 0), TAG, "SWRESET failed");
    vTaskDelay(pdMS_TO_TICKS(150));
    ESP_RETURN_ON_ERROR(lcd_cmd(LCD_CMD_SLPOUT, NULL, 0), TAG, "SLPOUT failed");
    vTaskDelay(pdMS_TO_TICKS(150));
    uint8_t color_mode = 0x05;
    uint8_t madctl = 0x00;
    ESP_RETURN_ON_ERROR(lcd_cmd(LCD_CMD_COLMOD, &color_mode, 1), TAG, "COLMOD failed");
    ESP_RETURN_ON_ERROR(lcd_cmd(LCD_CMD_MADCTL, &madctl, 1), TAG, "MADCTL failed");
#if CONFIG_WEARABLLM_TFT_INVERT_COLORS
    ESP_RETURN_ON_ERROR(lcd_cmd(LCD_CMD_INVON, NULL, 0), TAG, "INVON failed");
#else
    ESP_RETURN_ON_ERROR(lcd_cmd(LCD_CMD_INVOFF, NULL, 0), TAG, "INVOFF failed");
#endif
    ESP_RETURN_ON_ERROR(lcd_cmd(LCD_CMD_NORON, NULL, 0), TAG, "NORON failed");
    ESP_RETURN_ON_ERROR(lcd_cmd(LCD_CMD_DISPON, NULL, 0), TAG, "DISPON failed");
    vTaskDelay(pdMS_TO_TICKS(100));
    gpio_set_level(CONFIG_WEARABLLM_TFT_BL_GPIO, 1);
    return ESP_OK;
}

static void lcd_run_self_test(void)
{
#if CONFIG_WEARABLLM_DISPLAY_SELF_TEST_ON_BOOT
    ESP_LOGI(TAG, "running TFT display wiring self-test");
    int band_h = CONFIG_WEARABLLM_TFT_HEIGHT / 6;
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, CONFIG_WEARABLLM_TFT_HEIGHT, COLOR_BG);
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, band_h, rgb565(220, 20, 20));
    lcd_fill_rect(0, band_h, CONFIG_WEARABLLM_TFT_WIDTH, band_h, rgb565(0, 150, 50));
    lcd_fill_rect(0, band_h * 2, CONFIG_WEARABLLM_TFT_WIDTH, band_h, rgb565(0, 75, 190));
    lcd_fill_rect(0, band_h * 3, CONFIG_WEARABLLM_TFT_WIDTH, band_h, rgb565(220, 150, 0));
    lcd_fill_rect(0, band_h * 4, CONFIG_WEARABLLM_TFT_WIDTH, band_h, rgb565(145, 70, 210));
    lcd_fill_rect(0, band_h * 5, CONFIG_WEARABLLM_TFT_WIDTH, CONFIG_WEARABLLM_TFT_HEIGHT - band_h * 5, COLOR_TEXT);
    vTaskDelay(pdMS_TO_TICKS(850));

    char line[64];
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, CONFIG_WEARABLLM_TFT_HEIGHT, COLOR_BG);
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, 24, rgb565(50, 120, 220));
    lcd_draw_wrapped_text(6, 7, CONFIG_WEARABLLM_TFT_WIDTH - 12, 18, "TFT SELF TEST", COLOR_BG, rgb565(50, 120, 220), 1);
    lcd_draw_wrapped_text(8, 34, CONFIG_WEARABLLM_TFT_WIDTH - 16, 18, "IF READABLE", COLOR_TEXT, COLOR_BG, 1);
    lcd_draw_wrapped_text(8, 50, CONFIG_WEARABLLM_TFT_WIDTH - 16, 18, "SPI IS ALIVE", COLOR_TEXT, COLOR_BG, 1);
    snprintf(line, sizeof(line), "SCLK %d MOSI %d", CONFIG_WEARABLLM_TFT_SCLK_GPIO, CONFIG_WEARABLLM_TFT_MOSI_GPIO);
    lcd_draw_wrapped_text(8, 76, CONFIG_WEARABLLM_TFT_WIDTH - 16, 18, line, COLOR_MUTED, COLOR_BG, 1);
    snprintf(line, sizeof(line), "CS %d DC %d", CONFIG_WEARABLLM_TFT_CS_GPIO, CONFIG_WEARABLLM_TFT_DC_GPIO);
    lcd_draw_wrapped_text(8, 92, CONFIG_WEARABLLM_TFT_WIDTH - 16, 18, line, COLOR_MUTED, COLOR_BG, 1);
    snprintf(line, sizeof(line), "RST %d BL %d", CONFIG_WEARABLLM_TFT_RST_GPIO, CONFIG_WEARABLLM_TFT_BL_GPIO);
    lcd_draw_wrapped_text(8, 108, CONFIG_WEARABLLM_TFT_WIDTH - 16, 18, line, COLOR_MUTED, COLOR_BG, 1);
    vTaskDelay(pdMS_TO_TICKS(1600));
#endif
}

#endif

esp_err_t wearabllm_display_init(void)
{
#if CONFIG_WEARABLLM_DISPLAY_ENABLED
    ESP_LOGI(TAG, "initializing ST7735 display: %dx%d sclk=%d mosi=%d cs=%d dc=%d rst=%d bl=%d",
             CONFIG_WEARABLLM_TFT_WIDTH,
             CONFIG_WEARABLLM_TFT_HEIGHT,
             CONFIG_WEARABLLM_TFT_SCLK_GPIO,
             CONFIG_WEARABLLM_TFT_MOSI_GPIO,
             CONFIG_WEARABLLM_TFT_CS_GPIO,
             CONFIG_WEARABLLM_TFT_DC_GPIO,
             CONFIG_WEARABLLM_TFT_RST_GPIO,
             CONFIG_WEARABLLM_TFT_BL_GPIO);
    esp_err_t err = lcd_init_panel();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "display init failed: %s", esp_err_to_name(err));
        return err;
    }
    s_ready = true;
    lcd_run_self_test();
    wearabllm_display_show_state(WEARABLLM_DISPLAY_IDLE);
#else
    ESP_LOGI(TAG, "display disabled; using serial logs only");
#endif
    return ESP_OK;
}

void wearabllm_display_show_state(wearabllm_display_state_t state)
{
#if CONFIG_WEARABLLM_DISPLAY_ENABLED
    if (!s_ready) {
        return;
    }
    uint16_t accent = state_color(state);
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, CONFIG_WEARABLLM_TFT_HEIGHT, COLOR_BG);
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, 24, accent);
    lcd_draw_wrapped_text(6, 7, CONFIG_WEARABLLM_TFT_WIDTH - 12, 18, state_name(state), COLOR_BG, accent, 1);
    lcd_draw_wrapped_text(8, 42, CONFIG_WEARABLLM_TFT_WIDTH - 16, 48, "WEARABLLM", COLOR_TEXT, COLOR_BG, 2);
    lcd_draw_wrapped_text(8, 102, CONFIG_WEARABLLM_TFT_WIDTH - 16, 40, "HOLD BUTTON TO ASK", COLOR_MUTED, COLOR_BG, 1);
#else
    (void)state;
#endif
}

void wearabllm_display_show_response(const char *command, const char *transcript, const char *reply)
{
#if CONFIG_WEARABLLM_DISPLAY_ENABLED
    if (!s_ready) {
        return;
    }
    uint16_t accent = color_for_command(command);
    char title[24];
    snprintf(title, sizeof(title), "RESPONSE %s", command ? command : "--");

    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, CONFIG_WEARABLLM_TFT_HEIGHT, COLOR_BG);
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, 24, accent);
    lcd_draw_wrapped_text(6, 7, CONFIG_WEARABLLM_TFT_WIDTH - 12, 18, title, COLOR_BG, accent, 1);
    lcd_fill_rect(6, 32, CONFIG_WEARABLLM_TFT_WIDTH - 12, 40, COLOR_PANEL);
    lcd_draw_wrapped_text(12, 38, CONFIG_WEARABLLM_TFT_WIDTH - 24, 10, "HEARD", COLOR_MUTED, COLOR_PANEL, 1);
    lcd_draw_wrapped_text(12, 52, CONFIG_WEARABLLM_TFT_WIDTH - 24, 14,
                          transcript && transcript[0] ? transcript : "NO TRANSCRIPT", COLOR_TEXT, COLOR_PANEL, 1);

    lcd_fill_rect(6, 78, CONFIG_WEARABLLM_TFT_WIDTH - 12, CONFIG_WEARABLLM_TFT_HEIGHT - 84, COLOR_PANEL);
    lcd_draw_wrapped_text(12, 84, CONFIG_WEARABLLM_TFT_WIDTH - 24, 10, "REPLY", COLOR_MUTED, COLOR_PANEL, 1);
    lcd_draw_wrapped_text(12, 98, CONFIG_WEARABLLM_TFT_WIDTH - 24, CONFIG_WEARABLLM_TFT_HEIGHT - 106,
                          reply && reply[0] ? reply : "NO TEXT", COLOR_TEXT, COLOR_PANEL, 1);
#else
    (void)command;
    (void)transcript;
    (void)reply;
#endif
}

void wearabllm_display_show_error(const char *message)
{
#if CONFIG_WEARABLLM_DISPLAY_ENABLED
    if (!s_ready) {
        return;
    }
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, CONFIG_WEARABLLM_TFT_HEIGHT, COLOR_BG);
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, 24, COLOR_ERROR);
    lcd_draw_wrapped_text(6, 7, CONFIG_WEARABLLM_TFT_WIDTH - 12, 18, "ERROR", COLOR_BG, COLOR_ERROR, 1);
    lcd_draw_wrapped_text(10, 42, CONFIG_WEARABLLM_TFT_WIDTH - 20, CONFIG_WEARABLLM_TFT_HEIGHT - 50,
                          message ? message : "UNKNOWN ERROR", COLOR_TEXT, COLOR_BG, 1);
#else
    (void)message;
#endif
}
