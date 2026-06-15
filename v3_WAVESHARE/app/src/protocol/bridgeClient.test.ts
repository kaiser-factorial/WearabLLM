import {
  bridgeErrorMessage,
  normalizeBridgeBaseUrl,
  normalizeDeviceWifiBssid,
  normalizePttActiveLevel,
  normalizePttDebounceMs,
  normalizePttGpio,
  normalizePttPull,
  parseBridgeHealth,
  parseDeviceWifiConfigResponse,
  summarizeBridgeBenchStatus,
} from './bridgeClient';

function assertEqual(actual: string, expected: string): void {
  if (actual !== expected) {
    throw new Error(`Expected ${expected}, got ${actual}`);
  }
}

assertEqual(normalizeBridgeBaseUrl('http://192.168.86.31:8765'), 'http://192.168.86.31:8765');
assertEqual(normalizeBridgeBaseUrl('http://192.168.86.31:8765/'), 'http://192.168.86.31:8765');
assertEqual(normalizeBridgeBaseUrl('http://192.168.86.31:8765/v1/query'), 'http://192.168.86.31:8765');
assertEqual(normalizeBridgeBaseUrl('http://192.168.86.31:8765/v1/query_text'), 'http://192.168.86.31:8765');
assertEqual(normalizeBridgeBaseUrl('http://192.168.86.31:8765/v1/tts'), 'http://192.168.86.31:8765');
assertEqual(normalizeBridgeBaseUrl('  http://192.168.86.31:8765/v1/query?x=1#frag  '), 'http://192.168.86.31:8765');
assertEqual(normalizeDeviceWifiBssid(''), '');
assertEqual(normalizeDeviceWifiBssid('  CA:50:35:23:2B:1F  '), 'ca:50:35:23:2b:1f');
assertEqual(String(normalizePttGpio(' 8 ')), '8');
assertEqual(String(normalizePttActiveLevel('1')), '1');
assertEqual(String(normalizePttDebounceMs(' 45 ')), '45');
assertEqual(normalizePttPull(' DOWN '), 'down');
assertEqual(normalizePttPull(''), '');
assertEqual(bridgeErrorMessage({ error: 'Missing transcript' }, 'fallback'), 'Missing transcript');
assertEqual(bridgeErrorMessage({ reply: 'Bridge error: bad audio' }, 'fallback'), 'Bridge error: bad audio');
assertEqual(bridgeErrorMessage({ message: 'Updated' }, 'fallback'), 'Updated');
assertEqual(bridgeErrorMessage({}, 'plain text failure'), 'plain text failure');

try {
  normalizeDeviceWifiBssid('ca:50:35');
  throw new Error('Expected invalid BSSID to throw');
} catch (error) {
  if (!(error instanceof Error) || !error.message.includes('AP MAC')) {
    throw error;
  }
}

try {
  normalizePttGpio('abc');
  throw new Error('Expected invalid PTT GPIO to throw');
} catch (error) {
  if (!(error instanceof Error) || !error.message.includes('PTT GPIO')) {
    throw error;
  }
}

try {
  normalizePttActiveLevel('2');
  throw new Error('Expected invalid PTT active level to throw');
} catch (error) {
  if (!(error instanceof Error) || !error.message.includes('active level')) {
    throw error;
  }
}

try {
  normalizePttPull('floating');
  throw new Error('Expected invalid PTT pull to throw');
} catch (error) {
  if (!(error instanceof Error) || !error.message.includes('PTT pull')) {
    throw error;
  }
}

try {
  normalizePttDebounceMs('300');
  throw new Error('Expected invalid PTT debounce to throw');
} catch (error) {
  if (!(error instanceof Error) || !error.message.includes('debounce')) {
    throw error;
  }
}

const health = parseBridgeHealth({
  ok: true,
  service: 'wearabllm-bridge',
  config: {
    dry_run: true,
    dry_run_command: 'PP',
    dry_run_sequence: ['GS', 'RF'],
    device_config: true,
    max_audio_bytes: 524288,
    capture_count: 2,
    latest_capture: {
      audio_bytes: 32044,
      saved_wav: '/tmp/wearabllm.wav',
      wav_info: {
        valid: true,
        duration_ms: 1000,
        appears_silent: false,
      },
      transcript_len: 17,
      command: 'GS',
      timestamp: '2026-06-13T12:00:00-0700',
    },
    firmware_config: {
      available: true,
      wifi_ssid_set: true,
      wifi_password_set: true,
      wifi_bssid: 'ca:50:35:23:2b:1f',
      bridge_url: 'http://192.168.86.31:8765/v1/query',
      ptt_gpio: 0,
      ptt_active_level: 0,
      ptt_debounce_ms: 35,
      ptt_pull: 'up',
      led_self_test: true,
      display_enabled: true,
      display_self_test: false,
      ready: true,
      next: [],
    },
  },
});

