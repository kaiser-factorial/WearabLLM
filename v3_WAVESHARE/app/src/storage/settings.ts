import * as SecureStore from 'expo-secure-store';

import { normalizeBridgeBaseUrl } from '../protocol/bridgeClient';

const BRIDGE_URL_KEY = 'wearabllm_v3_bridge_url';
const BRIDGE_TOKEN_KEY = 'wearabllm_v3_bridge_token';
const DEVICE_WIFI_SSID_KEY = 'wearabllm_v3_device_wifi_ssid';
const DEVICE_WIFI_PASSWORD_KEY = 'wearabllm_v3_device_wifi_password';
const DEVICE_WIFI_BSSID_KEY = 'wearabllm_v3_device_wifi_bssid';
const DEVICE_PTT_GPIO_KEY = 'wearabllm_v3_device_ptt_gpio';
const DEVICE_PTT_ACTIVE_LEVEL_KEY = 'wearabllm_v3_device_ptt_active_level';
const DEVICE_PTT_DEBOUNCE_MS_KEY = 'wearabllm_v3_device_ptt_debounce_ms';
const DEVICE_PTT_PULL_KEY = 'wearabllm_v3_device_ptt_pull';
const DEVICE_AUDIO_OUT_ENABLED_KEY = 'wearabllm_v3_device_audio_out_enabled';
const DEVICE_AUDIO_OUT_VOLUME_KEY = 'wearabllm_v3_device_audio_out_volume';
const DEVICE_TTS_ENABLED_KEY = 'wearabllm_v3_device_tts_enabled';
const DEVICE_TTS_MAX_BYTES_KEY = 'wearabllm_v3_device_tts_max_bytes';
const DEVICE_LED_SELF_TEST_KEY = 'wearabllm_v3_device_led_self_test';
const DEVICE_DISPLAY_ENABLED_KEY = 'wearabllm_v3_device_display_enabled';
const DEVICE_DISPLAY_SELF_TEST_KEY = 'wearabllm_v3_device_display_self_test';
const DEFAULT_BRIDGE_URL = 'https://brick-factorial-wearabllm-agent.hf.space';
const APP_DEVICE_ID_KEY = 'wearabllm_v3_app_device_id';
const ANDROID_BODY_DEVICE_ID = 'wearabllm-android';

export async function loadAppDeviceId(): Promise<string> {
  const existing = await SecureStore.getItemAsync(APP_DEVICE_ID_KEY);
  if (existing === ANDROID_BODY_DEVICE_ID) {
    return existing;
  }
  await SecureStore.setItemAsync(APP_DEVICE_ID_KEY, ANDROID_BODY_DEVICE_ID);
  return ANDROID_BODY_DEVICE_ID;
}

export async function loadBridgeUrl(): Promise<string> {
  return normalizeBridgeBaseUrl((await SecureStore.getItemAsync(BRIDGE_URL_KEY)) || DEFAULT_BRIDGE_URL);
}

export async function saveBridgeUrl(url: string): Promise<void> {
  await SecureStore.setItemAsync(BRIDGE_URL_KEY, normalizeBridgeBaseUrl(url));
}

export async function loadBridgeToken(): Promise<string> {
  return (await SecureStore.getItemAsync(BRIDGE_TOKEN_KEY)) || '';
}

export async function saveBridgeToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(BRIDGE_TOKEN_KEY, token.trim());
}

export interface DeviceWifiSettings {
  ssid: string;
  password: string;
  bssid: string;
  pttGpio: string;
  pttActiveLevel: string;
  pttDebounceMs: string;
  pttPull: string;
  audioOutEnabled: boolean;
  audioOutVolume: string;
  ttsEnabled: boolean;
  ttsMaxBytes: string;
  ledSelfTest: boolean;
  displayEnabled: boolean;
  displaySelfTest: boolean;
}

