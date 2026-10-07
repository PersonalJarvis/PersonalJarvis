import type { ReactNode } from "react";
import { AlertTriangle, Info, Lightbulb, NotebookPen } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

export type CalloutType = "info" | "warning" | "tip" | "note";

/*
 * Every variant is built from theme tokens, so it reads in light and dark
 * alike: a hairline in the status hue, a faint wash of the same hue, and the
 * hue on the icon and label only. Body text stays at body ink.
 */
const VARIANTS: Record<
  CalloutType,
  { frame: string; tone: string; icon: typeof Info; label: string }
> = {
  note: {
    frame: "border-border bg-secondary/40",
    tone: "text-foreground-secondary",
    icon: NotebookPen,
    label: "docs_content.callout_note",
  },
  info: {
    frame: "border-accent/30 bg-accent-soft",
    tone: "text-accent",
    icon: Info,
    label: "docs_content.callout_info",
  },
  tip: {
    frame: "border-success/30 bg-success/[0.06]",
    tone: "text-success",
    icon: Lightbulb,
    label: "docs_content.callout_tip",
  },
  warning: {
    frame: "border-warning/35 bg-warning/[0.07]",
    tone: "text-warning",
    icon: AlertTriangle,
    label: "docs_content.callout_warning",
  },
};

interface Props {
  type?: CalloutType;
  children: ReactNode;
}

/**
 * Admonition block for info/warning/tip/note.
 *
 * Activation: in Markdown via `> [!info]`, `> [!warning]`, `> [!tip]`,
 * `> [!note]` (and GitHub's `[!important]` / `[!caution]`) as the first tag
 * inside a blockquote. A blockquote without a tag stays a plain blockquote.
 */
export function Callout({ type = "note", children }: Props) {
  const t = useT();
  const variant = VARIANTS[type];
  const Icon = variant.icon;
  return (
    <aside
      className={cn(
        "not-prose my-6 rounded-lg border px-4 py-3.5",
        variant.frame,
      )}
    >
      <div
        className={cn(
          "mb-1 flex items-center gap-2 text-sm font-semibold",
          variant.tone,
        )}
      >
        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
        {t(variant.label)}
      </div>
      <div className="text-base leading-6 text-foreground-secondary [&_a]:text-accent [&_a]:underline [&_a]:decoration-accent/40 [&_a]:underline-offset-2 [&_code]:rounded-sm [&_code]:bg-secondary [&_code]:px-1 [&_code]:font-mono [&_code]:text-sm [&_strong]:font-semibold [&_strong]:text-foreground-strong [&>p]:m-0 [&>p+p]:mt-2">
        {children}
      </div>
    </aside>
  );
}

const TAG_ALIASES: Record<string, CalloutType> = {
  info: "info",
  important: "info",
  warning: "warning",
  caution: "warning",
  tip: "tip",
  note: "note",
};

/**
 * Heuristic: looks at the first text node of a blockquote. If it starts
 * with a GitHub-style tag such as ``[!info]`` or ``[!warning]``, returns the
 * type + the rest as children.
 *
 * No match -> ``null``, the renderer falls back to a normal blockquote.
 */
export function parseCalloutTag(
  text: string,
): { type: CalloutType; rest: string } | null {
  const m = text.match(/^\s*\[!(info|important|warning|caution|tip|note)\]\s*/i);
  if (!m) return null;
  return {
    type: TAG_ALIASES[m[1].toLowerCase()],
    rest: text.slice(m[0].length),
  };
}
