import { StatusBar } from 'expo-status-bar';
import * as Speech from 'expo-speech';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Linking,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';

import {
  BridgeAction,
  BridgeConversationSession,
  BridgeConversationTurn,
  BridgeDevice,
  BridgeHealth,
  BridgeInteractionResponse,
  archiveBridgeSession,
  acknowledgeBridgeAction,
  claimBridgeAction,
  createBridgeInteraction,
  fetchBridgeAction,
  fetchBridgeConversation,
  fetchBridgeHealth,
  normalizeBridgeBaseUrl,
  queryBridgeText,
  renameBridgeSession,
  resetBridgeSession,
  sendBridgeHeartbeat,
} from './protocol/bridgeClient';
import {
  loadAppDeviceId,
  loadAllowAutomaticSpeech,
  loadBridgeToken,
  loadBridgeUrl,
  saveBridgeToken,
  saveBridgeUrl,
  saveAllowAutomaticSpeech,
} from './storage/settings';

const ANDROID_BODY_ID = 'wearabllm-android';
const WAVESHARE_BODY_ID = 'wearabllm-esp32';

const CANONICAL_BODIES: BridgeDevice[] = [
  { id: WAVESHARE_BODY_ID, label: 'Waveshare', kind: 'home', status: 'active', description: 'ESP32-S3 room body', seen: false },
  { id: ANDROID_BODY_ID, label: 'Android', kind: 'phone', status: 'active', description: 'This phone', seen: true },
  { id: 'web-console', label: 'Web console', kind: 'web', status: 'active', description: 'Browser dashboard', seen: false },
  { id: 'wearabllm-wearable', label: 'Wearable', kind: 'wearable', status: 'planned', description: 'Planned portable body', seen: false },
];

function mergeBodies(remote: BridgeDevice[]): BridgeDevice[] {
  const merged = new Map(CANONICAL_BODIES.map((body) => [body.id, body]));
  for (const body of remote) {
    if (!body.id || body.id === 'local-bridge') continue;
    merged.set(body.id, { ...(merged.get(body.id) ?? body), ...body });
  }
  const order = new Map(CANONICAL_BODIES.map((body, index) => [body.id, index]));
  return [...merged.values()].sort(
    (left, right) => (order.get(left.id) ?? 100) - (order.get(right.id) ?? 100),
  );
}

function bodyColor(kind: string): string {
  if (kind === 'home') return '#6ee7b7';
  if (kind === 'phone') return '#facc15';
  if (kind === 'web') return '#93c5fd';
  if (kind === 'wearable') return '#c084fc';
  return '#818cf8';
}

function expressionColor(command: string): string {
  if (command.startsWith('G')) return '#22c55e';
  if (command.startsWith('R')) return '#ef4444';
  if (command.startsWith('Y')) return '#eab308';
  if (command.startsWith('P')) return '#a855f7';
  return '#3b82f6';
}

