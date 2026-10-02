/**
 * A turn's work as a timeline, in the shape Claude and Codex draw it.
 *
 * Both tools read the same way: the model's own words carry the story (its
 * narration between calls, its reasoning summaries) as plain prose, and the
 * calls between them are terse single lines — "Explored" with the files it
 * read and the patterns it searched, "Ran `npm test`" with the first lines
 * the command printed, "Edited src/app.ts +3 −1". A finished turn folds to
 * one line, "Worked for 4m 07s", and opens to exactly that timeline.
 *
 * This module is the pure model behind it: blocks → entries, the header's
 * one-line summary, and the same timeline as Markdown for copying. The
 * renderer is TraceTimeline.tsx.
 *
 * What it never does: invent a reason the model did not give, draw a row for
 * wordless (redacted) thinking — its time goes to the header — or draw
 * plumbing (loading a tool's schema, polling a question card).
 *
 * Placeholders never use `{name}`: the i18n layer fills that token with the
 * assistant's name before any string reaches this module.
 */

import { fill } from "@/i18n";
import { describeToolStep } from "@/lib/toolStepLabel";
import { isQuestionTool, type ReasoningBlock, type TextBlock, type ToolBlock, type TurnBlock, type TurnStatus } from "./reduce";
import { toolDiff } from "./toolDiff";
import { traceToolIdentity, traceToolName } from "./traceActivity";

export type Translate = (key: string) => string;

/** How a finished call ended. */
export type EntryStatus = "done" | "failed" | "blocked" | "declined" | "interrupted";

/** What the model said: its reasoning text, or narration between calls. */
export interface ThoughtEntry { kind: "thought"; id: string; text: string; block: ReasoningBlock | TextBlock }
/** Live work the classic row draws (running call, approval, question card, streaming thought, reply). */
export interface LiveEntry { kind: "live"; id: string; block: TurnBlock }
/** Consecutive reads, listings and searches, merged Codex-style. */
export interface ExploreEntry { kind: "explore"; id: string; lines: ExploreLine[]; blocks: ToolBlock[]; durationMs: number }
export interface ExploreLine { verb: "read" | "list" | "search"; targets: string[] }
export interface CommandEntry {
  kind: "command"; id: string; block: ToolBlock; command: string; status: EntryStatus;
  /** The first lines it printed (or the error), and how many more there were. */
  output: OutputPreview | null; reason: string;
}
export interface EditEntry {
  kind: "edit"; id: string; block: ToolBlock; verb: "edit" | "write"; path: string;
  added: number; removed: number; status: EntryStatus; reason: string;
}
export interface ToolEntry {
  kind: "tool"; id: string; block: ToolBlock; label: string; detail: string;
  /** A terse result ("3 results", the first line it said); "" when none. */
  result: string; status: EntryStatus; reason: string;
  /** A failed read, listing or search: counted as that, not as a tool used. */
  explore?: "read" | "list" | "search";
  /** How the header's "used …" names it: "the wiki", "GitHub · Create Issue". */
  subject: string;
}
export interface OutputPreview { lines: string[]; more: number }

export type TimelineEntry = ThoughtEntry | LiveEntry | ExploreEntry | CommandEntry | EditEntry | ToolEntry;

export interface Timeline {
  entries: TimelineEntry[];
  /** Calls told (live ones included). */
  actionCount: number;
  /** Calls that failed, were blocked, declined or cut off. */
  problemCount: number;
  /** Wordless thinking, summed. */
  thinkingMs: number;
  /** "Explored 3 files, ran 2 commands" — the folded header's tail. */
  summary: string;
}

export interface TimelineOptions {
  t: Translate;
  /** UI language tag, for plurals and lists. */
  lang: string;
  status: TurnStatus;
  /**
   * Live turns keep their replies and streaming pieces as live entries;
   * a finished turn's fold tells narration as thoughts.
   */
  live: boolean;
}

const KEY = "trace_report";
export const tr = (t: Translate, key: string, vars: Record<string, string | number> = {}) => fill(t(`${KEY}.${key}`), vars);

