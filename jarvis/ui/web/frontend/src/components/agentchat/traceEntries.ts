/**
 * A turn's work in the shape the Codex app draws it.
 *
 * Codex reads like a conversation, not a log: what the model says between
 * its calls is ordinary prose at full contrast, and each stretch of calls in
 * between collapses to ONE quiet line that says what was done — "Ran
 * commands, searched the web" — with the stretch's real mark in front (the
 * plugin's logo, the CLI vendor's logo, else a plain terminal or pencil).
 * The line opens to the single calls; a call opens to its raw input and
 * output. A finished turn folds to "Worked for 4m 07s".
 *
 * This module is the pure model behind that: blocks → prose and activity
 * stretches, the wording of every line, the fold's label, and the same
 * timeline as Markdown for copying. The renderer is TraceTimeline.tsx.
 *
 * It never invents a reason the model did not give, never draws wordless
 * (redacted) thinking, and never draws plumbing (loading a tool's schema,
 * polling a question card).
 *
 * Placeholders never use `{name}`: the i18n layer fills that token with the
 * assistant's name before any string reaches this module.
 */

import { fill } from "@/i18n";
import { cliVendor } from "@/lib/cliVendors";
import { resolveToolBrand } from "@/lib/toolBrand";
import { describeToolStep } from "@/lib/toolStepLabel";
import { isQuestionTool, type ReasoningBlock, type TextBlock, type ToolBlock, type TurnBlock, type TurnStatus } from "./reduce";
import { toolDiff } from "./toolDiff";
import { traceToolIdentity, traceToolName } from "./traceActivity";

export type Translate = (key: string) => string;

/** How a call stands. */
export type CallStatus = "running" | "done" | "failed" | "blocked" | "declined" | "interrupted";

/** What a call is, for its icon and its words. */
export type CallKind = "command" | "read" | "list" | "search" | "edit" | "write" | "image" | "web" | "family" | "service" | "tool";

export interface OutputPreview { lines: string[]; more: number }

export interface Call {
  id: string;
  block: ToolBlock;
  kind: CallKind;
  /** The line's words: the command itself, "Read src/app.ts", "Searched the wiki". */
  text: string;
  /** A quieter tail: a query, a title, an operation's argument. */
  detail: string;
  status: CallStatus;
  /** Why it did not succeed, in plain words; "" otherwise. */
  reason: string;
  /** Edit size, when it is an edit. */
  added: number;
  removed: number;
  /** A terse result for tools that are not commands ("3 results", a message). */
  result: string;
  /** The stretch summary's bucket ("command", "family:wiki", "used:Linear"). */
  bucket: string;
  /** A brand to draw instead of a plain glyph: a plugin, an MCP server, a CLI vendor. */
  brand: string | null;
}

/** What the model said — narration between calls, or its reasoning text. */
export interface ProseEntry { kind: "prose"; id: string; text: string; tone: "narration" | "reasoning"; block: TextBlock | ReasoningBlock }
/** A stretch of calls between two pieces of prose. */
export interface ActivityEntry { kind: "activity"; id: string; calls: Call[]; summary: string }
/** Work the caller draws itself: approvals, question cards, a streaming thought, a live reply. */
export interface LiveEntry { kind: "live"; id: string; block: TurnBlock }

export type TimelineEntry = ProseEntry | ActivityEntry | LiveEntry;

export interface Timeline {
  entries: TimelineEntry[];
  /** Calls told (live ones included). */
  callCount: number;
  /** Calls that failed, were blocked, declined or cut off. */
  problemCount: number;
}

export interface TimelineOptions {
  t: Translate;
  /** UI language tag, for plurals. */
  lang: string;
  status: TurnStatus;
  /**
   * Live turns keep replies and streaming thoughts for the caller; a folded
   * finished turn tells its narration as prose.
   */
  live: boolean;
}

const KEY = "trace_report";
export const tr = (t: Translate, key: string, vars: Record<string, string | number> = {}) => fill(t(`${KEY}.${key}`), vars);

/** "49ms", "0.4s", "12s", "4m 07s". */
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

