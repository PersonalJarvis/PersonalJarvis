import { create } from "zustand";
import type { MessageRole } from "@/types/messages";
import {
  finalizeThinkingSteps,
  reduceThinkingSteps,
  type ThinkingStep,
  type ThinkingTraceSnapshot,
} from "@/lib/thinkingSteps";

export type VoiceState = "idle" | "listening" | "thinking" | "speaking" | "error";

export type SectionId =
  | "chats"
  | "agents"
  | "skills"
  | "plugins"
  | "docs"
  | "mcps"
  | "tasks"
  | "sessions"
  | "run_inspector"
  | "clis"
  | "cli-test-hub"
  | "board"
  | "languages"
  | "profile"
  | "memory"
  | "apikeys"
  | "settings"
  | "telephony"
  | "telephony-setup"
  | "outputs"
  | "socials"
  | "taskbar"
  | "contacts"
  | "browser-voice";

export const SECTION_IDS = [
  "chats",
  "agents",
  "skills",
  "plugins",
  "docs",
  "mcps",
  "tasks",
  "sessions",
  "run_inspector",
  "clis",
  "cli-test-hub",
  "board",
  "languages",
  "profile",
  "memory",
  "apikeys",
  "settings",
  "telephony",
  "telephony-setup",
  "outputs",
  "socials",
  "taskbar",
  "contacts",
  "browser-voice",
] as const satisfies readonly SectionId[];

export function isSectionId(value: unknown): value is SectionId {
  return typeof value === "string" && SECTION_IDS.includes(value as SectionId);
}

export const SECTION_LABELS: Record<SectionId, string> = {
  chats: "Chats",
  agents: "Agents",
  skills: "Skills",
  plugins: "Plugins",
  docs: "Docs",
  mcps: "MCPs",
  tasks: "Aufgaben",
  sessions: "Transkription",
  run_inspector: "Run Inspector",
  clis: "CLIs",
  "cli-test-hub": "CLI Test Hub",
  board: "Board",
  languages: "Sprachen",
  profile: "Profil",
  memory: "Notizen",
  apikeys: "API-Keys",
  settings: "Einstellungen",
  telephony: "Telefonie",
  "telephony-setup": "Telefonie-Setup",
  outputs: "Outputs",
  socials: "Socials",
  taskbar: "Taskbar",
  contacts: "Contacts",
  "browser-voice": "Browser Voice",
};

export interface EventItem {
  id: string;
  name: string;
  layer?: string;
  ts: number;
  trace_id?: string;
  payload?: unknown;
}

export interface Toast {
  id: string;
  kind: "info" | "success" | "warning" | "error";
  message: string;
  ts: number;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  ts: number;
  thread_id?: string;
}

// Mirror of jarvis/state/conversation_constants.py (5-layer anti-drift: this
// TS layer must stay in lockstep with the Python frozenset + the REST Literal).
export const CONVERSATION_KINDS = ["text", "voice"] as const;
export type ConversationKind = (typeof CONVERSATION_KINDS)[number];

/** One row in the unified Chats history (a text thread OR a voice session). */
export interface ConversationSummary {
  kind: ConversationKind;
  id: string;
  title: string;
  preview: string;
  created_ms: number;
  updated_ms: number;
  message_count: number;
}

export interface PendingTerminalCommand {
  command: string;
  shell: string;
  label: string;          // z.B. "Install GitHub CLI" — wird im Terminal als Banner gerendert
}

/**
 * Begleit-Overlay zur Terminal-Session bei CLI-Connect-Flows.
 *
 * Unabhaengig vom pendingTerminalCommand, weil der Coach waehrend der ganzen
 * Login-Dauer aktiv bleibt (Command wird ja nur einmal injiziert, Coach aber
 * pollt bis auth.status == "connected").
 */
