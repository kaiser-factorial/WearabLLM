/**
 * Wraps expo-speech-recognition for push-to-talk voice recognition.
 * Uses the device's native STT engine (no transcription API cost).
 *
 * iOS 26 workaround: uses pollEvents() instead of addListener() since
 * Expo's JSI event system crashes on iOS 26. The native module queues
 * events and JS polls for them every 200ms while recognition is active.
 */

import { ExpoSpeechRecognitionModule } from 'expo-speech-recognition';

export class VoiceListener {
  onResult: ((transcript: string) => void) | null = null;
  onError: ((message: string) => void) | null = null;
  onPartial: ((partial: string) => void) | null = null;

  private pollTimer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    // No native calls in constructor
  }

  private startPolling() {
    if (this.pollTimer) return;
    console.log('🟡 [VoiceListener] Starting event polling');
    this.pollTimer = setInterval(() => {
      try {
        const events = (ExpoSpeechRecognitionModule as any).pollEvents();
        if (!events || events.length === 0) return;
        for (const event of events) {
          this.handleEvent(event);
        }
      } catch (e) {
        console.error('🔴 [VoiceListener] pollEvents error:', e);
      }
    }, 150);
  }

  private stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
      console.log('🟡 [VoiceListener] Stopped event polling');
    }
  }

  private handleEvent(event: any) {
    const type = event.type;
    const data = event.data;

    if (type === 'result') {
      const text = data?.results?.[0]?.transcript;
      if (text && data?.isFinal) {
        this.onResult?.(text);
      } else if (text) {
        this.onPartial?.(text);
      }
    } else if (type === 'error') {
      const msg = data?.error ?? 'Speech recognition error';
      if (msg === 'no-speech') return;
      this.onError?.(msg);
    } else if (type === 'end') {
      this.stopPolling();
    }
  }

  async start(locale = 'en-US'): Promise<void> {
    console.log('🟡 [VoiceListener] start() called');

    const { granted } = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
    if (!granted) {
      this.onError?.('Microphone permission denied');
      return;
    }

    // Start polling for events BEFORE starting recognition
    this.startPolling();

    try {
      ExpoSpeechRecognitionModule.start({
        lang: locale,
        interimResults: true,
        continuous: true,
      });
      console.log('🟡 [VoiceListener] start() succeeded');
    } catch (e: any) {
      console.error('🔴 [VoiceListener] start() failed:', e);
      this.stopPolling();
      this.onError?.(e.message ?? 'Could not start voice recognition');
    }
  }

  async stop(): Promise<void> {
    console.log('🟡 [VoiceListener] stop() called');
    try {
      ExpoSpeechRecognitionModule.stop();
    } catch {
      // ignore — may already be stopped
    }
    // Poll one last time to catch any final results, then stop
    try {
      const events = (ExpoSpeechRecognitionModule as any).pollEvents();
      if (events && events.length > 0) {
        for (const event of events) {
          this.handleEvent(event);
        }
      }
    } catch {}
    // Give a brief window for the final result, then force stop
    setTimeout(() => {
      this.stopPolling();
    }, 500);
  }

  async cancel(): Promise<void> {
    try {
      ExpoSpeechRecognitionModule.abort();
    } catch {
      // ignore
    }
    this.stopPolling();
  }

  destroy() {
    this.stopPolling();
  }
}
