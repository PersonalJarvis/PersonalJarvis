import type { CSSProperties } from "react";
import {
  BookOpen,
  Brain,
  Bug,
  Code2,
  FlaskConical,
  GitBranch,
  ListChecks,
  Palette,
  Rocket,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  WandSparkles,
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

export const SKILL_ICON: Record<Exclude<SkillIcon, "auto">, LucideIcon> = {
  sparkles: Sparkles,
  code: Code2,
  bug: Bug,
  book: BookOpen,
  shield: ShieldCheck,
  rocket: Rocket,
  wand: WandSparkles,
  brain: Brain,
  flask: FlaskConical,
  palette: Palette,
  terminal: SquareTerminal,
  git: GitBranch,
  list: ListChecks,
};

/** Keywords that pick a glyph for a skill left on "auto", first match wins. */
const AUTO_RULES: readonly [RegExp, Exclude<SkillIcon, "auto">][] = [
  [/\b(bug|fix|debug|error|crash|trace)/i, "bug"],
  [/\b(test|spec|qa|tdd|coverage)/i, "flask"],
  [/\b(review|audit|secur|check|lint|verify)/i, "shield"],
  [/\b(git|commit|branch|merge|pull request|pr)\b/i, "git"],
  [/\b(design|ui|ux|style|css|colou?r|layout|visual)/i, "palette"],
  [/\b(deploy|release|ship|launch|publish)/i, "rocket"],
  [/\b(refactor|clean|simplif|tidy|rename)/i, "wand"],
  [/\b(plan|think|architect|brainstorm|idea|strategy)/i, "brain"],
  [/\b(shell|cli|terminal|bash|command|script)/i, "terminal"],
  [/\b(todo|checklist|steps|list|workflow)/i, "list"],
  [/\b(doc|readme|write|explain|blog|guide|prompt)/i, "book"],
  [/\b(code|function|api|implement|typescript|python)/i, "code"],
];

/** The glyph a skill wears: its own choice, else one its words suggest. */
export function skillIconFor(icon: string, title: string, description = ""): LucideIcon {
  if (icon !== "auto" && icon in SKILL_ICON) return SKILL_ICON[icon as Exclude<SkillIcon, "auto">];
  const words = `${title} ${description}`;
  const match = AUTO_RULES.find(([pattern]) => pattern.test(words));
  return SKILL_ICON[match ? match[1] : "sparkles"];
}

/**
 * The skill's seal: its glyph on a lit tile of its hue.
 *
 * Two soft layers of the hue (a wash and a brighter corner) with a hairline
 * rim of the same hue, so the tile reads as made of the colour rather than
 * painted on — and stays legible at 4 % dark and on white.
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
  const box = size === "lg" ? "h-12 w-12 rounded-xl" : size === "sm" ? "h-7 w-7 rounded-lg" : "h-9 w-9 rounded-[10px]";
  const glyph = size === "lg" ? "h-6 w-6" : size === "sm" ? "h-3.5 w-3.5" : "h-[18px] w-[18px]";
  return (
    <span
      aria-hidden
      style={skillStyle(hue)}
      className={cn(
        "relative inline-flex shrink-0 items-center justify-center overflow-hidden text-[hsl(var(--skill))]",
        "bg-[linear-gradient(135deg,hsl(var(--skill)/0.26),hsl(var(--skill)/0.08))]",
        "shadow-[inset_0_0_0_1px_hsl(var(--skill)/0.32),inset_0_1px_0_0_hsl(var(--skill)/0.25)]",
        box,
        className,
      )}
    >
      <span className="pointer-events-none absolute -right-2 -top-2 h-2/3 w-2/3 rounded-full bg-[hsl(var(--skill)/0.28)] blur-md" />
      <Icon className={cn("relative", glyph)} strokeWidth={1.9} />
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
