/**
 * Portions adapted from pingdotgg/t3code @ e22c880 (apps/web MessagesTimeline,
 * packages/client-runtime work-log presentation), MIT License,
 * Copyright (c) 2026 T3 Tools Inc. Full text: third_party/t3code/LICENSE.
 *
 * The pure model behind a thread turn's work log. A turn reads as the
 * agent's answer with its work folded in between: every stretch of thinking
 * and tool calls up to the next piece of prose becomes ONE group. A finished
 * group is one line that counts what happened ("Ran 3 commands and read 2
 * files"); the group the running turn is still in is one live line that
 * names the newest step ("Thinking", the command it runs). Both open to the
 * single steps, and a step opens to its thought, diff or output.
 */

import type { ReasoningBlock, TextBlock, ToolBlock, TurnBlock, TurnStatus } from "@/components/agentchat/reduce";
import { buildTimeline, type Call, type CallKind, type Translate } from "@/components/agentchat/traceEntries";

/** One step of a group: a tool call or a stretch of thinking. */
export type WorkItem =
  | { kind: "call"; id: string; call: Call; startedMs: number }
  | { kind: "thought"; id: string; block: ReasoningBlock; startedMs: number };

export interface WorkGroup {
  kind: "work";
  id: string;
  items: WorkItem[];
  /** "Ran 3 commands and read 2 files", "Thought". */
  summary: string;
  /** The single kind of call the group made, "thought" when it only thought, else null. */
  sole: CallKind | "thought" | null;
  added: number;
  removed: number;
  startedMs: number;
  /** The running turn's newest group: drawn as one live line. */
  live: boolean;
}

export type ThreadRow =
  | WorkGroup
  | { kind: "text"; id: string; text: string; block: TextBlock }
  /** A call that waits for the person: an approval or a question card. */
  | { kind: "pending"; id: string; block: ToolBlock };

export function isFailed(call: Call): boolean {
  return call.status === "failed" || call.status === "blocked" || call.status === "declined";
}

/** A thought's text as one plain line: no Markdown marks, no line breaks. */
export function thoughtPreview(text: string): string {
  return text
    .replace(/```[^\n]*\n?/g, " ")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
    .replace(/(\*\*|__|~~|`)/g, "")
    .replace(/(^|\s)[*_](\S[^*_]*\S|\S)[*_](?=\s|[.,;:!?]|$)/g, "$1$2")
    .replace(/\s+/g, " ")
    .trim();
}

function plural(count: number, one: string, other: string): string {
  return `${count} ${count === 1 ? one : other}`;
}

/** What a set of calls of one bucket did, counted. */
function bucketLabel(bucket: string, calls: Call[]): string {
  const n = calls.length;
  switch (bucket) {
    case "command": return `Ran ${plural(n, "command", "commands")}`;
    case "read": return `Read ${plural(new Set(calls.map((call) => call.text)).size, "file", "files")}`;
    case "list": return `Listed ${plural(n, "folder", "folders")}`;
    case "search": return n === 1 ? "Searched the code" : `Searched the code ${n} times`;
    case "edit": return `Edited ${plural(new Set(calls.map((call) => call.text)).size, "file", "files")}`;
    case "write": return `Created ${plural(n, "file", "files")}`;
    case "image": return `Viewed ${plural(n, "image", "images")}`;
    case "family:web": return n === 1 ? "Searched the web" : `Searched the web ${n} times`;
    default: return n === 1 ? calls[0].text : `${calls[0].text} ${n} times`;
  }
}

/** Commands and file changes say the most about a stretch; they lead. */
function priority(bucket: string): number {
  if (bucket === "command" || bucket === "edit" || bucket === "write") return 0;
  return 1;
}

function sentence(parts: string[]): string {
  const lowered = parts.map((part, index) => index === 0 ? part : part.charAt(0).toLowerCase() + part.slice(1));
  if (lowered.length < 3) return lowered.join(" and ");
  return `${lowered.slice(0, -1).join(", ")}, and ${lowered.at(-1)}`;
}

function names(list: string[]): string {
  if (list.length < 3) return list.join(" and ");
  return `${list.slice(0, -1).join(", ")}, and ${list.at(-1)}`;
}

/**
 * A group in one line: the services it used, then at most two kinds of
 * work, and every call left over still counted.
 */
