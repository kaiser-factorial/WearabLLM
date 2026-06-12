import React, { useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  Alert,
  ScrollView,
  Modal,
  Animated,
  Dimensions,
  Keyboard,
} from 'react-native';
import { bleManager, BLEStatus, SensorData, parseSensorData } from '../ble/BLEManager';
import { LEDCommand, COMMAND_DESCRIPTIONS } from '../ble/commands';
import { VoiceListener } from '../audio/VoiceListener';
import { getCommandFromLLM, LLMResponse } from '../api/llm';
import { loadProviderConfig, loadProviderApiKey, PROVIDER_LABELS } from '../storage/apiKey';

type ProcessingState = 'idle' | 'listening' | 'thinking' | 'sending';

const STATUS_COLOR: Record<BLEStatus, string> = {
  idle: '#555',
  scanning: '#f59e0b',
  connecting: '#f59e0b',
  connected: '#22c55e',
  error: '#ef4444',
};

const COMMAND_COLOR: Record<string, string> = {
  G: '#22c55e',
  R: '#ef4444',
  Y: '#eab308',
  B: '#3b82f6',
  P: '#a855f7',
};

export function HomeScreen() {
  const [bleStatus, setBleStatus] = useState<BLEStatus>('idle');
  const [bleMessage, setBleMessage] = useState('Not connected');
  const [processingState, setProcessingState] = useState<ProcessingState>('idle');
  const [partialText, setPartialText] = useState('');
  const [lastTranscript, setLastTranscript] = useState('');
  const [lastCommand, setLastCommand] = useState<LEDCommand | null>(null);
  const [lastExplanation, setLastExplanation] = useState('');
  const [errorMessage, setErrorMessage] = useState('');
  const [sessionLog, setSessionLog] = useState<
    { question: string; command: LEDCommand; answer: string; time: string }[]
  >([]);
  const [logVisible, setLogVisible] = useState(false);
  const [sensorsActive, setSensorsActive] = useState(false);
  const [displaySensors, setDisplaySensors] = useState<SensorData | null>(null);
  const [textInput, setTextInput] = useState('');

  const voice = useRef<VoiceListener | null>(null);
  const pendingTranscript = useRef<string | null>(null);
  const sensorData = useRef<SensorData | null>(null);
  const transcriptSent = useRef(false);  // guard against double-submit

  // Use a ref-based handler so the VoiceListener always calls the latest version
  const handleTranscriptRef = useRef(handleTranscript);
  handleTranscriptRef.current = handleTranscript;

  useEffect(() => {
    const vl = new VoiceListener();
    voice.current = vl;
    vl.onPartial = (text) => {
      setPartialText(text);
      // Stash the latest partial so we can use it if no final result arrives
      pendingTranscript.current = text;
    };
    vl.onResult = (transcript) => {
      pendingTranscript.current = null; // final result arrived
      handleTranscriptRef.current(transcript);
    };
    vl.onError = (msg) => {
      setProcessingState('idle');
      setErrorMessage(msg);
    };

    const unsubscribe = bleManager.onStatusChange((status, msg) => {
      setBleStatus(status);
      setBleMessage(msg ?? statusLabel(status));
      // no longer clearing log — it persists across connections
    });

    // Listen for button presses from CPB
    const unsubMessage = bleManager.onMessage((msg) => {
      console.log('🟡 [HomeScreen] BLE message from device:', msg);
      if (msg === 'VS' || msg.startsWith('VS:')) {
        // CPB button pressed → start voice (maybe with sensor data)
        sensorData.current = parseSensorData(msg);
        setSensorsActive(sensorData.current !== null);
        setDisplaySensors(sensorData.current);
        if (sensorData.current) {
          console.log('🟡 [HomeScreen] Sensor data:', sensorData.current);
        }
        if (voice.current) {
          transcriptSent.current = false;
          pendingTranscript.current = null;
          setProcessingState('listening');
          setPartialText('');
          setLastTranscript('');
          setErrorMessage('');
          voice.current.start();
        }
      } else if (msg.startsWith('SD:')) {
        // Sensor data response from CPB (requested via SR)
        const parts = msg.slice(3).split(',');
        if (parts.length === 5) {
          const [t, l, x, y, z] = parts.map(Number);
          if (![t, l, x, y, z].some(isNaN)) {
            const data: SensorData = { tempC: t, light: l, accelX: x, accelY: y, accelZ: z };
            sensorData.current = data;
            setDisplaySensors(data);
          }
        }
      } else if (msg === 'S1') {
        setSensorsActive(true);
      } else if (msg === 'S0') {
        setSensorsActive(false);
        setDisplaySensors(null);
      } else if (msg === 'VP') {
        // CPB button pressed again → stop voice & send to Claude immediately
        if (voice.current) {
          voice.current.stop();
          // Use whatever transcript we have right now (partial or final)
          const text = pendingTranscript.current;
          pendingTranscript.current = null;
          if (text && text.trim().length > 0) {
            handleTranscriptRef.current(text);
          } else {
            setProcessingState('idle');
          }
        }
      }
    });

    return () => {
      unsubscribe();
      unsubMessage();
      vl.destroy();
      voice.current = null;
    };
  }, []);

  function statusLabel(s: BLEStatus): string {
    switch (s) {
      case 'idle': return 'Not connected';
      case 'scanning': return 'Scanning...';
      case 'connecting': return 'Connecting...';
      case 'connected': return 'Connected';
      case 'error': return 'Error';
    }
  }

  async function handleTranscript(transcript: string) {
    // Guard: only process once per interaction
    if (transcriptSent.current) return;
    transcriptSent.current = true;

    setLastTranscript(transcript);
    setPartialText('');
    setProcessingState('thinking');
    setErrorMessage('');

    // Attach sensor context if available (keep data for future interactions)
    const sensors = sensorData.current;
    let messageForLLM = transcript;
    if (sensors) {
      const tempF = (sensors.tempC * 9 / 5 + 32).toFixed(1);
      messageForLLM += `\n\n[Sensor data: temperature=${sensors.tempC.toFixed(1)}°C (${tempF}°F), light=${sensors.light}%, accelerometer=(${sensors.accelX.toFixed(1)}, ${sensors.accelY.toFixed(1)}, ${sensors.accelZ.toFixed(1)}) m/s²]`;
    }

    try {
      const config = await loadProviderConfig();
      const apiKey = await loadProviderApiKey(config.provider);
      if (!apiKey && config.provider !== 'ollama') {
        Alert.alert('No API key', `Please add your ${PROVIDER_LABELS[config.provider]} API key in Settings.`);
        setProcessingState('idle');
        return;
      }

      const result = await getCommandFromLLM(messageForLLM, config, apiKey);
      setLastCommand(result.command);
      setLastExplanation(result.explanation);
      setSessionLog((prev) => [
        ...prev,
        {
          question: transcript,
          command: result.command,
          answer: result.explanation,
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        },
      ]);
      if (bleManager.isConnected) {
        setProcessingState('sending');
        await bleManager.sendCommand(result.command);
      }
    } catch (e: any) {
      setErrorMessage(e.message ?? 'Something went wrong.');
    } finally {
      setProcessingState('idle');
    }
  }

  async function handleScanPress() {
    if (bleStatus === 'connected') {
      await bleManager.disconnect();
    } else {
      await bleManager.scan();
    }
  }

  async function handleSpeakToggle() {
    if (!voice.current) return;

    if (processingState === 'listening') {
      // Second press → stop & send
      console.log('🟡 [HomeScreen] Toggle OFF — stopping voice');
      voice.current.stop();
      const text = pendingTranscript.current;
      pendingTranscript.current = null;
      if (text && text.trim().length > 0) {
        handleTranscriptRef.current(text);
      } else {
        setProcessingState('idle');
      }
    } else {
      // First press → start listening
      console.log('🟡 [HomeScreen] Toggle ON — starting voice');
      transcriptSent.current = false;
      pendingTranscript.current = null;
      setProcessingState('listening');
      setPartialText('');
      setLastTranscript('');
      setErrorMessage('');
      // Request sensor snapshot if sensors are active and connected
      if (sensorsActive && bleManager.isConnected) {
        try { await bleManager.sendRaw('SR'); } catch {}
      }
      await voice.current.start();
    }
  }

  async function handleTextSubmit() {
    const text = textInput.trim();
    if (!text || processingState !== 'idle') return;
    Keyboard.dismiss();
    setTextInput('');
    transcriptSent.current = false;
    handleTranscriptRef.current(text);
  }

  const canSpeak = processingState === 'idle';
  const isSpeaking = processingState === 'listening';
  const isProcessing = processingState === 'thinking' || processingState === 'sending';

  return (
    <View style={styles.container}>
      {/* Connection bar */}
      <View style={styles.connectionBar}>
        <View style={[styles.dot, { backgroundColor: STATUS_COLOR[bleStatus] }]} />
        <Text style={styles.connectionText}>{bleMessage}</Text>
        <TouchableOpacity
          style={[
            styles.connectButton,
            bleStatus === 'connected' && styles.connectButtonActive,
            (bleStatus === 'scanning' || bleStatus === 'connecting') && styles.connectButtonBusy,
          ]}
          onPress={handleScanPress}
          disabled={bleStatus === 'scanning' || bleStatus === 'connecting'}
        >
          <Text style={styles.connectButtonText}>
            {bleStatus === 'connected'
              ? 'Disconnect'
              : bleStatus === 'scanning' || bleStatus === 'connecting'
              ? 'Searching...'
              : 'Scan'}
          </Text>
        </TouchableOpacity>
      </View>

      {/* Last command + Claude's answer */}
      <View style={styles.commandArea}>
        {lastCommand ? (
          <>
            <Text style={[styles.commandCode, { color: COMMAND_COLOR[lastCommand[0]] ?? '#fff' }]}>
              {lastCommand}
            </Text>
            <Text style={styles.commandDesc}>{COMMAND_DESCRIPTIONS[lastCommand]}</Text>
            {lastExplanation ? (
              <Text style={styles.explanationText}>{lastExplanation}</Text>
            ) : null}
          </>
        ) : (
          <Text style={styles.commandPlaceholder}>---</Text>
        )}
      </View>

      {/* Transcript */}
      <View style={styles.transcriptArea}>
        {(partialText || lastTranscript) ? (
          <Text style={styles.transcriptText}>
            {partialText || lastTranscript}
          </Text>
        ) : null}
        {errorMessage ? (
          <Text style={styles.errorText}>{errorMessage}</Text>
        ) : null}
      </View>

      {/* Speak button — centered */}
      <View style={styles.speakArea}>
        {isProcessing ? (
          <View style={styles.processingContainer}>
            <ActivityIndicator size="large" color="#3b82f6" />
            <Text style={styles.processingText}>
              {processingState === 'thinking' ? 'Thinking...' : 'Sending to device...'}
            </Text>
          </View>
        ) : (
          <Pressable
            style={[
              styles.speakButton,
              isSpeaking && styles.speakButtonActive,
              !canSpeak && !isSpeaking && styles.speakButtonDisabled,
            ]}
            onPress={handleSpeakToggle}
            disabled={!canSpeak && !isSpeaking}

          >
            <Text style={styles.speakButtonIcon}>{isSpeaking ? '🎙' : '🎤'}</Text>
            <Text style={styles.speakButtonText}>
              {isSpeaking ? 'Tap to send' : 'Tap to speak'}
            </Text>
          </Pressable>
        )}
      </View>

      {/* Sensor info — below button */}
      {bleStatus === 'connected' && (
        <View style={styles.sensorBar}>
          <Text style={[styles.sensorText, sensorsActive && styles.sensorTextActive]}>
            {sensorsActive ? '📡 Sensors active' : '📡 Sensors off'}
          </Text>
          {sensorsActive && displaySensors && (
            <View style={styles.sensorDataContainer}>
              <Text style={styles.sensorDataText}>
                {displaySensors.tempC.toFixed(1)}°C / {(displaySensors.tempC * 9 / 5 + 32).toFixed(1)}°F
              </Text>
              <Text style={styles.sensorDataText}>
                Light: {displaySensors.light}%
              </Text>
              <Text style={styles.sensorDataText}>
                Accel: ({displaySensors.accelX.toFixed(1)}, {displaySensors.accelY.toFixed(1)}, {displaySensors.accelZ.toFixed(1)})
              </Text>
            </View>
          )}
        </View>
      )}

      {/* Text input */}
      <View style={styles.textInputBar}>
        <TextInput
          style={styles.textInput}
          placeholder="Or type a message..."
          placeholderTextColor="#555"
          value={textInput}
          onChangeText={setTextInput}
          onSubmitEditing={handleTextSubmit}
          returnKeyType="send"
          editable={processingState === 'idle'}
          autoCorrect={false}
        />
        <TouchableOpacity
          style={[styles.sendButton, (!textInput.trim() || processingState !== 'idle') && styles.sendButtonDisabled]}
          onPress={handleTextSubmit}
          disabled={!textInput.trim() || processingState !== 'idle'}
        >
          <Text style={styles.sendButtonText}>Send</Text>
        </TouchableOpacity>
      </View>

      {/* Log button */}
      {sessionLog.length > 0 && (
        <TouchableOpacity
          style={styles.logButton}
          onPress={() => setLogVisible(true)}
        >
          <Text style={styles.logButtonText}>
            History ({sessionLog.length})
          </Text>
        </TouchableOpacity>
      )}

      {/* Session log drawer */}
      <Modal
        visible={logVisible}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setLogVisible(false)}
      >
        <View style={styles.logModal}>
          <View style={styles.logHeader}>
            <Text style={styles.logTitle}>Session History</Text>
            <TouchableOpacity onPress={() => setLogVisible(false)}>
              <Text style={styles.logClose}>Done</Text>
            </TouchableOpacity>
          </View>
          {sessionLog.length === 0 ? (
            <Text style={styles.logEmpty}>No interactions yet</Text>
          ) : (
            <ScrollView style={styles.logScroll} showsVerticalScrollIndicator={false}>
              {[...sessionLog].reverse().map((entry, i) => (
                <View key={i} style={styles.logEntry}>
                  <View style={styles.logEntryHeader}>
                    <Text style={[styles.logCommand, { color: COMMAND_COLOR[entry.command[0]] ?? '#fff' }]}>
                      {entry.command}
                    </Text>
                    <Text style={styles.logTime}>{entry.time}</Text>
                  </View>
                  <Text style={styles.logQuestion}>"{entry.question}"</Text>
                  <Text style={styles.logExplanation}>{entry.answer}</Text>
                </View>
              ))}
              <TouchableOpacity
                style={styles.clearLogButton}
                onPress={() => { setSessionLog([]); setLogVisible(false); }}
              >
                <Text style={styles.clearLogText}>Clear History</Text>
              </TouchableOpacity>
            </ScrollView>
          )}
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0d0d0d',
    paddingHorizontal: 20,
  },
  sensorBar: {
    paddingTop: 16,
    paddingBottom: 24,
    alignItems: 'center',
  },
  sensorText: {
    color: '#555',
    fontSize: 12,
    fontWeight: '500',
  },
  sensorTextActive: {
    color: '#22c55e',
  },
  sensorDataContainer: {
    marginTop: 8,
    alignItems: 'center',
    gap: 2,
  },
  sensorDataText: {
    color: '#666',
    fontSize: 11,
    fontFamily: 'monospace',
  },
  connectionBar: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#1a1a1a',
    gap: 10,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  connectionText: {
    flex: 1,
    color: '#888',
    fontSize: 14,
  },
  connectButton: {
    backgroundColor: '#1e3a5f',
    paddingHorizontal: 16,
    paddingVertical: 7,
    borderRadius: 8,
  },
  connectButtonActive: {
    backgroundColor: '#1a2e1a',
  },
  connectButtonBusy: {
    opacity: 0.6,
  },
  connectButtonText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '600',
  },
  commandArea: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  commandCode: {
    fontSize: 48,
    fontWeight: '800',
    letterSpacing: 6,
    fontFamily: 'monospace',
  },
  commandDesc: {
    color: '#888',
    fontSize: 14,
    marginTop: 4,
  },
  explanationText: {
    color: '#bbb',
    fontSize: 15,
    marginTop: 12,
    textAlign: 'center',
    lineHeight: 22,
    paddingHorizontal: 10,
  },
  commandPlaceholder: {
    color: '#2a2a2a',
    fontSize: 48,
    letterSpacing: 12,
    fontWeight: '300',
  },
  transcriptArea: {
    flex: 1,
    justifyContent: 'flex-end',
    paddingBottom: 16,
    minHeight: 60,
  },
  transcriptText: {
    color: '#aaa',
    fontSize: 15,
    fontStyle: 'italic',
    textAlign: 'center',
    lineHeight: 22,
  },
  errorText: {
    color: '#ef4444',
    fontSize: 13,
    textAlign: 'center',
  },
  speakArea: {
    flex: 2,
    justifyContent: 'center',
    alignItems: 'center',
  },
  speakButton: {
    width: 160,
    height: 160,
    borderRadius: 80,
    backgroundColor: '#1a1a2e',
    borderWidth: 2,
    borderColor: '#2563eb',
    justifyContent: 'center',
    alignItems: 'center',
    gap: 8,
  },
  speakButtonActive: {
    backgroundColor: '#1e3a5f',
    borderColor: '#60a5fa',
    transform: [{ scale: 1.05 }],
  },
  speakButtonDisabled: {
    borderColor: '#2a2a2a',
    opacity: 0.4,
  },
  speakButtonIcon: {
    fontSize: 40,
  },
  speakButtonText: {
    color: '#aaa',
    fontSize: 13,
    fontWeight: '500',
  },
  processingContainer: {
    alignItems: 'center',
    gap: 16,
    height: 160,
    justifyContent: 'center',
  },
  processingText: {
    color: '#888',
    fontSize: 14,
  },
  textInputBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 12,
    borderTopWidth: 1,
    borderTopColor: '#1a1a1a',
  },
  textInput: {
    flex: 1,
    backgroundColor: '#1a1a1a',
    color: '#fff',
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 10,
    fontSize: 14,
    borderWidth: 1,
    borderColor: '#333',
  },
  sendButton: {
    backgroundColor: '#2563eb',
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  sendButtonDisabled: {
    opacity: 0.3,
  },
  sendButtonText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '600',
  },
  logButton: {
    alignSelf: 'center',
    paddingHorizontal: 20,
    paddingVertical: 8,
    marginBottom: 16,
    borderRadius: 16,
    backgroundColor: '#1a1a2e',
    borderWidth: 1,
    borderColor: '#333',
  },
  logButtonText: {
    color: '#888',
    fontSize: 13,
    fontWeight: '500',
  },
  logModal: {
    flex: 1,
    backgroundColor: '#0d0d0d',
    paddingTop: 20,
  },
  logHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#1a1a1a',
  },
  logTitle: {
    color: '#fff',
    fontSize: 18,
    fontWeight: '700',
  },
  logClose: {
    color: '#3b82f6',
    fontSize: 16,
    fontWeight: '600',
  },
  logEmpty: {
    color: '#555',
    fontSize: 14,
    textAlign: 'center',
    marginTop: 40,
  },
  logScroll: {
    flex: 1,
    paddingHorizontal: 20,
  },
  logEntry: {
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#1a1a1a',
  },
  logEntryHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  logCommand: {
    fontSize: 15,
    fontWeight: '700',
    fontFamily: 'monospace',
  },
  logTime: {
    color: '#555',
    fontSize: 12,
  },
  logQuestion: {
    color: '#777',
    fontSize: 13,
    fontStyle: 'italic',
    marginBottom: 6,
  },
  logExplanation: {
    color: '#bbb',
    fontSize: 14,
    lineHeight: 20,
  },
  clearLogButton: {
    alignSelf: 'center',
    marginVertical: 24,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#333',
  },
  clearLogText: {
    color: '#ef4444',
    fontSize: 13,
    fontWeight: '500',
  },
});
