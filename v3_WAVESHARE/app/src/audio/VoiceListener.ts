import { ExpoSpeechRecognitionModule } from 'expo-speech-recognition';

type SpeechEvent = {
  type?: string;
  data?: {
    error?: string;
    isFinal?: boolean;
    results?: Array<{
      transcript?: string;
    }>;
  };
};

export class VoiceListener {
  onResult: ((transcript: string) => void) | null = null;
  onPartial: ((transcript: string) => void) | null = null;
  onError: ((message: string) => void) | null = null;

  private pollTimer: ReturnType<typeof setInterval> | null = null;

  async start(locale = 'en-US'): Promise<void> {
    const { granted } = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
    if (!granted) {
      this.onError?.('Microphone permission denied');
      return;
    }

    this.startPolling();
    try {
      ExpoSpeechRecognitionModule.start({
        lang: locale,
        interimResults: true,
        continuous: true,
      });
    } catch (error) {
      this.stopPolling();
      const message = error instanceof Error ? error.message : 'Could not start speech recognition';
      this.onError?.(message);
    }
  }

  async stop(): Promise<void> {
    try {
      ExpoSpeechRecognitionModule.stop();
      this.pollOnce();
    } catch {
      // The native module can throw if recognition has already stopped.
    }

    setTimeout(() => {
      this.pollOnce();
      this.stopPolling();
    }, 500);
  }

  destroy(): void {
    this.stopPolling();
  }

  private startPolling(): void {
    if (this.pollTimer) {
      return;
    }
    this.pollTimer = setInterval(() => this.pollOnce(), 150);
  }

  private stopPolling(): void {
    if (!this.pollTimer) {
      return;
    }
    clearInterval(this.pollTimer);
    this.pollTimer = null;
  }

  private pollOnce(): void {
    let events: SpeechEvent[] | undefined;
    try {
      events = (ExpoSpeechRecognitionModule as unknown as { pollEvents?: () => SpeechEvent[] }).pollEvents?.();
    } catch {
      return;
    }

    if (!events) {
      return;
    }
    for (const event of events) {
      this.handleEvent(event);
    }
  }

  private handleEvent(event: SpeechEvent): void {
    if (event.type === 'result') {
      const text = event.data?.results?.[0]?.transcript?.trim();
      if (!text) {
        return;
      }
      if (event.data?.isFinal) {
        this.onResult?.(text);
      } else {
        this.onPartial?.(text);
      }
    }

    if (event.type === 'error') {
      const message = event.data?.error || 'Speech recognition error';
      if (message !== 'no-speech') {
        this.onError?.(message);
      }
    }

    if (event.type === 'end') {
      this.stopPolling();
    }
  }
}
