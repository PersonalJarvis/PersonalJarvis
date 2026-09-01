import { Moon, Sun } from "lucide-react";

import { useOptionalTheme } from "@/hooks/useTheme";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * Light ↔ dark, one click, from the app chrome.
 *
 * The full preference (including "follow the system") stays in Settings; this
 * is the quick flip the maintainer asked for in the top bar (2026-08-23).
 * Flipping from "system" resolves to the concrete opposite of what is on
 * screen, so the button always does the visible thing.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const t = useT();
  // Outside the provider (an isolated mount) there is nothing to flip: the
  // rest of the actions keep rendering, this one steps aside.
  const ctx = useOptionalTheme();
  if (!ctx) return null;
  const { theme, toggle } = ctx;
  const dark = theme === "dark";
  const label = dark ? t("home.theme_to_light") : t("home.theme_to_dark");
  const Icon = dark ? Sun : Moon;
  return (
    <button
      type="button"
      onClick={toggle}
      title={label}
      aria-label={label}
      data-testid="theme-toggle"
      className={cn(
        // Same recipe as the rest of the bar: no fill at rest, one step up
        // under the pointer. The old version answered a hover by recolouring
        // its BORDER, which is a rim doing a fill's job.
        "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
        "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
        className,
      )}
    >
      <Icon aria-hidden className="h-4 w-4" />
    </button>
  );
}