function formatTurnTime(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function formatSessionTime(value: string | null): string {
  if (!value) return 'New conversation';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? 'Conversation'
    : date.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function sessionTitle(session: BridgeConversationSession | null | undefined, activeId: string): string {
  if (!session) return 'Current conversation';
  if (session.title) return session.title;
  if (session.id === activeId) return 'Current conversation';
  const summary = (session.summary ?? '').replace(/\s+/g, ' ').trim();
  if (summary) return summary.length > 42 ? `${summary.slice(0, 41)}…` : summary;
  return formatSessionTime(session.started_at ?? session.last_turn_at);
}

export default function App() {
  const [bridgeUrl, setBridgeUrl] = useState('https://brick-factorial-wearabllm-agent.hf.space');
  const [bridgeToken, setBridgeToken] = useState('');
  const [appDeviceId, setAppDeviceId] = useState(ANDROID_BODY_ID);
  const [settingsReady, setSettingsReady] = useState(false);
  const [bridgeHealth, setBridgeHealth] = useState<BridgeHealth | null>(null);
  const [bodies, setBodies] = useState<BridgeDevice[]>(CANONICAL_BODIES);
  const [turns, setTurns] = useState<BridgeConversationTurn[]>([]);
  const [sessions, setSessions] = useState<BridgeConversationSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState('');
  const [selectedSessionId, setSelectedSessionId] = useState('');
  const [transcript, setTranscript] = useState('');
  const [lastAction, setLastAction] = useState<BridgeAction | null>(null);
  const [deliverToWaveshare, setDeliverToWaveshare] = useState(false);
  const [allowAutomaticSpeech, setAllowAutomaticSpeech] = useState(false);
  const [activeExpression, setActiveExpression] = useState<BridgeAction | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [isConfigOpen, setIsConfigOpen] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [isCheckingHealth, setIsCheckingHealth] = useState(false);
  const [isRefreshingThread, setIsRefreshingThread] = useState(false);
  const [renamingSessionId, setRenamingSessionId] = useState('');
  const [renameDraft, setRenameDraft] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [menuSessionId, setMenuSessionId] = useState('');
  const [status, setStatus] = useState('Sphere idle');

  const bridgeUrlRef = useRef(bridgeUrl);
  const bridgeTokenRef = useRef(bridgeToken);
  const selectedSessionIdRef = useRef(selectedSessionId);
  const healthRequestIdRef = useRef(0);
  const isSendingRef = useRef(false);
  const messageScrollRef = useRef<ScrollView | null>(null);
  const actionReceiverBusyRef = useRef(false);
  const activeExpressionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const selectedSession = useMemo(
    () => sessions.find((session) => session.id === selectedSessionId),
    [sessions, selectedSessionId],
  );
  const archivedCount = useMemo(() => sessions.filter((session) => Boolean(session.archived_at)).length, [sessions]);
  const visibleSessions = useMemo(
    () => sessions.filter((session) => Boolean(session.archived_at) === showArchived),
    [sessions, showArchived],
  );
  const isCurrentConversation = !selectedSessionId || selectedSessionId === activeSessionId;

  useEffect(() => {
    Promise.all([loadBridgeUrl(), loadBridgeToken(), loadAppDeviceId(), loadAllowAutomaticSpeech()])
      .then(([url, token, deviceId, automaticSpeech]) => {
        bridgeUrlRef.current = url;
        bridgeTokenRef.current = token;
        setBridgeUrl(url);
        setBridgeToken(token);
        setAppDeviceId(deviceId);
        setAllowAutomaticSpeech(automaticSpeech);
      })
      .finally(() => setSettingsReady(true));
  }, []);

  useEffect(() => { bridgeUrlRef.current = bridgeUrl; }, [bridgeUrl]);
  useEffect(() => { bridgeTokenRef.current = bridgeToken; }, [bridgeToken]);
  useEffect(() => { selectedSessionIdRef.current = selectedSessionId; }, [selectedSessionId]);

  async function refreshConversation(showError = false) {
    if (isSendingRef.current) return;
    const normalizedUrl = normalizeBridgeBaseUrl(bridgeUrlRef.current);
    if (!normalizedUrl) return;
    setIsRefreshingThread(true);
    try {
      const snapshot = await fetchBridgeConversation(
        normalizedUrl,
        '',
        bridgeTokenRef.current,
        selectedSessionIdRef.current,
      );
      setTurns(snapshot.turns);
      setBodies(mergeBodies(snapshot.devices));
      setSessions(snapshot.sessions);
      const nextActiveId = snapshot.active_session_id ?? '';
      setActiveSessionId(nextActiveId);
      if (!selectedSessionIdRef.current && nextActiveId) {
        selectedSessionIdRef.current = nextActiveId;
        setSelectedSessionId(nextActiveId);
      }
    } catch (error) {
      if (showError) {
        Alert.alert('Could not load conversation', error instanceof Error ? error.message : String(error));
      }
    } finally {
      setIsRefreshingThread(false);
    }
  }

  useEffect(() => {
    if (!settingsReady) return;
    void refreshConversation();
    const timer = setInterval(() => void refreshConversation(), 4000);
    return () => clearInterval(timer);
  }, [settingsReady, selectedSessionId]);

  useEffect(() => {
    if (!settingsReady) return;
    const heartbeat = () => {
      const normalizedUrl = normalizeBridgeBaseUrl(bridgeUrlRef.current);
      if (!normalizedUrl) return;
      void sendBridgeHeartbeat(normalizedUrl, appDeviceId, bridgeTokenRef.current).catch(() => undefined);
    };
    heartbeat();
    const timer = setInterval(heartbeat, 8000);
    return () => clearInterval(timer);
  }, [settingsReady, appDeviceId]);

  useEffect(() => {
    if (!settingsReady) return;
    let cancelled = false;
    const receiveAction = async () => {
      if (cancelled || actionReceiverBusyRef.current) return;
      const normalizedUrl = normalizeBridgeBaseUrl(bridgeUrlRef.current);
      if (!normalizedUrl) return;
      actionReceiverBusyRef.current = true;
      try {
        const action = await claimBridgeAction(normalizedUrl, appDeviceId, bridgeTokenRef.current);
        if (!action || cancelled) return;
        setActiveExpression(action);
        setStatus(`Sphere expression · ${action.command}`);
        if (activeExpressionTimerRef.current) clearTimeout(activeExpressionTimerRef.current);
        activeExpressionTimerRef.current = setTimeout(() => setActiveExpression(null), 8000);
        await acknowledgeBridgeAction(normalizedUrl, appDeviceId, action.id, 'delivered', bridgeTokenRef.current);
        await acknowledgeBridgeAction(normalizedUrl, appDeviceId, action.id, 'rendered', bridgeTokenRef.current);
        const shouldSpeak = allowAutomaticSpeech && action.expression.channels.includes('audio');
        if (!shouldSpeak) {
          await acknowledgeBridgeAction(normalizedUrl, appDeviceId, action.id, 'completed', bridgeTokenRef.current);
          return;
        }
        await new Promise<void>((resolve) => {
          Speech.speak(action.expression.text, {
            onStart: () => {
              void acknowledgeBridgeAction(
                normalizedUrl,
                appDeviceId,
                action.id,
                'tts_started',
                bridgeTokenRef.current,
              );
            },
            onDone: () => {
              void acknowledgeBridgeAction(
                normalizedUrl,
                appDeviceId,
                action.id,
                'played',
                bridgeTokenRef.current,
              ).finally(resolve);
            },
            onError: (error) => {
              void acknowledgeBridgeAction(
                normalizedUrl,
                appDeviceId,
                action.id,
                'failed',
                bridgeTokenRef.current,
                String(error),
              ).finally(resolve);
            },
          });
        });
      } catch (error) {
        setStatus(`Body action failed: ${error instanceof Error ? error.message : String(error)}`);
      } finally {
        actionReceiverBusyRef.current = false;
      }
    };
    void receiveAction();
    const timer = setInterval(() => void receiveAction(), 4000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [settingsReady, appDeviceId, allowAutomaticSpeech]);

  useEffect(() => () => {
    if (activeExpressionTimerRef.current) clearTimeout(activeExpressionTimerRef.current);
    Speech.stop();
  }, []);

  useEffect(() => {
    if (!lastAction || ['completed', 'played', 'failed', 'expired'].includes(lastAction.status)) return;
    const timer = setInterval(() => {
      void fetchBridgeAction(bridgeUrlRef.current, lastAction.id, bridgeTokenRef.current)
        .then((action) => {
          setLastAction(action);
          setStatus(`Waveshare ${action.status}`);
        })
        .catch((error) => setStatus(`Delivery check failed: ${error instanceof Error ? error.message : String(error)}`));
    }, 2000);
    return () => clearInterval(timer);
  }, [lastAction]);

  async function submitTranscript() {
    const cleanTranscript = transcript.trim();
    if (!cleanTranscript) return;
    if (!isCurrentConversation) {
      Alert.alert('Archived conversation', 'Start a new conversation before sending another message.');
      return;
    }
    setIsSending(true);
    isSendingRef.current = true;
    setIsThinking(true);
    setTurns((current) => [
      ...current,
      {
        id: `optimistic-${Date.now()}`,
        device_id: appDeviceId,
        role: 'user',
        content: cleanTranscript,
        sources: [],
        tool_results: [],
        created_at: new Date().toISOString(),
      },
    ]);
    setTranscript('');
    setStatus(deliverToWaveshare ? 'Queueing for Waveshare' : 'Sphere is thinking');
    try {
      const normalizedUrl = normalizeBridgeBaseUrl(bridgeUrlRef.current);
      if (!normalizedUrl) throw new Error('Enter the Sphere bridge URL in Connection first.');
      bridgeUrlRef.current = normalizedUrl;
      setBridgeUrl(normalizedUrl);
      await saveBridgeUrl(normalizedUrl);

      const response = deliverToWaveshare
        ? await createBridgeInteraction(
            normalizedUrl,
            cleanTranscript,
            appDeviceId,
            WAVESHARE_BODY_ID,
            `android-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            bridgeTokenRef.current,
            WAVESHARE_BODY_ID,
          )
        : await queryBridgeText(
            normalizedUrl,
            cleanTranscript,
            bridgeTokenRef.current,
            appDeviceId,
            ANDROID_BODY_ID,
          );

      const action = deliverToWaveshare ? (response as BridgeInteractionResponse).action : null;
      setLastAction(action);
      setStatus(action ? `Waveshare ${action.status}` : 'Reply shared');
      setIsThinking(false);
      isSendingRef.current = false;
      await refreshConversation();
    } catch (error) {
      setStatus('Sphere request failed');
      setIsThinking(false);
      isSendingRef.current = false;
      await refreshConversation();
      Alert.alert('Sphere request failed', error instanceof Error ? error.message : String(error));
    } finally {
      isSendingRef.current = false;
      setIsSending(false);
    }
  }

  async function handleCheckBridge() {
    setIsCheckingHealth(true);
    setStatus('Checking Sphere connection');
    try {
      const normalizedUrl = normalizeBridgeBaseUrl(bridgeUrl);
      if (!normalizedUrl) throw new Error('Enter a bridge URL first.');
      const requestId = healthRequestIdRef.current + 1;
      healthRequestIdRef.current = requestId;
      bridgeUrlRef.current = normalizedUrl;
      setBridgeUrl(normalizedUrl);
      await Promise.all([saveBridgeUrl(normalizedUrl), saveBridgeToken(bridgeTokenRef.current)]);
      const health = await fetchBridgeHealth(normalizedUrl, bridgeTokenRef.current);
      if (requestId !== healthRequestIdRef.current) return;
      setBridgeHealth(health);
      setStatus(health.config.dry_run ? 'Sphere connected · dry run' : 'Sphere connected');
      await refreshConversation();
    } catch (error) {
      setBridgeHealth(null);
      setStatus('Sphere connection failed');
      Alert.alert('Sphere connection failed', error instanceof Error ? error.message : String(error));
    } finally {
      setIsCheckingHealth(false);
    }
  }

  async function startNewConversation() {
    const normalizedUrl = normalizeBridgeBaseUrl(bridgeUrlRef.current);
    if (!normalizedUrl) {
      Alert.alert('Connection needed', 'Configure the Sphere bridge first.');
      return;
    }
    try {
      const payload = await resetBridgeSession(normalizedUrl, bridgeTokenRef.current);
      const nextId = payload.active_session_id ?? '';
      selectedSessionIdRef.current = nextId;
      setSelectedSessionId(nextId);
      setTurns([]);
      setStatus('New conversation started');
      setIsDrawerOpen(false);
      await refreshConversation();
    } catch (error) {
      Alert.alert('Could not start conversation', error instanceof Error ? error.message : String(error));
    }
  }

  function chooseConversation(sessionId: string) {
    selectedSessionIdRef.current = sessionId;
    setSelectedSessionId(sessionId);
    setMenuSessionId('');
    setIsDrawerOpen(false);
  }

  function toggleArchiveView() {
    const nextArchived = !showArchived;
    const candidates = sessions.filter((session) => Boolean(session.archived_at) === nextArchived);
    const nextId = nextArchived ? (candidates[0]?.id ?? '') : (activeSessionId || candidates[0]?.id || '');
    setShowArchived(nextArchived);
    setMenuSessionId('');
    selectedSessionIdRef.current = nextId;
    setSelectedSessionId(nextId);
  }

  function confirmArchiveConversation(session: BridgeConversationSession) {
    const isCurrent = session.id === activeSessionId;
    Alert.alert(
      'Archive conversation?',
      isCurrent
        ? 'The transcript will remain available, and Sphere will start a new conversation.'
        : 'The transcript will remain available in the conversation list.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Archive',
          style: 'destructive',
          onPress: () => {
            setMenuSessionId('');
            void archiveBridgeSession(bridgeUrlRef.current, session.id, bridgeTokenRef.current)
              .then(async (payload) => {
                if (selectedSessionIdRef.current === session.id && payload.active_session_id) {
                  selectedSessionIdRef.current = payload.active_session_id;
                  setSelectedSessionId(payload.active_session_id);
                }
                setShowArchived(false);
                setStatus('Conversation archived');
                await refreshConversation();
              })
              .catch((error) => Alert.alert('Could not archive conversation', error instanceof Error ? error.message : String(error)));
          },
        },
      ],
    );
  }

  async function saveConversationName(session: BridgeConversationSession) {
    const title = renameDraft.replace(/\s+/g, ' ').trim();
    if (!title) {
      Alert.alert('Name required', 'Enter a conversation name first.');
      return;
    }
    try {
      await renameBridgeSession(bridgeUrlRef.current, session.id, title, bridgeTokenRef.current);
      setRenamingSessionId('');
      setRenameDraft('');
      setStatus('Conversation renamed');
      await refreshConversation();
    } catch (error) {
      Alert.alert('Could not rename conversation', error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="light" />
      <KeyboardAvoidingView style={styles.keyboardView} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={styles.headerRow}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Open conversations"
            style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}
            onPress={() => setIsDrawerOpen(true)}
          >
            <Text style={styles.menuGlyph}>☰</Text>
          </Pressable>
          <View style={styles.headerCopy}>
            <Text style={styles.eyebrow}>WEARABLLM</Text>
            <Text style={styles.title}>Sphere</Text>
            <Text style={styles.subtitle} numberOfLines={1}>{sessionTitle(selectedSession, activeSessionId)}</Text>
          </View>
          {isRefreshingThread ? <ActivityIndicator color="#94a3b8" size="small" /> : null}
        </View>

        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.bodyStripScroll}
          contentContainerStyle={styles.bodyStrip}
        >
          {bodies.map((body) => (
            <View key={body.id} style={[styles.bodyChip, body.status === 'planned' && styles.planned]}>
              <View style={[styles.bodyDot, { backgroundColor: bodyColor(body.kind) }]} />
              <Text style={styles.bodyChipLabel}>{body.label}</Text>
              <Text style={styles.bodyChipState}>{body.status === 'planned' ? 'Planned' : body.seen ? 'Live' : 'Idle'}</Text>
            </View>
          ))}
        </ScrollView>

        {activeExpression ? (
          <View
            style={[
              styles.expressionBanner,
              { borderColor: expressionColor(activeExpression.expression.command) },
            ]}
          >
            <View
              style={[
                styles.expressionGlow,
                { backgroundColor: expressionColor(activeExpression.expression.command) },
              ]}
            />
            <View style={styles.expressionCopy}>
              <Text style={styles.expressionMeta}>
                SPHERE · {activeExpression.expression.command} · {activeExpression.expression.channels.join(' + ')}
              </Text>
              <Text style={styles.expressionText}>{activeExpression.expression.text}</Text>
            </View>
          </View>
        ) : null}

        <ScrollView
          ref={messageScrollRef}
          style={styles.messages}
          contentContainerStyle={styles.messageContent}
          keyboardShouldPersistTaps="handled"
          onContentSizeChange={() => messageScrollRef.current?.scrollToEnd({ animated: true })}
        >
          {turns.length === 0 ? (
            <View style={styles.emptyState}>
              <Text style={styles.emptyTitle}>Quiet sphere</Text>
              <Text style={styles.emptyText}>Send a message here or speak through Waveshare to begin.</Text>
            </View>
          ) : turns.map((turn, index) => {
            const body = bodies.find((item) => item.id === turn.device_id);
            const assistant = turn.role === 'assistant';
            return (
              <View key={`${turn.id}-${index}`} style={[styles.bubble, assistant ? styles.assistantBubble : styles.userBubble]}>
                <View style={styles.bubbleMeta}>
                  <View style={[styles.bodyDot, { backgroundColor: bodyColor(body?.kind ?? 'custom') }]} />
                  <Text style={styles.bubbleDevice}>{body?.label ?? turn.device_id}</Text>
                  <Text style={styles.bubbleRole}>{assistant ? 'WearabLLM' : 'You'}</Text>
                  <Text style={styles.bubbleTime}>{formatTurnTime(turn.created_at)}</Text>
                </View>
                <Text style={styles.bubbleText}>{turn.content}</Text>
                {turn.tool_results.length > 0 ? (
                  <View style={styles.toolActivityList}>
                    {turn.tool_results.map((activity, activityIndex) => (
                      <Text
                        key={`${activity.name}-${activityIndex}`}
                        style={[styles.toolActivityText, !activity.ok ? styles.toolActivityFailed : null]}
                      >
                        ↳ {activity.summary}
                      </Text>
                    ))}
                  </View>
                ) : null}
                {turn.sources.length > 0 ? (
                  <View style={styles.sourceList}>
                    <Text style={styles.sourceLabel}>Sources</Text>
                    {turn.sources.map((source, sourceIndex) => (
                      <Text
                        key={`${source.url}-${sourceIndex}`}
                        numberOfLines={1}
                        onPress={() => void Linking.openURL(source.url)}
                        style={styles.sourceLink}
                      >
                        {sourceIndex + 1}. {source.title || source.url}
                      </Text>
                    ))}
                  </View>
                ) : null}
              </View>
            );
          })}
          {isThinking ? (
            <View style={[styles.bubble, styles.assistantBubble, styles.thinkingBubble]}>
              <View style={styles.bubbleMeta}>
                <View style={[styles.bodyDot, { backgroundColor: '#7cf0c2' }]} />
                <Text style={styles.bubbleDevice}>Sphere</Text>
                <Text style={styles.bubbleRole}>WearabLLM</Text>
              </View>
              <Text style={styles.thinkingText}>Thinking…</Text>
            </View>
          ) : null}
        </ScrollView>

        <View style={styles.composer}>
          <View style={styles.deliveryRow}>
            <Switch
              value={deliverToWaveshare}
              onValueChange={setDeliverToWaveshare}
              trackColor={{ false: '#334155', true: '#287b63' }}
              thumbColor={deliverToWaveshare ? '#7cf0c2' : '#94a3b8'}
            />
            <Text style={styles.deliveryLabel}>Also play on Waveshare</Text>
            <Text style={styles.statusText} numberOfLines={1}>{status}</Text>
          </View>
          <View style={styles.inputRow}>
            <TextInput
              value={transcript}
              onChangeText={setTranscript}
              multiline
              editable={isCurrentConversation && !isSending}
              placeholder={isCurrentConversation ? 'Message Sphere…' : 'Archived conversation'}
              placeholderTextColor="#64748b"
              style={styles.messageInput}
            />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={deliverToWaveshare ? 'Send and play on Waveshare' : 'Send message'}
              style={({ pressed }) => [styles.sendButton, (pressed || isSending) && styles.pressed, (!transcript.trim() || !isCurrentConversation) && styles.disabled]}
              onPress={() => void submitTranscript()}
              disabled={isSending || !transcript.trim() || !isCurrentConversation}
            >
              {isSending ? <ActivityIndicator color="#07110e" size="small" /> : <Text style={styles.sendText}>↑</Text>}
            </Pressable>
          </View>
          {lastAction ? <Text style={styles.actionText}>Waveshare {lastAction.status}{lastAction.error ? ` · ${lastAction.error}` : ''}</Text> : null}
        </View>
      </KeyboardAvoidingView>

      {isDrawerOpen ? (
        <View style={styles.drawerLayer}>
          <Pressable accessibilityRole="button" accessibilityLabel="Close conversations" style={styles.drawerBackdrop} onPress={() => setIsDrawerOpen(false)} />
          <SafeAreaView style={styles.drawer}>
            <View style={styles.drawerHeader}>
              <View>
                <Text style={styles.eyebrow}>SPHERE</Text>
                <Text style={styles.drawerTitle}>{showArchived ? 'Archive' : 'Conversations'}</Text>
              </View>
              <View style={styles.drawerActions}>
                <Pressable style={styles.newButton} onPress={() => void startNewConversation()} accessibilityLabel="New conversation">
                  <Text style={styles.newButtonText}>+</Text>
                </Pressable>
                <Pressable style={styles.drawerClose} onPress={() => setIsDrawerOpen(false)} accessibilityLabel="Close">
                  <Text style={styles.drawerCloseText}>×</Text>
                </Pressable>
              </View>
            </View>
            <ScrollView style={styles.conversationScroll} contentContainerStyle={styles.conversationList}>
              {visibleSessions.length === 0 ? (
                <Text style={styles.drawerEmpty}>{showArchived ? 'No archived conversations yet.' : 'No conversations yet. Tap + to begin.'}</Text>
              ) : visibleSessions.map((session) => (
                <View key={session.id} style={styles.conversationRowWrap}>
                  {renamingSessionId === session.id ? (
                    <View style={[styles.conversationItem, styles.renameEditor]}>
                      <TextInput
                        value={renameDraft}
                        onChangeText={setRenameDraft}
                        autoFocus
                        maxLength={120}
                        placeholder="Conversation name"
                        placeholderTextColor="#64748b"
                        style={styles.renameInput}
                      />
                      <Pressable style={styles.conversationMenuAction} onPress={() => void saveConversationName(session)}>
                        <Text style={styles.conversationActionText}>Save</Text>
                      </Pressable>
                      <Pressable style={styles.conversationMenuAction} onPress={() => setRenamingSessionId('')}>
                        <Text style={styles.conversationActionText}>Cancel</Text>
                      </Pressable>
                    </View>
                  ) : (
                    <View style={[styles.conversationItem, session.id === selectedSessionId && styles.conversationItemActive]}>
                      <Pressable style={styles.conversationCopy} onPress={() => chooseConversation(session.id)}>
                        <Text style={styles.conversationTitle} numberOfLines={1}>{sessionTitle(session, activeSessionId)}</Text>
                        <Text style={styles.conversationTime}>{formatSessionTime(session.last_turn_at ?? session.started_at)}</Text>
                      </Pressable>
                      {session.id === activeSessionId ? <Text style={styles.liveBadge}>LIVE</Text> : session.archived_at ? <Text style={styles.archivedBadge}>ARCHIVED</Text> : null}
                      <Pressable
                        style={styles.moreButton}
                        accessibilityLabel={`Actions for ${sessionTitle(session, activeSessionId)}`}
                        onPress={() => setMenuSessionId((current) => current === session.id ? '' : session.id)}
                      >
                        <Text style={styles.moreButtonText}>…</Text>
                      </Pressable>
                    </View>
                  )}
                  {menuSessionId === session.id && renamingSessionId !== session.id ? (
                    <View style={styles.conversationMenu}>
                      <Pressable
                        style={styles.conversationMenuAction}
                        onPress={() => {
                          setMenuSessionId('');
                          setRenamingSessionId(session.id);
                          setRenameDraft(session.title || sessionTitle(session, activeSessionId));
                        }}
                      >
                        <Text style={styles.conversationActionText}>Rename</Text>
                      </Pressable>
                      {!session.archived_at ? (
                        <Pressable style={styles.conversationMenuAction} onPress={() => confirmArchiveConversation(session)}>
                          <Text style={styles.archiveActionText}>Archive</Text>
                        </Pressable>
                      ) : null}
                    </View>
                  ) : null}
                </View>
              ))}
            </ScrollView>

            <Pressable style={[styles.archiveToggle, showArchived && styles.archiveToggleActive]} onPress={toggleArchiveView}>
              <Text style={styles.archiveToggleText}>{showArchived ? '← Conversations' : 'Archive'}</Text>
              <Text style={styles.archiveCount}>{archivedCount}</Text>
            </Pressable>

            <View style={styles.configPanel}>
              <Pressable style={styles.configToggle} onPress={() => setIsConfigOpen((current) => !current)}>
                <View style={styles.configToggleCopy}>
                  <Text style={styles.configToggleTitle}>Connection</Text>
                  <Text style={styles.configToggleMeta} numberOfLines={1}>{normalizeBridgeBaseUrl(bridgeUrl) || 'Not configured'}</Text>
                </View>
                <Text style={styles.configChevron}>{isConfigOpen ? '▲' : '▼'}</Text>
              </Pressable>
              {isConfigOpen ? (
                <ScrollView style={styles.configBodyScroll} keyboardShouldPersistTaps="handled">
                  <View style={styles.configBody}>
                    <Text style={styles.label}>Sphere bridge URL</Text>
                    <TextInput
                      value={bridgeUrl}
                      onChangeText={(value) => {
                        bridgeUrlRef.current = value;
                        healthRequestIdRef.current += 1;
                        setBridgeUrl(value);
                        setBridgeHealth(null);
                      }}
                      autoCapitalize="none"
                      autoCorrect={false}
                      keyboardType="url"
                      placeholder="https://brick-factorial-wearabllm-agent.hf.space"
                      placeholderTextColor="#64748b"
                      style={styles.configInput}
                    />
                    <Text style={styles.label}>Bridge token</Text>
                    <TextInput
                      value={bridgeToken}
                      onChangeText={(value) => {
                        bridgeTokenRef.current = value;
                        healthRequestIdRef.current += 1;
                        setBridgeToken(value);
                        setBridgeHealth(null);
                      }}
                      autoCapitalize="none"
                      autoCorrect={false}
                      secureTextEntry
                      placeholder="Only if bridge auth is enabled"
                      placeholderTextColor="#64748b"
                      style={styles.configInput}
                    />
                    <View style={styles.preferenceRow}>
                      <View style={styles.preferenceCopy}>
                        <Text style={styles.preferenceTitle}>Automatic Sphere speech</Text>
                        <Text style={styles.helperText}>Allow targeted body actions to speak through this phone.</Text>
                      </View>
                      <Switch
                        value={allowAutomaticSpeech}
                        onValueChange={(value) => {
                          setAllowAutomaticSpeech(value);
                          void saveAllowAutomaticSpeech(value);
                        }}
                        trackColor={{ false: '#334155', true: '#287b63' }}
                        thumbColor={allowAutomaticSpeech ? '#7cf0c2' : '#94a3b8'}
                      />
                    </View>
                    <Pressable style={[styles.testButton, isCheckingHealth && styles.disabled]} onPress={() => void handleCheckBridge()} disabled={isCheckingHealth}>
                      {isCheckingHealth ? <ActivityIndicator color="#e2e8f0" size="small" /> : <Text style={styles.testButtonText}>Save & test</Text>}
                    </Pressable>
                    <Text style={bridgeHealth ? styles.connectedText : styles.helperText}>
                      {bridgeHealth ? '● Connected to Sphere' : `Body ID: ${appDeviceId}`}
                    </Text>
                  </View>
                </ScrollView>
              ) : null}
            </View>
          </SafeAreaView>
        </View>
      ) : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#0b1020' },
  keyboardView: { flex: 1 },
  headerRow: { alignItems: 'center', flexDirection: 'row', gap: 10, paddingHorizontal: 14, paddingBottom: 8, paddingTop: 8 },
  headerCopy: { flex: 1 },
  eyebrow: { color: '#94a3b8', fontSize: 9, fontWeight: '900', letterSpacing: 1.5 },
  title: { color: '#f8fafc', fontSize: 27, fontWeight: '800', lineHeight: 30 },
  subtitle: { color: '#94a3b8', fontSize: 12, marginTop: 1 },
  iconButton: { alignItems: 'center', backgroundColor: '#111827', borderColor: '#334155', borderRadius: 9, borderWidth: 1, height: 36, justifyContent: 'center', width: 36 },
  menuGlyph: { color: '#f8fafc', fontSize: 18 },
  pressed: { opacity: 0.68, transform: [{ scale: 0.97 }] },
  bodyStripScroll: { flexGrow: 0, height: 45 },
  bodyStrip: { alignItems: 'center', gap: 7, paddingHorizontal: 14, paddingVertical: 5 },
  bodyChip: { alignItems: 'center', backgroundColor: '#111827', borderColor: '#273449', borderRadius: 999, borderWidth: 1, flexDirection: 'row', gap: 6, paddingHorizontal: 9, paddingVertical: 6 },
  bodyDot: { borderRadius: 4, height: 8, width: 8 },
  bodyChipLabel: { color: '#e2e8f0', fontSize: 11, fontWeight: '800' },
  bodyChipState: { color: '#64748b', fontSize: 9, textTransform: 'uppercase' },
  planned: { opacity: 0.5 },
  expressionBanner: { alignItems: 'stretch', backgroundColor: '#101827', borderRadius: 12, borderWidth: 1, flexDirection: 'row', marginHorizontal: 14, marginBottom: 8, overflow: 'hidden' },
  expressionGlow: { opacity: 0.9, width: 7 },
  expressionCopy: { flex: 1, gap: 4, paddingHorizontal: 11, paddingVertical: 9 },
  expressionMeta: { color: '#94a3b8', fontSize: 9, fontWeight: '900', letterSpacing: 0.8, textTransform: 'uppercase' },
  expressionText: { color: '#f8fafc', fontSize: 14, lineHeight: 19 },
  messages: { borderTopColor: '#1e293b', borderTopWidth: 1, flex: 1 },
  messageContent: { flexGrow: 1, gap: 9, padding: 14 },
  emptyState: { alignItems: 'center', flex: 1, justifyContent: 'center', padding: 28 },
  emptyTitle: { color: '#f8fafc', fontSize: 20, fontWeight: '800' },
  emptyText: { color: '#64748b', lineHeight: 19, marginTop: 6, textAlign: 'center' },
  bubble: { borderRadius: 13, borderWidth: 1, gap: 7, maxWidth: '91%', paddingHorizontal: 12, paddingVertical: 10 },
  userBubble: { alignSelf: 'flex-end', backgroundColor: '#182236', borderBottomRightRadius: 4, borderColor: '#334155' },
  assistantBubble: { alignSelf: 'flex-start', backgroundColor: '#101827', borderBottomLeftRadius: 4, borderColor: '#273449' },
  thinkingBubble: { borderColor: '#287b63', minWidth: 132 },
  thinkingText: { color: '#7cf0c2', fontSize: 15, fontWeight: '800' },
  bubbleMeta: { alignItems: 'center', flexDirection: 'row', gap: 5 },
  bubbleDevice: { color: '#cbd5e1', fontSize: 10, fontWeight: '800' },
  bubbleRole: { color: '#64748b', fontSize: 10 },
  bubbleTime: { color: '#64748b', flex: 1, fontSize: 9, textAlign: 'right' },
  bubbleText: { color: '#f8fafc', fontSize: 15, lineHeight: 21 },
  toolActivityList: { borderTopColor: '#273449', borderTopWidth: 1, gap: 4, marginTop: 3, paddingTop: 7 },
  toolActivityText: { color: '#94a3b8', fontFamily: 'monospace', fontSize: 10, lineHeight: 15 },
  toolActivityFailed: { color: '#fca5a5' },
  sourceList: { borderTopColor: '#273449', borderTopWidth: 1, gap: 4, marginTop: 3, paddingTop: 7 },
  sourceLabel: { color: '#94a3b8', fontSize: 9, fontWeight: '900', letterSpacing: 0.7, textTransform: 'uppercase' },
  sourceLink: { color: '#93c5fd', fontSize: 11, lineHeight: 16, textDecorationLine: 'underline' },
  composer: { borderTopColor: '#273449', borderTopWidth: 1, gap: 7, paddingHorizontal: 12, paddingBottom: 10, paddingTop: 8 },
  deliveryRow: { alignItems: 'center', flexDirection: 'row', gap: 5, minHeight: 28 },
  deliveryLabel: { color: '#cbd5e1', fontSize: 11, fontWeight: '700' },
  statusText: { color: '#64748b', flex: 1, fontSize: 10, textAlign: 'right' },
  inputRow: { alignItems: 'flex-end', flexDirection: 'row', gap: 8 },
  messageInput: { backgroundColor: '#111827', borderColor: '#334155', borderRadius: 14, borderWidth: 1, color: '#f8fafc', flex: 1, fontSize: 15, maxHeight: 112, minHeight: 42, paddingHorizontal: 12, paddingVertical: 9, textAlignVertical: 'top' },
  sendButton: { alignItems: 'center', backgroundColor: '#7cf0c2', borderRadius: 21, height: 42, justifyContent: 'center', width: 42 },
  sendText: { color: '#07110e', fontSize: 23, fontWeight: '900', lineHeight: 25 },
  disabled: { opacity: 0.42 },
  actionText: { color: '#94a3b8', fontSize: 10 },
  drawerLayer: { ...StyleSheet.absoluteFillObject, flexDirection: 'row', zIndex: 20 },
  drawerBackdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(2, 6, 23, 0.72)' },
  drawer: { backgroundColor: '#0f1624', borderRightColor: '#334155', borderRightWidth: 1, elevation: 12, maxWidth: 360, width: '88%' },
  drawerHeader: { alignItems: 'center', borderBottomColor: '#273449', borderBottomWidth: 1, flexDirection: 'row', justifyContent: 'space-between', padding: 15 },
  drawerTitle: { color: '#f8fafc', fontSize: 23, fontWeight: '800' },
  drawerActions: { flexDirection: 'row', gap: 7 },
  newButton: { alignItems: 'center', backgroundColor: '#16342d', borderColor: '#4f9d83', borderRadius: 17, borderWidth: 1, height: 34, justifyContent: 'center', width: 34 },
  newButtonText: { color: '#7cf0c2', fontSize: 23, lineHeight: 25 },
  drawerClose: { alignItems: 'center', height: 34, justifyContent: 'center', width: 34 },
  drawerCloseText: { color: '#cbd5e1', fontSize: 27, fontWeight: '300' },
  conversationScroll: { flex: 1 },
  conversationList: { gap: 6, padding: 12 },
  drawerEmpty: { color: '#64748b', padding: 20, textAlign: 'center' },
  conversationItem: { alignItems: 'center', backgroundColor: '#111827', borderColor: '#273449', borderRadius: 11, borderWidth: 1, flexDirection: 'row', gap: 8, padding: 10 },
  conversationRowWrap: { gap: 5 },
  conversationItemActive: { backgroundColor: '#17272d', borderColor: '#4f9d83' },
  conversationCopy: { flex: 1 },
  conversationTitle: { color: '#f8fafc', fontSize: 14, fontWeight: '800' },
  conversationTime: { color: '#64748b', fontSize: 10, marginTop: 2 },
  liveBadge: { color: '#7cf0c2', fontSize: 9, fontWeight: '900', letterSpacing: 0.8 },
  archivedBadge: { color: '#64748b', fontSize: 8, fontWeight: '900', letterSpacing: 0.5 },
  conversationActionText: { color: '#93c5fd', fontSize: 9, fontWeight: '800' },
  archiveActionText: { color: '#f0b27a', fontSize: 9, fontWeight: '800' },
  renameEditor: { gap: 5 },
  renameInput: { backgroundColor: '#0b1020', borderColor: '#4f9d83', borderRadius: 7, borderWidth: 1, color: '#f8fafc', flex: 1, fontSize: 12, paddingHorizontal: 8, paddingVertical: 6 },
  moreButton: { alignItems: 'center', borderColor: '#334155', borderRadius: 8, borderWidth: 1, height: 30, justifyContent: 'center', width: 30 },
  moreButtonText: { color: '#94a3b8', fontSize: 18, lineHeight: 18 },
  conversationMenu: { alignSelf: 'flex-end', backgroundColor: '#0b1020', borderColor: '#334155', borderRadius: 8, borderWidth: 1, flexDirection: 'row', gap: 4, padding: 4 },
  conversationMenuAction: { paddingHorizontal: 9, paddingVertical: 6 },
  archiveToggle: { alignItems: 'center', borderColor: '#273449', borderRadius: 9, borderWidth: 1, flexDirection: 'row', justifyContent: 'center', marginHorizontal: 12, marginBottom: 8, padding: 8 },
  archiveToggleActive: { borderColor: '#93c5fd' },
  archiveToggleText: { color: '#94a3b8', fontSize: 11, fontWeight: '800' },
  archiveCount: { backgroundColor: '#1e293b', borderRadius: 10, color: '#cbd5e1', fontSize: 9, marginLeft: 6, minWidth: 20, paddingHorizontal: 5, paddingVertical: 3, textAlign: 'center' },
  configPanel: { borderTopColor: '#334155', borderTopWidth: 1, flexShrink: 0 },
  configToggle: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', minHeight: 54, paddingHorizontal: 14, paddingVertical: 9 },
  configToggleCopy: { flex: 1, gap: 2 },
  configToggleTitle: { color: '#f8fafc', fontSize: 14, fontWeight: '800' },
  configToggleMeta: { color: '#94a3b8', fontSize: 10, maxWidth: '92%' },
  configChevron: { color: '#facc15', fontSize: 11, fontWeight: '900' },
  configBodyScroll: { maxHeight: 310 },
  configBody: { borderTopColor: '#273449', borderTopWidth: 1, gap: 8, padding: 13 },
  preferenceRow: { alignItems: 'center', borderColor: '#273449', borderRadius: 8, borderWidth: 1, flexDirection: 'row', gap: 10, padding: 10 },
  preferenceCopy: { flex: 1, gap: 2 },
  preferenceTitle: { color: '#e2e8f0', fontSize: 12, fontWeight: '800' },
  label: { color: '#cbd5e1', fontSize: 10, fontWeight: '800', letterSpacing: 0.7, textTransform: 'uppercase' },
  configInput: { backgroundColor: '#111827', borderColor: '#334155', borderRadius: 8, borderWidth: 1, color: '#f8fafc', fontSize: 14, paddingHorizontal: 11, paddingVertical: 9 },
  testButton: { alignItems: 'center', backgroundColor: '#1e293b', borderColor: '#475569', borderRadius: 8, borderWidth: 1, justifyContent: 'center', minHeight: 40, paddingHorizontal: 12 },
  testButtonText: { color: '#e2e8f0', fontSize: 13, fontWeight: '800' },
  helperText: { color: '#94a3b8', fontSize: 11 },
  connectedText: { color: '#6ee7b7', fontSize: 11, fontWeight: '700' },
});
