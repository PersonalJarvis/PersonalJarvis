import { create } from "zustand";
import type { MessageRole } from "@/types/messages";
import { readCachedAssistantName } from "@/lib/assistantNameCache";
import {
  finalizeThinkingSteps,
  reduceThinkingSteps,
  type ThinkingStep,
  type ThinkingTraceSnapshot,
} from "@/lib/thinkingSteps";

/**
 * Mirror of `jarvis/state/supervisor.py::SupervisorState`, lowercased.
 *
 * Every member must exist here, in `isVoiceState` (useWebSocket.ts), in both
 * style maps (Sidebar.tsx, VoiceIndicator.tsx) and as a `voice_state.*` key in
 * en/de/es — a value the backend can publish and this union does not know is
 * dropped silently, freezing the only live indicator the desktop has on its
 * previous state. Guarded by `voice-state-parity.test.ts`.
 */
export type VoiceState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "paused"
  | "error";

/** The full lowercased supervisor vocabulary, in the order it is declared. */
export const VOICE_STATES: readonly VoiceState[] = [
  "idle",
  "listening",
  "thinking",
  "speaking",
  "paused",
  "error",
];

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
  | "feedback"
  | "agent-instructions"
  | "dictionary"
  | "dictation"
  // The three tabs added by the merged voice section. "dictation" (default
  // landing) and "dictionary" keep their original ids on purpose — renaming
  // them would break the Command-Registry ui_section binding and every existing
  // voice deep-link for no gain.
  | "voice-shortcuts"
  | "voice-language"
  | "voice-api-keys"
  | "agentic-ide"
  // The rebuilt chat surface — projects, their chats, one conversation on
  // screen. Lives alongside "agentic-ide" until its parity checklist is empty.
  | "chat-workspace";

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
  "feedback",
  "agent-instructions",
  "dictionary",
  "dictation",
  // `satisfies` only catches array entries that are missing from the union,
  // never a union member missing from the array — these three have to be added
  // by hand as well (docs/BUGS.md, the recurring enum-drift class).
  "voice-shortcuts",
  "voice-language",
  "voice-api-keys",
  "agentic-ide",
  "chat-workspace",
] as const satisfies readonly SectionId[];

export function isSectionId(value: unknown): value is SectionId {
  return typeof value === "string" && SECTION_IDS.includes(value as SectionId);
}

export function initialSectionFromSearch(search: string): SectionId {
  return new URLSearchParams(search).has("doc") ? "docs" : "chats";
}

export const SECTION_LABELS: Record<SectionId, string> = {
  chats: "Chats",
  agents: "Agents",
  skills: "Skills",
  plugins: "Plugins",
  docs: "Docs",
  mcps: "MCPs",
  tasks: "Tasks",
  sessions: "Transcription",
  run_inspector: "Run Inspector",
  clis: "CLIs",
  "cli-test-hub": "CLI Test Hub",
  board: "Board",
  languages: "Languages",
  profile: "Profile",
  memory: "Notes",
  apikeys: "API Keys",
  settings: "Settings",
  telephony: "Telephony",
  "telephony-setup": "Telephony setup",
  outputs: "Outputs",
  socials: "Socials",
  taskbar: "Taskbar",
  contacts: "Contacts",
  feedback: "Feedback",
  "agent-instructions": "Agent Instructions",
  dictionary: "Dictionary",
  dictation: "Dictation",
  // Plain English, deliberately NOT the "{name} Voice" brand: these labels are
  // read back by the voice-navigation toast, which does not interpolate the
  // assistant name. Naming the sub-section is also the more useful readback.
  "voice-shortcuts": "Voice Shortcuts",
  "voice-language": "Dictation Language",
  "voice-api-keys": "Voice Input Keys",
  "agentic-ide": "Agentic IDE",
  "chat-workspace": "Chat",
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
  /**
   * How often this identical notice was raised while it was already on screen.
   * Repeats COLLAPSE into one toast carrying this counter instead of stacking:
   * a provider card that answers every click with the same "connect this first"
   * line used to build a six-high wall of identical boxes covering the very
   * button that resolves it.
   */
  count: number;
  /**
   * Wall-clock ms at which this toast auto-dismisses. A repeat pushes it out,
   * so the expiry timer must re-check it rather than firing blindly.
   */
  expiresAt: number;
  /**
   * Absolute path of a file that was just saved to the user's Downloads. When
   * set (desktop only), the toast renders "Show in folder" / "Open" actions and
   * stays up longer so the user has time to click them. Empty in the browser/VPS.
   */
  filePath?: string;
  /** Display name of the saved file (shown next to the actions). */
  filename?: string;
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
  label: string;          // e.g. "Install GitHub CLI" — rendered as a banner in the terminal
}

