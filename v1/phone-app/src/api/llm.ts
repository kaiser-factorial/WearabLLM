/**
 * Multi-provider LLM abstraction.
 * Sends a transcript to an LLM and gets back a 2-byte LED command + explanation.
 * Supports Anthropic (Claude), OpenAI-compatible APIs, and Ollama (local).
 */

import { LEDCommand, isValidCommand } from '../ble/commands';
import { ProviderConfig } from '../storage/apiKey';

// ── System Prompt (shared across all providers) ──────────────────────────────

const SYSTEM_PROMPT = `You are the response engine for a wearable LED device.
When given a user's statement or question, respond in exactly this format:

Line 1: One of the LED codes below (just the 2-character code, nothing else)
Line 2+: A short, conversational answer (1 sentence, 2 max). Keep it punchy — this displays on a phone screen.

LED codes:
GS — green solid    : yes, confident affirmation
GP — green pulse    : yes, gentle / warm agreement
GC — green chase    : yes, enthusiastic or excited
RS — red solid      : no, firm refusal or negative answer
RF — red flicker    : warning, urgent concern, or danger
YP — yellow pulse   : uncertain, maybe, or nuanced answer
BS — blue solid     : neutral information, acknowledgment, or factual statement
PS — purple solid   : creative, imaginative, or inspired ("cool idea!", "that's inventive")
PP — purple pulse   : deep, philosophical, or profound ("that's a big question")

The user's message may include [Sensor data: ...] with readings from the wearable
device's sensors (temperature, light level, accelerometer). Use this data to give
more grounded responses.

Examples:
GS
Yes! 2 + 2 is definitely 4.

PS
That's a really inventive idea — mixing music with robotics could be amazing.

PP
That's one of the oldest questions in philosophy. Nobody has a definitive answer.`;

// ── Types ────────────────────────────────────────────────────────────────────

export { SYSTEM_PROMPT as DEFAULT_SYSTEM_PROMPT };

export interface LLMResponse {
  command: LEDCommand;
  explanation: string;
}

// ── Provider-specific request builders ───────────────────────────────────────

async function fetchAnthropic(
  transcript: string,
  model: string,
  apiKey: string,
  systemPrompt: string,
): Promise<string> {
  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model,
      max_tokens: 100,
      system: systemPrompt,
      messages: [{ role: 'user', content: transcript }],
    }),
  });

  if (!response.ok) {
    const err = await response.text();
    throw new Error(`Anthropic API error ${response.status}: ${err}`);
  }

  const data = await response.json();
  return (data.content?.[0]?.text ?? '').trim();
}

async function fetchOpenAI(
  transcript: string,
  model: string,
  apiKey: string,
  systemPrompt: string,
): Promise<string> {
  const response = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      max_tokens: 100,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: transcript },
      ],
    }),
  });

  if (!response.ok) {
    const err = await response.text();
    throw new Error(`OpenAI API error ${response.status}: ${err}`);
  }

  const data = await response.json();
  return (data.choices?.[0]?.message?.content ?? '').trim();
}

async function fetchOllama(
  transcript: string,
  model: string,
  ollamaUrl: string,
  systemPrompt: string,
): Promise<string> {
  const baseUrl = ollamaUrl.replace(/\/+$/, '');
  const response = await fetch(`${baseUrl}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      stream: false,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: transcript },
      ],
    }),
  });

  if (!response.ok) {
    const err = await response.text();
    throw new Error(`Ollama error ${response.status}: ${err}`);
  }

  const data = await response.json();
  return (data.message?.content ?? '').trim();
}

// ── Response Parser (shared) ─────────────────────────────────────────────────

function parseResponse(raw: string): LLMResponse {
  const lines = raw.split('\n');
  const firstLine = (lines[0] ?? '').trim().toUpperCase();
  const explanation = lines.slice(1).join('\n').trim();

  let command: LEDCommand = 'BS';

  if (isValidCommand(firstLine)) {
    command = firstLine;
  } else {
    const match = firstLine.match(/\b(GS|GP|GC|RS|RF|YP|BS|PS|PP)\b/);
    if (match && isValidCommand(match[1])) {
      command = match[1];
    } else {
      console.warn(`Unexpected LLM response: "${firstLine}", falling back to BS`);
    }
  }

  return {
    command,
    explanation: explanation || raw,
  };
}

// ── Main Entry Point ─────────────────────────────────────────────────────────

export async function getCommandFromLLM(
  transcript: string,
  config: ProviderConfig,
  apiKey: string | null,
): Promise<LLMResponse> {
  const prompt = config.customSystemPrompt?.trim() || SYSTEM_PROMPT;
  let raw: string;

  switch (config.provider) {
    case 'anthropic':
      if (!apiKey) throw new Error('Anthropic API key is required.');
      raw = await fetchAnthropic(transcript, config.model, apiKey, prompt);
      break;

    case 'openai':
      if (!apiKey) throw new Error('OpenAI API key is required.');
      raw = await fetchOpenAI(transcript, config.model, apiKey, prompt);
      break;

    case 'ollama':
      raw = await fetchOllama(
        transcript,
        config.model,
        config.ollamaUrl || 'http://localhost:11434',
        prompt,
      );
      break;

    default:
      throw new Error(`Unknown provider: ${config.provider}`);
  }

  return parseResponse(raw);
}