export function summarizeWork(items: WorkItem[]): string {
  const calls = items.flatMap((item) => item.kind === "call" ? [item.call] : []);
  if (calls.length === 0) {
    const thoughts = items.length;
    return thoughts <= 1 ? "Thought" : `Thought ${thoughts} times`;
  }
  if (calls.length === 1 && calls[0].bucket !== "edit") return calls[0].text;
  const services: string[] = [];
  const buckets = new Map<string, Call[]>();
  for (const call of calls) {
    if (call.bucket.startsWith("used:")) {
      const name = call.bucket.slice(5);
      if (!services.includes(name)) services.push(name);
      continue;
    }
    const list = buckets.get(call.bucket);
    if (list) list.push(call);
    else buckets.set(call.bucket, [call]);
  }
  const ranked = [...buckets].map(([bucket, list], index) => ({ bucket, list, index }));
  const chosen = [...ranked]
    .sort((a, b) => priority(a.bucket) - priority(b.bucket) || a.index - b.index)
    .slice(0, 2)
    .sort((a, b) => a.index - b.index);
  const parts = chosen.map(({ bucket, list }) => bucketLabel(bucket, list));
  if (services.length) parts.unshift(`Used ${names(services.slice(0, 3))}${services.length > 3 ? ` and ${services.length - 3} more` : ""}`);
  const told = chosen.reduce((sum, { list }) => sum + list.length, 0);
  const serviceCalls = calls.filter((call) => call.bucket.startsWith("used:")).length;
  const rest = calls.length - told - serviceCalls;
  if (rest > 0) parts.push(`Performed ${plural(rest, "other action", "other actions")}`);
  return sentence(parts);
}

function soleKind(items: WorkItem[]): WorkGroup["sole"] {
  const calls = items.flatMap((item) => item.kind === "call" ? [item.call] : []);
  if (calls.length === 0) return "thought";
  const kinds = new Set(calls.map((call) => call.kind));
  return kinds.size === 1 ? calls[0].kind : null;
}

/** The step the live line names: the newest running call, else the newest step. */
export function liveItem(group: WorkGroup): WorkItem {
  for (let i = group.items.length - 1; i >= 0; i -= 1) {
    const item = group.items[i];
    if (item.kind === "call" && item.call.status === "running") return item;
  }
  return group.items[group.items.length - 1];
}

/** Lay out a turn's blocks as text, waiting cards and work groups. */
export function buildThreadRows(blocks: TurnBlock[], options: { t: Translate; lang: string; status: TurnStatus }): ThreadRow[] {
  const timeline = buildTimeline(blocks, { ...options, live: true });
  const started = new Map<string, number>();
  for (const block of blocks) {
    if (block.kind === "tool") started.set(block.callId, block.startedMs);
    else if (block.kind === "reasoning") started.set(block.id, block.startedMs);
  }
  const rows: ThreadRow[] = [];
  let items: WorkItem[] = [];
  const flush = () => {
    if (!items.length) return;
    const calls = items.flatMap((item) => item.kind === "call" ? [item.call] : []);
    rows.push({
      kind: "work",
      id: `work:${items[0].id}`,
      items,
      summary: summarizeWork(items),
      sole: soleKind(items),
      added: calls.reduce((sum, call) => sum + call.added, 0),
      removed: calls.reduce((sum, call) => sum + call.removed, 0),
      startedMs: items[0].startedMs,
      live: false,
    });
    items = [];
  };
  const thought = (block: ReasoningBlock) => {
    // Wordless (redacted) thinking is no step; a live one still shows the turn thinks.
    if (!block.text.trim() && !(block.live && options.status === "running")) return;
    items.push({ kind: "thought", id: block.id, block, startedMs: started.get(block.id) ?? 0 });
  };

  for (const entry of timeline.entries) {
    if (entry.kind === "activity") {
      for (const call of entry.calls) items.push({ kind: "call", id: call.id, call, startedMs: started.get(call.id) ?? 0 });
      continue;
    }
    const block = entry.block;
    if (block.kind === "reasoning") {
      thought(block);
      continue;
    }
    flush();
    if (block.kind === "text") rows.push({ kind: "text", id: entry.id, text: block.text, block });
    else rows.push({ kind: "pending", id: entry.id, block });
  }
  flush();
  const last = rows[rows.length - 1];
  if (options.status === "running" && last?.kind === "work") last.live = true;
  return rows;
}
