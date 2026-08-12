/**
 * The Jarvis node design system — one vocabulary for every card on the run
 * canvas, the way n8n gives every node family its own colour and glyph.
 *
 * A node's CATEGORY answers "what sort of thing happened here" before the
 * title is read: a trigger, a thought, a shell command, a file edit, a
 * search, a web call, an integration (MCP), a sub-agent launch, the result,
 * or a deliverable. Each category owns exactly one icon and one colour
 * token, defined here and nowhere else — a new tool name gets classified in
 * THIS file or it renders as the neutral "tool" fallback, never as a
 * fresh ad-hoc style.
 *
 * Colours are `--viz-*` theme tokens (index.css defines both the light and
 * the dark value), consumed as `hsl(var(--viz-x))` — never a literal hex,
 * per the app-wide theming rule. The view builds its inline styles through
 * `categoryColor`/`categoryTint` so the alpha maths also lives in one place.
 */

import {
  Brain,
  FileCode,
  FileImage,
  FileText,
  Flag,
  Globe,
  MessageSquare,
  Plug,
  Search,
  SquarePen,
  Split,
  Terminal,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import type { RunGraphNode } from "@/lib/runGraph";
import { classifyVisual } from "@/hooks/useVisualArtifacts";

export type NodeCategory =
  | "trigger"
  | "reasoning"
  | "terminal"
  | "file"
  | "search"
  | "web"
  | "integration"
  | "agent"
  | "tool"
  | "result"
  | "deliverable";

export interface NodeCategorySpec {
  /** The card glyph — one per category, like n8n's if-node branch sign. */
  icon: LucideIcon;
  /** The `--viz-*` custom property carrying the category hue (both themes). */
  cssVar: string;
  /** i18n key of the little uppercase kicker on the card. */
  labelKey: string;
}

export const NODE_CATEGORIES: Record<NodeCategory, NodeCategorySpec> = {
  trigger: {
    icon: MessageSquare,
    cssVar: "--viz-trigger",
    labelKey: "visualization.cat_trigger",
  },
  reasoning: {
    icon: Brain,
    cssVar: "--viz-reasoning",
    labelKey: "visualization.cat_reasoning",
  },
  terminal: {
    icon: Terminal,
    cssVar: "--viz-terminal",
    labelKey: "visualization.cat_terminal",
  },
  file: {
    icon: SquarePen,
    cssVar: "--viz-file",
    labelKey: "visualization.cat_file",
  },
  search: {
    icon: Search,
    cssVar: "--viz-search",
    labelKey: "visualization.cat_search",
  },
  web: {
    icon: Globe,
    cssVar: "--viz-web",
    labelKey: "visualization.cat_web",
  },
  integration: {
    icon: Plug,
    cssVar: "--viz-integration",
    labelKey: "visualization.cat_integration",
  },
  agent: {
    icon: Split,
    cssVar: "--viz-agent",
    labelKey: "visualization.cat_agent",
  },
  tool: {
    icon: Wrench,
    cssVar: "--viz-tool",
    labelKey: "visualization.cat_tool",
  },
  result: {
    icon: Flag,
    cssVar: "--viz-result",
    labelKey: "visualization.cat_result",
  },
  deliverable: {
    icon: FileText,
    cssVar: "--viz-deliverable",
    labelKey: "visualization.cat_deliverable",
  },
};

/* Tool-name families. Lowercased membership, mirrored from what the archived
 * streams of every provider actually contain (claude tools, codex synthetics,
 * generic snake_case fallbacks). */
const TERMINAL_TOOLS = new Set([
  "bash",
  "powershell",
  "runcommand",
  "shell",
  "exec",
  "execute",
  "run_command",
  "run_shell_command",
]);
const FILE_TOOLS = new Set([
  "write",
  "edit",
  "multiedit",
  "notebookedit",
  "file_write",
  "write_file",
  "create_file",
  "replace", // gemini-cli's edit tool
]);
const SEARCH_TOOLS = new Set([
  "read",
  "glob",
  "grep",
  "search",
  "ls",
  "list_files",
  // gemini-cli's read/search family
  "read_file",
  "read_many_files",
  "search_file_content",
  "list_directory",
]);

/** Which category a tool call belongs to — the n8n-style family sort. */
export function toolCategory(toolName: string | null | undefined): NodeCategory {
  const name = (toolName ?? "").toLowerCase();
  if (name.startsWith("mcp__") || name.includes("/")) return "integration";
  if (TERMINAL_TOOLS.has(name)) return "terminal";
  if (FILE_TOOLS.has(name)) return "file";
  if (SEARCH_TOOLS.has(name)) return "search";
  if (name.includes("web") || name.includes("fetch")) return "web";
  return "tool";
}

/** The category of a laid-out graph node. */
export function nodeCategory(node: RunGraphNode): NodeCategory {
  if (node.kind === "start") return "trigger";
  if (node.kind === "result") return "result";
  if (node.kind === "artifact") return "deliverable";
  if (node.step?.kind === "reasoning") return "reasoning";
  if (node.step?.kind === "spawn") return "agent";
  return toolCategory(node.step?.tool_name);
}

/** The glyph for a node — deliverables refine by what the file IS. */
export function nodeGlyph(node: RunGraphNode): LucideIcon {
  if (node.kind === "artifact") {
    const kind = classifyVisual(node.artifact?.path ?? "");
    if (kind === "image" || kind === "vector") return FileImage;
    if (kind === "page" || kind === "document") return FileCode;
    return FileText;
  }
  return NODE_CATEGORIES[nodeCategory(node)].icon;
}

/** The category hue as a CSS colour value (theme-following). */
export function categoryColor(category: NodeCategory): string {
  return `hsl(var(${NODE_CATEGORIES[category].cssVar}))`;
}

/** The same hue as a translucent tint — icon-tile grounds, hover washes. */
export function categoryTint(category: NodeCategory, alpha: number): string {
  return `hsl(var(${NODE_CATEGORIES[category].cssVar}) / ${alpha})`;
}
