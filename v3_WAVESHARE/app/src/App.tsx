import { StatusBar } from 'expo-status-bar';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import {
  configureDeviceWifi,
  fetchBridgeHealth,
  normalizeDeviceWifiBssid,
  normalizeBridgeBaseUrl,
  normalizePttActiveLevel,
  normalizePttDebounceMs,
  normalizePttGpio,
  normalizePttPull,
  queryBridgeText,
  summarizeBridgeBenchStatus,
  BridgeHealth,
  BridgeResponse,
} from './protocol/bridgeClient';
import { COMMAND_COLORS, COMMAND_DESCRIPTIONS } from './protocol/commands';
import {
  loadBridgeUrl,
  loadDeviceWifiSettings,
  saveBridgeUrl,
  saveDeviceWifiSettings,
} from './storage/settings';
import { VoiceListener } from './audio/VoiceListener';

type HistoryEntry = BridgeResponse & {
  time: string;
};

function formatBytes(bytes?: number): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes <= 0) {
    return 'unknown';
  }
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }
  if (bytes >= 1024) {
    return `${Math.round(bytes / 1024)} KiB`;
  }
  return `${bytes} B`;
}

export default function App() {
  const [bridgeUrl, setBridgeUrl] = useState('http://192.168.1.10:8765');
  const [deviceWifiSsid, setDeviceWifiSsid] = useState('');
  const [deviceWifiPassword, setDeviceWifiPassword] = useState('');
  const [deviceWifiBssid, setDeviceWifiBssid] = useState('');
  const [devicePttGpio, setDevicePttGpio] = useState('0');
  const [devicePttActiveLevel, setDevicePttActiveLevel] = useState('0');
  const [devicePttDebounceMs, setDevicePttDebounceMs] = useState('35');
  const [devicePttPull, setDevicePttPull] = useState('up');
  const [deviceLedSelfTest, setDeviceLedSelfTest] = useState(false);
  const [deviceDisplayEnabled, setDeviceDisplayEnabled] = useState(false);
  const [deviceDisplaySelfTest, setDeviceDisplaySelfTest] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [bridgeHealth, setBridgeHealth] = useState<BridgeHealth | null>(null);
  const [lastResponse, setLastResponse] = useState<BridgeResponse | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [isCheckingHealth, setIsCheckingHealth] = useState(false);
  const [isConfiguringWifi, setIsConfiguringWifi] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [status, setStatus] = useState('Bridge idle');
  const voiceListenerRef = useRef<VoiceListener | null>(null);

  useEffect(() => {
    loadBridgeUrl().then(setBridgeUrl).catch(() => undefined);
    loadDeviceWifiSettings()
      .then((settings) => {
        setDeviceWifiSsid(settings.ssid);
        setDeviceWifiPassword(settings.password);
        setDeviceWifiBssid(settings.bssid);
        setDevicePttGpio(settings.pttGpio);
        setDevicePttActiveLevel(settings.pttActiveLevel);
        setDevicePttDebounceMs(settings.pttDebounceMs);
        setDevicePttPull(settings.pttPull);
        setDeviceLedSelfTest(settings.ledSelfTest);
        setDeviceDisplayEnabled(settings.displayEnabled);
        setDeviceDisplaySelfTest(settings.displaySelfTest);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const listener = new VoiceListener();
    listener.onPartial = (partial) => {
      setTranscript(partial);
      setStatus('Listening');
    };
    listener.onResult = (result) => {
      setTranscript(result);
      setIsListening(false);
      void submitTranscript(result);
    };
    listener.onError = (message) => {
      setIsListening(false);
      setStatus('Speech recognition failed');
      Alert.alert('Speech recognition failed', message);
    };
    voiceListenerRef.current = listener;

    return () => {
      listener.destroy();
      voiceListenerRef.current = null;
    };
  }, [bridgeUrl]);

  const commandColor = useMemo(
    () => (lastResponse ? COMMAND_COLORS[lastResponse.command] : '#64748b'),
    [lastResponse],
  );
  const bridgeBenchSummary = useMemo(
    () => (bridgeHealth ? summarizeBridgeBenchStatus(bridgeHealth) : null),
    [bridgeHealth],
  );

  async function handleSaveBridgeUrl() {
    const normalizedUrl = normalizeBridgeBaseUrl(bridgeUrl);
    setBridgeUrl(normalizedUrl);
    await saveBridgeUrl(normalizedUrl);
    setStatus('Bridge URL saved');
  }

  async function handleCheckBridge() {
    setIsCheckingHealth(true);
    setStatus('Checking bridge health');
    try {
      const normalizedUrl = normalizeBridgeBaseUrl(bridgeUrl);
      setBridgeUrl(normalizedUrl);
      await saveBridgeUrl(normalizedUrl);
      const health = await fetchBridgeHealth(normalizedUrl);
      setBridgeHealth(health);
      setStatus(health.config.dry_run ? 'Bridge reachable in dry-run mode' : 'Bridge reachable in live mode');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setBridgeHealth(null);
      setStatus('Bridge health check failed');
      Alert.alert('Bridge health check failed', message);
    } finally {
      setIsCheckingHealth(false);
    }
  }

  async function handleSaveDeviceWifi() {
    let normalizedBssid = '';
    let normalizedPttGpio: number | null = null;
    let normalizedPttActiveLevel: number | null = null;
    let normalizedPttDebounceMs: number | null = null;
    let normalizedPttPull = '';
    try {
      normalizedBssid = normalizeDeviceWifiBssid(deviceWifiBssid);
      normalizedPttGpio = normalizePttGpio(devicePttGpio);
      normalizedPttActiveLevel = normalizePttActiveLevel(devicePttActiveLevel);
      normalizedPttDebounceMs = normalizePttDebounceMs(devicePttDebounceMs);
      normalizedPttPull = normalizePttPull(devicePttPull);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      Alert.alert('Invalid device config', message);
      setStatus('Device config invalid');
      return;
    }

    await saveDeviceWifiSettings({
      ssid: deviceWifiSsid.trim(),
      password: deviceWifiPassword,
      bssid: normalizedBssid,
      pttGpio: normalizedPttGpio == null ? '' : String(normalizedPttGpio),
      pttActiveLevel: normalizedPttActiveLevel == null ? '' : String(normalizedPttActiveLevel),
      pttDebounceMs: normalizedPttDebounceMs == null ? '' : String(normalizedPttDebounceMs),
      pttPull: normalizedPttPull,
      ledSelfTest: deviceLedSelfTest,
      displayEnabled: deviceDisplayEnabled || deviceDisplaySelfTest,
      displaySelfTest: deviceDisplaySelfTest,
    });
    setDeviceDisplayEnabled(deviceDisplayEnabled || deviceDisplaySelfTest);
    setDeviceWifiBssid(normalizedBssid);
    setDevicePttGpio(normalizedPttGpio == null ? '' : String(normalizedPttGpio));
    setDevicePttActiveLevel(normalizedPttActiveLevel == null ? '' : String(normalizedPttActiveLevel));
    setDevicePttDebounceMs(normalizedPttDebounceMs == null ? '' : String(normalizedPttDebounceMs));
    setDevicePttPull(normalizedPttPull);
    setStatus('Device config saved locally');
  }

  async function handleConfigureDeviceWifi() {
    const ssid = deviceWifiSsid.trim();
    if (!ssid || !deviceWifiPassword) {
      Alert.alert('Missing Wi-Fi credentials', 'Enter both the device Wi-Fi name and password.');
      return;
    }

    setIsConfiguringWifi(true);
    setStatus('Sending device config to bridge');
    try {
      const normalizedBssid = normalizeDeviceWifiBssid(deviceWifiBssid);
      const normalizedPttGpio = normalizePttGpio(devicePttGpio);
      const normalizedPttActiveLevel = normalizePttActiveLevel(devicePttActiveLevel);
      const normalizedPttDebounceMs = normalizePttDebounceMs(devicePttDebounceMs);
      const normalizedPttPull = normalizePttPull(devicePttPull);
      await saveDeviceWifiSettings({
        ssid,
        password: deviceWifiPassword,
        bssid: normalizedBssid,
        pttGpio: normalizedPttGpio == null ? '' : String(normalizedPttGpio),
        pttActiveLevel: normalizedPttActiveLevel == null ? '' : String(normalizedPttActiveLevel),
        pttDebounceMs: normalizedPttDebounceMs == null ? '' : String(normalizedPttDebounceMs),
        pttPull: normalizedPttPull,
        ledSelfTest: deviceLedSelfTest,
        displayEnabled: deviceDisplayEnabled || deviceDisplaySelfTest,
        displaySelfTest: deviceDisplaySelfTest,
      });
      setDeviceDisplayEnabled(deviceDisplayEnabled || deviceDisplaySelfTest);
      setDeviceWifiBssid(normalizedBssid);
      setDevicePttGpio(normalizedPttGpio == null ? '' : String(normalizedPttGpio));
      setDevicePttActiveLevel(normalizedPttActiveLevel == null ? '' : String(normalizedPttActiveLevel));
      setDevicePttDebounceMs(normalizedPttDebounceMs == null ? '' : String(normalizedPttDebounceMs));
      setDevicePttPull(normalizedPttPull);
      const normalizedUrl = normalizeBridgeBaseUrl(bridgeUrl);
      setBridgeUrl(normalizedUrl);
      await saveBridgeUrl(normalizedUrl);
      const response = await configureDeviceWifi(normalizedUrl, {
        ssid,
        password: deviceWifiPassword,
        bssid: normalizedBssid,
        ptt_gpio: normalizedPttGpio,
        ptt_active_level: normalizedPttActiveLevel,
        ptt_debounce_ms: normalizedPttDebounceMs,
        ptt_pull: normalizedPttPull,
        led_self_test: deviceLedSelfTest,
        display_enabled: deviceDisplayEnabled || deviceDisplaySelfTest,
        display_self_test: deviceDisplaySelfTest,
      });
      setStatus('Device config saved for next flash');
      Alert.alert('Device config updated', response.message || 'Rebuild and flash firmware for changes to take effect.');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setStatus('Device config failed');
      Alert.alert('Device config failed', message);
    } finally {
      setIsConfiguringWifi(false);
    }
  }

  async function submitTranscript(value: string) {
    const cleanTranscript = value.trim();
    if (!cleanTranscript) {
      Alert.alert('Missing transcript', 'Type a test phrase before sending.');
      return;
    }

    setIsSending(true);
    setStatus('Sending transcript to bridge');
    try {
      const normalizedUrl = normalizeBridgeBaseUrl(bridgeUrl);
      setBridgeUrl(normalizedUrl);
      await saveBridgeUrl(normalizedUrl);
      const response = await queryBridgeText(normalizedUrl, cleanTranscript);
      setLastResponse(response);
      setHistory((current) => [
        { ...response, time: new Date().toLocaleTimeString() },
        ...current.slice(0, 9),
      ]);
      setStatus('Bridge response received');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setStatus('Bridge request failed');
      Alert.alert('Bridge request failed', message);
    } finally {
      setIsSending(false);
    }
  }

  async function handleSend() {
    await submitTranscript(transcript);
  }

  async function handleListenStart() {
    if (isSending || isListening) {
      return;
    }
    setTranscript('');
    setIsListening(true);
    setStatus('Listening');
    await voiceListenerRef.current?.start();
  }

  async function handleListenStop() {
    if (!isListening) {
      return;
    }
    setStatus('Processing speech');
    await voiceListenerRef.current?.stop();
  }

  function setDisplayEnabled(value: boolean) {
    setDeviceDisplayEnabled(value);
    if (!value) {
      setDeviceDisplaySelfTest(false);
    }
  }

  function setDisplaySelfTest(value: boolean) {
    setDeviceDisplaySelfTest(value);
    if (value) {
      setDeviceDisplayEnabled(true);
    }
  }

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <View style={styles.header}>
          <Text style={styles.title}>WearabLLM v3</Text>
          <Text style={styles.subtitle}>Android bridge test console</Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.label}>Bridge URL</Text>
          <View style={styles.row}>
            <TextInput
              value={bridgeUrl}
              onChangeText={setBridgeUrl}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              placeholder="http://192.168.1.23:8765"
              placeholderTextColor="#64748b"
              style={[styles.input, styles.urlInput]}
            />
            <Pressable style={styles.secondaryButton} onPress={handleSaveBridgeUrl}>
              <Text style={styles.secondaryButtonText}>Save</Text>
            </Pressable>
            <Pressable
              style={[styles.secondaryButton, isCheckingHealth && styles.disabledButton]}
              onPress={handleCheckBridge}
              disabled={isCheckingHealth}
            >
              <Text style={styles.secondaryButtonText}>{isCheckingHealth ? 'Checking' : 'Check'}</Text>
            </Pressable>
          </View>
          {bridgeHealth ? (
            <View style={styles.healthPanel}>
              <View style={styles.healthHeader}>
                <Text style={styles.healthTitle}>
                  {bridgeHealth.config.dry_run ? `Dry Run ${bridgeHealth.config.dry_run_command ?? ''}` : 'Live API'}
                </Text>
                <Text style={styles.healthService}>{bridgeHealth.service || 'wearabllm-bridge'}</Text>
              </View>
              {bridgeBenchSummary ? (
                <View style={styles.benchSummaryRow}>
                  <View
                    style={[
                      styles.benchSummaryDot,
                      {
                        backgroundColor: bridgeBenchSummary.latestAudioAudible
                          ? '#22c55e'
                          : bridgeBenchSummary.readyForDryRun
                            ? '#f59e0b'
                            : '#ef4444',
                      },
                    ]}
                  />
                  <Text style={styles.benchSummaryText}>{bridgeBenchSummary.message}</Text>
                </View>
              ) : null}
              <Text style={styles.healthLine}>STT: {bridgeHealth.config.stt ?? 'unknown'} / {bridgeHealth.config.stt_model ?? 'unknown'}</Text>
              <Text style={styles.healthLine}>LLM: {bridgeHealth.config.llm_model ?? 'unknown'}</Text>
              <Text style={styles.healthLine}>TTS: {bridgeHealth.config.tts_model ?? 'unknown'} / {bridgeHealth.config.tts_voice ?? 'unknown'}</Text>
              <Text style={styles.healthLine}>Audio cap: {formatBytes(bridgeHealth.config.max_audio_bytes)}</Text>
              {bridgeHealth.config.dry_run_sequence?.length ? (
                <Text style={styles.healthLine}>Sequence: {bridgeHealth.config.dry_run_sequence.join(' -> ')}</Text>
              ) : null}
              <Text style={styles.healthLine}>
                Device config: {bridgeHealth.config.device_config ? 'enabled' : 'disabled'}
              </Text>
              {bridgeHealth.config.firmware_config ? (
                bridgeHealth.config.firmware_config.available ? (
                  <>
                    <Text style={styles.healthLine}>
                      Firmware ready: {bridgeHealth.config.firmware_config.ready ? 'yes' : 'no'}
                    </Text>
                    <Text style={styles.healthLine}>
                      Device Wi-Fi: {bridgeHealth.config.firmware_config.wifi_ssid_set ? 'SSID set' : 'SSID empty'} / {bridgeHealth.config.firmware_config.wifi_password_set ? 'password set' : 'password empty'}
                    </Text>
                    <Text style={styles.healthLine}>
                      PTT: GPIO {bridgeHealth.config.firmware_config.ptt_gpio ?? 'default'} / active {bridgeHealth.config.firmware_config.ptt_active_level ?? 'default'} / debounce {bridgeHealth.config.firmware_config.ptt_debounce_ms ?? 'default'} ms / pull {bridgeHealth.config.firmware_config.ptt_pull ?? 'default'}
                    </Text>
                    <Text style={styles.healthLine}>
                      RGB ring boot test: {bridgeHealth.config.firmware_config.led_self_test ? 'on' : 'off'}
                    </Text>
                    <Text style={styles.healthLine}>
                      TFT: {bridgeHealth.config.firmware_config.display_enabled ? 'on' : 'off'} / boot test {bridgeHealth.config.firmware_config.display_self_test ? 'on' : 'off'}
                    </Text>
                  </>
                ) : (
                  <Text style={styles.healthLine}>
                    Firmware config: {bridgeHealth.config.firmware_config.error ?? 'unavailable'}
                  </Text>
                )
              ) : null}
              {bridgeHealth.config.save_wav_dir ? (
                <Text style={styles.healthLine}>WAV capture: {bridgeHealth.config.save_wav_dir}</Text>
              ) : null}
              <Text style={styles.healthLine}>Audio uploads: {bridgeHealth.config.capture_count ?? 0}</Text>
              {bridgeHealth.config.latest_capture ? (
                <Text style={styles.healthLine}>
                  Last audio: {formatBytes(bridgeHealth.config.latest_capture.audio_bytes)}
                  {bridgeHealth.config.latest_capture.command ? ` / ${bridgeHealth.config.latest_capture.command}` : ''}
                  {bridgeHealth.config.latest_capture.wav_info?.duration_ms ? ` / ${bridgeHealth.config.latest_capture.wav_info.duration_ms} ms` : ''}
                  {bridgeHealth.config.latest_capture.wav_info?.appears_silent ? ' / silent' : ' / not silent'}
                </Text>
              ) : null}
            </View>
          ) : null}
        </View>

        <View style={styles.section}>
          <Text style={styles.label}>Device Config</Text>
          <TextInput
            value={deviceWifiSsid}
            onChangeText={setDeviceWifiSsid}
            autoCapitalize="none"
            autoCorrect={false}
            placeholder="Wi-Fi name"
            placeholderTextColor="#64748b"
            style={styles.input}
          />
          <TextInput
            value={deviceWifiPassword}
            onChangeText={setDeviceWifiPassword}
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
            placeholder="Wi-Fi password"
            placeholderTextColor="#64748b"
            style={styles.input}
          />
          <TextInput
            value={deviceWifiBssid}
            onChangeText={setDeviceWifiBssid}
            autoCapitalize="none"
            autoCorrect={false}
            placeholder="AP MAC optional, e.g. ca:50:35:23:2b:1f"
            placeholderTextColor="#64748b"
            style={styles.input}
          />
          <View style={styles.pttGrid}>
            <View style={styles.pttInputBlock}>
              <Text style={styles.fieldLabel}>PTT GPIO</Text>
              <TextInput
                value={devicePttGpio}
                onChangeText={setDevicePttGpio}
                keyboardType="number-pad"
                placeholder="0"
                placeholderTextColor="#64748b"
                style={styles.input}
              />
            </View>
            <View style={styles.pttInputBlock}>
              <Text style={styles.fieldLabel}>Active Level</Text>
              <View style={styles.segmentRow}>
                {['0', '1'].map((level) => (
                  <Pressable
                    key={level}
                    style={[styles.segmentButton, devicePttActiveLevel === level && styles.segmentButtonActive]}
                    onPress={() => setDevicePttActiveLevel(level)}
                  >
                    <Text style={[styles.segmentText, devicePttActiveLevel === level && styles.segmentTextActive]}>
                      {level}
                    </Text>
                  </Pressable>
                ))}
              </View>
            </View>
            <View style={styles.pttInputBlock}>
              <Text style={styles.fieldLabel}>Debounce MS</Text>
              <TextInput
                value={devicePttDebounceMs}
                onChangeText={setDevicePttDebounceMs}
                keyboardType="number-pad"
                placeholder="35"
                placeholderTextColor="#64748b"
                style={styles.input}
              />
            </View>
          </View>
          <View>
            <Text style={styles.fieldLabel}>PTT Pull</Text>
            <View style={styles.segmentRow}>
              {['up', 'down', 'none'].map((pull) => (
                <Pressable
                  key={pull}
                  style={[styles.segmentButton, devicePttPull === pull && styles.segmentButtonActive]}
                  onPress={() => setDevicePttPull(pull)}
                >
                  <Text style={[styles.segmentText, devicePttPull === pull && styles.segmentTextActive]}>
                    {pull}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>
          <View style={styles.toggleGrid}>
            <Pressable style={styles.toggleRow} onPress={() => setDeviceLedSelfTest(!deviceLedSelfTest)}>
              <View style={[styles.checkbox, deviceLedSelfTest && styles.checkboxChecked]}>
                <Text style={[styles.checkboxMark, deviceLedSelfTest && styles.checkboxMarkChecked]}>x</Text>
              </View>
              <View style={styles.toggleTextBlock}>
                <Text style={styles.toggleLabel}>RGB Boot Test</Text>
                <Text style={styles.toggleMeta}>Cycle the 9 ring commands on reset</Text>
              </View>
            </Pressable>
            <Pressable style={styles.toggleRow} onPress={() => setDisplayEnabled(!deviceDisplayEnabled)}>
              <View style={[styles.checkbox, deviceDisplayEnabled && styles.checkboxChecked]}>
                <Text style={[styles.checkboxMark, deviceDisplayEnabled && styles.checkboxMarkChecked]}>x</Text>
              </View>
              <View style={styles.toggleTextBlock}>
                <Text style={styles.toggleLabel}>TFT Display</Text>
                <Text style={styles.toggleMeta}>Normal status and reply screen</Text>
              </View>
            </Pressable>
            <Pressable style={styles.toggleRow} onPress={() => setDisplaySelfTest(!deviceDisplaySelfTest)}>
              <View style={[styles.checkbox, deviceDisplaySelfTest && styles.checkboxChecked]}>
                <Text style={[styles.checkboxMark, deviceDisplaySelfTest && styles.checkboxMarkChecked]}>x</Text>
              </View>
              <View style={styles.toggleTextBlock}>
                <Text style={styles.toggleLabel}>TFT Boot Test</Text>
                <Text style={styles.toggleMeta}>Color bands and pin-map text</Text>
              </View>
            </Pressable>
          </View>
          <View style={styles.row}>
            <Pressable style={styles.secondaryButton} onPress={handleSaveDeviceWifi}>
              <Text style={styles.secondaryButtonText}>Save Local</Text>
            </Pressable>
            <Pressable
              style={[styles.secondaryButton, isConfiguringWifi && styles.disabledButton]}
              onPress={handleConfigureDeviceWifi}
              disabled={isConfiguringWifi}
            >
              <Text style={styles.secondaryButtonText}>{isConfiguringWifi ? 'Sending' : 'Send To Bridge'}</Text>
            </Pressable>
          </View>
          <Text style={styles.helperText}>
            Sends Wi-Fi, PTT, and TFT settings to the local bridge only when device config is enabled; rebuild and flash afterward.
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.label}>Test transcript</Text>
          <Pressable
            style={[
              styles.listenButton,
              isListening && styles.listenButtonActive,
              (isSending || isListening) && styles.wideDisabledButton,
            ]}
            onPressIn={handleListenStart}
            onPressOut={handleListenStop}
            disabled={isSending}
          >
            <Text style={[styles.listenButtonText, isListening && styles.listenButtonTextActive]}>
              {isListening ? 'Listening' : 'Hold To Speak'}
            </Text>
          </Pressable>
          <TextInput
            value={transcript}
            onChangeText={setTranscript}
            multiline
            placeholder="Should I keep wiring the display this way?"
            placeholderTextColor="#64748b"
            style={[styles.input, styles.transcriptInput]}
          />
          <Pressable
            style={[styles.primaryButton, isSending && styles.disabledButton]}
            onPress={handleSend}
            disabled={isSending}
          >
            {isSending ? <ActivityIndicator color="#020617" /> : <Text style={styles.primaryButtonText}>Ask Bridge</Text>}
          </Pressable>
        </View>

        <View style={styles.statusBar}>
          <View style={[styles.statusDot, { backgroundColor: commandColor }]} />
          <Text style={styles.statusText}>{status}</Text>
        </View>

        <View style={styles.responsePanel}>
          <Text style={styles.responseCode}>{lastResponse?.command ?? '--'}</Text>
          <Text style={styles.responseMeaning}>
            {lastResponse ? COMMAND_DESCRIPTIONS[lastResponse.command] : 'No response yet'}
          </Text>
          <Text style={styles.responseText}>{lastResponse?.reply ?? 'Send a transcript to see the same valence code the ring receives.'}</Text>
          {lastResponse?.wav_info ? (
            <Text style={styles.audioMetaText}>
              WAV: {lastResponse.wav_info.valid ? 'valid' : 'invalid'}
              {lastResponse.wav_info.sample_rate ? ` / ${lastResponse.wav_info.sample_rate} Hz` : ''}
              {lastResponse.wav_info.channels ? ` / ${lastResponse.wav_info.channels} ch` : ''}
              {lastResponse.wav_info.duration_ms ? ` / ${lastResponse.wav_info.duration_ms} ms` : ''}
              {lastResponse.wav_info.rms_dbfs ? ` / ${lastResponse.wav_info.rms_dbfs} dBFS RMS` : ''}
              {lastResponse.wav_info.appears_silent ? ' / silent' : ''}
            </Text>
          ) : null}
        </View>

        <View style={styles.section}>
          <Text style={styles.label}>Recent responses</Text>
          {history.length === 0 ? (
            <Text style={styles.emptyText}>No bridge responses yet.</Text>
          ) : (
            history.map((entry, index) => (
              <View key={`${entry.time}-${index}`} style={styles.historyRow}>
                <Text style={[styles.historyCode, { color: COMMAND_COLORS[entry.command] }]}>{entry.command}</Text>
                <View style={styles.historyTextBlock}>
                  <Text style={styles.historyTranscript} numberOfLines={1}>
                    {entry.transcript}
                  </Text>
                  <Text style={styles.historyReply} numberOfLines={2}>
                    {entry.reply}
                  </Text>
                </View>
                <Text style={styles.historyTime}>{entry.time}</Text>
              </View>
            ))
          )}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#0b1020',
  },
  content: {
    padding: 18,
    gap: 18,
  },
  header: {
    paddingTop: 10,
    paddingBottom: 2,
  },
  title: {
    color: '#f8fafc',
    fontSize: 30,
    fontWeight: '700',
  },
  subtitle: {
    color: '#94a3b8',
    fontSize: 15,
    marginTop: 4,
  },
  section: {
    gap: 10,
  },
  label: {
    color: '#cbd5e1',
    fontSize: 13,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  fieldLabel: {
    color: '#94a3b8',
    fontSize: 12,
    fontWeight: '700',
    marginBottom: 6,
    textTransform: 'uppercase',
  },
  row: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  input: {
    backgroundColor: '#111827',
    borderColor: '#334155',
    borderRadius: 8,
    borderWidth: 1,
    color: '#f8fafc',
    fontSize: 16,
    paddingHorizontal: 12,
    paddingVertical: 12,
  },
  urlInput: {
    flex: 1,
    minWidth: 210,
  },
  transcriptInput: {
    minHeight: 120,
    textAlignVertical: 'top',
  },
  pttGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  pttInputBlock: {
    flex: 1,
    minWidth: 140,
  },
  segmentRow: {
    flexDirection: 'row',
    gap: 8,
  },
  segmentButton: {
    alignItems: 'center',
    backgroundColor: '#111827',
    borderColor: '#334155',
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    justifyContent: 'center',
    minHeight: 46,
  },
  segmentButtonActive: {
    backgroundColor: '#e2e8f0',
    borderColor: '#f8fafc',
  },
  segmentText: {
    color: '#cbd5e1',
    fontWeight: '800',
    textTransform: 'uppercase',
  },
  segmentTextActive: {
    color: '#020617',
  },
  toggleGrid: {
    gap: 8,
  },
  toggleRow: {
    alignItems: 'center',
    backgroundColor: '#111827',
    borderColor: '#334155',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 10,
    minHeight: 58,
    padding: 10,
  },
  checkbox: {
    alignItems: 'center',
    borderColor: '#64748b',
    borderRadius: 4,
    borderWidth: 1,
    height: 22,
    justifyContent: 'center',
    width: 22,
  },
  checkboxChecked: {
    backgroundColor: '#e2e8f0',
    borderColor: '#f8fafc',
  },
  checkboxMark: {
    color: 'transparent',
    fontSize: 13,
    fontWeight: '900',
    lineHeight: 16,
  },
  checkboxMarkChecked: {
    color: '#020617',
  },
  toggleTextBlock: {
    flex: 1,
    gap: 2,
  },
  toggleLabel: {
    color: '#f8fafc',
    fontSize: 15,
    fontWeight: '800',
  },
  toggleMeta: {
    color: '#94a3b8',
    fontSize: 12,
  },
  primaryButton: {
    alignItems: 'center',
    backgroundColor: '#f8fafc',
    borderRadius: 8,
    minHeight: 48,
    justifyContent: 'center',
  },
  primaryButtonText: {
    color: '#020617',
    fontSize: 16,
    fontWeight: '700',
  },
  secondaryButton: {
    alignItems: 'center',
    backgroundColor: '#1e293b',
    borderColor: '#475569',
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: 'center',
    paddingHorizontal: 16,
  },
  secondaryButtonText: {
    color: '#e2e8f0',
    fontWeight: '700',
  },
  healthPanel: {
    backgroundColor: '#111827',
    borderColor: '#334155',
    borderRadius: 8,
    borderWidth: 1,
    gap: 4,
    padding: 12,
  },
  healthHeader: {
    alignItems: 'baseline',
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 2,
  },
  healthTitle: {
    color: '#f8fafc',
    fontSize: 16,
    fontWeight: '800',
  },
  healthService: {
    color: '#64748b',
    fontSize: 12,
  },
  healthLine: {
    color: '#cbd5e1',
    fontSize: 13,
  },
  benchSummaryRow: {
    alignItems: 'center',
    backgroundColor: '#0f172a',
    borderColor: '#334155',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 8,
    marginBottom: 4,
    padding: 10,
  },
  benchSummaryDot: {
    borderRadius: 5,
    height: 10,
    width: 10,
  },
  benchSummaryText: {
    color: '#e2e8f0',
    flex: 1,
    fontSize: 13,
    fontWeight: '700',
    lineHeight: 18,
  },
  helperText: {
    color: '#94a3b8',
    fontSize: 13,
    lineHeight: 18,
  },
  disabledButton: {
    opacity: 0.7,
  },
  wideDisabledButton: {
    opacity: 0.8,
  },
  listenButton: {
    alignItems: 'center',
    backgroundColor: '#1e293b',
    borderColor: '#475569',
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 56,
    justifyContent: 'center',
  },
  listenButtonActive: {
    backgroundColor: '#f8fafc',
    borderColor: '#f8fafc',
  },
  listenButtonText: {
    color: '#e2e8f0',
    fontSize: 18,
    fontWeight: '800',
  },
  listenButtonTextActive: {
    color: '#020617',
  },
  statusBar: {
    alignItems: 'center',
    backgroundColor: '#111827',
    borderColor: '#334155',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 10,
    padding: 12,
  },
  statusDot: {
    borderRadius: 7,
    height: 14,
    width: 14,
  },
  statusText: {
    color: '#cbd5e1',
    fontSize: 14,
  },
  responsePanel: {
    backgroundColor: '#111827',
    borderColor: '#334155',
    borderRadius: 8,
    borderWidth: 1,
    padding: 16,
  },
  responseCode: {
    color: '#f8fafc',
    fontSize: 44,
    fontWeight: '800',
  },
  responseMeaning: {
    color: '#cbd5e1',
    fontSize: 16,
    marginTop: 2,
  },
  responseText: {
    color: '#f8fafc',
    fontSize: 18,
    lineHeight: 25,
    marginTop: 14,
  },
  audioMetaText: {
    color: '#94a3b8',
    fontSize: 13,
    marginTop: 10,
  },
  emptyText: {
    color: '#64748b',
  },
  historyRow: {
    alignItems: 'center',
    backgroundColor: '#111827',
    borderColor: '#1f2937',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 12,
    padding: 12,
  },
  historyCode: {
    fontSize: 18,
    fontWeight: '800',
    width: 34,
  },
  historyTextBlock: {
    flex: 1,
    gap: 3,
  },
  historyTranscript: {
    color: '#e2e8f0',
    fontSize: 14,
  },
  historyReply: {
    color: '#94a3b8',
    fontSize: 13,
  },
  historyTime: {
    color: '#64748b',
    fontSize: 12,
  },
});