/**
 * Companion overlay to the terminal session during CLI-connect flows.
 *
 * Independent of pendingTerminalCommand, because the coach stays active for
 * the entire login duration (the command is only injected once, but the coach
 * polls until auth.status == "connected").
 */
export interface CliConnectCoach {
  cliName: string;                    // API name, for polling: /api/clis/{cliName}/check
  displayName: string;                // e.g. "GitHub CLI"
  authMode: "oauth_cli" | "api_key" | "config_file" | "none";
  loginCommand: string;               // shell-ready, e.g. "gh auth login"
  statusCommand: string | null;       // e.g. "gh auth status" — optional, for a manual recheck
}

/**
 * Whether Jarvis is currently an Agentic IDE, for any surface that must say so.
 *
 * `active` mirrors the backend predicate `agentic_ide.session.coding_mode_active`
 * exactly — a workspace is open AND its focused coding mode is on. It is the
 * whole reason this is one flag and not two booleans a component combines
 * itself: the assistant behaves differently only when BOTH hold, so a surface
 * that re-derives the rule can end up telling the user something the assistant
 * does not agree with.
 *
 * `hasWorkspace` is separate because it drives VISIBILITY, not state: with no
 * workspace open at all there is nothing to report, and a permanent "coding
 * mode off" chip would be noise for everyone who never opens the IDE.
 */
