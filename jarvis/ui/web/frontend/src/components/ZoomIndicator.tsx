/**
 * The bubble that names the zoom level after a zoom step, the way Chrome shows
 * one under its address bar: the percentage, a minus and a plus, and a way
 * back to 100 %.
 *
 * It hides itself after a moment, but not while the pointer rests on it or a
 * keyboard user is inside it — a bubble that vanishes under the cursor on the
 * way to its own buttons is worse than none. Every further step (keys or its
 * own buttons) restarts the timer.
 *
 * Colours come from theme tokens only, so it reads in light and dark.
 */
import { useEffect, useRef, useState } from "react";
import { Minus, Plus } from "lucide-react";

import { hideZoomIndicator, showZoomIndicator, useZoomIndicator } from "@/hooks/useAppZoom";
import { useT } from "@/i18n";
import { APP_ZOOM_LEVELS, nextAppZoom } from "@/lib/appZoom";
import { cn } from "@/lib/utils";
import { useAppZoomSettings } from "@/store/appZoomSettings";

/** Chrome keeps its bubble up for about this long after the last step. */
export const ZOOM_INDICATOR_MS = 2000;

const ICON_BUTTON =
  "inline-flex h-8 w-8 items-center justify-center rounded-full text-foreground transition-colors " +
  "hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "disabled:pointer-events-none disabled:opacity-40 [&>svg]:h-4 [&>svg]:w-4";

export function ZoomIndicator() {
  const t = useT();
  const open = useZoomIndicator((s) => s.open);
  const seq = useZoomIndicator((s) => s.seq);
  const level = useAppZoomSettings((s) => s.level);
  const setLevel = useAppZoomSettings((s) => s.setLevel);
  const [held, setHeld] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || held) return;
    const timer = window.setTimeout(hideZoomIndicator, ZOOM_INDICATOR_MS);
    return () => window.clearTimeout(timer);
  }, [open, held, seq]);

  // Closing must not strand the hover/focus hold for the next opening.
  useEffect(() => {
    if (!open) setHeld(false);
  }, [open]);

  if (!open) return null;

  const step = (next: number) => {
    setLevel(next);
    showZoomIndicator();
  };
  const min = APP_ZOOM_LEVELS[0];
  const max = APP_ZOOM_LEVELS[APP_ZOOM_LEVELS.length - 1];

  return (
    <div
      ref={root}
      role="status"
      aria-live="polite"
      data-testid="zoom-indicator"
      onPointerEnter={() => setHeld(true)}
      onPointerLeave={() => {
        if (!root.current?.contains(document.activeElement)) setHeld(false);
      }}
      onFocus={() => setHeld(true)}
      onBlur={(event) => {
        if (!root.current?.contains(event.relatedTarget as Node | null)) setHeld(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") hideZoomIndicator();
      }}
      className={cn(
        "fixed right-4 top-14 z-[100] flex items-center gap-1 rounded-2xl border border-border-strong",
        "bg-popover py-2 pl-5 pr-2 text-popover-foreground shadow-float",
        "animate-in fade-in-0 slide-in-from-top-1 duration-150 motion-reduce:animate-none",
      )}
    >
      <span
        className="mr-4 min-w-[3.5rem] text-sm tabular-nums text-foreground"
        data-testid="zoom-indicator-level"
      >
        {Math.round(level * 100)} %
      </span>
      <button
        type="button"
        className={ICON_BUTTON}
        aria-label={t("settings_view.app_zoom.out_label")}
        title={t("settings_view.app_zoom.out_label")}
        data-testid="zoom-indicator-out"
        disabled={level <= min}
        onClick={() => step(nextAppZoom(level, "out"))}
      >
        <Minus />
      </button>
      <button
        type="button"
        className={ICON_BUTTON}
        aria-label={t("settings_view.app_zoom.in_label")}
        title={t("settings_view.app_zoom.in_label")}
        data-testid="zoom-indicator-in"
        disabled={level >= max}
        onClick={() => step(nextAppZoom(level, "in"))}
      >
        <Plus />
      </button>
      <button
        type="button"
        className={cn(
          "ml-3 h-9 rounded-full border border-border-strong px-4 text-sm font-medium text-accent transition-colors",
          "hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          "disabled:pointer-events-none disabled:opacity-40",
        )}
        data-testid="zoom-indicator-reset"
        disabled={level === 1}
        onClick={() => step(1)}
      >
        {t("settings_view.app_zoom.reset_button")}
      </button>
    </div>
  );
}
