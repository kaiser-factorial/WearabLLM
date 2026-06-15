import { isLEDCommand, LEDCommand } from './commands';

export interface BridgeResponse {
  command: LEDCommand;
  reply: string;
  transcript: string;
  audio_bytes: number;
  saved_wav: string | null;
  wav_info: BridgeWavInfo | null;
}

export interface BridgeWavInfo {
  valid: boolean;
  sample_rate?: number;
  channels?: number;
  sample_width_bytes?: number;
  frames?: number;
  duration_ms?: number;
  peak_abs?: number;
  peak_dbfs?: number | null;
  rms?: number;
  rms_dbfs?: number | null;
  appears_silent?: boolean;
  error?: string;
}

export interface BridgeHealth {
  ok: boolean;
  service: string;
  config: {
    provider?: string;
    dry_run?: boolean;
    dry_run_command?: string;
    dry_run_sequence?: string[];
    device_config?: boolean;
    stt?: string;
    stt_model?: string;
    llm_model?: string;
    tts_model?: string;
    tts_voice?: string;
    typed_bypass?: boolean;
    save_wav_dir?: string | null;
    capture_count?: number;
    latest_capture?: BridgeCaptureInfo | null;
    max_audio_bytes?: number;
    firmware_config?: FirmwareConfigStatus | null;
  };
}

export interface BridgeCaptureInfo {
  audio_bytes: number;
  saved_wav: string | null;
  wav_info: BridgeWavInfo | null;
  transcript_len: number;
  command: string;
  timestamp: string;
}

export interface FirmwareConfigStatus {
  available: boolean;
  error?: string;
  sdkconfig?: string;
  wifi_ssid_set?: boolean;
  wifi_password_set?: boolean;
  wifi_bssid?: string | null;
  bridge_url?: string | null;
  ptt_gpio?: number | null;
  ptt_active_level?: number | null;
  ptt_debounce_ms?: number | null;
  ptt_pull?: DevicePttPull | null;
  wifi_timeout_ms?: number | null;
  audio_min_capture_ms?: number | null;
  audio_max_seconds?: number | null;
  led_self_test?: boolean;
  display_enabled?: boolean;
  display_self_test?: boolean;
  ready?: boolean;
  next?: string[];
}

export interface DeviceWifiConfigResponse {
  ok: boolean;
  ssid?: string;
  bssid?: string | null;
  password_set?: boolean;
  ptt_gpio?: number | null;
  ptt_active_level?: number | null;
  ptt_debounce_ms?: number | null;
  ptt_pull?: DevicePttPull | null;
  led_self_test?: boolean | null;
  display_enabled?: boolean | null;
  display_self_test?: boolean | null;
  message?: string;
  error?: string;
}

export type DevicePttPull = 'none' | 'up' | 'down';

export interface DeviceWifiConfigRequest {
  ssid: string;
  password: string;
  bssid?: string;
  ptt_gpio?: number | null;
  ptt_active_level?: number | null;
  ptt_debounce_ms?: number | null;
  ptt_pull?: DevicePttPull | '';
  led_self_test?: boolean;
  display_enabled?: boolean;
  display_self_test?: boolean;
}

export interface BridgeBenchSummary {
  readyForDryRun: boolean;
  hasAudioUpload: boolean;
  latestAudioAudible: boolean;
  message: string;
}

export function normalizeBridgeBaseUrl(rawUrl: string): string {
  const trimmed = rawUrl.trim();
  if (!trimmed) {
    return '';
  }

  try {
    const url = new URL(trimmed);
    url.hash = '';
    url.search = '';
    url.pathname = url.pathname.replace(/\/+$/, '').replace(/\/v1\/(query_text|query|tts)$/i, '');
    return url.toString().replace(/\/+$/, '');
  } catch {
    return trimmed.replace(/\/+$/, '').replace(/\/v1\/(query_text|query|tts)$/i, '');
  }
}

export function normalizeDeviceWifiBssid(rawBssid: string): string {
  const trimmed = rawBssid.trim();
  if (!trimmed) {
    return '';
  }

  const normalized = trimmed.toLowerCase();
  if (!/^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/.test(normalized)) {
    throw new Error('AP MAC must look like ca:50:35:23:2b:1f, or be left blank.');
  }
  return normalized;
}

export function normalizePttGpio(rawGpio: string): number | null {
  const trimmed = rawGpio.trim();
  if (!trimmed) {
    return null;
  }
  if (!/^\d+$/.test(trimmed)) {
    throw new Error('PTT GPIO must be a whole number, or be left blank for the firmware default.');
  }
  const gpio = Number(trimmed);
  if (!Number.isInteger(gpio) || gpio < 0 || gpio > 48) {
    throw new Error('PTT GPIO must be between 0 and 48.');
  }
  return gpio;
}

