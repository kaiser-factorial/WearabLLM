import { isLEDCommand, LEDCommand } from './commands';

export interface BridgeResponse {
  command: LEDCommand;
  reply: string;
  transcript: string;
  audio_bytes: number;
  saved_wav: string | null;
  wav_info: BridgeWavInfo | null;
  sources: BridgeSource[];
  tool_results: BridgeToolActivity[];
  persistence: BridgePersistence;
}

export type BridgePersistenceStatus =
  | 'persisted'
  | 'failed'
  | 'skipped'
  | 'not_configured'
  | 'unknown';

export interface BridgePersistence {
  status: BridgePersistenceStatus;
  backend: string;
  session_id: string | null;
  error_code?: string;
  message?: string;
}

export interface BridgeSource {
  url: string;
  title: string;
}

export interface BridgeToolActivity {
  name: string;
  ok: boolean;
  summary: string;
}

export type ActionStatus =
  | 'queued'
  | 'dispatched'
  | 'delivered'
  | 'rendered'
  | 'tts_started'
  | 'completed'
  | 'played'
  | 'failed'
  | 'expired';

export type ExpressionChannel = 'visual' | 'display' | 'audio';

export interface SphereExpression {
  version: 1;
  command: LEDCommand;
  text: string;
  channels: ExpressionChannel[];
}

export interface BridgeAction {
  id: string;
  origin_device_id: string;
  target_device_id: string;
  transcript: string;
  command: LEDCommand;
  reply: string;
  action_type: 'expression';
  expression: SphereExpression;
  status: ActionStatus;
  attempts: number;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  error: string | null;
}

export interface BridgeInteractionResponse extends BridgeResponse {
  action: BridgeAction;
  action_created: boolean;
}

export interface BridgeDevice {
  id: string;
  label: string;
  kind: string;
  status: 'active' | 'planned' | string;
  description: string;
  seen: boolean;
  online?: boolean;
  last_seen_at?: string | null;
}

export interface BridgeConversationTurn {
  id: string | number;
  device_id: string;
  role: 'user' | 'assistant';
  content: string;
  sources: BridgeSource[];
  tool_results: BridgeToolActivity[];
  created_at: string | null;
  local_session_id?: string;
  persistence_status?: BridgePersistenceStatus;
}

export interface BridgeConversationSession {
  id: string;
  started_at: string | null;
  last_turn_at: string | null;
  ended_at: string | null;
  archived_at: string | null;
  summary: string | null;
  title: string | null;
}

export interface BridgeConversationSnapshot {
  ok: true;
  conversation_backend: string;
  active_session_id: string | null;
  session: BridgeConversationSession | null;
  sessions: BridgeConversationSession[];
  turns: BridgeConversationTurn[];
  devices: BridgeDevice[];
  filter_device_id: string | null;
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
    max_output_tokens?: number;
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
  audio_out_enabled?: boolean;
  audio_out_volume?: number | null;
  tts_enabled?: boolean;
  tts_url?: string | null;
  tts_max_bytes?: number | null;
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
  audio_out_enabled?: boolean | null;
  audio_out_volume?: number | null;
  tts_enabled?: boolean | null;
  tts_max_bytes?: number | null;
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
  audio_out_enabled?: boolean;
  audio_out_volume?: number | null;
  tts_enabled?: boolean;
  tts_max_bytes?: number | null;
  led_self_test?: boolean;
  display_enabled?: boolean;
  display_self_test?: boolean;
}

export interface BridgeBenchSummary {
  readyForDryRun: boolean;
  hasAudioUpload: boolean;
  latestAudioAudible: boolean;
  bridgeTargetMatches?: boolean;
  message: string;
}