export interface CodingModeState {
  active: boolean;
  hasWorkspace: boolean;
  /** Label of the workspace the mode belongs to; "" when the mode is off. */
  workspace: string;
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
  // True while the WS keeps getting closed with code 1013 by the fast-boot
  // bootstrap (backend still warming up). Distinct from `connected`: drives the
  // honest "Starting…" indicator instead of "OFFLINE". Defaults true so the
  // first paint after a restart never flashes "OFFLINE".
  wsWarming: boolean;
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
  // Optimistic thinking indicator for the text chat: ChatInput.send()
  // sets it true; an incoming assistant reply (or ErrorOccurred from the brain layer,
  // or a 60s timeout) resets it. Deliberately separate from the global voiceState,
  // because that is also set by voice-pipeline turns (not a text-chat wait).
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
  // The model id the active provider is configured to use (e.g.
  // "claude-opus-4-8"). Seeded from /api/brain/status on mount and refreshed on
  // a provider switch. Empty until the first status fetch resolves.
  brainModel: string;
  // Whether Jarvis is currently an Agentic IDE — see CodingModeState. Lives in
  // the store rather than in the one component that renders it because the mode
  // changes how the assistant answers on EVERY screen, so any surface may need
  // to reflect it. Kept in sync by useCodingMode(), called once in App.tsx.
  codingMode: CodingModeState;
  // How the assistant refers to itself (resolved name: derived from the wake
  // phrase with its prefix stripped, else the neutral "Assistant" default —
  // [persona].name was removed 2026-06-20). Seeded once at app start by
  // useAssistantNameSeed and refreshed on a wake-word save, so the
  // header wordmark + every assistant byline follow the configured identity.
  // The initial value is read SYNCHRONOUSLY from the localStorage cache (the
  // last resolved name) so the user's own name paints instantly at boot with no
  // added latency; it falls back to the neutral "Assistant" — NEVER a
  // trademarked placeholder like "Jarvis" — only on the very first run before
  // any name has been cached. See src/lib/assistantNameCache.ts.
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
  // When the user installs a CLI from within ClisView (clicking
  // "Install in terminal"), we set the name here — TerminalView
  // detects after exit_code=0 that it should verify an install
  // (POST /check + toast). One-shot: reset to null after verify.
  pendingInstallCliName: string | null;
  pushEvent: (e: EventItem) => void;
  setVoice: (v: VoiceState) => void;
  setVoiceReady: (ready: boolean) => void;
  setConnected: (c: boolean) => void;
  setWarming: (warming: boolean) => void;
  clearEvents: () => void;
  setActiveSection: (s: SectionId) => void;
  setTranscription: (text: string, isFinal: boolean) => void;
  pushToast: (
    kind: Toast["kind"],
    message: string,
    opts?: { filePath?: string; filename?: string },
  ) => void;
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
  setBrainModel: (m: string) => void;
  setCodingMode: (m: CodingModeState) => void;
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
// A file toast carries "Show in folder" / "Open" actions the user must have time
// to aim at and click — keep it up noticeably longer than a plain notification.
const TOAST_FILE_TTL_MS = 12000;
// Finished reasoning traces are per-message UI sugar, not history — cap the
// map so a long session cannot grow it unbounded (insertion order = age).
const MAX_TRACES = 24;

/**
 * Arm the auto-dismiss timer for one toast.
 *
 * A repeat refreshes ``expiresAt`` and arms a NEW timer, so an older timer must
 * not dismiss a toast that has since been pushed out — it re-checks the expiry
 * and lets the newest timer own the removal. Without that check, the second of
 * two rapid repeats would vanish after the FIRST one's remaining time.
 */
function scheduleToastExpiry(
  get: () => EventStore,
  id: string,
  ttl: number,
): void {
  setTimeout(() => {
    const toast = get().toasts.find((candidate) => candidate.id === id);
    if (!toast) return;
    if (toast.expiresAt > Date.now()) return;
    get().dismissToast(id);
  }, ttl);
}

export const useEventStore = create<EventStore>((set, get) => ({
  events: [],
  voiceState: "idle",
  voiceReady: false,
  connected: false,
  wsWarming: true,
  activeSection: initialSectionFromSearch(
    typeof window === "undefined" ? "" : window.location.search,
  ),
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
  codingMode: { active: false, hasWorkspace: false, workspace: "" },
  brainModel: "",
  assistantName: readCachedAssistantName(),
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
  setWarming: (warming) => set({ wsWarming: warming }),
  clearEvents: () => set({ events: [] }),
  setActiveSection: (s) => set({ activeSection: s }),

  setTranscription: (text, isFinal) =>
    set({ transcription: text, transcriptionFinal: isFinal }),

  pushToast: (kind, message, opts) => {
    const filePath = opts?.filePath;
    const ttl = filePath ? TOAST_FILE_TTL_MS : TOAST_TTL_MS;
    const now = Date.now();
    const expiresAt = now + ttl;

    // Collapse a repeat of a notice that is still on screen. Every surface that
    // answers a click with a fixed sentence ("connect this provider first") can
    // otherwise emit it once per click — and a double click on a card emits it
    // twice more — until the stack buries the control the user needs to press.
    // File toasts are matched on their path too, so two different saved files
    // stay two separate toasts with their own actions.
    const existing = get().toasts.find(
      (toast) =>
        toast.kind === kind &&
        toast.message === message &&
        toast.filePath === filePath,
    );
    if (existing) {
      set((state) => ({
        toasts: state.toasts.map((toast) =>
          toast.id === existing.id
            ? { ...toast, count: toast.count + 1, ts: now, expiresAt }
            : toast,
        ),
      }));
      scheduleToastExpiry(get, existing.id, ttl);
      return;
    }

    const id = `toast-${now}-${Math.random().toString(36).slice(2, 7)}`;
    set((state) => ({
      toasts: [
        ...state.toasts,
        {
          id,
          kind,
          message,
          ts: now,
          count: 1,
          expiresAt,
          filePath,
          filename: opts?.filename,
        },
      ],
    }));
    scheduleToastExpiry(get, id, ttl);
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
  setBrainModel: (m) => set({ brainModel: m }),
  setCodingMode: (m) => set({ codingMode: m }),

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