/** "49ms", "0.4s", "12s", "4m 07s" — the duration on a trace line. */
export function traceDuration(ms: number): string {
  // Keep short, measured calls visible instead of rounding 49 ms to "0.0s".
  if (ms > 0 && ms < 100) return `${Math.ceil(ms)}ms`;
  const seconds = Math.max(0, ms) / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;
}

export function plural(lang: string, count: number): "one" | "other" {
  try {
    return new Intl.PluralRules(lang).select(count) === "one" ? "one" : "other";
  } catch {
    return count === 1 ? "one" : "other";
  }
}

export function joinList(lang: string, items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  try {
    return new Intl.ListFormat(lang, { style: "long", type: "conjunction" }).format(items);
  } catch {
    return items.join(", ");
  }
}

function oneLine(text: string, max: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max - 1).trimEnd()}…` : clean;
}

function inputRecord(block: ToolBlock): Record<string, unknown> {
  return block.input && typeof block.input === "object" && !Array.isArray(block.input)
    ? block.input as Record<string, unknown> : {};
}

function firstString(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

/** The last two or three segments of a path — what a person recognises. */
export function shortPath(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.length > 3 ? `…/${parts.slice(-3).join("/")}` : path;
}

/** Plumbing calls draw nothing: loading a tool's schema, polling a question card. */
function isPlumbing(block: ToolBlock): boolean {
  if (isQuestionTool(block.name) && !block.question) return true;
  if (block.name.replace(/^mcp__[^_]+__/, "") === "ToolSearch") {
    return firstString(inputRecord(block), ["query"]).startsWith("select:");
  }
  return false;
}

/** Still asks for something or still running: the live row draws it. */
function isLiveBlock(block: ToolBlock, status: TurnStatus): boolean {
  if (block.question) return true;
  if (block.approval && block.approval.decision === null) return true;
  return block.output === null && status === "running" && block.approval?.decision !== "deny";
}

/** Drop the "… (+N more chars)" cap marker the backend appends. */
function stripCapMarker(text: string): string {
  return text.replace(/…\s*\(\+\d+ more chars\)\s*$/, "").trimEnd();
}

/** What a wrapped command printed ({exit_code, stdout, stderr}), or null. */
function printedOutput(output: string): string | null {
  if (!output.trimStart().startsWith("{")) return null;
  try {
    const record = JSON.parse(output) as Record<string, unknown>;
    if (!record || typeof record !== "object" || typeof record.stdout !== "string") return null;
    return record.stdout.trim() || (typeof record.stderr === "string" ? record.stderr : "");
  } catch {
    return null;
  }
}

const PREVIEW_LINES = 3;

/** The first lines of an output, the way Codex and Claude Code show them. */
export function previewOutput(output: string, max = PREVIEW_LINES): OutputPreview | null {
  const lines = output.split(/\r?\n/).map((line) => line.replace(/\s+$/, "")).filter((line) => line.trim());
  if (!lines.length) return null;
  return { lines: lines.slice(0, max).map((line) => (line.length > 160 ? `${line.slice(0, 159)}…` : line)), more: Math.max(0, lines.length - max) };
}

/** A refusal reason in plain words, when it is one of the system's own. */
function refusalReason(reason: string, t: Translate): string | null {
  const text = reason.trim();
  if (/^blacklist\b/i.test(text) || /tool-declared-block/i.test(text)) return tr(t, "reason.rule");
  if (/^plan mode/i.test(text)) return tr(t, "reason.plan_mode");
  if (/^cancelled\b/i.test(text)) return tr(t, "reason.cancelled");
  if (/^timeout$/i.test(text) || /approval timed out/i.test(text)) return tr(t, "reason.timeout");
  if (/approval[_ ]unavailable|no approval channel/i.test(text)) return tr(t, "reason.no_approver");
  return null;
}

/** How a finished call ended, and why when it did not succeed. */
function outcome(block: ToolBlock, t: Translate): { status: EntryStatus; reason: string } {
  if (block.approval?.decision === "deny") {
    const rule = refusalReason(block.approval.summary || block.output || "", t);
    return rule ? { status: "blocked", reason: rule } : { status: "declined", reason: "" };
  }
  if (block.output === null) return { status: "interrupted", reason: "" };
  if (block.isError) {
    const raw = stripCapMarker(block.output);
    const rule = refusalReason(raw, t);
    if (rule) return { status: "blocked", reason: rule };
    const first = raw.split(/\r?\n/).find((line) => line.trim()) ?? "";
    return { status: "failed", reason: oneLine(first, 200) };
  }
  return { status: "done", reason: "" };
}

/** Argument keys that name what a coding action touched. */
const ACTION_KEYS: Record<string, string[]> = {
  command: ["command", "cmd", "CommandLine", "commandLine", "script"],
  read: ["file_path", "path", "notebook_path", "filename"],
  edit: ["file_path", "path", "notebook_path"],
  write: ["file_path", "path", "filename"],
  list: ["path", "directory", "dir", "pattern"],
  search: ["pattern", "query", "regex", "q"],
};

/** Families of Jarvis's own tools that have a past-tense label. */
const FAMILY_LABELS = new Set([
  "wiki", "wiki_write", "artifact", "skill", "skill_create", "web", "screen", "screen_recall",
  "control", "navigate", "app", "memory", "memory_update", "profile", "contact", "call",
  "worker", "model", "mcp_admin", "settings", "verify",
]);

/** Keys whose string value says what a structured result was about. */
const GIST_KEYS = ["summary", "message", "result", "status", "title", "answer", "text", "detail", "description"];

/** A terse result for a generic tool: a count, its message, or its first line. */
function terseResult(block: ToolBlock, t: Translate, lang: string): string {
  let output = stripCapMarker(block.output ?? "");
  const printed = printedOutput(output);
  if (printed !== null) output = printed;
  const text = output.trim();
  if (!text) return "";
  if (text.startsWith("{") || text.startsWith("[")) {
    let parsed: unknown;
    try { parsed = JSON.parse(text); } catch { parsed = undefined; }
    if (Array.isArray(parsed)) {
      return parsed.length ? tr(t, `result.items_${plural(lang, parsed.length)}`, { count: parsed.length }) : tr(t, "result.none");
    }
    if (parsed && typeof parsed === "object") {
      const record = parsed as Record<string, unknown>;
      for (const key of GIST_KEYS) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) return oneLine(value, 140);
      }
      for (const value of Object.values(record)) {
        if (Array.isArray(value)) {
          return value.length ? tr(t, `result.items_${plural(lang, value.length)}`, { count: value.length }) : tr(t, "result.none");
        }
      }
      return "";
    }
    // Cut by the preview cap: the top level still says a little.
    if (/^\{\s*["'][\w-]+["']\s*:\s*\[\s*\]/.test(text)) return tr(t, "result.none");
    const nested = text.slice(1).search(/[[{]/);
    const top = nested < 0 ? text : text.slice(0, nested + 1);
    const gist = new RegExp(`["'](?:${GIST_KEYS.join("|")})["']\\s*:\\s*["']([^"']{2,})["']`).exec(top);
    return gist ? oneLine(gist[1], 140) : "";
  }
  return oneLine(text.split(/\r?\n/).find((line) => line.trim()) ?? "", 140);
}