if (health.config.dry_run_sequence?.join(',') !== 'GS,RF') {
  throw new Error(`Expected dry_run_sequence to parse, got ${health.config.dry_run_sequence}`);
}

if (health.config.device_config !== true) {
  throw new Error('Expected device_config to parse');
}

if (health.config.max_audio_bytes !== 524288) {
  throw new Error(`Expected max_audio_bytes to parse, got ${health.config.max_audio_bytes}`);
}

if (
  health.config.capture_count !== 2 ||
  health.config.latest_capture?.audio_bytes !== 32044 ||
  health.config.latest_capture.wav_info?.appears_silent !== false ||
  health.config.latest_capture.command !== 'GS'
) {
  throw new Error('Expected latest_capture to parse');
}

if (
  health.config.firmware_config?.available !== true ||
  health.config.firmware_config.ptt_pull !== 'up' ||
  health.config.firmware_config.ptt_debounce_ms !== 35 ||
  health.config.firmware_config.led_self_test !== true ||
  health.config.firmware_config.display_enabled !== true ||
  health.config.firmware_config.display_self_test !== false ||
  health.config.firmware_config.ready !== true
) {
  throw new Error('Expected firmware_config to parse');
}

const readySummary = summarizeBridgeBenchStatus(parseBridgeHealth({
  ok: true,
  service: 'wearabllm-bridge',
  config: {
    dry_run: true,
    capture_count: 0,
    latest_capture: null,
    firmware_config: {
      available: true,
      ready: true,
    },
  },
}));

if (!readySummary.readyForDryRun || readySummary.hasAudioUpload || !readySummary.message.includes('Ready')) {
  throw new Error(`Expected ready/no-audio summary, got ${JSON.stringify(readySummary)}`);
}

const notReadySummary = summarizeBridgeBenchStatus(parseBridgeHealth({
  ok: true,
  service: 'wearabllm-bridge',
  config: {
    dry_run: true,
    firmware_config: {
      available: true,
      ready: false,
    },
  },
}));

if (notReadySummary.readyForDryRun || !notReadySummary.message.includes('Set device Wi-Fi')) {
  throw new Error(`Expected not-ready summary, got ${JSON.stringify(notReadySummary)}`);
}

const silentSummary = summarizeBridgeBenchStatus(parseBridgeHealth({
  ok: true,
  service: 'wearabllm-bridge',
  config: {
    dry_run: true,
    capture_count: 1,
    latest_capture: {
      audio_bytes: 32044,
      wav_info: {
        valid: true,
        appears_silent: true,
      },
    },
  },
}));

if (!silentSummary.readyForDryRun || !silentSummary.hasAudioUpload || silentSummary.latestAudioAudible) {
  throw new Error(`Expected silent latest-audio summary, got ${JSON.stringify(silentSummary)}`);
}

const audibleSummary = summarizeBridgeBenchStatus(health);
if (!audibleSummary.readyForDryRun || !audibleSummary.latestAudioAudible || !audibleSummary.message.includes('non-silent')) {
  throw new Error(`Expected audible latest-audio summary, got ${JSON.stringify(audibleSummary)}`);
}

const wifiConfig = parseDeviceWifiConfigResponse({
  ok: true,
  ssid: 'hyperplex',
  bssid: 'ca:50:35:23:2b:1f',
  password_set: true,
  ptt_gpio: 8,
  ptt_active_level: 1,
  ptt_debounce_ms: 45,
  ptt_pull: 'down',
  led_self_test: true,
  display_enabled: true,
  display_self_test: true,
  message: 'Updated',
});

if (
  !wifiConfig.ok ||
  wifiConfig.ssid !== 'hyperplex' ||
  wifiConfig.bssid !== 'ca:50:35:23:2b:1f' ||
  wifiConfig.password_set !== true ||
  wifiConfig.ptt_gpio !== 8 ||
  wifiConfig.ptt_active_level !== 1 ||
  wifiConfig.ptt_debounce_ms !== 45 ||
  wifiConfig.ptt_pull !== 'down' ||
  wifiConfig.led_self_test !== true ||
  wifiConfig.display_enabled !== true ||
  wifiConfig.display_self_test !== true
) {
  throw new Error('Expected device config response to parse');
}

console.log('bridgeClient tests passed');
