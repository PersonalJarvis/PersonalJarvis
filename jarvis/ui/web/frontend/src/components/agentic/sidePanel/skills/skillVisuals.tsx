import type { CSSProperties } from "react";
import {
  BookOpen,
  Bug,
  ClipboardList,
  CodeXml,
  Database,
  FileText,
  FlaskConical,
  Gauge,
  GitPullRequest,
  ListChecks,
  Palette,
  Rocket,
  ScanSearch,
  ShieldCheck,
  SquareTerminal,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { SKILL_HUES, type SkillHue, type SkillIcon } from "@/lib/ideSkillsApi";

/**
 * How a skill looks: its hue and its glyph.
 *
 * A hue is a NAME the backend stores; here each name points at an existing
 * theme token, never a literal colour, so a skill reads in light and dark mode
 * alike and follows any later palette change. Everything that paints with the
 * hue reads it through one custom property, `--skill`, set by `skillStyle`.
 */
const HUE_TOKEN: Record<SkillHue, string> = {
  blue: "--accent",
  violet: "--viz-reasoning",
  teal: "--viz-terminal",
  amber: "--viz-file",
  rose: "--viz-agent",
  green: "--viz-result",
  magenta: "--viz-integration",
  slate: "--viz-tool",
};

export function isSkillHue(value: string): value is SkillHue {
  return (SKILL_HUES as readonly string[]).includes(value);
}

/** Sets `--skill` for everything inside to paint with this hue. */
export function skillStyle(hue: string): CSSProperties {
  const token = HUE_TOKEN[isSkillHue(hue) ? hue : "blue"];
  return { "--skill": `var(${token})` } as CSSProperties;
}

/**
 * Every glyph names a KIND OF WORK — plan, review, test, refactor — never a
 * mood. No sparkles, wands or brains: a skill is a Markdown document the user
 * wrote, and its mark says what the document is for. The fallback is that
 * document itself.
 */
export const SKILL_ICON: Record<Exclude<SkillIcon, "auto">, LucideIcon> = {
  doc: FileText,
  plan: ClipboardList,
  code: CodeXml,
  bug: Bug,
  flask: FlaskConical,
  review: ScanSearch,
  shield: ShieldCheck,
  refactor: Wrench,
  book: BookOpen,
  list: ListChecks,
  git: GitPullRequest,
  terminal: SquareTerminal,
  palette: Palette,
  data: Database,
  perf: Gauge,
  rocket: Rocket,
};

/** Keywords that pick a glyph for a skill left on "auto", first match wins. */
const AUTO_RULES: readonly [RegExp, Exclude<SkillIcon, "auto">][] = [
  [/\b(bug|fix|debug|error|crash|trace)/i, "bug"],
  [/\b(test|spec|qa|tdd|coverage)/i, "flask"],
  [/\b(secur|auth|vulnerab|secret|permission)/i, "shield"],
  [/\b(review|audit|check|lint|verify|inspect)/i, "review"],
  [/\b(git|commit|branch|merge|pull request|pr)\b/i, "git"],
  [/\b(perf|speed|fast|slow|latency|optimi[sz])/i, "perf"],
  [/\b(sql|database|schema|migration|query|data)\b/i, "data"],
  [/\b(design|ui|ux|style|css|colou?r|layout|visual)/i, "palette"],
  [/\b(deploy|release|ship|launch|publish)/i, "rocket"],
  [/\b(refactor|clean|simplif|tidy|rename)/i, "refactor"],
  [/\b(plan|think|architect|brainstorm|idea|strategy|spec)/i, "plan"],
  [/\b(shell|cli|terminal|bash|command|script)/i, "terminal"],
  [/\b(todo|checklist|steps|list|workflow)/i, "list"],
  [/\b(docs?|readme|explain|blog|guide|tutorial)\b/i, "book"],
  [/\b(code|function|api|implement|typescript|python)/i, "code"],
];

/** The glyph a skill wears: its own choice, else one its words suggest. */
export function skillIconFor(icon: string, title: string, description = ""): LucideIcon {
  if (icon !== "auto" && icon in SKILL_ICON) return SKILL_ICON[icon as Exclude<SkillIcon, "auto">];
  // The title names the job; the description only breaks a tie the title left open.
  const match =
    AUTO_RULES.find(([pattern]) => pattern.test(title)) ??
    AUTO_RULES.find(([pattern]) => pattern.test(description));
  return SKILL_ICON[match ? match[1] : "doc"];
}

/**
 * The skill's mark: its glyph in its hue on a flat tile.
 *
 * Deliberately plain — one quiet wash of the hue and a hairline rim, no
 * gradient, no glow: the colour tells skills apart, the glyph says what each
 * one is for, and nothing else competes with the title beside it.
 */
export function SkillSeal({ hue, icon, title, description, size = "md", className }: {
  hue: string;
  icon: string;
  title: string;
  description?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const Icon = skillIconFor(icon, title, description);
  const box = size === "lg" ? "h-11 w-11 rounded-lg" : size === "sm" ? "h-7 w-7 rounded-md" : "h-9 w-9 rounded-lg";
  const glyph = size === "lg" ? "h-5 w-5" : size === "sm" ? "h-3.5 w-3.5" : "h-[18px] w-[18px]";
  return (
    <span
      aria-hidden
      style={skillStyle(hue)}
      className={cn(
        "inline-flex shrink-0 items-center justify-center text-[hsl(var(--skill))]",
        "bg-[hsl(var(--skill)/0.10)] ring-1 ring-inset ring-[hsl(var(--skill)/0.22)]",
        box,
        className,
      )}
    >
      <Icon className={glyph} strokeWidth={1.75} />
    </span>
  );
}

/** Rough token count for a text: about four characters per token. */
export function approxTokens(text: string): number {
  return Math.max(1, Math.round(text.length / 4));
}

export function lineCount(text: string): number {
  return text ? text.split("\n").length : 0;
}

/** "1.2k" for anything past a thousand, so the meta row keeps its width. */
export function compactCount(value: number): string {
  if (value < 1000) return String(value);
  const thousands = value / 1000;
  return `${thousands >= 10 ? Math.round(thousands) : thousands.toFixed(1).replace(/\.0$/, "")}k`;
}

/** The body of a Markdown text without its YAML frontmatter, and the frontmatter's fields. */
export function splitSkillFrontmatter(content: string): { fields: [string, string][]; body: string } {
  // A byte-order mark in front of the block (a file saved by Notepad) is not text.
  const text = content.charCodeAt(0) === 0xfeff ? content.slice(1) : content;
  const match = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(text);
  if (!match) return { fields: [], body: text };
  const fields: [string, string][] = [];
  for (const line of match[1].split(/\r?\n/)) {
    const field = /^([A-Za-z][\w-]*)\s*:\s*(.*)$/.exec(line);
    if (field && field[2].trim() && !/^[>|]-?$/.test(field[2].trim())) {
      fields.push([field[1], field[2].trim().replace(/^["']|["']$/g, "")]);
    }
  }
  return { fields, body: text.slice(match[0].length) };
}
