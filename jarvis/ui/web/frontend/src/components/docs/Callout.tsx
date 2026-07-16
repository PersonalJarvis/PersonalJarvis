import type { ReactNode } from "react";
import { Info, AlertTriangle, Lightbulb, FileText } from "lucide-react";

import { cn } from "@/lib/utils";

export type CalloutType = "info" | "warning" | "tip" | "note";

const VARIANTS: Record<CalloutType, { className: string; icon: typeof Info }> = {
  info: {
    className:
      "border-l-blue-500/60 bg-blue-500/10 text-blue-100",
    icon: Info,
  },
  warning: {
    className:
      "border-l-amber-500/60 bg-amber-500/10 text-amber-100",
    icon: AlertTriangle,
  },
  tip: {
    className:
      "border-l-emerald-500/60 bg-emerald-500/10 text-emerald-100",
    icon: Lightbulb,
  },
  note: {
    className:
      "border-l-slate-500/60 bg-slate-500/10 text-slate-100",
    icon: FileText,
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
 * `> [!note]` as the first tag inside a blockquote. A standard Markdown
 * blockquote without a tag stays a default blockquote (the note variant).
 */
export function Callout({ type = "note", children }: Props) {
  const variant = VARIANTS[type];
  const Icon = variant.icon;
  return (
    <aside
      className={cn(
        "not-prose my-4 flex gap-3 rounded-md border-l-4 px-4 py-3 text-sm",
        variant.className,
      )}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="flex-1 [&>p]:m-0 [&>p+p]:mt-2">{children}</div>
    </aside>
  );
}

/**
 * Heuristic: looks at the first text node of a blockquote. If it
 * starts with ``[!info]``, ``[!warning]``, ``[!tip]``, or ``[!note]``
 * (GitHub Markdown convention), returns the type + the rest as children.
 *
 * No match -> ``null``, the renderer falls back to a normal blockquote.
 */
export function parseCalloutTag(
  text: string,
): { type: CalloutType; rest: string } | null {
  const m = text.match(/^\s*\[!(info|warning|tip|note)\]\s*/i);
  if (!m) return null;
  return {
    type: m[1].toLowerCase() as CalloutType,
    rest: text.slice(m[0].length),
  };
}