export function normalizePttActiveLevel(rawLevel: string): number | null {
  const trimmed = rawLevel.trim();
  if (!trimmed) {
    return null;
  }
  if (trimmed !== '0' && trimmed !== '1') {
    throw new Error('PTT active level must be 0 or 1.');
  }
  return Number(trimmed);
}

export function normalizePttDebounceMs(rawMs: string): number | null {
  const trimmed = rawMs.trim();
  if (!trimmed) {
    return null;
  }
  if (!/^\d+$/.test(trimmed)) {
    throw new Error('PTT debounce must be a whole number of milliseconds, or be left blank for the firmware default.');
  }
  const debounceMs = Number(trimmed);
  if (!Number.isInteger(debounceMs) || debounceMs < 0 || debounceMs > 250) {
    throw new Error('PTT debounce must be between 0 and 250 ms.');
  }
  return debounceMs;
}

export function normalizePttPull(rawPull: string): DevicePttPull | '' {
  const normalized = rawPull.trim().toLowerCase();
  if (!normalized) {
    return '';
  }
  if (normalized === 'none' || normalized === 'up' || normalized === 'down') {
    return normalized;
  }
  throw new Error('PTT pull must be none, up, or down.');
}

export function summarizeBridgeBenchStatus(health: BridgeHealth): BridgeBenchSummary {
  const firmware = health.config.firmware_config;
  const latestCapture = health.config.latest_capture;
  const firmwareReady = firmware == null || firmware.ready === true;
  const bridgeDryRun = health.config.dry_run === true;
  const hasAudioUpload = (health.config.capture_count ?? 0) > 0 || latestCapture != null;
  const latestAudioAudible = latestCapture?.wav_info?.valid === true && latestCapture.wav_info.appears_silent === false;

  if (firmware && firmware.available === false) {
    return {
      readyForDryRun: false,
      hasAudioUpload,
      latestAudioAudible,
      message: `Firmware config unavailable: ${firmware.error ?? 'unknown error'}`,
    };
  }

  if (!firmwareReady) {
    return {
      readyForDryRun: false,
      hasAudioUpload,
      latestAudioAudible,
      message: 'Set device Wi-Fi and bridge URL, then rebuild and flash.',
    };
  }

  if (!bridgeDryRun) {
    return {
      readyForDryRun: false,
      hasAudioUpload,
      latestAudioAudible,
      message: 'Use dry-run bridge mode for the first board loop.',
    };
  }

  if (!hasAudioUpload) {
    return {
      readyForDryRun: true,
      hasAudioUpload,
      latestAudioAudible,
      message: 'Ready: flash/monitor, hold PTT, speak, then release.',
    };
  }

  if (!latestAudioAudible) {
    return {
      readyForDryRun: true,
      hasAudioUpload,
      latestAudioAudible,
      message: 'Bridge received audio; inspect mic path because latest WAV looks silent or invalid.',
    };
  }

  return {
    readyForDryRun: true,
    hasAudioUpload,
    latestAudioAudible,
    message: 'Board audio reached bridge and looks non-silent.',
  };
}

export async function fetchBridgeHealth(baseUrl: string): Promise<BridgeHealth> {
  const cleanBaseUrl = normalizeBridgeBaseUrl(baseUrl);
  const response = await fetch(`${cleanBaseUrl}/health`);
  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Bridge health returned non-JSON response: ${rawText.slice(0, 160)}`);
  }

  if (!response.ok) {
    throw new Error(`Bridge health error ${response.status}: ${bridgeErrorMessage(parsed, rawText)}`);
  }

  return parseBridgeHealth(parsed);
}

export async function queryBridgeText(baseUrl: string, transcript: string): Promise<BridgeResponse> {
  const cleanBaseUrl = normalizeBridgeBaseUrl(baseUrl);
  const response = await fetch(`${cleanBaseUrl}/v1/query_text`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ transcript }),
  });

  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Bridge returned non-JSON response: ${rawText.slice(0, 160)}`);
  }

  if (!response.ok) {
    throw new Error(`Bridge error ${response.status}: ${bridgeErrorMessage(parsed, rawText)}`);
  }

  return parseBridgeResponse(parsed);
}

