/**
 * A finished turn's work, told as prose.
 *
 * The live rail shows a turn while it happens: a node per tool call, a
 * shimmer on the step that is working. Read afterwards, that list was a poor
 * record — "Thought for 0.8s" rows with nothing inside, registry names
 * ("find-app-action"), a result cut to `{'actions': [{'...`, and the model's
 * own explanation (its narration between calls, the sentence next to a call)
 * scattered between rows or dropped. Nobody could look back at it and say
 * what happened.
 *
 * This module turns the same blocks into a report a person can read and
 * keep: one overview paragraph (how long, how many steps, what kinds of
 * work, how it ended, which model), then every step as a full sentence —
 * the model's reason for it first, in its own words, then what it did, then
 * what came of it. `narrativeMarkdown` renders the same report as Markdown
 * for copying.
 *
 * Rules the narrative keeps:
 *   - nothing is invented: a step without a reason has none, a result that
 *     cannot be read says so and points at the details;
 *   - redacted (wordless) thinking is never a step; its time is summed into
 *     the overview;
 *   - plumbing (loading a tool's definition, polling a question card) is not
 *     a step;
 *   - every sentence comes from `trace_report.*` keys, so all locales read
 *     as their own language; runtime text (commands, paths, queries) is
 *     inserted verbatim, Markdown-escaped or as inline code.
 *
 * Placeholders never use `{name}`: the i18n layer fills that token with the
 * assistant's name before any sentence sees it.
 *
 * Pure: no React, no store — `t` and the UI language come in as arguments.
 */

import { fill } from "@/i18n";
import { isQuestionTool, type ToolBlock, type TurnBlock, type TurnStatus } from "./reduce";
import { toolDiff } from "./toolDiff";
import { describeToolStep } from "@/lib/toolStepLabel";
import { traceToolIdentity, traceToolName } from "./traceActivity";

export type Translate = (key: string) => string;

/** How a step ended, in the words the report uses. */
export type StepOutcome = "done" | "failed" | "denied" | "declined" | "interrupted" | "pending";

export interface NarrativeStep {
  id: string;
  /** "action": a tool call. "note": the model's words with no call after them. */
  kind: "action" | "note";
  /** Markdown sentence of what was done; empty for a note. */
  sentence: string;
  /** The model's own words that led to this step, oldest first (Markdown). */
  why: string[];
  /** Markdown sentence of what came of it; empty when there is nothing to say. */
  result: string;
  outcome: StepOutcome;
  durationMs: number | null;
  /** The call itself, for its details; null for a note. */
  block: ToolBlock | null;
}

export interface TraceNarrative {
  /** The overview paragraph (Markdown). */
  overview: string;
  steps: NarrativeStep[];
  /** Tool calls told as steps. */
  actionCount: number;
  /** Steps that did not succeed (failed, refused, declined, interrupted). */
  problemCount: number;
  /** Time spent in wordless thinking, summed. */
  thinkingMs: number;
}

export interface NarrateOptions {
  t: Translate;
  /** UI language, for list joining and plurals ("en", "de", "es"). */
  lang: string;
  status: TurnStatus;
  durationMs: number | null;
  /** The model that answered, when the turn said. */
  model?: string;
}

const KEY = "trace_report";
/**
 * Fill a sentence and tidy its end: a quoted value that closes with its own
 * stop ("It returned “Done.”.") keeps one, the way a person writes it.
 */