export async function loadDeviceWifiSettings(): Promise<DeviceWifiSettings> {
  const [
    ssid,
    password,
    bssid,
    pttGpio,
    pttActiveLevel,
    pttDebounceMs,
    pttPull,
    audioOutEnabled,
    audioOutVolume,
    ttsEnabled,
    ttsMaxBytes,
    ledSelfTest,
    displayEnabled,
    displaySelfTest,
  ] = await Promise.all([
    SecureStore.getItemAsync(DEVICE_WIFI_SSID_KEY),
    SecureStore.getItemAsync(DEVICE_WIFI_PASSWORD_KEY),
    SecureStore.getItemAsync(DEVICE_WIFI_BSSID_KEY),
    SecureStore.getItemAsync(DEVICE_PTT_GPIO_KEY),
    SecureStore.getItemAsync(DEVICE_PTT_ACTIVE_LEVEL_KEY),
    SecureStore.getItemAsync(DEVICE_PTT_DEBOUNCE_MS_KEY),
    SecureStore.getItemAsync(DEVICE_PTT_PULL_KEY),
    SecureStore.getItemAsync(DEVICE_AUDIO_OUT_ENABLED_KEY),
    SecureStore.getItemAsync(DEVICE_AUDIO_OUT_VOLUME_KEY),
    SecureStore.getItemAsync(DEVICE_TTS_ENABLED_KEY),
    SecureStore.getItemAsync(DEVICE_TTS_MAX_BYTES_KEY),
    SecureStore.getItemAsync(DEVICE_LED_SELF_TEST_KEY),
    SecureStore.getItemAsync(DEVICE_DISPLAY_ENABLED_KEY),
    SecureStore.getItemAsync(DEVICE_DISPLAY_SELF_TEST_KEY),
  ]);
  return {
    ssid: ssid ?? '',
    password: password ?? '',
    bssid: bssid ?? '',
    pttGpio: pttGpio ?? '0',
    pttActiveLevel: pttActiveLevel ?? '0',
    pttDebounceMs: pttDebounceMs ?? '35',
    pttPull: pttPull ?? 'up',
    audioOutEnabled: audioOutEnabled === 'true',
    audioOutVolume: audioOutVolume ?? '45',
    ttsEnabled: ttsEnabled === 'true',
    ttsMaxBytes: ttsMaxBytes ?? '131072',
    ledSelfTest: ledSelfTest === 'true',
    displayEnabled: displayEnabled === 'true',
    displaySelfTest: displaySelfTest === 'true',
  };
}

export async function saveDeviceWifiSettings(settings: DeviceWifiSettings): Promise<void> {
  await Promise.all([
    SecureStore.setItemAsync(DEVICE_WIFI_SSID_KEY, settings.ssid),
    SecureStore.setItemAsync(DEVICE_WIFI_PASSWORD_KEY, settings.password),
    SecureStore.setItemAsync(DEVICE_WIFI_BSSID_KEY, settings.bssid),
    SecureStore.setItemAsync(DEVICE_PTT_GPIO_KEY, settings.pttGpio),
    SecureStore.setItemAsync(DEVICE_PTT_ACTIVE_LEVEL_KEY, settings.pttActiveLevel),
    SecureStore.setItemAsync(DEVICE_PTT_DEBOUNCE_MS_KEY, settings.pttDebounceMs),
    SecureStore.setItemAsync(DEVICE_PTT_PULL_KEY, settings.pttPull),
    SecureStore.setItemAsync(DEVICE_AUDIO_OUT_ENABLED_KEY, String(settings.audioOutEnabled)),
    SecureStore.setItemAsync(DEVICE_AUDIO_OUT_VOLUME_KEY, settings.audioOutVolume),
    SecureStore.setItemAsync(DEVICE_TTS_ENABLED_KEY, String(settings.ttsEnabled)),
    SecureStore.setItemAsync(DEVICE_TTS_MAX_BYTES_KEY, settings.ttsMaxBytes),
    SecureStore.setItemAsync(DEVICE_LED_SELF_TEST_KEY, String(settings.ledSelfTest)),
    SecureStore.setItemAsync(DEVICE_DISPLAY_ENABLED_KEY, String(settings.displayEnabled)),
    SecureStore.setItemAsync(DEVICE_DISPLAY_SELF_TEST_KEY, String(settings.displaySelfTest)),
  ]);
}
