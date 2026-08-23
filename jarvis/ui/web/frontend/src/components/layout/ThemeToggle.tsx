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
        "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border bg-secondary/40 text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground",
        className,
      )}
    >
      <Icon aria-hidden className="h-3.5 w-3.5" />
    </button>
  );
}