function bridgeHeaders(deviceToken: string, contentType = false, deviceId = ''): Record<string, string> {
  const headers: Record<string, string> = {
    'X-WearabLLM-Client': 'android',
    'X-WearabLLM-Client-Version': '0.1.0',
  };
  if (contentType) {
    headers['Content-Type'] = 'application/json';
  }
  if (deviceToken.trim()) {
    headers['X-WearabLLM-Device-Token'] = deviceToken.trim();
  }
  if (deviceId.trim()) {
    headers['X-WearabLLM-Device-Id'] = deviceId.trim();
  }
  return headers;
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
    url.pathname = url.pathname.replace(/\/+$/, '').replace(/\/v[12]\/(query_text|query|tts)$/i, '');
    return url.toString().replace(/\/+$/, '');
  } catch {
    return trimmed.replace(/\/+$/, '').replace(/\/v[12]\/(query_text|query|tts)$/i, '');
  }
}

export function normalizeDeviceWifiBssid(rawBssid: string): string {
  const trimmed = rawBssid.trim();
  if (!trimmed) {
    return '';
  }

  const normalized = trimmed.toLowerCase();
  if (!/^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/.test(normalized)) {
    throw new Error('AP MAC must look like 02:00:00:00:00:01, or be left blank.');
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

export function normalizeAudioOutVolume(rawVolume: string): number | null {
  const trimmed = rawVolume.trim();
  if (!trimmed) {
    return null;
  }
  if (!/^\d+$/.test(trimmed)) {
    throw new Error('Speaker volume must be a whole number, or be left blank for the firmware default.');
  }
  const volume = Number(trimmed);
  if (!Number.isInteger(volume) || volume < 0 || volume > 100) {
    throw new Error('Speaker volume must be between 0 and 100.');
  }
  return volume;
}

export function normalizeTtsMaxBytes(rawBytes: string): number | null {
  const trimmed = rawBytes.trim();
  if (!trimmed) {
    return null;
  }
  if (!/^\d+$/.test(trimmed)) {
    throw new Error('TTS max bytes must be a whole number, or be left blank for the firmware default.');
  }
  const maxBytes = Number(trimmed);
  if (!Number.isInteger(maxBytes) || maxBytes < 4096 || maxBytes > 1048576) {
    throw new Error('TTS max bytes must be between 4096 and 1048576.');
  }
  return maxBytes;
}

export function bridgeTargetKey(rawUrl: string | null | undefined): string {
  if (!rawUrl) {
    return '';
  }
  const normalized = normalizeBridgeBaseUrl(rawUrl);
  if (!normalized) {
    return '';
  }
  try {
    const url = new URL(normalized);
    const defaultPort = url.protocol === 'https:' ? '443' : '80';
    return `${url.hostname}:${url.port || defaultPort}`;
  } catch {
    return normalized.replace(/^https?:\/\//i, '').replace(/\/.*$/, '');
  }
}

export function firmwareBridgeTargetMatchesApp(appBridgeUrl: string, firmwareBridgeUrl: string | null | undefined): boolean | undefined {
  if (!firmwareBridgeUrl) {
    return undefined;
  }
  const appTarget = bridgeTargetKey(appBridgeUrl);
  const firmwareTarget = bridgeTargetKey(firmwareBridgeUrl);
  if (!appTarget || !firmwareTarget) {
    return undefined;
  }
  return appTarget === firmwareTarget;
}

export function summarizeBridgeBenchStatus(health: BridgeHealth, appBridgeUrl = ''): BridgeBenchSummary {
  const firmware = health.config.firmware_config;
  const latestCapture = health.config.latest_capture;
  const firmwareReady = firmware == null || firmware.ready === true;
  const bridgeDryRun = health.config.dry_run === true;
  const hasAudioUpload = (health.config.capture_count ?? 0) > 0 || latestCapture != null;
  const latestAudioAudible = latestCapture?.wav_info?.valid === true && latestCapture.wav_info.appears_silent === false;
  const bridgeTargetMatches = firmwareBridgeTargetMatchesApp(appBridgeUrl, firmware?.bridge_url);

  if (firmware && firmware.available === false) {
    return {
      readyForDryRun: false,
      hasAudioUpload,
      latestAudioAudible,
      bridgeTargetMatches,
      message: `Firmware config unavailable: ${firmware.error ?? 'unknown error'}`,
    };
  }

  if (!firmwareReady) {
    return {
      readyForDryRun: false,
      hasAudioUpload,
      latestAudioAudible,
      bridgeTargetMatches,
      message: 'Set device Wi-Fi and bridge URL, then rebuild and flash.',
    };
  }

  if (bridgeTargetMatches === false) {
    return {
      readyForDryRun: false,
      hasAudioUpload,
      latestAudioAudible,
      bridgeTargetMatches,
      message: 'App bridge URL differs from staged firmware bridge target.',
    };
  }

  if (!bridgeDryRun) {
    return {
      readyForDryRun: false,
      hasAudioUpload,
      latestAudioAudible,
      bridgeTargetMatches,
      message: 'Use dry-run bridge mode for the first board loop.',
    };
  }

  if (!hasAudioUpload) {
    return {
      readyForDryRun: true,
      hasAudioUpload,
      latestAudioAudible,
      bridgeTargetMatches,
      message: 'Ready: flash/monitor, hold PTT, speak, then release.',
    };
  }

  if (!latestAudioAudible) {
    return {
      readyForDryRun: true,
      hasAudioUpload,
      latestAudioAudible,
      bridgeTargetMatches,
      message: 'Bridge received audio; inspect mic path because latest WAV looks silent or invalid.',
    };
  }

  return {
    readyForDryRun: true,
    hasAudioUpload,
    latestAudioAudible,
    bridgeTargetMatches,
    message: 'Board audio reached bridge and looks non-silent.',
  };
}

export async function fetchBridgeHealth(baseUrl: string, deviceToken = ''): Promise<BridgeHealth> {
  const cleanBaseUrl = normalizeBridgeBaseUrl(baseUrl);
  const response = await fetch(`${cleanBaseUrl}/v2/health`, { headers: bridgeHeaders(deviceToken) });
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

  return parseBridgeHealth(unwrapBridgeV2Envelope(parsed));
}

export async function queryBridgeText(
  baseUrl: string,
  transcript: string,
  deviceToken = '',
  originDeviceId = '',
  responseDeviceId = '',
): Promise<BridgeResponse> {
  const cleanBaseUrl = normalizeBridgeBaseUrl(baseUrl);
  const response = await fetch(`${cleanBaseUrl}/v2/query_text`, {
    method: 'POST',
    headers: bridgeHeaders(deviceToken, true, originDeviceId),
    body: JSON.stringify({
      transcript,
      response_device_id: responseDeviceId || undefined,
    }),
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

  return parseBridgeResponse(unwrapBridgeV2Envelope(parsed));
}

export async function createBridgeInteraction(
  baseUrl: string,
  transcript: string,
  originDeviceId: string,
  targetDeviceId: string,
  idempotencyKey: string,
  deviceToken = '',
  responseDeviceId = '',
): Promise<BridgeInteractionResponse> {
  const cleanBaseUrl = normalizeBridgeBaseUrl(baseUrl);
  const response = await fetch(`${cleanBaseUrl}/v2/interactions`, {
    method: 'POST',
    headers: bridgeHeaders(deviceToken, true),
    body: JSON.stringify({
      transcript,
      origin_device_id: originDeviceId,
      target_device_id: targetDeviceId,
      idempotency_key: idempotencyKey,
      response_device_id: responseDeviceId || undefined,
    }),
  });
  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Bridge interaction returned non-JSON response: ${rawText.slice(0, 160)}`);
  }
  if (!response.ok || !parsed || typeof parsed !== 'object') {
    throw new Error(`Bridge interaction error ${response.status}: ${bridgeErrorMessage(parsed, rawText)}`);
  }
  const payload = unwrapBridgeV2Envelope(parsed);
  if (!isLEDCommand(String(payload.command ?? '')) || typeof payload.reply !== 'string') {
    throw new Error('Bridge interaction response is missing a valid action.');
  }
  return {
    ...parseBridgeResponse(payload),
    action: parseBridgeAction(payload.action),
    action_created: Boolean(payload.action_created),
  };
}

export async function fetchBridgeConversation(
  baseUrl: string,
  filterDeviceId = '',
  deviceToken = '',
  sessionId = '',
): Promise<BridgeConversationSnapshot> {
  const cleanBaseUrl = normalizeBridgeBaseUrl(baseUrl);
  const params = new URLSearchParams({ limit: '300' });
  if (filterDeviceId && filterDeviceId !== 'all') {
    params.set('device_id', filterDeviceId);
  }
  if (sessionId) {
    params.set('session_id', sessionId);
  }
  const response = await fetch(`${cleanBaseUrl}/v2/conversation?${params.toString()}`, {
    headers: bridgeHeaders(deviceToken),
  });
  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Bridge conversation returned non-JSON response: ${rawText.slice(0, 160)}`);
  }
  if (!response.ok) {
    throw new Error(`Bridge conversation error ${response.status}: ${bridgeErrorMessage(parsed, rawText)}`);
  }
  return parseBridgeConversation(unwrapBridgeV2Envelope(parsed));
}

export function parseBridgeConversation(payload: unknown): BridgeConversationSnapshot {
  if (typeof payload !== 'object' || payload === null) {
    throw new Error('Bridge conversation must be a JSON object.');
  }
  const data = payload as Record<string, unknown>;
  if (data.ok !== true) {
    throw new Error('Bridge conversation did not report ok=true.');
  }
  const devices = Array.isArray(data.devices)
    ? data.devices
        .map(parseBridgeDevice)
        .filter((device): device is BridgeDevice => device !== null && device.id !== 'local-bridge')
    : [];
  const turns = Array.isArray(data.turns)
    ? data.turns.map(parseBridgeConversationTurn).filter((turn): turn is BridgeConversationTurn => turn !== null)
    : [];
  const sessions = Array.isArray(data.sessions)
    ? data.sessions
        .map(parseBridgeConversationSession)
        .filter((session): session is BridgeConversationSession => session !== null)
    : [];
  return {
    ok: true,
    conversation_backend: String(data.conversation_backend ?? 'unknown'),
    active_session_id: data.active_session_id == null ? null : String(data.active_session_id),
    session: parseBridgeConversationSession(data.session),
    sessions,
    turns,
    devices,
    filter_device_id: data.filter_device_id == null ? null : String(data.filter_device_id),
  };
}

function parseBridgeConversationSession(payload: unknown): BridgeConversationSession | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const data = payload as Record<string, unknown>;
  const id = String(data.id ?? '').trim();
  if (!id) return null;
  const nullable = (value: unknown): string | null => value == null ? null : String(value);
  return {
    id,
    started_at: nullable(data.started_at),
    last_turn_at: nullable(data.last_turn_at),
    ended_at: nullable(data.ended_at),
    archived_at: nullable(data.archived_at),
    summary: nullable(data.summary),
    title: nullable(data.title),
  };
}

export async function archiveBridgeSession(
  baseUrl: string,
  sessionId: string,
  deviceToken = '',
): Promise<{ ok: true; active_session_id: string | null }> {
  const response = await fetch(
    `${normalizeBridgeBaseUrl(baseUrl)}/v2/conversation/sessions/${encodeURIComponent(sessionId)}/archive`,
    { method: 'POST', headers: bridgeHeaders(deviceToken, true), body: '{}' },
  );
  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Bridge archive response was not JSON: ${rawText.slice(0, 160)}`);
  }
  if (!response.ok) {
    throw new Error(`Bridge archive error ${response.status}: ${bridgeErrorMessage(parsed, rawText)}`);
  }
  const data = unwrapBridgeV2Envelope(parsed);
  return { ok: true, active_session_id: data.active_session_id == null ? null : String(data.active_session_id) };
}

export async function renameBridgeSession(
  baseUrl: string,
  sessionId: string,
  title: string,
  deviceToken = '',
): Promise<void> {
  const response = await fetch(
    `${normalizeBridgeBaseUrl(baseUrl)}/v2/conversation/sessions/${encodeURIComponent(sessionId)}/rename`,
    {
      method: 'POST',
      headers: bridgeHeaders(deviceToken, true),
      body: JSON.stringify({ title }),
    },
  );
  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Bridge rename response was not JSON: ${rawText.slice(0, 160)}`);
  }
  if (!response.ok) {
    throw new Error(`Bridge rename error ${response.status}: ${bridgeErrorMessage(parsed, rawText)}`);
  }
  unwrapBridgeV2Envelope(parsed);
}