function diffSize(block: ToolBlock): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const file of toolDiff(block.name, block.input, block.output) ?? []) {
    for (const line of file.lines) {
      if (line.kind === "add") added += 1;
      else if (line.kind === "del") removed += 1;
    }
  }
  return { added, removed };
}

/** One finished call as its entry (explore calls come back as a line to merge). */
function entryOf(block: ToolBlock, t: Translate, lang: string): ExploreLine | CommandEntry | EditEntry | ToolEntry {
  const view = traceToolIdentity(block);
  const record = inputRecord(block);
  const { status, reason } = outcome(block, t);
  const action = view.action === "memory" ? null : view.action;
  const name = traceToolName(block.name);
  const own = /^jarvis\//i.test(name) ? describeToolStep(name.slice("jarvis/".length), block.input) : null;
  const description = own && (FAMILY_LABELS.has(own.family) || own.family === "shell") ? own : view.description;

  if (action === "read" || action === "list" || action === "search") {
    if (status === "done") {
      const target = firstString(record, ACTION_KEYS[action]) || description.detail;
      const where = action === "search" ? firstString(record, ["path", "glob", "include"]) : "";
      const text = action === "search" && where
        ? tr(t, "entry.search_in", { pattern: oneLine(target, 60), path: shortPath(where) })
        : oneLine(action === "search" ? target : shortPath(target), 80);
      return { verb: action, targets: text ? [text] : [] };
    }
  }
  if (action === "command" || description.family === "shell") {
    let command = firstString(record, ACTION_KEYS.command) || (typeof block.input === "string" ? block.input : "") || description.detail;
    command = (command.split(/\r?\n/).find((line) => line.trim()) ?? "").trim();
    const raw = stripCapMarker(block.output ?? "");
    const printed = printedOutput(raw);
    return {
      kind: "command", id: block.callId, block, command: oneLine(command, 160), status, reason,
      output: status === "done" ? previewOutput(printed ?? raw) : null,
    };
  }
  if (action === "edit" || action === "write") {
    const path = shortPath(firstString(record, ACTION_KEYS[action]) || description.detail);
    return { kind: "edit", id: block.callId, block, verb: action, path, ...diffSize(block), status, reason };
  }
  const family = description.family;
  const label = FAMILY_LABELS.has(family) ? tr(t, `family.${family}`)
    : (() => {
      const raw = description.label || view.service || block.name;
      return /[A-Z]/.test(raw) ? raw : raw.split(/[-_\s.]+/).filter(Boolean).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
    })();
  if (action === "read" || action === "list" || action === "search") {
    // A failed read is told like any failed call.
    return { kind: "tool", id: block.callId, block, label: tr(t, `entry.${action}`), detail: oneLine(description.detail, 100), result: "", status, reason, explore: action, subject: "" };
  }
  return {
    kind: "tool", id: block.callId, block, label, detail: oneLine(description.detail, 100),
    result: status === "done" ? terseResult(block, t, lang) : "", status, reason,
    subject: FAMILY_LABELS.has(family) ? tr(t, `subject.${family}`) : label,
  };
}