export async function configureDeviceWifi(
  baseUrl: string,
  config: DeviceWifiConfigRequest,
): Promise<DeviceWifiConfigResponse> {
  const cleanBaseUrl = normalizeBridgeBaseUrl(baseUrl);
  const normalizedBssid = normalizeDeviceWifiBssid(config.bssid ?? '');
  const pttPull = normalizePttPull(config.ptt_pull ?? '');
  const body: Record<string, string | number | boolean | undefined> = {
    ssid: config.ssid,
    password: config.password,
    bssid: normalizedBssid || undefined,
    ptt_gpio: config.ptt_gpio == null ? undefined : config.ptt_gpio,
    ptt_active_level: config.ptt_active_level == null ? undefined : config.ptt_active_level,
    ptt_debounce_ms: config.ptt_debounce_ms == null ? undefined : config.ptt_debounce_ms,
    ptt_pull: pttPull || undefined,
    led_self_test: config.led_self_test,
    display_enabled: config.display_enabled,
    display_self_test: config.display_self_test,
  };
  const response = await fetch(`${cleanBaseUrl}/v1/device_wifi`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Device Wi-Fi config returned non-JSON response: ${rawText.slice(0, 160)}`);
  }

  const payload = parseDeviceWifiConfigResponse(parsed);
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || bridgeErrorMessage(parsed, rawText) || `Device Wi-Fi config failed with HTTP ${response.status}`);
  }
  return payload;
}

export function parseBridgeHealth(payload: unknown): BridgeHealth {
  if (typeof payload !== 'object' || payload === null) {
    throw new Error('Bridge health must be a JSON object.');
  }

  const data = payload as Record<string, unknown>;
  if (data.ok !== true) {
    throw new Error('Bridge health did not report ok=true.');
  }

  const config = typeof data.config === 'object' && data.config !== null ? (data.config as Record<string, unknown>) : {};

  return {
    ok: true,
    service: String(data.service ?? ''),
    config: {
      provider: config.provider == null ? undefined : String(config.provider),
      dry_run: config.dry_run == null ? undefined : Boolean(config.dry_run),
      dry_run_command: config.dry_run_command == null ? undefined : String(config.dry_run_command),
      dry_run_sequence: parseStringArray(config.dry_run_sequence),
      device_config: config.device_config == null ? undefined : Boolean(config.device_config),
      stt: config.stt == null ? undefined : String(config.stt),
      stt_model: config.stt_model == null ? undefined : String(config.stt_model),
      llm_model: config.llm_model == null ? undefined : String(config.llm_model),
      tts_model: config.tts_model == null ? undefined : String(config.tts_model),
      tts_voice: config.tts_voice == null ? undefined : String(config.tts_voice),
      typed_bypass: config.typed_bypass == null ? undefined : Boolean(config.typed_bypass),
      save_wav_dir: config.save_wav_dir == null ? null : String(config.save_wav_dir),
      capture_count: config.capture_count == null ? undefined : Number(config.capture_count),
      latest_capture: parseBridgeCaptureInfo(config.latest_capture),
      max_audio_bytes: config.max_audio_bytes == null ? undefined : Number(config.max_audio_bytes),
      firmware_config: parseFirmwareConfigStatus(config.firmware_config),
    },
  };
}

export function parseBridgeCaptureInfo(payload: unknown): BridgeCaptureInfo | null {
  if (payload == null) {
    return null;
  }
  if (typeof payload !== 'object') {
    return null;
  }
  const data = payload as Record<string, unknown>;
  return {
    audio_bytes: Number(data.audio_bytes ?? 0),
    saved_wav: data.saved_wav == null ? null : String(data.saved_wav),
    wav_info: parseBridgeWavInfo(data.wav_info),
    transcript_len: Number(data.transcript_len ?? 0),
    command: String(data.command ?? ''),
    timestamp: String(data.timestamp ?? ''),
  };
}

export function parseFirmwareConfigStatus(payload: unknown): FirmwareConfigStatus | null {
  if (payload == null) {
    return null;
  }
  if (typeof payload !== 'object') {
    return {
      available: false,
      error: 'firmware_config was not an object',
    };
  }

  const data = payload as Record<string, unknown>;
  const pttPull = data.ptt_pull == null ? null : normalizePttPull(String(data.ptt_pull)) || null;
  return {
    available: Boolean(data.available),
    error: data.error == null ? undefined : String(data.error),
    sdkconfig: data.sdkconfig == null ? undefined : String(data.sdkconfig),
    wifi_ssid_set: data.wifi_ssid_set == null ? undefined : Boolean(data.wifi_ssid_set),
    wifi_password_set: data.wifi_password_set == null ? undefined : Boolean(data.wifi_password_set),
    wifi_bssid: data.wifi_bssid == null ? null : String(data.wifi_bssid),
    bridge_url: data.bridge_url == null ? null : String(data.bridge_url),
    ptt_gpio: data.ptt_gpio == null ? null : Number(data.ptt_gpio),
    ptt_active_level: data.ptt_active_level == null ? null : Number(data.ptt_active_level),
    ptt_debounce_ms: data.ptt_debounce_ms == null ? null : Number(data.ptt_debounce_ms),
    ptt_pull: pttPull,
    wifi_timeout_ms: data.wifi_timeout_ms == null ? null : Number(data.wifi_timeout_ms),
    audio_min_capture_ms: data.audio_min_capture_ms == null ? null : Number(data.audio_min_capture_ms),
    audio_max_seconds: data.audio_max_seconds == null ? null : Number(data.audio_max_seconds),
    led_self_test: data.led_self_test == null ? undefined : Boolean(data.led_self_test),
    display_enabled: data.display_enabled == null ? undefined : Boolean(data.display_enabled),
    display_self_test: data.display_self_test == null ? undefined : Boolean(data.display_self_test),
    ready: data.ready == null ? undefined : Boolean(data.ready),
    next: parseStringArray(data.next),
  };
}

export function parseDeviceWifiConfigResponse(payload: unknown): DeviceWifiConfigResponse {
  if (typeof payload !== 'object' || payload === null) {
    throw new Error('Device Wi-Fi config response must be a JSON object.');
  }

  const data = payload as Record<string, unknown>;
  const pttPull = data.ptt_pull == null ? null : normalizePttPull(String(data.ptt_pull)) || null;
  return {
    ok: Boolean(data.ok),
    ssid: data.ssid == null ? undefined : String(data.ssid),
    bssid: data.bssid == null ? null : String(data.bssid),
    password_set: data.password_set == null ? undefined : Boolean(data.password_set),
    ptt_gpio: data.ptt_gpio == null ? null : Number(data.ptt_gpio),
    ptt_active_level: data.ptt_active_level == null ? null : Number(data.ptt_active_level),
    ptt_debounce_ms: data.ptt_debounce_ms == null ? null : Number(data.ptt_debounce_ms),
    ptt_pull: pttPull,
    led_self_test: data.led_self_test == null ? null : Boolean(data.led_self_test),
    display_enabled: data.display_enabled == null ? null : Boolean(data.display_enabled),
    display_self_test: data.display_self_test == null ? null : Boolean(data.display_self_test),
    message: data.message == null ? undefined : String(data.message),
    error: data.error == null ? undefined : String(data.error),
  };
}

export function parseBridgeResponse(payload: unknown): BridgeResponse {
  if (typeof payload !== 'object' || payload === null) {
    throw new Error('Bridge response must be a JSON object.');
  }

  const data = payload as Record<string, unknown>;
  const command = String(data.command ?? '').toUpperCase();
  if (!isLEDCommand(command)) {
    throw new Error(`Bridge response has invalid command: ${command || '(missing)'}`);
  }

  return {
    command,
    reply: String(data.reply ?? ''),
    transcript: String(data.transcript ?? ''),
    audio_bytes: Number(data.audio_bytes ?? 0),
    saved_wav: data.saved_wav == null ? null : String(data.saved_wav),
    wav_info: parseBridgeWavInfo(data.wav_info),
  };
}

function parseStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  return value.map((item) => String(item));
}

export function bridgeErrorMessage(payload: unknown, fallbackText = ''): string {
  if (typeof payload === 'object' && payload !== null) {
    const data = payload as Record<string, unknown>;
    if (data.error != null) {
      return String(data.error);
    }
    if (data.reply != null) {
      return String(data.reply);
    }
    if (data.message != null) {
      return String(data.message);
    }
  }
  return fallbackText.slice(0, 160);
}

function parseBridgeWavInfo(value: unknown): BridgeWavInfo | null {
  if (value == null) {
    return null;
  }
  if (typeof value !== 'object') {
    return { valid: false, error: 'wav_info was not an object' };
  }

  const data = value as Record<string, unknown>;
  return {
    valid: Boolean(data.valid),
    sample_rate: data.sample_rate == null ? undefined : Number(data.sample_rate),
    channels: data.channels == null ? undefined : Number(data.channels),
    sample_width_bytes: data.sample_width_bytes == null ? undefined : Number(data.sample_width_bytes),
    frames: data.frames == null ? undefined : Number(data.frames),
    duration_ms: data.duration_ms == null ? undefined : Number(data.duration_ms),
    peak_abs: data.peak_abs == null ? undefined : Number(data.peak_abs),
    peak_dbfs: data.peak_dbfs == null ? null : Number(data.peak_dbfs),
    rms: data.rms == null ? undefined : Number(data.rms),
    rms_dbfs: data.rms_dbfs == null ? null : Number(data.rms_dbfs),
    appears_silent: data.appears_silent == null ? undefined : Boolean(data.appears_silent),
    error: data.error == null ? undefined : String(data.error),
  };
}
