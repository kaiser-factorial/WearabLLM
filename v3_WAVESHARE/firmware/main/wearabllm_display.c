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
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_st7789.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "wearabllm_display";

#if CONFIG_WEARABLLM_DISPLAY_ENABLED

// TFT200C V1.3: native 240x320 ST7789, presented as a 320x240 landscape UI.
#define LCD_HOST SPI2_HOST
#define LCD_FILL_ROWS 8
#define LCD_CHAR_MAX_SCALE 3
#define LCD_DRAW_TIMEOUT_MS 2000

#define COLOR_BG 0x0861
#define COLOR_TEXT 0xFFFF
#define COLOR_DARK_TEXT 0x0000
#define COLOR_MUTED 0xBDF7
#define COLOR_LISTENING 0x069F
#define COLOR_SENDING 0x781F
#define COLOR_THINKING 0xFD20
#define COLOR_ERROR 0xF800
#define COLOR_ERROR_BG 0xFFFF

static esp_lcd_panel_io_handle_t s_lcd_io;
static esp_lcd_panel_handle_t s_lcd_panel;
static SemaphoreHandle_t s_color_done;
static uint16_t s_fill_buf[CONFIG_WEARABLLM_TFT_WIDTH * LCD_FILL_ROWS];
static uint16_t s_char_buf[6 * LCD_CHAR_MAX_SCALE * 8 * LCD_CHAR_MAX_SCALE];
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

static bool lcd_color_done_cb(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_io_event_data_t *edata, void *user_ctx)
{
    (void)panel_io;
    (void)edata;
    (void)user_ctx;
    BaseType_t high_task_woken = pdFALSE;
    xSemaphoreGiveFromISR(s_color_done, &high_task_woken);
    return high_task_woken == pdTRUE;
}