const tr = (t: Translate, key: string, vars: Record<string, string | number> = {}) =>
  fill(t(`${KEY}.${key}`), vars).replace(/([.!?…])([”“»"])\.$/, "$1$2");

/** A duration the way a sentence says it: "49 ms", "0.4 s", "12 s", "2 min 05 s". */
export function proseDuration(ms: number): string {
  // A measured short call stays measured instead of rounding to "0.0 s".
  if (ms > 0 && ms < 100) return `${Math.ceil(ms)} ms`;
  const seconds = Math.max(0, ms) / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  return `${Math.floor(seconds / 60)} min ${String(Math.floor(seconds % 60)).padStart(2, "0")} s`;
}

function plural(lang: string, count: number): "one" | "other" {
  try {
    return new Intl.PluralRules(lang).select(count) === "one" ? "one" : "other";
  } catch {
    return count === 1 ? "one" : "other";
  }
}

function joinList(lang: string, items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  try {
    return new Intl.ListFormat(lang, { style: "long", type: "conjunction" }).format(items);
  } catch {
    return `${items.slice(0, -1).join(", ")} & ${items[items.length - 1]}`;
  }
}

/** Escape free text so it reads as text inside a Markdown sentence. */
export function escapeMarkdown(text: string): string {
  return text.replace(/([\\`*_[\]<>#|~])/g, "\\$1");
}

/** Inline code that survives backticks inside the value. */
function code(text: string): string {
  const fence = text.includes("`") ? "``" : "`";
  const pad = fence === "``" ? " " : "";
  return `${fence}${pad}${text}${pad}${fence}`;
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

/** Plumbing calls draw no step: loading a tool's schema, polling a question card. */
function isPlumbing(block: ToolBlock): boolean {
  if (isQuestionTool(block.name) && !block.question) return true;
  const name = block.name.replace(/^mcp__[^_]+__/, "");
  if (name === "ToolSearch") {
    const query = firstString(inputRecord(block), ["query"]);
    return query.startsWith("select:");
  }
  return false;
}

/** Families of Jarvis's own tools that have their own sentence. */
const FAMILY_SENTENCES = new Set([
  "wiki", "wiki_write", "artifact", "skill", "skill_create", "web", "screen", "screen_recall",
  "control", "navigate", "app", "memory", "memory_update", "profile", "contact", "call",
  "worker", "model", "mcp_admin", "settings", "verify",
]);

/** Argument keys that name what a coding action touched. */
const ACTION_DETAIL: Record<string, string[]> = {
  command: ["command", "cmd", "CommandLine", "commandLine", "script"],
  read: ["file_path", "path", "notebook_path", "filename"],
  edit: ["file_path", "path", "notebook_path"],
  write: ["file_path", "path", "filename"],
  list: ["path", "pattern", "directory", "dir"],
  search: ["pattern", "query", "regex", "q"],
};

/** What a step is about, for its sentence and for the overview's tally. */
interface StepSubject {
  /** Sentence key under trace_report.step.* (without the _plain suffix). */
  key: string;
  /** Detail already formatted for Markdown (code or escaped text); "" when none. */
  detail: string;
  /** Variables besides the detail ({tool}). */
  vars: Record<string, string>;
  /** Tally bucket for the overview: an action kind, or "used:<name>". */
  tally: string;
}

function subjectOf(block: ToolBlock, t: Translate): StepSubject {
  const view = traceToolIdentity(block);
  const record = inputRecord(block);
  // Memory tools speak through their Jarvis family ("Remembered …"), not as
  // a file action.
  if (view.action && view.action !== "memory") {
    const action = view.action;
    let raw = firstString(record, ACTION_DETAIL[action] ?? []);
    if (!raw && typeof block.input === "string") raw = block.input;
    if (!raw) raw = view.description.detail;
    // A command is one line in prose; the full text stays in the details.
    const firstLine = raw.split(/\r?\n/).find((line) => line.trim()) ?? "";
    const text = oneLine(firstLine, action === "command" ? 120 : 100);
    return { key: action, detail: text ? code(text) : "", vars: {}, tally: action };
  }
  // Jarvis's own tools reached through its MCP server
  // ("mcp__jarvis__create_artifact") are the tools of its own loop and read
  // the same way.
  const name = traceToolName(block.name);
  const own = /^jarvis\//i.test(name) ? describeToolStep(name.slice("jarvis/".length), block.input) : null;
  const description = own && (FAMILY_SENTENCES.has(own.family) || own.family === "shell") ? own : view.description;
  const family = description.family;
  if (family === "shell") {
    const text = oneLine(firstString(record, ACTION_DETAIL.command) || description.detail, 120);
    return { key: "command", detail: text ? code(text) : "", vars: {}, tally: "command" };
  }
  const detailText = oneLine(description.detail, 100);
  const detail = detailText ? escapeMarkdown(detailText) : "";
  if (FAMILY_SENTENCES.has(family)) {
    return { key: family, detail, vars: {}, tally: `used:${t(`${KEY}.subject.${family}`)}` };
  }
  if (view.integration || family === "mcp" || family === "service") {
    const label = view.description.label || view.service;
    return { key: "service", detail, vars: { tool: escapeMarkdown(label) }, tally: `used:${view.service || label}` };
  }
  // A registry name ("find-app-action") reads as a name: "Find App Action".
  const raw = view.description.label || block.name;
  const label = /[A-Z]/.test(raw) ? raw
    : raw.split(/[-_\s.]+/).filter(Boolean).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  return { key: "tool", detail, vars: { tool: escapeMarkdown(label) }, tally: `used:${label}` };
}

function sentenceOf(subject: StepSubject, t: Translate): string {
  const key = subject.detail ? `step.${subject.key}` : `step.${subject.key}_plain`;
  return tr(t, key, { ...subject.vars, detail: subject.detail });
}

// ── Results ──────────────────────────────────────────────────────────────

/** Keys whose string value says what a structured result was about. */
const GIST_KEYS = ["summary", "message", "result", "status", "title", "answer", "text", "detail", "description"];

/** Drop the "… (+N more chars)" cap marker the backend appends. */
function stripCapMarker(text: string): string {
  return text.replace(/…\s*\(\+\d+ more chars\)\s*$/, "").trimEnd();
}

/** Free text quoted the way the UI language quotes. */
function quote(t: Translate, text: string, max = 160): string {
  return tr(t, "quote", { text: escapeMarkdown(oneLine(text, max)) });
}

/** A sentence for structured output, or null when it is not structured. */
function structuredResult(output: string, t: Translate, lang: string): string | null {
  const text = output.trim();
  if (!text.startsWith("{") && !text.startsWith("[")) return null;
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { parsed = undefined; }
  if (Array.isArray(parsed)) {
    return parsed.length === 0 ? tr(t, "result.none")
      : tr(t, `result.items_${plural(lang, parsed.length)}`, { count: parsed.length });
  }
  if (parsed && typeof parsed === "object") {
    const record = parsed as Record<string, unknown>;
    for (const key of GIST_KEYS) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return tr(t, "result.text", { text: quote(t, value) });
    }
    for (const [key, value] of Object.entries(record)) {
      if (!Array.isArray(value)) continue;
      const what = escapeMarkdown(key.replace(/_/g, " "));
      return value.length === 0 ? tr(t, "result.named_none", { what })
        : tr(t, `result.named_items_${plural(lang, value.length)}`, { count: value.length, what });
    }
    const fields = Object.keys(record).slice(0, 4).map(code);
    if (fields.length) return tr(t, "result.fields", { fields: joinList(lang, fields) });
  }
  // Cut or Python-shaped: the leading key still tells what it was about.
  const list = /^\{\s*["']([\w-]+)["']\s*:\s*\[(\s*\])?/.exec(text);
  if (list) {
    const what = escapeMarkdown(list[1].replace(/_/g, " "));
    return list[2] ? tr(t, "result.named_none", { what }) : tr(t, "result.named_list", { what });
  }
  // Only the top level speaks for the whole: stop at the first nested value.
  const nested = text.slice(1).search(/[[{]/);
  const top = nested < 0 ? text : text.slice(0, nested + 1);
  const gist = new RegExp(`["'](?:${GIST_KEYS.join("|")})["']\\s*:\\s*["']([^"']{2,})["']`).exec(top);
  if (gist) return tr(t, "result.text", { text: quote(t, gist[1]) });
  return tr(t, "result.data");
}

/** What a wrapped command printed (stdout, else stderr), or null when not wrapped. */
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

function lineCount(text: string): number {
  return text.split(/\r?\n/).filter((line) => line.trim()).length;
}

/** Edits read as their size; everything else by what the output says. */
function successResult(block: ToolBlock, action: string | null, t: Translate, lang: string): string {
  const diff = toolDiff(block.name, block.input, block.output);
  if (diff) {
    let added = 0;
    let removed = 0;
    for (const file of diff) for (const line of file.lines) {
      if (line.kind === "add") added += 1;
      else if (line.kind === "del") removed += 1;
    }
    if (added || removed) return tr(t, "result.diff", { added, removed });
  }
  let output = stripCapMarker(block.output ?? "");
  // A command runner that wraps its output ({exit_code, stdout, stderr})
  // is told by what it printed.
  const printed = printedOutput(output);
  if (printed !== null) output = printed;
  if (!output.trim()) return "";
  if (action === "edit" || action === "write") return "";
  const structured = structuredResult(output, t, lang);
  if (structured) return structured;
  const lines = lineCount(output);
  if (action === "read") return tr(t, `result.read_${plural(lang, lines)}`, { count: lines });
  if (action === "search" || action === "list") {
    return tr(t, `result.found_${plural(lang, lines)}`, { count: lines });
  }
  const first = output.split(/\r?\n/).find((line) => line.trim()) ?? "";
  if (lines <= 1) return tr(t, "result.text", { text: quote(t, first, 200) });
  return tr(t, "result.lines", { count: lines, text: quote(t, first, 120) });
}

/** A refusal reason in plain words, when it is one of the system's own. */
function refusalReason(reason: string, t: Translate): string | null {
  const text = reason.trim();
  if (/^blacklist\b/i.test(text) || /tool-declared-block/i.test(text)) return tr(t, "reason.rule");
  if (/^plan mode/i.test(text)) return tr(t, "reason.plan_mode");
  if (/^cancelled\b/i.test(text)) return tr(t, "reason.cancelled");
  if (/^timeout$/i.test(text) || /approval timed out/i.test(text)) return tr(t, "reason.timeout");
  if (/approval[_ ]unavailable|no approval channel/i.test(text)) return tr(t, "reason.no_approver");
  if (/^(user|denied|deny|declined|rejected)\b/i.test(text)) return tr(t, "reason.declined");
  return null;
}

function resultOf(block: ToolBlock, status: TurnStatus, t: Translate, lang: string): { result: string; outcome: StepOutcome } {
  if (block.question) {
    const answers = block.question.answers.filter((answer) => answer && answer.text.trim());
    if (answers.length) {
      return { result: tr(t, "result.answered", { text: quote(t, answers.map((a) => a!.text).join(" · ")) }), outcome: "done" };
    }
    return block.question.closed ? { result: tr(t, "result.unanswered"), outcome: "interrupted" }
      : { result: tr(t, "result.waiting_answer"), outcome: "pending" };
  }
  if (block.approval && block.approval.decision === null) return { result: tr(t, "result.pending"), outcome: "pending" };
  if (block.approval?.decision === "deny") {
    const why = refusalReason(block.approval.summary || block.output || "", t);
    return why && why !== tr(t, "reason.declined")
      ? { result: tr(t, "result.refused", { reason: why }), outcome: "denied" }
      : { result: tr(t, "result.declined"), outcome: "declined" };
  }
  if (block.output === null) {
    return status === "running" ? { result: "", outcome: "pending" } : { result: tr(t, "result.interrupted"), outcome: "interrupted" };
  }
  if (block.isError) {
    const raw = stripCapMarker(block.output);
    const why = refusalReason(raw, t);
    if (why) return { result: tr(t, "result.refused", { reason: why }), outcome: "denied" };
    const first = raw.split(/\r?\n/).find((line) => line.trim()) ?? "";
    // The reason is the tool's own first line; it closes the sentence.
    const reason = escapeMarkdown(oneLine(first, 200));
    return first ? { result: tr(t, "result.failed", { reason: /[.!?…:]$/.test(reason) ? reason : `${reason}.` }), outcome: "failed" }
      : { result: tr(t, "result.failed_plain"), outcome: "failed" };
  }
  return { result: successResult(block, traceToolIdentity(block).action, t, lang), outcome: "done" };
}

// ── The narrative ────────────────────────────────────────────────────────

/** Tell a turn's work. `blocks` is the work only — the final reply stays out. */
export function narrateTurn(blocks: TurnBlock[], options: NarrateOptions): TraceNarrative {
  const { t, lang, status } = options;
  const steps: NarrativeStep[] = [];
  const tally = new Map<string, number>();
  let pendingWhy: string[] = [];
  let thinkingMs = 0;
  let firstNoteId = "";

  for (const block of blocks) {
    if (block.kind === "reasoning") {
      const text = block.text.trim();
      if (text) {
        if (!pendingWhy.length) firstNoteId = block.id;
        pendingWhy.push(text);
      } else thinkingMs += block.durationMs ?? 0;
      continue;
    }
    if (block.kind === "text") {
      const text = block.text.trim();
      if (text) {
        if (!pendingWhy.length) firstNoteId = block.id;
        pendingWhy.push(text);
      }
      continue;
    }
    if (isPlumbing(block)) continue;
    const subject = block.question
      ? { key: "question", detail: block.question.questions[0]?.question ? quote(t, block.question.questions[0].question, 200) : "", vars: {}, tally: "question" }
      : subjectOf(block, t);
    const { result, outcome } = resultOf(block, status, t, lang);
    tally.set(subject.tally, (tally.get(subject.tally) ?? 0) + 1);
    steps.push({
      id: block.callId,
      kind: "action",
      sentence: sentenceOf(subject, t),
      why: pendingWhy,
      result,
      outcome,
      durationMs: block.durationMs,
      block,
    });
    pendingWhy = [];
  }
  if (pendingWhy.length) {
    steps.push({ id: `note:${firstNoteId}`, kind: "note", sentence: "", why: pendingWhy, result: "", outcome: "done", durationMs: null, block: null });
  }

  const actions = steps.filter((step) => step.kind === "action");
  const problems = actions.filter((step) => step.outcome !== "done" && step.outcome !== "pending");
  return {
    overview: overviewOf({ actions, problems, tally, thinkingMs, options }),
    steps,
    actionCount: actions.length,
    problemCount: problems.length,
    thinkingMs,
  };
}

function overviewOf({ actions, problems, tally, thinkingMs, options }: {
  actions: NarrativeStep[]; problems: NarrativeStep[]; tally: Map<string, number>; thinkingMs: number; options: NarrateOptions;
}): string {
  const { t, lang, status, durationMs, model } = options;
  const parts: string[] = [];
  const count = actions.length;
  const duration = durationMs !== null && durationMs > 0 ? proseDuration(durationMs) : "";
  if (count > 0) {
    parts.push(duration ? tr(t, `lead_${plural(lang, count)}`, { duration, count })
      : tr(t, `lead_nodur_${plural(lang, count)}`, { count }));
    if (thinkingMs >= 1000) parts.push(tr(t, "thinking_share", { duration: proseDuration(thinkingMs) }));
  } else if (duration) {
    parts.push(tr(t, "lead_thinking", { duration }));
  }

  if (count >= 2) {
    const did: string[] = [];
    const used: string[] = [];
    for (const [bucket, n] of tally) {
      if (bucket.startsWith("used:")) used.push(escapeMarkdown(bucket.slice(5)));
      else did.push(tr(t, `did.${bucket}_${plural(lang, n)}`, { count: n }));
    }
    if (used.length) did.push(tr(t, "did.used", { list: joinList(lang, used) }));
    if (did.length) parts.push(tr(t, "did_sentence", { list: joinList(lang, did) }));
  }

  if (count > 0) {
    const by = (outcome: StepOutcome) => problems.filter((step) => step.outcome === outcome).length;
    const problemParts: string[] = [];
    for (const outcome of ["failed", "denied", "declined", "interrupted"] as const) {
      const n = by(outcome);
      if (n) problemParts.push(tr(t, `outcome.${outcome}_${plural(lang, n)}`, { count: n }));
    }
    if (problemParts.length) parts.push(...problemParts);
    else if (!actions.some((step) => step.outcome === "pending")) {
      parts.push(tr(t, count === 1 ? "outcome.ok_one" : "outcome.ok_other"));
    }
  }
  if (status === "cancelled") parts.push(tr(t, "turn_stopped"));
  else if (status === "error") parts.push(tr(t, "turn_failed"));
  if (model) parts.push(tr(t, "model", { model: escapeMarkdown(model) }));
  return parts.join(" ");
}

/** The report as Markdown — what "Copy" puts on the clipboard. */
export function narrativeMarkdown(narrative: TraceNarrative, t: Translate): string {
  const lines: string[] = [`### ${tr(t, "title")}`, "", narrative.overview];
  let n = 0;
  for (const step of narrative.steps) {
    lines.push("");
    if (step.kind === "note") {
      for (const why of step.why) lines.push(...why.split(/\r?\n/).map((line) => `> ${line}`));
      continue;
    }
    n += 1;
    for (const why of step.why) lines.push(...why.split(/\r?\n/).map((line) => `> ${line}`), "");
    const tail = step.durationMs !== null && step.durationMs > 0 ? ` (${proseDuration(step.durationMs)})` : "";
    lines.push(`${n}. ${step.sentence}${tail}${step.result ? ` ${step.result}` : ""}`);
  }
  return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}
