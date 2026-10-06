import {
  BookOpen,
  Blocks,
  FileCode2,
  FileText,
  MessageSquare,
  Rocket,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";

import { isMacClient } from "@/lib/embeddedDesktop";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/** Where the Markdown sources of the bundled docs live publicly. */
export const DOCS_REPO_URL = "https://github.com/PersonalJarvis/PersonalJarvis";
export const ONLINE_DOCS_URL = `${DOCS_REPO_URL}/tree/main/docs`;

/** Public URL of one guide's source file (``path`` is repo-relative). */
export function docSourceUrl(path: string): string {
  return `${DOCS_REPO_URL}/blob/main/${path.replace(/^\/+/, "")}`;
}

/**
 * One quiet glyph per topic. Section names come from the guides' front
 * matter, so the lookup is by keyword and any unknown section falls back to a
 * plain page glyph instead of failing.
 */
const SECTION_ICONS: Array<[RegExp, LucideIcon]> = [
  [/start/i, Rocket],
  [/everyday|use/i, MessageSquare],
  [/personali[sz]e|connect/i, SlidersHorizontal],
  [/knowledge|shar/i, BookOpen],
  [/extend|automat/i, Blocks],
  [/privacy|safety|support/i, ShieldCheck],
  [/reference/i, FileCode2],
];

export function sectionIcon(name: string): LucideIcon {
  for (const [pattern, icon] of SECTION_ICONS) {
    if (pattern.test(name)) return icon;
  }
  return FileText;
}

/** The platform's own name for the search chord. */
export function searchShortcutLabel(): string {
  const mac =
    typeof navigator !== "undefined" && isMacClient(navigator.userAgent);
  return mac ? "⌘K" : "Ctrl K";
}

/** Rounded-up reading time at a calm 220 words per minute. */
export function readingMinutes(markdown: string): number {
  const words = markdown
    .replace(/```[\s\S]*?```/g, " ")
    .split(/\s+/)
    .filter(Boolean).length;
  return Math.max(1, Math.ceil(words / 220));
}

/**
 * The search trigger: reads as a field, opens the full-text search dialog.
 * ``size="lg"`` is the hero variant on the docs home.
 */
export function SearchTrigger({
  onClick,
  size = "md",
  className,
}: {
  onClick: () => void;
  size?: "md" | "lg";
  className?: string;
}) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={t("docs.search_modal_title")}
      className={cn(
        "group flex w-full items-center gap-2.5 rounded-md border border-border bg-input text-left text-muted-foreground transition-colors",
        "hover:border-border-strong hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        size === "lg" ? "h-11 px-4 text-lg" : "h-9 px-3 text-base",
        className,
      )}
    >
      <Search
        className={cn("shrink-0", size === "lg" ? "h-4 w-4" : "h-3.5 w-3.5")}
        aria-hidden="true"
      />
      <span className="flex-1 truncate">
        {size === "lg" ? t("docs.search_hero") : t("docs.search_button")}
      </span>
      <kbd className="shrink-0 rounded-sm border border-border px-1.5 py-0.5 font-sans text-xs font-medium text-foreground-faint">
        {searchShortcutLabel()}
      </kbd>
    </button>
  );
}