export async function resetBridgeSession(
  baseUrl: string,
  deviceToken = '',
): Promise<{ ok: true; active_session_id: string | null }> {
  const response = await fetch(`${normalizeBridgeBaseUrl(baseUrl)}/v2/session/reset`, {
    method: 'POST',
    headers: bridgeHeaders(deviceToken, true),
    body: '{}',
  });
  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Bridge new-conversation response was not JSON: ${rawText.slice(0, 160)}`);
  }
  if (!response.ok) {
    throw new Error(`Bridge new-conversation error ${response.status}: ${bridgeErrorMessage(parsed, rawText)}`);
  }
  const data = unwrapBridgeV2Envelope(parsed);
  return {
    ok: true,
    active_session_id: data.active_session_id == null ? null : String(data.active_session_id),
  };
}

export async function sendBridgeHeartbeat(
  baseUrl: string,
  deviceId: string,
  deviceToken = '',
): Promise<void> {
  const response = await fetch(`${normalizeBridgeBaseUrl(baseUrl)}/v2/heartbeat`, {
    method: 'POST',
    headers: bridgeHeaders(deviceToken, true, deviceId),
    body: '{}',
  });
  if (!response.ok) {
    const rawText = await response.text();
    throw new Error(`Bridge heartbeat error ${response.status}: ${rawText.slice(0, 160)}`);
  }
}

function parseBridgeDevice(payload: unknown): BridgeDevice | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const data = payload as Record<string, unknown>;
  const id = String(data.id ?? '').trim();
  if (!id) return null;
  return {
    id,
    label: String(data.label ?? id),
    kind: String(data.kind ?? 'custom'),
    status: String(data.status ?? 'active'),
    description: String(data.description ?? ''),
    seen: Boolean(data.seen),
    online: Boolean(data.online ?? data.seen),
    last_seen_at: data.last_seen_at == null ? null : String(data.last_seen_at),
  };
}

function parseBridgeConversationTurn(payload: unknown): BridgeConversationTurn | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const data = payload as Record<string, unknown>;
  const role = String(data.role ?? '');
  if (role !== 'user' && role !== 'assistant') return null;
  const rawDeviceId = String(data.device_id ?? '').trim();
  const metadata = typeof data.metadata === 'object' && data.metadata !== null
    ? data.metadata as Record<string, unknown>
    : {};
  return {
    id: typeof data.id === 'number' ? data.id : String(data.id ?? ''),
    device_id: rawDeviceId === 'local-bridge' ? 'web-console' : rawDeviceId || 'wearabllm-unknown',
    role,
    content: String(data.content ?? ''),
    sources: parseBridgeSources(metadata.sources),
    tool_results: parseBridgeToolActivity(metadata.tool_results),
    created_at: data.created_at == null ? null : String(data.created_at),
  };
}

function parseBridgeSources(payload: unknown): BridgeSource[] {
  return Array.isArray(payload)
    ? payload
        .filter((source): source is Record<string, unknown> => typeof source === 'object' && source !== null)
        .map((source) => ({ url: String(source.url ?? ''), title: String(source.title ?? source.url ?? '') }))
        .filter((source) => Boolean(source.url))
    : [];
}

function parseBridgeToolActivity(payload: unknown): BridgeToolActivity[] {
  return Array.isArray(payload)
    ? payload
        .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
        .map((item) => ({
          name: String(item.name ?? 'tool'),
          ok: Boolean(item.ok),
          summary: String(item.summary ?? '').trim(),
        }))
        .filter((item) => Boolean(item.summary))
    : [];
}

export async function fetchBridgeAction(baseUrl: string, actionId: string, deviceToken = ''): Promise<BridgeAction> {
  const response = await fetch(`${normalizeBridgeBaseUrl(baseUrl)}/v2/interactions/${encodeURIComponent(actionId)}`, {
    headers: bridgeHeaders(deviceToken),
  });
  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Bridge action returned non-JSON response: ${rawText.slice(0, 160)}`);
  }
  if (!response.ok || !parsed || typeof parsed !== 'object') {
    throw new Error(`Bridge action error ${response.status}: ${bridgeErrorMessage(parsed, rawText)}`);
  }
  return parseBridgeAction(unwrapBridgeV2Envelope(parsed).action);
}

