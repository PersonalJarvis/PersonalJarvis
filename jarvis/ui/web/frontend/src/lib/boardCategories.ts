import {
  Bot,
  BookOpen,
  Globe,
  Mail,
  Settings2,
  Users,
  type LucideIcon,
} from "lucide-react";

/**
 * Mirror of ``jarvis/board/categories.py`` ``BOARD_CATEGORY_KEYS``.
 *
 * These keys are a wire-format vocabulary that crosses Python -> Pydantic ->
 * TS -> UI label. Keep this array in lock-step with the Python source; a
 * parity test (``tests/board/test_categories_parity.py``) fails the build if
 * the two drift. Order is the canonical display order for empty/tie buckets.
 */
export const BOARD_CATEGORY_KEYS = [
  "agents",
  "browser",
  "mail",
  "community",
  "knowledge",
  "system",
] as const;

export type BoardCategoryKey = (typeof BOARD_CATEGORY_KEYS)[number];

/** Icon + accent color per category. Accent doubles as the usage-bar fill. */
export const CATEGORY_META: Record<
  BoardCategoryKey,
  { icon: LucideIcon; accent: string; bar: string; glow: string }
> = {
  agents: { icon: Bot, accent: "text-primary", bar: "bg-primary", glow: "shadow-[0_0_12px_-2px] shadow-primary/50" },
  browser: { icon: Globe, accent: "text-sky-400", bar: "bg-sky-400", glow: "" },
  mail: { icon: Mail, accent: "text-rose-400", bar: "bg-rose-400", glow: "" },
  community: { icon: Users, accent: "text-violet-400", bar: "bg-violet-400", glow: "" },
  knowledge: { icon: BookOpen, accent: "text-emerald-400", bar: "bg-emerald-400", glow: "" },
  system: { icon: Settings2, accent: "text-zinc-400", bar: "bg-zinc-400", glow: "" },
};

/** i18n key for a category's display label (``board_view.category.<key>``). */
export function categoryLabelKey(key: BoardCategoryKey): string {
  return `board_view.category.${key}`;
}