export function capitalize(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/** A registry name as words, sentence case: "society_routines" → "Society routines". */
function humanize(name: string): string {
  return capitalize(name.split(/[-_\s.]+/).filter(Boolean).join(" ").toLowerCase());
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

/** The last three segments of a path — what a person recognises. */
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

/** Asks the person for something: drawn by the caller as its card. */
function needsPerson(block: ToolBlock): boolean {
  return Boolean(block.question) || Boolean(block.approval && block.approval.decision === null);
}

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

/** The output a person reads: unwrapped, cap marker gone. */
export function readableOutput(block: ToolBlock): string {
  const raw = stripCapMarker(block.output ?? "");
  return printedOutput(raw) ?? raw;
}

/** The first lines of an output and how many more there were. */
export function previewOutput(output: string, max = 3): OutputPreview | null {
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

function statusOf(block: ToolBlock, turn: TurnStatus, t: Translate): { status: CallStatus; reason: string } {
  if (block.approval?.decision === "deny") {
    const rule = refusalReason(block.approval.summary || block.output || "", t);
    return rule ? { status: "blocked", reason: rule } : { status: "declined", reason: "" };
  }
  if (block.output === null) return turn === "running" ? { status: "running", reason: "" } : { status: "interrupted", reason: "" };
  if (block.isError) {
    const raw = stripCapMarker(block.output);
    const rule = refusalReason(raw, t);
    if (rule) return { status: "blocked", reason: rule };
    return { status: "failed", reason: oneLine(raw.split(/\r?\n/).find((line) => line.trim()) ?? "", 200) };
  }
  return { status: "done", reason: "" };
}

const ACTION_KEYS: Record<string, string[]> = {
  command: ["command", "cmd", "CommandLine", "commandLine", "script"],
  read: ["file_path", "path", "notebook_path", "filename"],
  edit: ["file_path", "path", "notebook_path"],
  write: ["file_path", "path", "filename"],
  list: ["path", "directory", "dir", "pattern"],
  search: ["pattern", "query", "regex", "q"],
};

/** Families of Jarvis's own tools that have their own words. */
const FAMILIES = new Set([
  "wiki", "wiki_write", "artifact", "skill", "skill_create", "web", "screen", "screen_recall",
  "control", "navigate", "app", "memory", "memory_update", "profile", "contact", "call",
  "worker", "model", "mcp_admin", "settings", "verify",
]);

const IMAGE_FILE = /\.(png|jpe?g|gif|webp|bmp|svg|heic|avif)$/i;

/** Keys whose string value says what a structured result was about. */
const GIST_KEYS = ["summary", "message", "result", "status", "title", "answer", "text", "detail", "description"];

/** A terse result: a count, the tool's own message, or its first line. */
function terseResult(block: ToolBlock, t: Translate, lang: string): string {
  const text = readableOutput(block).trim();
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
        if (typeof value === "string" && value.trim()) return oneLine(value, 120);
      }
      for (const value of Object.values(record)) {
        if (Array.isArray(value)) {
          return value.length ? tr(t, `result.items_${plural(lang, value.length)}`, { count: value.length }) : tr(t, "result.none");
        }
      }
      return "";
    }
    if (/^\{\s*["'][\w-]+["']\s*:\s*\[\s*\]/.test(text)) return tr(t, "result.none");
    const nested = text.slice(1).search(/[[{]/);
    const top = nested < 0 ? text : text.slice(0, nested + 1);
    const gist = new RegExp(`["'](?:${GIST_KEYS.join("|")})["']\\s*:\\s*["']([^"']{2,})["']`).exec(top);
    return gist ? oneLine(gist[1], 120) : "";
  }
  return oneLine(text.split(/\r?\n/).find((line) => line.trim()) ?? "", 120);
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

/**
 * A command line without the directory change in front of it: agents start
 * most commands with `cd "C:/long/path" && …`, and the line should show what
 * ran, not where. The full command stays in the call's details.
 */
export function withoutCd(command: string): string {
  let text = command.trim();
  for (let i = 0; i < 3; i++) {
    const next = text.replace(/^(?:cd|Set-Location|pushd)\s+(?:"[^"]*"|'[^']*'|\S+)\s*(?:&&|;|\|\|)\s*/i, "");
    if (next === text) break;
    text = next;
  }
  return text || command.trim();
}

/** The program a command runs, past env assignments and wrappers ("gh" in "FOO=1 npx gh …"). */
export function commandBinary(command: string): string {
  const words = command.trim().split(/\s+/);
  for (const word of words) {
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(word)) continue;
    if (/^(sudo|npx|pnpm|bunx|uvx|time|env|&|cmd|\/c|powershell|pwsh|-Command|-NoProfile)$/i.test(word)) continue;
    return word.replace(/^["'&]+|["']+$/g, "").split(/[\\/]/).pop()?.replace(/\.(exe|cmd|bat)$/i, "") ?? "";
  }
  return "";
}

/** One finished or running call as its line. */
function callOf(block: ToolBlock, turn: TurnStatus, t: Translate, lang: string): Call {
  const view = traceToolIdentity(block);
  const record = inputRecord(block);
  const { status, reason } = statusOf(block, turn, t);
  const name = traceToolName(block.name);
  const own = /^jarvis\//i.test(name) ? describeToolStep(name.slice("jarvis/".length), block.input) : null;
  const description = own && (FAMILIES.has(own.family) || own.family === "shell") ? own : view.description;
  const action = view.action === "memory" ? null : view.action;
  const base = { id: block.callId, block, status, reason, added: 0, removed: 0, result: "", detail: "", brand: null as string | null };

  // Jarvis's CLI wrappers ("mcp__jarvis__cli_jarvisctl") run a command line.
  const wrapper = /^jarvis\/cli_/i.test(name) && firstString(record, ACTION_KEYS.command);
  if (action === "command" || description.family === "shell" || wrapper) {
    const raw = firstString(record, ACTION_KEYS.command) || (typeof block.input === "string" ? block.input : "") || description.detail;
    const command = oneLine(withoutCd(raw.split(/\r?\n/).find((line) => line.trim()) ?? ""), 200);
    // A vendor CLI ("gh", "vercel", "docker") wears its vendor's logo.
    const binary = commandBinary(command);
    return { ...base, kind: "command", text: command || capitalize(tr(t, "act.command_one")), bucket: "command", brand: cliVendor(binary) ? binary : null };
  }
  if (action === "read") {
    const path = firstString(record, ACTION_KEYS.read) || description.detail;
    if (IMAGE_FILE.test(path)) return { ...base, kind: "image", text: tr(t, "call.image", { target: shortPath(path) }), bucket: "image" };
    return { ...base, kind: "read", text: tr(t, "call.read", { target: shortPath(path) }), bucket: "read" };
  }
  if (action === "list") {
    const path = firstString(record, ACTION_KEYS.list) || description.detail || ".";
    return { ...base, kind: "list", text: tr(t, "call.list", { target: shortPath(path) }), bucket: "list" };
  }
  if (action === "search") {
    const pattern = oneLine(firstString(record, ACTION_KEYS.search) || description.detail, 60);
    const where = firstString(record, ["path", "glob", "include"]);
    const text = where ? tr(t, "call.search_in", { pattern, path: shortPath(where) }) : tr(t, "call.search", { pattern });
    return { ...base, kind: "search", text, bucket: "search" };
  }
  if (action === "edit" || action === "write") {
    const path = shortPath(firstString(record, ACTION_KEYS[action]) || description.detail);
    return { ...base, ...diffSize(block), kind: action, text: tr(t, `call.${action}`, { target: path }), bucket: action };
  }
  const family = description.family;
  const result = status === "done" ? terseResult(block, t, lang) : "";
  if (FAMILIES.has(family)) {
    const kind: CallKind = family === "web" ? "web" : "family";
    return { ...base, kind, text: capitalize(tr(t, `family.${family}`)), detail: oneLine(description.detail, 100), result, bucket: `family:${family}` };
  }
  if (own) {
    // Jarvis's own server is the road, not the destination: "jarvis/gmail"
    // is the Gmail plugin, "jarvis/society_routines" is that tool by name.
    const rest = name.slice("jarvis/".length);
    const brand = resolveToolBrand(rest);
    if (brand.logoUrl) {
      return { ...base, kind: "service", text: brand.label, detail: oneLine(own.detail, 100), result, bucket: `used:${brand.label}`, brand: brand.brandId ?? brand.label };
    }
    const label = humanize(rest);
    return { ...base, kind: "tool", text: label, detail: oneLine(own.detail, 100), result, bucket: `used:${label}` };
  }
  const raw = description.label || view.service || block.name;
  const label = /[A-Z]/.test(raw) ? raw : humanize(raw);
  if (view.integration || family === "mcp" || family === "service") {
    const service = view.service || label;
    return { ...base, kind: "service", text: label, detail: oneLine(description.detail, 100), result, bucket: `used:${service}`, brand: view.identity.logo ? view.identity.key ?? service : null };
  }
  return { ...base, kind: "tool", text: label, detail: oneLine(description.detail, 100), result, bucket: `used:${label}` };
}

/** "Ran commands, searched the web, used Linear" — a stretch in one line. */
export function summarize(calls: Call[], t: Translate, lang: string): string {
  if (calls.length === 1) return calls[0].text;
  const counts = new Map<string, number>();
  for (const call of calls) counts.set(call.bucket, (counts.get(call.bucket) ?? 0) + 1);
  const used: string[] = [];
  const parts: string[] = [];
  for (const [bucket, n] of counts) {
    if (bucket.startsWith("used:")) used.push(bucket.slice(5));
    else if (bucket.startsWith("family:")) parts.push(tr(t, `family.${bucket.slice(7)}`));
    else parts.push(tr(t, `act.${bucket}_${plural(lang, n)}`));
  }
  if (used.length) parts.push(tr(t, "act.used", { what: used.slice(0, 3).join(", ") }));
  return capitalize(parts.join(", "));
}

/** Lay out a turn's blocks as its timeline. */
export function buildTimeline(blocks: TurnBlock[], options: TimelineOptions): Timeline {
  const { t, lang, status, live } = options;
  const entries: TimelineEntry[] = [];
  let stretch: Call[] = [];
  let callCount = 0;
  let problemCount = 0;
  const flush = () => {
    if (!stretch.length) return;
    entries.push({ kind: "activity", id: `act:${stretch[0].id}`, calls: stretch, summary: summarize(stretch, t, lang) });
    stretch = [];
  };

  for (const block of blocks) {
    if (block.kind === "reasoning") {
      if (live && status === "running" && block.live) {
        flush();
        entries.push({ kind: "live", id: block.id, block });
        continue;
      }
      const text = block.text.trim();
      if (!text) continue;
      flush();
      entries.push({ kind: "prose", id: block.id, text, tone: "reasoning", block });
      continue;
    }
    if (block.kind === "text") {
      if (!block.text.trim()) continue;
      flush();
      if (live) entries.push({ kind: "live", id: block.id, block });
      else entries.push({ kind: "prose", id: block.id, text: block.text.trim(), tone: "narration", block });
      continue;
    }
    if (isPlumbing(block)) continue;
    callCount += 1;
    if (needsPerson(block)) {
      flush();
      entries.push({ kind: "live", id: block.callId, block });
      continue;
    }
    const call = callOf(block, status, t, lang);
    if (call.status !== "done" && call.status !== "running") problemCount += 1;
    stretch.push(call);
  }
  flush();
  return { entries, callCount, problemCount };
}

/** The folded line: "Worked for 4m 07s" when calls ran, "Thought for 3.0s" otherwise. */
export function headerLabel(timeline: Timeline, durationMs: number | null, t: Translate): string {
  const worked = timeline.callCount > 0;
  if (durationMs === null || durationMs <= 0) return tr(t, worked ? "worked" : "thought");
  return tr(t, worked ? "worked_for" : "thought_for", { duration: traceDuration(durationMs) });
}

function codeSpan(text: string): string {
  const fence = text.includes("`") ? "``" : "`";
  const pad = fence === "``" ? " " : "";
  return `${fence}${pad}${text}${pad}${fence}`;
}

/** The timeline as Markdown — what "Copy" puts on the clipboard. */
export function timelineMarkdown(timeline: Timeline, header: string, t: Translate): string {
  const out: string[] = [`**${header}**`];
  for (const entry of timeline.entries) {
    if (entry.kind === "live") continue;
    out.push("");
    if (entry.kind === "prose") {
      out.push(entry.text);
      continue;
    }
    if (entry.calls.length > 1) out.push(`*${entry.summary}*`);
    for (const call of entry.calls) {
      const words = call.kind === "command" ? codeSpan(call.text) : call.text;
      const size = call.added || call.removed ? ` (+${call.added} −${call.removed})` : "";
      const tail = call.detail ? ` — ${call.detail}` : "";
      const result = call.result ? `: ${call.result}` : "";
      const state = call.status === "done" || call.status === "running" ? ""
        : ` — ${tr(t, `state.${call.status}`)}${call.reason ? `: ${call.reason}` : ""}`;
      out.push(`- ${words}${size}${tail}${result}${state}`);
    }
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}
