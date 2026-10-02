/**
 * "First steps": ten small, real things a new user does with the assistant
 * right after setup, as data.
 *
 * The explainer in setup says what the assistant is; this guide lets the
 * user feel it. Each quest gives one concrete thing to say or open, waits
 * until the app reports that it really happened (a live event, a reply, a
 * visited section), and then explains what just went on behind the scenes.
 *
 * The guide never starts work on its own. "Try it" either navigates, or —
 * for a quest that IS a message — sends the shown example once, because the
 * user pressed the button for exactly that.
 */
import type { ChatMessage, EventItem, SectionId, VoiceState } from "@/store/events";

export type QuestId =
  | "wake"
  | "ask"
  | "tool"
  | "screen"
  | "memory"
  | "artifact"
  | "agent"
  | "team"
  | "plugin"
  | "ide";

/** What "Try it" does: send the example, open voice mode, or open a section. */
export type QuestAction = { kind: "send" } | { kind: "voice" } | { kind: "open"; section: SectionId };

export interface EventRule {
  names: readonly string[];
  includes?: readonly string[];
}

export interface QuestDetect {
  /**
   * Backend events (WS `event_name`) that prove the quest happened. A rule
   * with `includes` also needs one of those words (case-insensitive) in the
   * event's payload — "a tool ran" is not yet "the screen tool ran".
   */
  events?: readonly EventRule[];
  /** An assistant reply after the quest started counts. */
  assistantReply?: boolean;
  /** The voice loop leaving idle counts (the wake word or the voice button). */
  voiceActive?: boolean;
  /** Being on this section counts. */
  section?: SectionId;
}

export interface Quest {
  id: QuestId;
  action: QuestAction;
  detect: QuestDetect;
  /** Where the result can be seen, offered after the quest is done. */
  showSection?: SectionId;
}

export const QUESTS: readonly Quest[] = [
  { id: "wake", action: { kind: "voice" }, detect: { voiceActive: true, events: [{ names: ["WakeWordDetected", "VoiceTurnStarted"] }] } },
  { id: "ask", action: { kind: "send" }, detect: { assistantReply: true, events: [{ names: ["BrainTurnCompleted", "VoiceTurnCompleted"] }] } },
  { id: "tool", action: { kind: "send" }, detect: { events: [{ names: ["ToolCallStarted", "ActionExecuted", "CliInvoked"] }] } },
  {
    id: "screen",
    action: { kind: "send" },
    detect: {
      events: [
        { names: ["ScreenCaptureCompleted", "ObservationCaptured", "AppshotTaken"] },
        { names: ["ToolCallStarted"], includes: ["screen", "appshot", "snapshot"] },
      ],
    },
  },
  {
    id: "memory",
    action: { kind: "send" },
    detect: {
      events: [
        { names: ["WikiPageChanged"] },
        { names: ["ToolCallStarted"], includes: ["wiki", "profile", "memory", "remember"] },
      ],
    },
    showSection: "memory",
  },
  {
    id: "artifact",
    action: { kind: "send" },
    detect: { events: [{ names: ["ToolCallStarted", "JarvisAgentTaskStarted"], includes: ["artifact"] }] },
    showSection: "visualization",
  },
  {
    id: "agent",
    action: { kind: "send" },
    detect: {
      events: [
        { names: ["JarvisAgentTaskStarted"] },
        { names: ["ToolCallStarted"], includes: ["spawn-worker", "delegate"] },
      ],
    },
    showSection: "agents",
  },
  { id: "team", action: { kind: "open", section: "agents" }, detect: { section: "agents" } },
  { id: "plugin", action: { kind: "open", section: "plugins" }, detect: { section: "plugins" } },
  { id: "ide", action: { kind: "open", section: "agentic-ide" }, detect: { section: "agentic-ide" } },
];

export interface DetectInput {
  /** Newest first, as the event store keeps them. */
  events: readonly EventItem[];
  messages: readonly ChatMessage[];
  voiceState: VoiceState;
  activeSection: SectionId;
  /** Wall-clock ms the quest was armed; nothing older counts. */
  since: number;
}

function payloadText(payload: unknown): string {
  if (payload === undefined || payload === null) return "";
  try {
    return JSON.stringify(payload).toLowerCase();
  } catch {
    // A payload that cannot be serialised simply never matches a keyword.
    return "";
  }
}

/** True when the app shows the quest really happened after `since`. */
export function questDone(quest: Quest, input: DetectInput): boolean {
  const d = quest.detect;
  if (d.section && input.activeSection === d.section) return true;
  if (d.voiceActive && input.voiceState !== "idle" && input.voiceState !== "connecting") return true;
  if (d.assistantReply && input.messages.some((m) => m.role === "assistant" && m.ts >= input.since)) return true;
  if (d.events) {
    for (const e of input.events) {
      if (e.ts < input.since) break; // newest first: everything after is older
      for (const rule of d.events) {
        if (!rule.names.includes(e.name)) continue;
        if (!rule.includes) return true;
        const text = payloadText(e.payload);
        if (rule.includes.some((w) => text.includes(w.toLowerCase()))) return true;
      }
    }
  }
  return false;
}

/* ---------------------------------------------------------------- storage */

export type FirstStepsStatus = "active" | "dismissed" | "finished";

export interface FirstStepsState {
  status: FirstStepsStatus;
  /** Quests the app saw happen. */
  done: QuestId[];
  /** Quests the user skipped. */
  skipped: QuestId[];
  current: QuestId;
  collapsed: boolean;
}

const STORAGE_KEY = "jarvis.firstSteps.v1";

export function freshState(): FirstStepsState {
  return { status: "active", done: [], skipped: [], current: QUESTS[0].id, collapsed: false };
}

function isQuestId(value: unknown): value is QuestId {
  return QUESTS.some((q) => q.id === value);
}

export function readFirstSteps(): FirstStepsState | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as Partial<FirstStepsState>;
    if (data.status !== "active" && data.status !== "dismissed" && data.status !== "finished") return null;
    return {
      status: data.status,
      done: Array.isArray(data.done) ? data.done.filter(isQuestId) : [],
      skipped: Array.isArray(data.skipped) ? data.skipped.filter(isQuestId) : [],
      current: isQuestId(data.current) ? data.current : QUESTS[0].id,
      collapsed: data.collapsed === true,
    };
  } catch {
    // Blocked or corrupted storage: the guide simply does not resume.
    return null;
  }
}

export function writeFirstSteps(state: FirstStepsState): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage may be blocked; the guide then lives for this window only.
  }
}

/** The quest after `id` that is neither done nor skipped, or null at the end. */
export function nextOpenQuest(state: Pick<FirstStepsState, "done" | "skipped">, id: QuestId): QuestId | null {
  const start = QUESTS.findIndex((q) => q.id === id);
  for (let i = start + 1; i < QUESTS.length; i++) {
    const q = QUESTS[i].id;
    if (!state.done.includes(q) && !state.skipped.includes(q)) return q;
  }
  return null;
}