static esp_err_t lcd_draw_bitmap_sync(int x0, int y0, int x1, int y1, const uint16_t *pixels)
{
    while (xSemaphoreTake(s_color_done, 0) == pdTRUE) {
    }
    ESP_RETURN_ON_ERROR(esp_lcd_panel_draw_bitmap(s_lcd_panel, x0, y0, x1, y1, pixels), TAG,
                        "draw bitmap failed");
    if (xSemaphoreTake(s_color_done, pdMS_TO_TICKS(LCD_DRAW_TIMEOUT_MS)) != pdTRUE) {
        s_ready = false;
        ESP_LOGE(TAG, "display transfer timed out; disabling further screen writes");
        return ESP_ERR_TIMEOUT;
    }
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

    uint16_t wire_color = __builtin_bswap16(color);
    for (int offset = 0; offset < h; offset += LCD_FILL_ROWS) {
        int rows = h - offset;
        if (rows > LCD_FILL_ROWS) {
            rows = LCD_FILL_ROWS;
        }
        int pixel_count = w * rows;
        for (int i = 0; i < pixel_count; i++) {
            s_fill_buf[i] = wire_color;
        }
        ESP_RETURN_ON_ERROR(lcd_draw_bitmap_sync(x, y + offset, x + w, y + offset + rows, s_fill_buf), TAG,
                            "fill chunk failed");
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
    if (!s_ready || scale < 1 || scale > LCD_CHAR_MAX_SCALE) {
        return;
    }
    int width = 6 * scale;
    int height = 8 * scale;
    if (x < 0 || y < 0 || x + width > CONFIG_WEARABLLM_TFT_WIDTH ||
        y + height > CONFIG_WEARABLLM_TFT_HEIGHT) {
        return;
    }

    ch = (char)toupper((unsigned char)ch);
    const uint8_t *glyph = glyph5x7(ch);
    uint16_t wire_fg = __builtin_bswap16(fg);
    uint16_t wire_bg = __builtin_bswap16(bg);
    for (int py = 0; py < height; py++) {
        int glyph_row = py / scale;
        for (int px = 0; px < width; px++) {
            int glyph_col = px / scale;
            bool set = glyph_col < 5 && (glyph[glyph_col] & (1U << glyph_row));
            s_char_buf[py * width + px] = set ? wire_fg : wire_bg;
        }
    }
    if (lcd_draw_bitmap_sync(x, y, x + width, y + height, s_char_buf) != ESP_OK) {
        ESP_LOGE(TAG, "glyph draw failed");
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
    bool insert_space = false;

    for (const char *p = text; *p && cy + line_h <= y + max_h;) {
        if (*p == '\n') {
            cx = x;
            cy += line_h;
            p++;
            insert_space = false;
            continue;
        }
        while (*p == ' ') {
            insert_space = cx != x;
            p++;
        }
        if (!*p || *p == '\n') continue;
        const char *word = p;
        while (*p && *p != ' ' && *p != '\n') p++;
        int word_len = (int)(p - word);
        int word_w = word_len * char_w;
        int space_w = insert_space ? char_w : 0;
        if (cx != x && cx + space_w + word_w > x + max_w) {
            cx = x;
            cy += line_h;
            if (cy + line_h > y + max_h) break;
        }
        if (insert_space && cx != x) {
            lcd_draw_char(cx, cy, ' ', fg, bg, scale);
            cx += char_w;
        }
        for (int i = 0; i < word_len && cy + line_h <= y + max_h; i++) {
            if (cx + char_w > x + max_w) {
                cx = x;
                cy += line_h;
            }
            if (cy + line_h > y + max_h) break;
            lcd_draw_char(cx, cy, word[i], fg, bg, scale);
            cx += char_w;
        }
        insert_space = false;
    }
    return cy + line_h;
}

static uint16_t state_color(wearabllm_display_state_t state)
{
    switch (state) {
    case WEARABLLM_DISPLAY_LISTENING: return COLOR_LISTENING;
    case WEARABLLM_DISPLAY_SENDING: return COLOR_SENDING;
    case WEARABLLM_DISPLAY_THINKING: return COLOR_THINKING;
    case WEARABLLM_DISPLAY_ERROR: return COLOR_ERROR;
    default: return rgb565(50, 120, 220);
    }
}

static esp_err_t lcd_apply_rotation(void)
{
    bool swap_xy = false;
    bool mirror_x = false;
    bool mirror_y = false;
    switch (CONFIG_WEARABLLM_TFT_ROTATION) {
    case 1:
        swap_xy = true;
        mirror_x = true;
        break;
    case 2:
        mirror_x = true;
        mirror_y = true;
        break;
    case 3:
        swap_xy = true;
        mirror_y = true;
        break;
    default:
        break;
    }
    ESP_RETURN_ON_ERROR(esp_lcd_panel_swap_xy(s_lcd_panel, swap_xy), TAG, "panel axis swap failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_mirror(s_lcd_panel, mirror_x, mirror_y), TAG, "panel mirror failed");
    return ESP_OK;
}

static esp_err_t lcd_init_panel(void)
{
    spi_bus_config_t buscfg = {
        .mosi_io_num = CONFIG_WEARABLLM_TFT_MOSI_GPIO,
        .miso_io_num = -1,
        .sclk_io_num = CONFIG_WEARABLLM_TFT_SCLK_GPIO,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = CONFIG_WEARABLLM_TFT_WIDTH * LCD_FILL_ROWS * sizeof(uint16_t),
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
        .on_color_trans_done = lcd_color_done_cb,
    };

    s_color_done = xSemaphoreCreateBinary();
    ESP_RETURN_ON_FALSE(s_color_done, ESP_ERR_NO_MEM, TAG, "display semaphore allocation failed");
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)LCD_HOST, &io_config, &s_lcd_io),
                        TAG, "panel io init failed");

    gpio_config_t out_conf = {
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << CONFIG_WEARABLLM_TFT_BL_GPIO),
    };
    ESP_RETURN_ON_ERROR(gpio_config(&out_conf), TAG, "display gpio config failed");

    gpio_set_level(CONFIG_WEARABLLM_TFT_BL_GPIO, 0);

    esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = CONFIG_WEARABLLM_TFT_RST_GPIO,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .data_endian = LCD_RGB_DATA_ENDIAN_BIG,
        .bits_per_pixel = 16,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7789(s_lcd_io, &panel_config, &s_lcd_panel), TAG,
                        "ST7789 panel allocation failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_lcd_panel), TAG, "panel reset failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_lcd_panel), TAG, "panel init failed");
    ESP_RETURN_ON_ERROR(lcd_apply_rotation(), TAG, "panel rotation failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(s_lcd_panel, CONFIG_WEARABLLM_TFT_X_OFFSET,
                                              CONFIG_WEARABLLM_TFT_Y_OFFSET), TAG, "panel gap failed");
#if CONFIG_WEARABLLM_TFT_INVERT_COLORS
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(s_lcd_panel, true), TAG, "color inversion failed");
#else
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(s_lcd_panel, false), TAG, "color inversion failed");
#endif
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(s_lcd_panel, true), TAG, "display enable failed");
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
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, 34, rgb565(50, 120, 220));
    lcd_draw_wrapped_text(10, 8, CONFIG_WEARABLLM_TFT_WIDTH - 20, 18, "TFT SELF TEST", COLOR_BG, rgb565(50, 120, 220), 2);
    lcd_draw_wrapped_text(10, 48, CONFIG_WEARABLLM_TFT_WIDTH - 20, 18, "IF READABLE", COLOR_TEXT, COLOR_BG, 2);
    lcd_draw_wrapped_text(10, 78, CONFIG_WEARABLLM_TFT_WIDTH - 20, 18, "SPI IS ALIVE", COLOR_TEXT, COLOR_BG, 2);
    snprintf(line, sizeof(line), "SCLK %d MOSI %d", CONFIG_WEARABLLM_TFT_SCLK_GPIO, CONFIG_WEARABLLM_TFT_MOSI_GPIO);
    lcd_draw_wrapped_text(10, 120, CONFIG_WEARABLLM_TFT_WIDTH - 20, 18, line, COLOR_MUTED, COLOR_BG, 2);
    snprintf(line, sizeof(line), "CS %d DC %d", CONFIG_WEARABLLM_TFT_CS_GPIO, CONFIG_WEARABLLM_TFT_DC_GPIO);
    lcd_draw_wrapped_text(10, 150, CONFIG_WEARABLLM_TFT_WIDTH - 20, 18, line, COLOR_MUTED, COLOR_BG, 2);
    snprintf(line, sizeof(line), "RST %d BL %d", CONFIG_WEARABLLM_TFT_RST_GPIO, CONFIG_WEARABLLM_TFT_BL_GPIO);
    lcd_draw_wrapped_text(10, 180, CONFIG_WEARABLLM_TFT_WIDTH - 20, 18, line, COLOR_MUTED, COLOR_BG, 2);
    vTaskDelay(pdMS_TO_TICKS(1600));
