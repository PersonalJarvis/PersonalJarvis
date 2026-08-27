/**
 * Tool call → how the agent chat draws its row: an icon (or the vendor mark
 * for an MCP server), the name the way the agent's own log spells it, and a
 * one-line gist of the input.
 *
 * The name is NOT translated or renamed — a row that says "PowerShell" when
 * the agent's transcript says "PowerShell" is the whole point (maintainer,
 * 2026-08-23: the row has to match the log). What this module adds is the
 * right mark per tool: Claude Code's and Codex's tool names first, then MCP
 * calls ("mcp__server__tool" / "server/tool") with the server's brand,
 * then the Jarvis tool families of the API runner, then a plain wrench.
 *
 * Pure — no React — so the mapping is unit-testable and identical wherever
 * a tool row is drawn.
 */

import {
  Bot,
  FileDiff,
  FilePen,
  FileText,
  FolderSearch,
  Globe,
  ListChecks,
  Map as MapIcon,
  MessageCircleQuestion,
  PackageSearch,
  Plug,
  Search,
  TerminalSquare,
  Wand2,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { describeToolStep } from "@/lib/toolStepLabel";

export interface AgentToolView {
  /** The name as the log spells it (MCP: "server · tool"). */
  label: string;
  Icon: LucideIcon;
  /** A vendor SVG for the tile when the call belongs to a known MCP server. */
  logoUrl?: string;
  /** One-line gist of the input: the command, the path, the pattern, the query. */
  summary: string;
  /** Coarse family for styling hooks and tests. */
  family: "shell" | "edit" | "write" | "read" | "search" | "web" | "agent" | "plan" | "ask" | "skill" | "tools" | "mcp" | "jarvis" | "other";
}

/**
 * Claude Code, Codex, Grok Build, OpenCode, Kimi and agy tool names — the
 * agent's own vocabulary, one regex per family. A name none of them match
 * still gets its row; it just wears the plain wrench.
 */
const CLI_TOOLS: Array<[RegExp, AgentToolView["family"], LucideIcon]> = [
  [/^(bash|powershell|shell|run_?command|command|exec|terminal|cmd)$/i, "shell", TerminalSquare],
  [
    /^(edit|multi_?edit|apply_?patch|str_replace(_based_edit_tool)?|notebook_?edit|search_?replace|(multi_?)?replace_?file_?content|edit_?file)$/i,
    "edit",
    FileDiff,
  ],
  [/^(write|create_?file|write_?file|write_?to_?file)$/i, "write", FilePen],
  [/^(read|cat|view|open_?file|read_?file|view_?file|read_?media_?file|view_?code_?item)$/i, "read", FileText],
  [/^(glob|ls|list_?dir(ectory)?|find_?by_?name)$/i, "search", FolderSearch],
  [/^(grep|search|find|rg|code_?search|grep_?search|search_?web)$/i, "search", Search],
  [/^tool_?search$/i, "tools", PackageSearch],
  [/^(web_?fetch|web_?search|fetch|browse|http)$/i, "web", Globe],
  [/^(agent|task|sub_?agent|spawn(_?agents?)?|delegate)$/i, "agent", Bot],
  [/^(todo_?write|todo_?read|update_?plan|plan)$/i, "plan", ListChecks],
  [/^(enter|exit)_?plan_?mode$/i, "plan", MapIcon],
  [/^(ask_?user(_?question)?|question)$/i, "ask", MessageCircleQuestion],
  [/^skill$/i, "skill", Wand2],
];

/**
 * Which argument IS the call, per family.
 *
 * The order matters more than it looks: a Grep carries both a pattern and a
 * path, and a row that shows the path says nothing about what was searched
 * for — next to the agent's own log it reads as the wrong call
 * (maintainer, 2026-08-23). So each family names its own headline field
 * first and falls through to the generic list after.
 */
const SUMMARY_KEYS: Record<string, string[]> = {
  shell: ["command", "CommandLine", "cmd", "script"],
  search: ["pattern", "Pattern", "query", "Query", "glob", "path", "target_directory", "SearchDirectory", "file_path"],
  read: ["file_path", "path", "target_file", "filePath", "AbsolutePath", "absolute_path", "notebook_path", "url"],
  write: ["file_path", "path", "target_file", "filePath", "TargetFile", "AbsolutePath"],
  edit: ["file_path", "path", "target_file", "filePath", "TargetFile", "AbsolutePath", "notebook_path"],
  web: ["url", "query", "prompt"],
  agent: ["description", "prompt", "task"],
  plan: ["description", "plan", "todos"],
  ask: ["question", "prompt", "description"],
  skill: ["skill", "command", "name"],
  tools: ["query"],
};

const GENERIC_SUMMARY_KEYS = [
  "command",
  "CommandLine",
  "cmd",
  "pattern",
  "Pattern",
  "query",
  "Query",
  "file_path",
  "path",
  "target_file",
  "filePath",
  "AbsolutePath",
  "url",
  "prompt",
  "description",
  "skill",
  "title",
  "name",
];

/**
 * A one-line gist of a tool's input: the command, the pattern, the path.
 *
 * ``family`` picks the headline field for that kind of call; without one the
 * generic order applies. Anything else falls back to the first readable
 * string in the arguments, so a row is never blank when there was an input.
 */
export function inputSummary(input: unknown, family?: string): string {
  if (input === null || input === undefined) return "";
  if (typeof input === "string") return firstLine(input);
  if (typeof input !== "object") return String(input);
  const obj = input as Record<string, unknown>;
  const keys = [...(family ? (SUMMARY_KEYS[family] ?? []) : []), ...GENERIC_SUMMARY_KEYS];
  for (const key of keys) {
    const v = obj[key];
    if (typeof v === "string" && v.trim()) return shortenPath(firstLine(v));
  }
  const first = Object.values(obj).find((v) => typeof v === "string" && v.trim());
  return typeof first === "string" ? firstLine(first) : "";
}

/**
 * An absolute path, shortened to the part that differs between rows.
 *
 * Every file in a workspace shares its first six or seven segments, so a row
 * showing the whole of it spends its width saying where the project is and
 * runs out before saying which file (live check, 2026-08-27). The last three
 * segments are what a person reads anyway, and the leading ellipsis says
 * plainly that something was cut.
 *
 * Windows separators become forward slashes on the way: one shape for a path
 * beats two, and every agent in the column writes both.
 */
export function shortenPath(value: string): string {
  const flat = value.replace(/\\/g, "/");
  // Not a path: a sentence with a slash in it, a URL, a bare name.
  if (!/^([A-Za-z]:\/|\/|\.{1,2}\/)/.test(flat) || /\s{2,}/.test(flat)) return value;
  const parts = flat.split("/").filter(Boolean);
  if (parts.length <= 3) return flat;
  return `…/${parts.slice(-3).join("/")}`;
}

function firstLine(text: string): string {
  const line = text.trim().split(/\r?\n/, 1)[0] ?? "";
  return line.length > 200 ? `${line.slice(0, 199)}…` : line;
}

function humanise(token: string): string {
  return token
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** "mcp__github__create_issue" → ["github", "create_issue"]; "github/create_issue" → same. */
function splitMcp(name: string): [string, string] | null {
  const m = /^mcp__(.+?)__(.+)$/.exec(name);
  if (m) return [m[1], m[2]];
  const slash = name.indexOf("/");
  if (slash > 0 && slash < name.length - 1) return [name.slice(0, slash), name.slice(slash + 1)];
  return null;
}

export function agentToolView(name: string, input: unknown): AgentToolView {
  const raw = (name ?? "").trim() || "tool";

  for (const [re, family, Icon] of CLI_TOOLS) {
    if (re.test(raw)) return { label: raw, Icon, summary: inputSummary(input, family), family };
  }
  const summary = inputSummary(input);

  const mcp = splitMcp(raw);
  if (mcp) {
    const [server, tool] = mcp;
    const view = describeToolStep(`${server}/${tool}`, input);
    return {
      label: view.label || `${humanise(server)} · ${humanise(tool)}`,
      Icon: Plug,
      logoUrl: view.brand?.logoUrl,
      summary,
      family: "mcp",
    };
  }

  // The API runner's own tools and anything Jarvis-shaped (wiki-recall,
  // create_artifact, …) keep their log name but borrow the family's mark.
  const jarvis = describeToolStep(raw, input);
  if (jarvis.family !== "other") {
    return {
      label: raw,
      Icon: jarvis.brand?.logoUrl ? Plug : Wrench,
      logoUrl: jarvis.brand?.logoUrl,
      summary: summary || jarvis.detail,
      family: "jarvis",
    };
  }
  return { label: raw, Icon: Wrench, summary, family: "other" };
}

/** "4800" → "4.8k", "1200000" → "1.2M"; small numbers stay as they are. */
export function formatTokens(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "0";
  if (n < 1000) return String(Math.round(n));
  if (n < 1_000_000) return `${trimZero((n / 1000).toFixed(1))}k`;
  return `${trimZero((n / 1_000_000).toFixed(1))}M`;
}

function trimZero(s: string): string {
  return s.endsWith(".0") ? s.slice(0, -2) : s;
}

/**
 * Output tokens so far, from whichever usage shape the runner reported.
 *
 * The only token figure the chat column shows (maintainer, 2026-08-25). The
 * input side of a coding CLI re-counts the whole conversation on every step,
 * so it grows into a number that says nothing about the question that was
 * asked — "283.2k" for one line of chat (BUG-173). What the turn produced is
 * a real, monotonic count, honest live and honest on the receipt.
 */
export function outputTokens(usage: Record<string, unknown> | null | undefined): number | null {
  if (!usage) return null;
  const v = usage.output_tokens ?? usage.output ?? usage.completion_tokens;
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
