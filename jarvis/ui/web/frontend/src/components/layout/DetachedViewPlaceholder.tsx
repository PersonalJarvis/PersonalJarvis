import { useState } from "react";
import { AppWindow } from "lucide-react";

import { EmptyState } from "@/components/layout/EmptyState";
import { Button } from "@/components/ui/button";
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

  // The shared empty state, not a hand-rolled one: this IS the case that
  // primitive exists for — one sentence saying where the section went, and the
  // one action that brings it back.
  return (
    <div
      className="flex h-full w-full items-center justify-center p-7"
      data-testid="detached-view-placeholder"
    >
      <EmptyState
        icon={<AppWindow />}
        title={t("topbar.detached_title")}
        body={t("topbar.detached_body")}
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => void post("/api/window/detach")}
              data-testid="detached-focus-button"
            >
              {t("topbar.detach_focus")}
            </Button>
            <Button
              size="sm"
              disabled={busy}
              onClick={() => void post("/api/window/reattach")}
              data-testid="detached-reattach-button"
            >
              {t("topbar.detach_bring_back")}
            </Button>
          </div>
        }
      />
    </div>
  );
}