#endif
}

#endif

esp_err_t wearabllm_display_init(void)
{
#if CONFIG_WEARABLLM_DISPLAY_ENABLED
    ESP_LOGI(TAG, "initializing ST7789 display: %dx%d sclk=%d mosi=%d cs=%d dc=%d rst=%d bl=%d",
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
    uint16_t background = state_color(state);
    uint16_t foreground = COLOR_DARK_TEXT;
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, CONFIG_WEARABLLM_TFT_HEIGHT, background);
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, 10, COLOR_TEXT);

    switch (state) {
    case WEARABLLM_DISPLAY_LISTENING:
        lcd_draw_wrapped_text(12, 30, 296, 20, "LISTENING", foreground, background, 2);
        lcd_draw_wrapped_text(12, 96, 296, 20, "SPEAK NOW", foreground, background, 2);
        lcd_draw_wrapped_text(12, 184, 296, 20, "RELEASE TO SEND", foreground, background, 2);
        break;
    case WEARABLLM_DISPLAY_SENDING:
        lcd_draw_wrapped_text(12, 52, 296, 20, "SENDING", COLOR_TEXT, background, 2);
        lcd_draw_wrapped_text(12, 136, 296, 20, "TO SPHERE", COLOR_TEXT, background, 2);
        break;
    case WEARABLLM_DISPLAY_THINKING:
        lcd_draw_wrapped_text(12, 52, 296, 40, "SPHERE IS THINKING", foreground, background, 2);
        lcd_draw_wrapped_text(12, 136, 296, 20, "PLEASE WAIT...", foreground, background, 2);
        break;
    case WEARABLLM_DISPLAY_IDLE:
    default:
        lcd_draw_wrapped_text(12, 30, 296, 20, "WEARABLLM SPHERE", COLOR_TEXT, background, 2);
        lcd_draw_wrapped_text(12, 96, 296, 20, "READY", COLOR_TEXT, background, 2);
        lcd_draw_wrapped_text(12, 184, 296, 20, "HOLD BUTTON TO ASK", COLOR_MUTED, background, 2);
        break;
    }
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
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, CONFIG_WEARABLLM_TFT_HEIGHT, COLOR_BG);
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, 10, accent);
    lcd_draw_wrapped_text(106, 18, 108, 20, "ASSISTANT", COLOR_MUTED, COLOR_BG, 2);
    lcd_draw_wrapped_text(10, 52, CONFIG_WEARABLLM_TFT_WIDTH - 20, 152,
                          reply && reply[0] ? reply : "NO TEXT RESPONSE", COLOR_TEXT, COLOR_BG, 2);
    (void)transcript;
    lcd_draw_wrapped_text(106, 220, CONFIG_WEARABLLM_TFT_WIDTH - 116, 10,
                          "HOLD TO ASK AGAIN", COLOR_MUTED, COLOR_BG, 1);
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
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, CONFIG_WEARABLLM_TFT_HEIGHT, COLOR_ERROR_BG);
    lcd_fill_rect(0, 0, CONFIG_WEARABLLM_TFT_WIDTH, 12, COLOR_ERROR);
    lcd_draw_wrapped_text(12, 30, CONFIG_WEARABLLM_TFT_WIDTH - 24, 20, "ERROR", COLOR_DARK_TEXT, COLOR_ERROR_BG, 2);
    lcd_draw_wrapped_text(12, 78, CONFIG_WEARABLLM_TFT_WIDTH - 24, 94,
                          message ? message : "UNKNOWN ERROR", COLOR_DARK_TEXT, COLOR_ERROR_BG, 2);
    lcd_draw_wrapped_text(12, 202, CONFIG_WEARABLLM_TFT_WIDTH - 24, 20,
                          "CHECK WIFI OR BRIDGE", COLOR_DARK_TEXT, COLOR_ERROR_BG, 2);
#else
    (void)message;
#endif
}
