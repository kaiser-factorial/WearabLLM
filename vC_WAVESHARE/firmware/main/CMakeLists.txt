idf_component_register(
    SRCS
        "main.c"
        "wearabllm_audio.c"
        "wearabllm_display.c"
    INCLUDE_DIRS "."
    REQUIRES
        esp_driver_gpio
        esp_driver_i2c
        esp_driver_i2s
        esp_driver_spi
        esp_http_client
        esp-tls
        esp_lcd
        esp_timer
        esp_wifi
        json
        led_strip
        nvs_flash
        esp_codec_dev
)

if(CONFIG_WEARABLLM_DIRECT_OPENAI)
    target_sources(${COMPONENT_LIB} PRIVATE "wearabllm_openai.c")
endif()

if(CONFIG_WEARABLLM_TRANSCRIPT_LOG_ENABLED)
    target_sources(${COMPONENT_LIB} PRIVATE "wearabllm_transcript_log.c")
endif()
