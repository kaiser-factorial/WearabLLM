/**
 * Secure storage for LLM provider configuration and API keys.
 * Uses iOS Keychain / Android Keystore — never stored in plain text.
 */

import * as SecureStore from 'expo-secure-store';

// ── Types ────────────────────────────────────────────────────────────────────

export type Provider = 'anthropic' | 'openai' | 'ollama';

export interface ProviderConfig {
  provider: Provider;
  model: string;
  ollamaUrl?: string;
  customSystemPrompt?: string;
}

export const DEFAULT_MODELS: Record<Provider, string> = {
  anthropic: 'claude-opus-4-6',
  openai: 'gpt-4o',
  ollama: 'hermes3',
};

export const PROVIDER_LABELS: Record<Provider, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  ollama: 'Ollama',
};

// ── Storage Keys ─────────────────────────────────────────────────────────────

const API_KEY_PREFIX = '_api_key'; // e.g. "anthropic_api_key", "openai_api_key"
const CONFIG_KEY = 'llm_provider_config';

function apiKeyStorageKey(provider: Provider): string {
  return `${provider}${API_KEY_PREFIX}`;
}

// ── Provider Config ──────────────────────────────────────────────────────────

export async function saveProviderConfig(config: ProviderConfig): Promise<void> {
  await SecureStore.setItemAsync(CONFIG_KEY, JSON.stringify(config));
}

export async function loadProviderConfig(): Promise<ProviderConfig> {
  const raw = await SecureStore.getItemAsync(CONFIG_KEY);
  if (raw) {
    try {
      return JSON.parse(raw) as ProviderConfig;
    } catch {
      // corrupted — fall through to default
    }
  }
  // Default config
  return { provider: 'anthropic', model: DEFAULT_MODELS.anthropic };
}

// ── API Keys (per-provider) ──────────────────────────────────────────────────

export async function saveProviderApiKey(provider: Provider, key: string): Promise<void> {
  await SecureStore.setItemAsync(apiKeyStorageKey(provider), key);
}

export async function loadProviderApiKey(provider: Provider): Promise<string | null> {
  return SecureStore.getItemAsync(apiKeyStorageKey(provider));
}

export async function deleteProviderApiKey(provider: Provider): Promise<void> {
  await SecureStore.deleteItemAsync(apiKeyStorageKey(provider));
}

// ── Backward compat (used by existing code during migration) ─────────────────

export async function saveApiKey(key: string): Promise<void> {
  await saveProviderApiKey('anthropic', key);
}

export async function loadApiKey(): Promise<string | null> {
  return loadProviderApiKey('anthropic');
}

export async function deleteApiKey(): Promise<void> {
  await deleteProviderApiKey('anthropic');
}