export async function claimBridgeAction(
  baseUrl: string,
  deviceId: string,
  deviceToken = '',
): Promise<BridgeAction | null> {
  const response = await fetch(
    `${normalizeBridgeBaseUrl(baseUrl)}/v2/devices/${encodeURIComponent(deviceId)}/actions`,
    { headers: bridgeHeaders(deviceToken, false, deviceId) },
  );
  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Bridge action claim returned non-JSON response: ${rawText.slice(0, 160)}`);
  }
  if (!response.ok || !parsed || typeof parsed !== 'object') {
    throw new Error(`Bridge action claim error ${response.status}: ${bridgeErrorMessage(parsed, rawText)}`);
  }
  const action = unwrapBridgeV2Envelope(parsed).action;
  return action == null ? null : parseBridgeAction(action);
}

export async function acknowledgeBridgeAction(
  baseUrl: string,
  deviceId: string,
  actionId: string,
  status: 'delivered' | 'rendered' | 'tts_started' | 'completed' | 'played' | 'failed',
  deviceToken = '',
  error = '',
): Promise<BridgeAction> {
  const response = await fetch(
    `${normalizeBridgeBaseUrl(baseUrl)}/v2/devices/${encodeURIComponent(deviceId)}/actions/${encodeURIComponent(actionId)}/ack`,
    {
      method: 'POST',
      headers: bridgeHeaders(deviceToken, true, deviceId),
      body: JSON.stringify({ status, error: error || undefined }),
    },
  );
  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Bridge action acknowledgement returned non-JSON response: ${rawText.slice(0, 160)}`);
  }
  if (!response.ok || !parsed || typeof parsed !== 'object') {
    throw new Error(`Bridge action acknowledgement error ${response.status}: ${bridgeErrorMessage(parsed, rawText)}`);
  }
  return parseBridgeAction(unwrapBridgeV2Envelope(parsed).action);
}

