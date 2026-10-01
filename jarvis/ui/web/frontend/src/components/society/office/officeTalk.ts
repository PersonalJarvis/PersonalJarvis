/**
 * Talking to an agent inside the office, without leaving the map.
 *
 * The person picks an agent and speaks (or types); the words go into that
 * agent's own canonical chat — the same session the chat view shows — and the
 * answer comes back as a bubble over the agent's head: a thought bubble while
 * it works, a speech bubble with its reply once it is done.
 *
 * Specialists share one office-only chat store (its own socket through the
 * shared connect budget), so the chat the rest of the app has open is never
 * switched underneath it. The lead is Jarvis itself and answers in the Jarvis
 * chat store, exactly like the lead's card does.
 *
 * The bubble text is derived here, pure and tested (`officeTalk.test.ts`).
 */
import { create } from "zustand";
import { createAgentChatStore, useAgentChatStore, type AgentChatStoreHook } from "@/store/agentChat";
import type { Timeline, TimelineItem, TurnItem } from "@/components/agentchat/reduce";
import type { SocietyAgent } from "../data";
import { toolLabel, type ChatLine } from "./deskChat";

/** One socket for every specialist the person talks to in the office. */
export const officeTalkChat: AgentChatStoreHook = createAgentChatStore("society", "office-talk");

/** The chat store an agent answers in: the lead speaks through Jarvis's own chat. */
export function talkStoreFor(agent: Pick<SocietyAgent, "tier">): AgentChatStoreHook {
  return agent.tier === "lead" ? useAgentChatStore : officeTalkChat;
}

/** A spoken line the person's character shows over its head. */
export interface PlayerLine { text: string; atMs: number }

interface TalkState {
  /** The agent the person is talking to right now; its bubble follows that chat. */
  agentId: string | null;
  /** The microphone is open for the office (push-to-talk or tap). */
  listening: boolean;
  /** Last thing the person said, shown over their character for a moment. */
  playerLine: PlayerLine | null;
  setAgent: (agentId: string | null) => void;
  setListening: (listening: boolean) => void;
  say: (text: string) => void;
}

export const useOfficeTalk = create<TalkState>((set) => ({
  agentId: null,
  listening: false,
  playerLine: null,
  setAgent: (agentId) => set((s) => (s.agentId === agentId ? s : { agentId })),
  setListening: (listening) => set((s) => (s.listening === listening ? s : { listening })),
  say: (text) => set({ playerLine: { text, atMs: Date.now() } }),
}));

/** How long a finished reply stays over the agent's head once its panel is closed. */
export const REPLY_LINGER_MS = 20_000;
/** How long the person's own line stays over their character. */
export const PLAYER_LINE_MS = 7_000;

export type BubbleKind = "thought" | "speech" | "ask" | "error";

export interface Bubble {
  kind: BubbleKind;
  text: string;
  /** Still streaming or working: the bubble shows its "…" and never expires. */
  live: boolean;
  /** When the content was last settled; a finished bubble expires from here. */
  atMs: number;
}

/**
 * Plain text for a small bubble: markdown marks, code fences and link targets
 * removed, whitespace flattened, cut at a word boundary with an ellipsis.
 */
export function bubbleText(raw: string, max = 220): string {
  const plain = raw
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!\[[^\]]*]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, "")
    .replace(/(\*\*|__|~~|\*|_)(?=\S)([^*_~]+?)\1/g, "$2")
    .replace(/\s+/g, " ")
    .trim();
  if (plain.length <= max) return plain;
  const cut = plain.slice(0, max - 1);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,.;:]+$/, "")}…`;
}

/** The items the office may read: only the agent's own session, never whatever else the store holds. */
export function itemsFor(agent: Pick<SocietyAgent, "tier" | "chatSessionId">, activeSessionId: string | null, timeline: Timeline): TimelineItem[] {
  if (agent.tier === "lead") return timeline.items;
  return agent.chatSessionId && agent.chatSessionId === activeSessionId ? timeline.items : [];
}

export function turnBubble(turn: TurnItem, labels: BubbleLabels): Bubble {
  const last = turn.blocks[turn.blocks.length - 1];
  if (turn.status === "running") {
    if (last?.kind === "text" && last.text.trim()) return { kind: "speech", text: bubbleText(last.text, 160), live: true, atMs: turn.startedMs };
    if (last?.kind === "tool") return { kind: "thought", text: labels.tool.replace("{0}", toolLabel(last.name)), live: true, atMs: turn.startedMs };
    const thinking = last?.kind === "reasoning" && last.text.trim() ? bubbleText(last.text.slice(-240), 90) : "";
    return { kind: "thought", text: thinking || labels.thinking, live: true, atMs: turn.startedMs };
  }
  const settled = turn.startedMs + (turn.durationMs ?? 0);
  if (turn.status === "error") return { kind: "error", text: bubbleText(turn.error || labels.failed, 140), live: false, atMs: settled };
  const text = [...turn.blocks].reverse().find((b) => b.kind === "text" && b.text.trim());
  if (text && text.kind === "text") return { kind: "speech", text: bubbleText(text.text), live: false, atMs: settled };
  if (turn.status === "cancelled") return { kind: "thought", text: labels.cancelled, live: false, atMs: settled };
  return { kind: "speech", text: labels.done, live: false, atMs: settled };
}

export interface BubbleLabels { thinking: string; tool: string; failed: string; cancelled: string; done: string; approval: string; waiting: string }

/** What the agent the person talks to shows over its head, from its live chat. */
export function conversationBubble(items: readonly TimelineItem[], pendingApprovals: number, labels: BubbleLabels): Bubble | null {
  if (pendingApprovals > 0) return { kind: "ask", text: labels.approval, live: true, atMs: Date.now() };
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const item = items[i];
    if (item.type === "turn") return turnBubble(item, labels);
    // Sent, and the turn has not started yet: the agent is already mulling it over.
    if (item.type === "user") return { kind: "thought", text: labels.thinking, live: true, atMs: item.tsMs };
    if (item.type === "error") return { kind: "error", text: bubbleText(item.text, 140), live: false, atMs: item.tsMs };
  }
  return null;
}

/**
 * What every other agent thinks out loud, from the tail its desk monitor
 * already polls: working agents think about their current step, waiting
 * agents ask. Idle agents keep quiet — the office must not turn into noise.
 */
export function ambientBubble(state: SocietyAgent["state"], lines: readonly ChatLine[] | undefined, labels: Pick<BubbleLabels, "tool" | "thinking" | "waiting">): Bubble | null {
  const last = lines && lines.length > 0 ? lines[lines.length - 1] : null;
  if (state === "working") {
    if (last?.kind === "tool") return { kind: "thought", text: labels.tool.replace("{0}", bubbleText(last.text, 48)), live: true, atMs: 0 };
    if (last?.kind === "agent") return { kind: "thought", text: bubbleText(last.text, 70), live: true, atMs: 0 };
    return { kind: "thought", text: labels.thinking, live: true, atMs: 0 };
  }
  if (state === "waiting") {
    const asked = lines ? [...lines].reverse().find((l) => l.kind === "agent") : undefined;
    return { kind: "ask", text: asked ? bubbleText(asked.text, 90) : labels.waiting, live: true, atMs: 0 };
  }
  return null;
}