/** Lay out a turn's blocks as its timeline. */
export function buildTimeline(blocks: TurnBlock[], options: TimelineOptions): Timeline {
  const { t, lang, status, live } = options;
  const entries: TimelineEntry[] = [];
  let thinkingMs = 0;
  const tally = { read: 0, list: 0, search: 0, command: 0, edit: 0, write: 0 };
  const used: string[] = [];
  let actionCount = 0;
  let problemCount = 0;

  for (const block of blocks) {
    if (block.kind === "reasoning") {
      if (live && status === "running" && block.live) {
        entries.push({ kind: "live", id: block.id, block });
        continue;
      }
      const text = block.text.trim();
      if (text) entries.push({ kind: "thought", id: block.id, text, block });
      else thinkingMs += block.durationMs ?? 0;
      continue;
    }
    if (block.kind === "text") {
      if (!block.text.trim()) continue;
      if (live) entries.push({ kind: "live", id: block.id, block });
      else entries.push({ kind: "thought", id: block.id, text: block.text.trim(), block });
      continue;
    }
    if (isPlumbing(block)) continue;
    actionCount += 1;
    if (isLiveBlock(block, status)) {
      entries.push({ kind: "live", id: block.callId, block });
      continue;
    }
    const entry = entryOf(block, t, lang);
    if ("verb" in entry && !("kind" in entry)) {
      tally[entry.verb] += 1;
      const previous = entries[entries.length - 1];
      if (previous?.kind === "explore") {
        const last = previous.lines[previous.lines.length - 1];
        if (last && last.verb === entry.verb && entry.verb === "read") last.targets.push(...entry.targets);
        else previous.lines.push(entry);
        previous.blocks.push(block);
        previous.durationMs += block.durationMs ?? 0;
      } else {
        entries.push({ kind: "explore", id: block.callId, lines: [entry], blocks: [block], durationMs: block.durationMs ?? 0 });
      }
      continue;
    }
    const full = entry as CommandEntry | EditEntry | ToolEntry;
    if (full.status !== "done") problemCount += 1;
    if (full.kind === "command") tally.command += 1;
    else if (full.kind === "edit") tally[full.verb] += 1;
    else if (full.explore) tally[full.explore] += 1;
    else if (full.status === "done" && !used.includes(full.subject)) used.push(full.subject);
    entries.push(full);
  }

  const parts: string[] = [];
  const count = (key: string, n: number) => { if (n) parts.push(tr(t, `sum.${key}_${plural(lang, n)}`, { count: n })); };
  count("read", tally.read);
  count("list", tally.list);
  count("search", tally.search);
  count("command", tally.command);
  count("edit", tally.edit);
  count("write", tally.write);
  if (used.length) parts.push(tr(t, "sum.used", { list: joinList(lang, used.slice(0, 3)) }));
  const joined = joinList(lang, parts);
  return {
    entries,
    actionCount,
    problemCount,
    thinkingMs,
    summary: joined ? joined.charAt(0).toUpperCase() + joined.slice(1) : "",
  };
}