export interface CliConnectCoach {
  cliName: string;                    // API-Name, fuer Polling: /api/clis/{cliName}/check
  displayName: string;                // z.B. "GitHub CLI"
  authMode: "oauth_cli" | "api_key" | "config_file" | "none";
  loginCommand: string;               // shell-ready, z.B. "gh auth login"
  statusCommand: string | null;       // z.B. "gh auth status" — optional fuer manuellen Recheck
}

interface EventStore {
  events: EventItem[];
  voiceState: VoiceState;
  // The desktop window connects in ~1s, but the voice feature warms up ~20s in
  // the background (wake/STT/VAD model load). False until the backend announces
  // readiness (VoiceBootStatus WS event / GET /api/voice/status seed) — drives
  // the sidebar "Voice starting…" indicator.
  voiceReady: boolean;
  connected: boolean;
  activeSection: SectionId;
  transcription: string;
  transcriptionFinal: boolean;
  toasts: Toast[];
  messages: ChatMessage[];
  // Chats conversation manager: unified history list + which conversation is
  // currently open in the right pane. activeThreadId is null for an unsaved
  // "New chat" (the first sent message lazily creates the text thread).
  conversations: ConversationSummary[];
  activeThreadId: string | null;
  activeKind: ConversationKind;
  // Optimistischer Thinking-Indikator fuer den Text-Chat: ChatInput.send()
  // setzt true, eintreffender Assistant-Reply (oder ErrorOccurred aus brain-Layer
  // bzw. 60s-Timeout) setzt zurueck. Bewusst getrennt vom globalen voiceState,
  // weil der auch durch Voice-Pipeline-Turns gesetzt wird (kein Text-Chat-Wait).
  chatThinking: boolean;
  // Live reasoning trace rendered inside the ThinkingTrace card. Steps are
  // ingested from WS events ONLY while chatThinking is true (voice turns in
  // the background must not paint ghost steps into the text chat).
  thinkingSteps: ThinkingStep[];
  // Wall-clock ms when the current thinking phase began (drives the live
  // elapsed timer in the card header). Null when idle.
  thinkingStartedTs: number | null;
  // Finished traces keyed by the assistant message id that ended the turn —
  // renders as the collapsible "Thought for Xs" disclosure above the reply.
  thinkingTraces: Record<string, ThinkingTraceSnapshot>;
  brainProvider: string;
  // How the assistant refers to itself (resolved name: explicit [persona].name,
  // else derived from the wake phrase, else the neutral default). Seeded once at
  // app start by useAssistantNameSeed and refreshed on a Settings rename, so the
  // header wordmark + every assistant byline follow the configured identity
  // instead of a hardcoded "Jarvis". Defaults to "Jarvis" only for the sub-tick
  // before the local seed fetch resolves (zero-regression first paint).
  assistantName: string;
  // Chat mic-dictation (transcribe-only). ``dictating`` is true while the mic
  // session runs; ``dictationText`` is the live interim tail (overwritten by
  // each partial). A final transcript bumps ``dictationCommitSeq`` and carries
  // its text in ``dictationCommitText`` — ChatInput watches the seq to append
  // the finalized text to its textarea exactly once.
  dictating: boolean;
  dictationText: string;
  dictationCommitSeq: number;
  dictationCommitText: string;
  pendingTerminalCommand: PendingTerminalCommand | null;
  cliConnectCoach: CliConnectCoach | null;
  // Wenn der User aus ClisView heraus eine CLI installieren laesst (Klick
  // "Im Terminal installieren"), setzen wir hier den Namen — das TerminalView
  // erkennt nach exit_code=0, dass es einen Install verifizieren soll
  // (POST /check + Toast). One-shot: nach Verify wieder auf null.
  pendingInstallCliName: string | null;
  pushEvent: (e: EventItem) => void;
  setVoice: (v: VoiceState) => void;
  setVoiceReady: (ready: boolean) => void;
  setConnected: (c: boolean) => void;
  clearEvents: () => void;
  setActiveSection: (s: SectionId) => void;
  setTranscription: (text: string, isFinal: boolean) => void;
  pushToast: (kind: Toast["kind"], message: string) => void;
  dismissToast: (id: string) => void;
  pushMessage: (m: ChatMessage) => void;
  setMessages: (m: ChatMessage[]) => void;
  setConversations: (c: ConversationSummary[]) => void;
  setActiveConversation: (kind: ConversationKind, id: string | null) => void;
  /** Returns a text thread id to post into, creating one if the active
   *  conversation is unsaved or a (read-only) voice session. */
  ensureActiveThread: () => Promise<string>;
  setChatThinking: (thinking: boolean) => void;
  /** Feed one WS event into the live reasoning trace (no-op while idle). */
  ingestThinkingEvent: (name: string, payload: unknown, tsMs: number) => void;
  /** Turn ended with an assistant reply: snapshot the trace onto that message. */
  finishThinking: (messageId: string) => void;
  setBrainProvider: (p: string) => void;
  setAssistantName: (name: string) => void;
  setDictating: (b: boolean) => void;
  setDictationInterim: (text: string) => void;
  commitDictation: (text: string) => void;
  setPendingTerminalCommand: (cmd: PendingTerminalCommand | null) => void;
  setCliConnectCoach: (coach: CliConnectCoach | null) => void;
  setPendingInstallCliName: (name: string | null) => void;
}