export async function configureDeviceWifi(
  baseUrl: string,
  config: DeviceWifiConfigRequest,
  deviceToken = '',
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
    audio_out_enabled: config.audio_out_enabled,
    audio_out_volume: config.audio_out_volume == null ? undefined : config.audio_out_volume,
    tts_enabled: config.tts_enabled,
    tts_max_bytes: config.tts_max_bytes == null ? undefined : config.tts_max_bytes,
    led_self_test: config.led_self_test,
    display_enabled: config.display_enabled,
    display_self_test: config.display_self_test,
  };
  const response = await fetch(`${cleanBaseUrl}/v2/device_wifi`, {
    method: 'POST',
    headers: bridgeHeaders(deviceToken, true),
    body: JSON.stringify(body),
  });

  const rawText = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error(`Device Wi-Fi config returned non-JSON response: ${rawText.slice(0, 160)}`);
  }

  const payload = response.ok
    ? parseDeviceWifiConfigResponse(unwrapBridgeV2Envelope(parsed))
    : parseDeviceWifiConfigResponse(parsed);
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
    audio_out_enabled: data.audio_out_enabled == null ? undefined : Boolean(data.audio_out_enabled),
    audio_out_volume: data.audio_out_volume == null ? null : Number(data.audio_out_volume),
    tts_enabled: data.tts_enabled == null ? undefined : Boolean(data.tts_enabled),
    tts_url: data.tts_url == null ? null : String(data.tts_url),
    tts_max_bytes: data.tts_max_bytes == null ? null : Number(data.tts_max_bytes),
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
    audio_out_enabled: data.audio_out_enabled == null ? null : Boolean(data.audio_out_enabled),
    audio_out_volume: data.audio_out_volume == null ? null : Number(data.audio_out_volume),
    tts_enabled: data.tts_enabled == null ? null : Boolean(data.tts_enabled),
    tts_max_bytes: data.tts_max_bytes == null ? null : Number(data.tts_max_bytes),
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
    sources: parseBridgeSources(data.sources),
    tool_results: parseBridgeToolActivity(data.tool_results),
    persistence: parseBridgePersistence(data.persistence),
  };
}