/** The folded header: "Worked for 4m 07s" when calls ran, "Thought for 3.0s" otherwise. */
export function headerLabel(timeline: Timeline, durationMs: number | null, t: Translate): string {
  const worked = timeline.actionCount > 0;
  if (durationMs === null || durationMs <= 0) return tr(t, worked ? "worked" : "thought");
  return tr(t, worked ? "worked_for" : "thought_for", { duration: traceDuration(durationMs) });
}

function statusLine(entry: CommandEntry | EditEntry | ToolEntry, t: Translate): string {
  if (entry.status === "done") return "";
  const label = tr(t, `status.${entry.status}`);
  return entry.reason ? `${label}: ${entry.reason}` : label;
}

function codeSpan(text: string): string {
  const fence = text.includes("`") ? "``" : "`";
  const pad = fence === "``" ? " " : "";
  return `${fence}${pad}${text}${pad}${fence}`;
}

/** The timeline as Markdown — what "Copy" puts on the clipboard. */
export function timelineMarkdown(timeline: Timeline, header: string, t: Translate): string {
  const out: string[] = [`**${header}**${timeline.summary ? ` — ${timeline.summary}` : ""}`];
  for (const entry of timeline.entries) {
    out.push("");
    if (entry.kind === "thought") {
      out.push(entry.text);
      continue;
    }
    if (entry.kind === "live") continue;
    if (entry.kind === "explore") {
      out.push(`- ${tr(t, "entry.explored")}`);
      for (const line of entry.lines) out.push(`  - ${tr(t, `entry.${line.verb}`)} ${line.targets.join(", ")}`);
      continue;
    }
    const time = entry.block.durationMs ? ` (${traceDuration(entry.block.durationMs)})` : "";
    const problem = statusLine(entry, t);
    if (entry.kind === "command") {
      out.push(`- ${tr(t, "entry.ran")} ${codeSpan(entry.command)}${time}`);
      if (entry.output) {
        out.push("  ```", ...entry.output.lines.map((line) => `  ${line}`));
        if (entry.output.more) out.push(`  ${tr(t, `entry.more_${plural("en", entry.output.more)}`, { count: entry.output.more })}`);
        out.push("  ```");
      }
    } else if (entry.kind === "edit") {
      const size = entry.added || entry.removed ? ` (+${entry.added} −${entry.removed})` : "";
      out.push(`- ${tr(t, entry.verb === "edit" ? "entry.edited" : "entry.created")} ${codeSpan(entry.path)}${size}${time}`);
    } else {
      out.push(`- ${entry.label}${entry.detail ? ` — ${entry.detail}` : ""}${time}${entry.result ? `: ${entry.result}` : ""}`);
    }
    if (problem) out.push(`  ${problem}`);
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}
