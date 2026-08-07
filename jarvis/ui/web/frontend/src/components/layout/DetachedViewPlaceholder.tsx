import { useState } from "react";
import { AppWindow } from "lucide-react";

import { useT } from "@/i18n";
import type { SectionId } from "@/store/events";

/**
 * What a section shows in the MAIN window while it lives in its own detached
 * desktop window.
 *
 * The Agentic IDE must genuinely unmount here — a second mounted instance
 * steals every pane's output stream (see MainView) — so the section cannot
 * simply keep rendering. Instead of an empty screen, this explains where the
 * view went and offers the two useful moves: focus the detached window
 * (an idempotent re-detach — the backend focuses the existing window) or
 * close it so the section returns to this window (the close travels through
 * the same pywebview closed-hook as the window's own X).
 */
export function DetachedViewPlaceholder({ view }: { view: SectionId }) {
  const t = useT();
  const [busy, setBusy] = useState(false);

  async function post(path: string) {
    if (busy) return;
    setBusy(true);
    try {
      await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ view }),
      });
      // No local state flip: the DetachedViewClosed WS event is the single
      // source of truth for the remount, same as a manual window close.
    } catch {
      /* backend unreachable — the buttons stay, the user can retry */
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="flex h-full w-full flex-col items-center justify-center gap-4 text-center"
      data-testid="detached-view-placeholder"
    >
      <AppWindow aria-hidden className="h-10 w-10 text-muted-foreground/60" />
      <div className="max-w-sm space-y-1">
        <div className="text-sm font-medium text-foreground">
          {t("topbar.detached_title")}
        </div>
        <div className="text-xs text-muted-foreground">
          {t("topbar.detached_body")}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void post("/api/window/detach")}
          data-testid="detached-focus-button"
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground disabled:cursor-default disabled:opacity-70"
        >
          {t("topbar.detach_focus")}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void post("/api/window/reattach")}
          data-testid="detached-reattach-button"
          className="inline-flex items-center gap-1.5 rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:cursor-default disabled:opacity-70"
        >
          {t("topbar.detach_bring_back")}
        </button>
      </div>
    </div>
  );
}
