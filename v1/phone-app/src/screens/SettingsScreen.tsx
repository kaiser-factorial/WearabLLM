import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
} from 'react-native';
import { DEFAULT_SYSTEM_PROMPT } from '../api/llm';
import {
  Provider,
  ProviderConfig,
  PROVIDER_LABELS,
  DEFAULT_MODELS,
  saveProviderConfig,
  loadProviderConfig,
  saveProviderApiKey,
  loadProviderApiKey,
  deleteProviderApiKey,
} from '../storage/apiKey';

const PROVIDERS: Provider[] = ['anthropic', 'openai', 'ollama'];

const PROVIDER_COLORS: Record<Provider, string> = {
  anthropic: '#d4a27f',
  openai: '#10a37f',
  ollama: '#888',
};

const KEY_PREFIXES: Record<string, string> = {
  anthropic: 'sk-ant-',
  openai: 'sk-',
};

const KEY_PLACEHOLDERS: Record<string, string> = {
  anthropic: 'sk-ant-...',
  openai: 'sk-...',
};

const RESPONSES = [
  { code: 'GS', color: '#22c55e', label: 'Green Solid', meaning: 'Yes, confident' },
  { code: 'GP', color: '#22c55e', label: 'Green Pulse', meaning: 'Yes, gentle' },
  { code: 'GC', color: '#22c55e', label: 'Green Chase', meaning: 'Yes, enthusiastic' },
  { code: 'RS', color: '#ef4444', label: 'Red Solid', meaning: 'No, firm' },
  { code: 'RF', color: '#ef4444', label: 'Red Flicker', meaning: 'Warning / urgent' },
  { code: 'YP', color: '#eab308', label: 'Yellow Pulse', meaning: 'Uncertain / maybe' },
  { code: 'BS', color: '#3b82f6', label: 'Blue Solid', meaning: 'Neutral info' },
  { code: 'PS', color: '#a855f7', label: 'Purple Solid', meaning: 'Creative / imaginative' },
  { code: 'PP', color: '#a855f7', label: 'Purple Pulse', meaning: 'Deep / philosophical' },
];