export function parseBridgePersistence(payload: unknown): BridgePersistence {
  if (typeof payload !== 'object' || payload === null) {
    return { status: 'unknown', backend: '', session_id: null };
  }
  const data = payload as Record<string, unknown>;
  const rawStatus = String(data.status ?? 'unknown');
  const allowed = new Set<BridgePersistenceStatus>([
    'persisted',
    'failed',
    'skipped',
    'not_configured',
    'unknown',
  ]);
  const status = allowed.has(rawStatus as BridgePersistenceStatus)
    ? rawStatus as BridgePersistenceStatus
    : 'unknown';
  return {
    status,
    backend: String(data.backend ?? ''),
    session_id: data.session_id == null ? null : String(data.session_id),
    error_code: data.error_code == null ? undefined : String(data.error_code),
    message: data.message == null ? undefined : String(data.message),
  };
}

export function parseBridgeAction(payload: unknown): BridgeAction {
  if (typeof payload !== 'object' || payload === null) {
    throw new Error('Bridge action must be a JSON object.');
  }
  const data = payload as Record<string, unknown>;
  const command = String(data.command ?? '').toUpperCase();
  if (!data.id || !isLEDCommand(command)) {
    throw new Error('Bridge action response is invalid.');
  }
  const rawExpression = typeof data.expression === 'object' && data.expression !== null
    ? data.expression as Record<string, unknown>
    : {};
  const expressionCommand = String(rawExpression.command ?? command).toUpperCase();
  if (!isLEDCommand(expressionCommand)) {
    throw new Error('Bridge expression command is invalid.');
  }
  const allowedChannels = new Set<ExpressionChannel>(['visual', 'display', 'audio']);
  const channels = Array.isArray(rawExpression.channels)
    ? rawExpression.channels
        .map((channel) => String(channel) as ExpressionChannel)
        .filter((channel): channel is ExpressionChannel => allowedChannels.has(channel))
    : ['visual', 'display', 'audio'] as ExpressionChannel[];
  return {
    id: String(data.id),
    origin_device_id: String(data.origin_device_id ?? ''),
    target_device_id: String(data.target_device_id ?? ''),
    transcript: String(data.transcript ?? ''),
    command,
    reply: String(data.reply ?? ''),
    action_type: 'expression',
    expression: {
      version: 1,
      command: expressionCommand,
      text: String(rawExpression.text ?? data.reply ?? ''),
      channels: channels.length ? channels : ['display'],
    },
    status: String(data.status ?? 'queued') as ActionStatus,
    attempts: Number(data.attempts ?? 0),
    created_at: String(data.created_at ?? ''),
    updated_at: String(data.updated_at ?? ''),
    expires_at: data.expires_at == null ? null : String(data.expires_at),
    error: data.error == null ? null : String(data.error),
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
      if (typeof data.error === 'object') {
        const typedError = data.error as Record<string, unknown>;
        if (typedError.message != null) {
          return String(typedError.message);
        }
      }
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

export function unwrapBridgeV2Envelope(payload: unknown): Record<string, unknown> {
  if (typeof payload !== 'object' || payload === null) {
    throw new Error('Bridge v2 response must be a JSON object.');
  }
  const envelope = payload as Record<string, unknown>;
  if (envelope.ok !== true) {
    throw new Error(bridgeErrorMessage(payload, 'Bridge v2 request failed.'));
  }
  if (typeof envelope.data !== 'object' || envelope.data === null || Array.isArray(envelope.data)) {
    throw new Error('Bridge v2 success data must be a JSON object.');
  }
  return { ok: true, ...(envelope.data as Record<string, unknown>) };
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
