import { useCallback, useEffect, useState } from "react";
import { Copy, Minus, Square, X } from "lucide-react";

import { hasEmbeddedDesktopBridge } from "@/components/voice/BrowserRealtimeControl";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

export interface DesktopChrome {
  frameless: boolean;
  controls: "leading" | "trailing" | "none";
  maximized: boolean;
}

const IDLE: DesktopChrome = { frameless: false, controls: "none", maximized: false };

/**
 * The desktop window's own buttons, drawn by the page once the native title
 * bar is gone. A browser tab has none of these — the operating system already
 * provides them, and a fake set would close nothing.
 */
export function useDesktopChrome(): DesktopChrome & {
  command: (action: "minimize" | "maximize" | "close") => void;
} {
  const [chrome, setChrome] = useState<DesktopChrome>(IDLE);

  useEffect(() => {
    if (!hasEmbeddedDesktopBridge()) return;
    let live = true;
    fetch("/api/window/chrome")
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { frameless?: boolean; controls?: string } | null) => {
        if (!live || !body) return;
        const controls = body.controls === "leading" || body.controls === "trailing" ? body.controls : "none";
        setChrome({ frameless: Boolean(body.frameless), controls, maximized: false });
      })
      .catch((error: unknown) => {
        console.warn("Window chrome is unavailable", error);
      });
    return () => {
      live = false;
    };
  }, []);

  const command = useCallback((action: "minimize" | "maximize" | "close") => {
    const solo = useEventStore.getState().solo;
    const view = solo ? useEventStore.getState().activeSection : null;
    void fetch("/api/window/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, view }),
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { maximized?: boolean } | null) => {
        if (!body || typeof body.maximized !== "boolean") return;
        setChrome((current) => ({ ...current, maximized: body.maximized as boolean }));
      })
      .catch((error: unknown) => {
        console.warn("Window command failed", error);
      });
  }, []);

  return { ...chrome, command };
}

const CAPTION_BUTTON =
  "inline-flex h-8 w-10 shrink-0 items-center justify-center text-muted-foreground " +
  "transition-colors hover:bg-secondary hover:text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** Minimize, maximize and close. Render nothing unless this window owns them. */
export function WindowControls({
  controls,
  maximized,
  onCommand,
}: {
  controls: DesktopChrome["controls"];
  maximized: boolean;
  onCommand: (action: "minimize" | "maximize" | "close") => void;
}) {
  const t = useT();
  if (controls === "none") return null;
  const MaxIcon = maximized ? Copy : Square;
  return (
    <div className="flex shrink-0 items-stretch" data-testid="window-controls" data-side={controls}>
      <button
        type="button"
        data-testid="window-minimize"
        title={t("topbar.minimize")}
        aria-label={t("topbar.minimize")}
        onClick={() => onCommand("minimize")}
        className={CAPTION_BUTTON}
      >
        <Minus aria-hidden className="h-4 w-4" />
      </button>
      <button
        type="button"
        data-testid="window-maximize"
        title={t(maximized ? "topbar.restore" : "topbar.maximize")}
        aria-label={t(maximized ? "topbar.restore" : "topbar.maximize")}
        onClick={() => onCommand("maximize")}
        className={CAPTION_BUTTON}
      >
        <MaxIcon aria-hidden className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        data-testid="window-close"
        title={t("topbar.close")}
        aria-label={t("topbar.close")}
        onClick={() => onCommand("close")}
        className={cn(CAPTION_BUTTON, "hover:bg-destructive hover:text-destructive-foreground")}
      >
        <X aria-hidden className="h-4 w-4" />
      </button>
    </div>
  );
}