const MAX_EVENTS = 500;
const MAX_MESSAGES = 200;
const TOAST_TTL_MS = 3500;
// Finished reasoning traces are per-message UI sugar, not history — cap the
// map so a long session cannot grow it unbounded (insertion order = age).
const MAX_TRACES = 24;

export const useEventStore = create<EventStore>((set, get) => ({
  events: [],
  voiceState: "idle",
  voiceReady: false,
  connected: false,
  activeSection: "chats",
  transcription: "",
  transcriptionFinal: true,
  toasts: [],
  messages: [],
  conversations: [],
  activeThreadId: null,
  activeKind: "text",
  chatThinking: false,
  thinkingSteps: [],
  thinkingStartedTs: null,
  thinkingTraces: {},
  brainProvider: "unknown",
  assistantName: "Jarvis",
  dictating: false,
  dictationText: "",
  dictationCommitSeq: 0,
  dictationCommitText: "",
  pendingTerminalCommand: null,
  cliConnectCoach: null,
  pendingInstallCliName: null,

  pushEvent: (e) =>
    set((state) => {
      const next = [e, ...state.events];
      if (next.length > MAX_EVENTS) next.length = MAX_EVENTS;
      return { events: next };
    }),

  setVoice: (v) => set({ voiceState: v }),
  setVoiceReady: (ready) => set({ voiceReady: ready }),
  setConnected: (c) => set({ connected: c }),
  clearEvents: () => set({ events: [] }),
  setActiveSection: (s) => set({ activeSection: s }),

  setTranscription: (text, isFinal) =>
    set({ transcription: text, transcriptionFinal: isFinal }),

  pushToast: (kind, message) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    set((state) => ({
      toasts: [...state.toasts, { id, kind, message, ts: Date.now() }],
    }));
    setTimeout(() => get().dismissToast(id), TOAST_TTL_MS);
  },

  dismissToast: (id) =>
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

  pushMessage: (m) =>
    set((state) => {
      // WebSocket delivery is at-least-once: reconnect replays, connection-churn
      // double-forwards (a page reload via main.tsx's vite:preloadError /
      // ViewErrorBoundary onRecover briefly overlaps two /ws sockets), and
      // multi-window all re-deliver the SAME logical MessageSent. Its id
      // (`${timestamp_ns}-${trace_id}`) is stable, so a repeat is never a new
      // message — drop it to keep the chat render idempotent. Without this guard
      // a single answer surfaces as two identical bubbles ("Jarvis repeated his
      // answer twice"). Regression: store/events.test.ts.
      if (state.messages.some((x) => x.id === m.id)) return state;
      const next = [...state.messages, m];
      if (next.length > MAX_MESSAGES) next.splice(0, next.length - MAX_MESSAGES);
      return { messages: next };
    }),

  setMessages: (m) =>
    set({ messages: m.length > MAX_MESSAGES ? m.slice(m.length - MAX_MESSAGES) : m }),

  setConversations: (c) => set({ conversations: c }),

  setActiveConversation: (kind, id) => set({ activeKind: kind, activeThreadId: id }),

  ensureActiveThread: async () => {
    const { activeThreadId, activeKind } = get();
    if (activeThreadId && activeKind === "text") return activeThreadId;
    // Unsaved "New chat" OR continuing a (read-only) voice session by text →
    // create a fresh text thread to post into. The voice session's context has
    // already been seeded into the brain via the /resume call.
    const res = await fetch("/api/chats", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: "New Chat" }),
    });
    // Guard against a 503 (chat-store-unavailable on a headless host) whose
    // {detail} body would otherwise leave activeThreadId = undefined.
    if (!res.ok) throw new Error(`create-thread-failed:${res.status}`);
    const data = (await res.json()) as { id: string };
    set({ activeThreadId: data.id, activeKind: "text" });
    return data.id;
  },

  setChatThinking: (thinking) =>
    set(
      thinking
        ? // New turn: arm the live trace. A re-send while already thinking
          // restarts the trace — the old steps belonged to the superseded turn.
          { chatThinking: true, thinkingSteps: [], thinkingStartedTs: Date.now() }
        : // Timeout / brain error: discard the live trace without a snapshot.
          { chatThinking: false, thinkingSteps: [], thinkingStartedTs: null },
    ),

  ingestThinkingEvent: (name, payload, tsMs) => {
    const { chatThinking, thinkingSteps } = get();
    if (!chatThinking) return;
    const next = reduceThinkingSteps(thinkingSteps, name, payload, tsMs);
    if (next) set({ thinkingSteps: next });
  },

  finishThinking: (messageId) => {
    const { chatThinking, thinkingSteps, thinkingStartedTs, thinkingTraces } = get();
    if (!chatThinking) return;
    const now = Date.now();
    const idle = {
      chatThinking: false,
      thinkingSteps: [] as ThinkingStep[],
      thinkingStartedTs: null,
    };
    // Fast turns with zero observed steps get no disclosure — a "Thought for
    // 0.4s · 0 steps" row on every smalltalk reply would be pure noise.
    if (thinkingSteps.length === 0) {
      set(idle);
      return;
    }
    const snapshot: ThinkingTraceSnapshot = {
      steps: finalizeThinkingSteps(thinkingSteps, now),
      durationMs: Math.max(0, now - (thinkingStartedTs ?? now)),
    };
    const traces = { ...thinkingTraces, [messageId]: snapshot };
    const keys = Object.keys(traces);
    if (keys.length > MAX_TRACES) {
      for (const k of keys.slice(0, keys.length - MAX_TRACES)) delete traces[k];
    }
    set({ ...idle, thinkingTraces: traces });
  },

  setBrainProvider: (p) => set({ brainProvider: p }),

  setAssistantName: (name) => set({ assistantName: name }),

  setDictating: (b) =>
    set(b ? { dictating: true, dictationText: "" } : { dictating: false }),
  setDictationInterim: (text) => set({ dictationText: text }),
  commitDictation: (text) =>
    set((s) => ({
      dictationCommitText: text,
      dictationCommitSeq: s.dictationCommitSeq + 1,
      dictationText: "",
      dictating: false,
    })),

  setPendingTerminalCommand: (cmd) => set({ pendingTerminalCommand: cmd }),

  setCliConnectCoach: (coach) => set({ cliConnectCoach: coach }),

  setPendingInstallCliName: (name) => set({ pendingInstallCliName: name }),
}));
