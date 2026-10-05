/**
 * Portions adapted from pingdotgg/t3code @ e22c880 (apps/web MessagesTimeline,
 * packages/client-runtime work-log presentation), MIT License,
 * Copyright (c) 2026 T3 Tools Inc. Full text: third_party/t3code/LICENSE.
 *
 * The pure model behind a thread turn's work log. A turn reads as the
 * agent's own words — its answer and every thought it put into words — with
 * its work in between: ONE group per run of tool calls between two
 * paragraphs, named by what it did ("Edited files, ran commands", the call
 * itself when it ran alone). A thought with words is a paragraph of its own
 * and splits the work around it; wordless (redacted) thinking is no row,
 * except the thought the running turn is having, which shows as a live
 * "Thinking" step so the turn never looks idle. The group the running turn is
 * still in is live.
 */

import type { ReasoningBlock, TextBlock, ToolBlock, TurnBlock, TurnStatus } from "@/components/agentchat/reduce";
import { buildTimeline, summarize, type Call, type CallKind, type Translate } from "@/components/agentchat/traceEntries";

/** One step of a group: a tool call, or the wordless thought a running turn is having. */
export type WorkItem =
  | { kind: "call"; id: string; call: Call; startedMs: number }
  | { kind: "thought"; id: string; block: ReasoningBlock; startedMs: number };

export interface WorkGroup {
  kind: "work";
  id: string;
  items: WorkItem[];
  /** "Edited files, ran commands", the command itself, "Thinking". */
  summary: string;
  /** The single kind of call the group made, "thought" when it only thinks, else null. */
  sole: CallKind | "thought" | null;
  added: number;
  removed: number;
  startedMs: number;
  /** The running turn's newest group: it stays open and follows its newest step. */
  live: boolean;
}

export type ThreadRow =
  | WorkGroup
  | { kind: "text"; id: string; text: string; block: TextBlock }
  /** A thought in the agent's own words, read as a paragraph between its work. */
  | { kind: "thought"; id: string; text: string; block: ReasoningBlock; live: boolean }
  /** A call that waits for the person: an approval or a question card. */
  | { kind: "pending"; id: string; block: ToolBlock };

export function isFailed(call: Call): boolean {
  return call.status === "failed" || call.status === "blocked" || call.status === "declined";
}

function soleKind(items: WorkItem[]): WorkGroup["sole"] {
  const calls = items.flatMap((item) => item.kind === "call" ? [item.call] : []);
  if (calls.length === 0) return "thought";
  const kinds = new Set(calls.map((call) => call.kind));
  return kinds.size === 1 ? calls[0].kind : null;
}

/** Lay out a turn's blocks as text, thoughts, waiting cards and work groups. */
export function buildThreadRows(blocks: TurnBlock[], options: { t: Translate; lang: string; status: TurnStatus }): ThreadRow[] {
  const timeline = buildTimeline(blocks, { ...options, live: true });
  const started = new Map<string, number>();
  for (const block of blocks) {
    if (block.kind === "tool") started.set(block.callId, block.startedMs);
    else if (block.kind === "reasoning") started.set(block.id, block.startedMs);
  }
  const running = options.status === "running";
  const rows: ThreadRow[] = [];
  let items: WorkItem[] = [];

  const flush = () => {
    if (!items.length) return;
    const calls = items.flatMap((item) => item.kind === "call" ? [item.call] : []);
    rows.push({
      kind: "work",
      id: `work:${items[0].id}`,
      items,
      summary: calls.length ? summarize(calls, options.t, options.lang) : "Thinking",
      sole: soleKind(items),
      added: calls.reduce((sum, call) => sum + call.added, 0),
      removed: calls.reduce((sum, call) => sum + call.removed, 0),
      startedMs: items[0].startedMs,
      live: false,
    });
    items = [];
  };

  for (const entry of timeline.entries) {
    if (entry.kind === "activity") {
      for (const call of entry.calls) items.push({ kind: "call", id: call.id, call, startedMs: started.get(call.id) ?? 0 });
      continue;
    }
    const block = entry.block;
    if (block.kind === "reasoning") {
      const live = running && block.live;
      const text = block.text.trim();
      if (text) {
        flush();
        rows.push({ kind: "thought", id: block.id, text, block, live });
      } else if (live) {
        items.push({ kind: "thought", id: block.id, block, startedMs: started.get(block.id) ?? 0 });
      }
      continue;
    }
    flush();
    if (block.kind === "text") rows.push({ kind: "text", id: entry.id, text: block.text, block });
    else rows.push({ kind: "pending", id: entry.id, block });
  }
  flush();
  const last = rows[rows.length - 1];
  if (running && last?.kind === "work") last.live = true;
  return rows;
}