export function SettingsScreen() {
  const [provider, setProvider] = useState<Provider>('anthropic');
  const [model, setModel] = useState(DEFAULT_MODELS.anthropic);
  const [ollamaUrl, setOllamaUrl] = useState('http://localhost:11434');
  const [customPrompt, setCustomPrompt] = useState('');
  const [promptExpanded, setPromptExpanded] = useState(false);
  const [keyInput, setKeyInput] = useState('');
  const [hasSavedKey, setHasSavedKey] = useState(false);

  // Load saved config on mount
  useEffect(() => {
    (async () => {
      const config = await loadProviderConfig();
      setProvider(config.provider);
      setModel(config.model);
      if (config.ollamaUrl) setOllamaUrl(config.ollamaUrl);
      if (config.customSystemPrompt) setCustomPrompt(config.customSystemPrompt);

      const key = await loadProviderApiKey(config.provider);
      setHasSavedKey(!!key);
    })();
  }, []);

  // When provider changes, load its saved key status and set default model
  async function handleProviderChange(p: Provider) {
    setProvider(p);
    setModel(DEFAULT_MODELS[p]);
    setKeyInput('');

    const key = await loadProviderApiKey(p);
    setHasSavedKey(!!key);

    // Save config immediately
    await saveProviderConfig({
      provider: p,
      model: DEFAULT_MODELS[p],
      ollamaUrl: p === 'ollama' ? ollamaUrl : undefined,
      customSystemPrompt: p === 'ollama' ? customPrompt || undefined : undefined,
    });
  }

  async function handleSaveConfig() {
    await saveProviderConfig({
      provider,
      model: model.trim() || DEFAULT_MODELS[provider],
      ollamaUrl: provider === 'ollama' ? ollamaUrl.trim() : undefined,
      customSystemPrompt: provider === 'ollama' ? customPrompt.trim() || undefined : undefined,
    });
    Alert.alert('Saved', 'Model configuration updated.');
  }

  function handleResetPrompt() {
    setCustomPrompt('');
    Alert.alert('Reset', 'System prompt will use the default on next save.');
  }

  async function handleSaveKey() {
    const trimmed = keyInput.trim();
    const prefix = KEY_PREFIXES[provider];
    if (prefix && !trimmed.startsWith(prefix)) {
      Alert.alert('Invalid key', `${PROVIDER_LABELS[provider]} API keys start with "${prefix}".`);
      return;
    }
    await saveProviderApiKey(provider, trimmed);
    setHasSavedKey(true);
    setKeyInput('');
    Alert.alert('Saved', 'API key stored securely on this device.');
  }

  async function handleDeleteKey() {
    Alert.alert('Remove API key', 'Are you sure?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Remove',
        style: 'destructive',
        onPress: async () => {
          await deleteProviderApiKey(provider);
          setHasSavedKey(false);
        },
      },
    ]);
  }

  const needsApiKey = provider !== 'ollama';

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {/* Provider Section */}
        <Text style={styles.sectionTitle}>LLM Provider</Text>
        <Text style={styles.hint}>
          Choose which AI service powers your wearable responses.
        </Text>

        <View style={styles.providerRow}>
          {PROVIDERS.map((p) => (
            <TouchableOpacity
              key={p}
              style={[
                styles.providerButton,
                provider === p && {
                  borderColor: PROVIDER_COLORS[p],
                  backgroundColor: PROVIDER_COLORS[p] + '18',
                },
              ]}
              onPress={() => handleProviderChange(p)}
            >
              <Text
                style={[
                  styles.providerButtonText,
                  provider === p && { color: PROVIDER_COLORS[p] },
                ]}
              >
                {PROVIDER_LABELS[p]}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Model Section */}
        <Text style={styles.label}>Model</Text>
        <TextInput
          style={styles.input}
          placeholder={DEFAULT_MODELS[provider]}
          placeholderTextColor="#555"
          value={model}
          onChangeText={setModel}
          autoCapitalize="none"
          autoCorrect={false}
        />

        {/* Ollama URL (only for Ollama) */}
        {provider === 'ollama' && (
          <>
            <Text style={styles.label}>Ollama URL</Text>
            <TextInput
              style={styles.input}
              placeholder="http://localhost:11434"
              placeholderTextColor="#555"
              value={ollamaUrl}
              onChangeText={setOllamaUrl}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
            />
            <Text style={styles.hint}>
              Use your Mac's IP address (e.g. http://10.0.0.5:11434) so the phone can reach it over Wi-Fi. No API key needed.
            </Text>
          </>
        )}

        {/* System Prompt (only for Ollama) */}
        {provider === 'ollama' && (
          <>
            <TouchableOpacity
              style={styles.promptToggle}
              onPress={() => setPromptExpanded(!promptExpanded)}
            >
              <Text style={styles.label}>
                System Prompt {promptExpanded ? '▼' : '▶'}
              </Text>
              <Text style={styles.promptStatus}>
                {customPrompt.trim() ? 'Custom' : 'Default'}
              </Text>
            </TouchableOpacity>

            {promptExpanded && (
              <>
                <TextInput
                  style={styles.promptInput}
                  placeholder={DEFAULT_SYSTEM_PROMPT}
                  placeholderTextColor="#333"
                  value={customPrompt}
                  onChangeText={setCustomPrompt}
                  multiline
                  textAlignVertical="top"
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                {customPrompt.trim() ? (
                  <TouchableOpacity style={styles.resetButton} onPress={handleResetPrompt}>
                    <Text style={styles.resetText}>Reset to default</Text>
                  </TouchableOpacity>
                ) : (
                  <Text style={styles.hint}>
                    Leave empty to use the default prompt. The prompt must instruct the model to respond with a 2-character LED code on line 1.
                  </Text>
                )}
              </>
            )}
          </>
        )}

        <TouchableOpacity style={styles.button} onPress={handleSaveConfig}>
          <Text style={styles.buttonText}>Save Model Config</Text>
        </TouchableOpacity>

        {/* API Key Section (not for Ollama) */}
        {needsApiKey && (
          <>
            <View style={styles.divider} />
            <Text style={styles.sectionTitle}>API Key</Text>
            <Text style={styles.hint}>
              {hasSavedKey
                ? `A ${PROVIDER_LABELS[provider]} key is saved. Paste a new one to replace it.`
                : `Enter your ${PROVIDER_LABELS[provider]} API key. Stored securely in the device keychain.`}
            </Text>

            <TextInput
              style={styles.input}
              placeholder={KEY_PLACEHOLDERS[provider] || 'API key...'}
              placeholderTextColor="#555"
              value={keyInput}
              onChangeText={setKeyInput}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
            />

            <TouchableOpacity
              style={[styles.button, !keyInput.trim() && styles.buttonDisabled]}
              onPress={handleSaveKey}
              disabled={!keyInput.trim()}
            >
              <Text style={styles.buttonText}>Save Key</Text>
            </TouchableOpacity>

            {hasSavedKey && (
              <TouchableOpacity style={styles.deleteButton} onPress={handleDeleteKey}>
                <Text style={styles.deleteText}>Remove saved key</Text>
              </TouchableOpacity>
            )}
          </>
        )}
      </KeyboardAvoidingView>

      {/* Response Guide */}
      <View style={styles.divider} />
      <Text style={styles.sectionTitle}>Response Guide</Text>
      <Text style={styles.hint}>
        The AI chooses a color and animation based on the meaning of your question.
      </Text>

      <View style={styles.responseList}>
        {RESPONSES.map((r) => (
          <View key={r.code} style={styles.responseRow}>
            <View style={[styles.colorDot, { backgroundColor: r.color }]} />
            <Text style={styles.responseCode}>{r.code}</Text>
            <View style={styles.responseInfo}>
              <Text style={styles.responseLabel}>{r.label}</Text>
              <Text style={styles.responseMeaning}>{r.meaning}</Text>
            </View>
          </View>
        ))}
      </View>

      {/* About */}
      <View style={styles.divider} />
      <Text style={styles.sectionTitle}>About</Text>

      <Text style={styles.aboutText}>
        Ask a question, and the AI responds — in words, and in light. LED animations
        and sounds on an Adafruit Circuit Playground Bluefruit give the AI another
        dimension of expression, embodying its response beyond text.
      </Text>

      <Text style={styles.aboutText}>
        Voice is transcribed on-device using native speech recognition (no audio leaves
        your phone). Sensor data from the wearable gives the AI real-world context.
      </Text>

      <View style={styles.creatorsSection}>
        <Text style={styles.creatorsTitle}>Created by</Text>
        <Text style={styles.creatorName}>Corina Kaiser</Text>
        <Text style={styles.creatorRole}>Design, hardware, & vision</Text>
        <Text style={styles.creatorName}>Claude</Text>
        <Text style={styles.creatorRole}>Code, architecture, & debugging</Text>
      </View>

      <Text style={styles.footerText}>
        Built with Expo, React Native, CircuitPython, and a lot of patience with iOS 26.
      </Text>

      <View style={styles.bottomPadding} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0d0d0d',
  },
  content: {
    padding: 24,
    paddingTop: 20,
  },
  sectionTitle: {
    color: '#fff',
    fontSize: 18,
    fontWeight: '700',
    marginBottom: 8,
  },
  label: {
    color: '#aaa',
    fontSize: 13,
    fontWeight: '600',
    marginBottom: 6,
    marginTop: 4,
  },
  hint: {
    color: '#888',
    fontSize: 13,
    marginBottom: 16,
    lineHeight: 20,
  },
  providerRow: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 20,
  },
  providerButton: {
    flex: 1,
    borderWidth: 1.5,
    borderColor: '#333',
    borderRadius: 10,
    padding: 12,
    alignItems: 'center',
  },
  providerButtonText: {
    color: '#666',
    fontSize: 13,
    fontWeight: '600',
  },
  input: {
    backgroundColor: '#1a1a1a',
    color: '#fff',
    borderRadius: 10,
    padding: 14,
    fontSize: 14,
    fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace',
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#333',
  },
  promptToggle: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 8,
    marginBottom: 8,
  },
  promptStatus: {
    color: '#666',
    fontSize: 11,
    fontStyle: 'italic',
  },
  promptInput: {
    backgroundColor: '#1a1a1a',
    color: '#fff',
    borderRadius: 10,
    padding: 14,
    fontSize: 12,
    fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace',
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#333',
    minHeight: 200,
    maxHeight: 400,
    lineHeight: 18,
  },
  resetButton: {
    padding: 10,
    alignItems: 'center',
    marginBottom: 12,
  },
  resetText: {
    color: '#f59e0b',
    fontSize: 13,
  },
  button: {
    backgroundColor: '#2563eb',
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
    marginBottom: 12,
  },
  buttonDisabled: {
    opacity: 0.4,
  },
  buttonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
  deleteButton: {
    padding: 12,
    alignItems: 'center',
  },
  deleteText: {
    color: '#ef4444',
    fontSize: 14,
  },
  divider: {
    height: 1,
    backgroundColor: '#1a1a1a',
    marginVertical: 28,
  },
  responseList: {
    gap: 10,
  },
  responseRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  colorDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  responseCode: {
    color: '#666',
    fontSize: 13,
    fontFamily: 'monospace',
    fontWeight: '600',
    width: 24,
  },
  responseInfo: {
    flex: 1,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  responseLabel: {
    color: '#ccc',
    fontSize: 13,
    fontWeight: '500',
  },
  responseMeaning: {
    color: '#777',
    fontSize: 12,
  },
  aboutText: {
    color: '#999',
    fontSize: 13,
    lineHeight: 20,
    marginBottom: 12,
  },
  creatorsSection: {
    backgroundColor: '#111',
    borderRadius: 12,
    padding: 16,
    marginTop: 8,
    marginBottom: 16,
  },
  creatorsTitle: {
    color: '#666',
    fontSize: 11,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: 12,
  },
  creatorName: {
    color: '#fff',
    fontSize: 15,
    fontWeight: '600',
  },
  creatorRole: {
    color: '#777',
    fontSize: 12,
    marginBottom: 10,
  },
  footerText: {
    color: '#444',
    fontSize: 11,
    textAlign: 'center',
    lineHeight: 16,
    fontStyle: 'italic',
  },
  bottomPadding: {
    height: 40,
  },
});
