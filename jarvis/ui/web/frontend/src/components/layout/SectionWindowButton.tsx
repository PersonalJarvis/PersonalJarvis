import { useEffect, useRef, useState } from "react";
import { AppWindow, PanelLeftClose } from "lucide-react";
import { hasEmbeddedDesktopBridge } from "@/lib/embeddedDesktop";
import { useT } from "@/i18n";
import { DETACHABLE_SECTIONS, detachedWindowFor, sectionWindow, windowIdentity } from "@/lib/sectionWindows";
import { useEventStore } from "@/store/events";

/** Native windows are owned by the desktop shell; browsers use named tabs. */
export function SectionWindowButton() {
  const t = useT();
  const section = useEventStore((s) => s.activeSection);
  const solo = useEventStore((s) => s.solo);
  const detached = useEventStore((s) => s.detachedViews);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [embedded, setEmbedded] = useState(hasEmbeddedDesktopBridge);
  useEffect(() => {
    const ready = () => setEmbedded(hasEmbeddedDesktopBridge());
    window.addEventListener("jarvis-token-ready", ready);
    window.addEventListener("pywebviewready", ready);
    ready();
    return () => {
      window.removeEventListener("jarvis-token-ready", ready);
      window.removeEventListener("pywebviewready", ready);
    };
  }, []);
  const owner = sectionWindow(section);
  if (!solo && !DETACHABLE_SECTIONS.includes(owner)) return null;
  // Only the native registry can hand off the IDE's single PTY subscription.
  // A browser tab cannot safely keep a second terminal view alive.
  if (!solo && owner === "agentic-ide" && !embedded) return null;
  const existing = detachedWindowFor(section, detached);
  const label = t(solo ? "topbar.return_to_main" : existing ? "topbar.detach_focus" : "topbar.detach");
  const hint = solo ? t("topbar.return_to_main_hint") : existing ? label : t("topbar.detach_hint");

  async function onClick() {
    if (pending.current) return;
    const native = hasEmbeddedDesktopBridge();
    if (!native) {
      if (solo) {
        try {
          const parent = window.opener as Window | null;
          if (parent && !parent.closed && parent.location.origin === window.location.origin) {
            parent.focus();
            window.close();
            return;
          }
        } catch {
          // The opener may have navigated to another origin. Keep this tab as main.
        }
        window.location.assign(`/?view=${encodeURIComponent(section)}`);
      } else {
        // Synchronous with the gesture so popup blockers do not eat the tab.
        const opened = window.open(`/?view=${section}&solo=1&window=${owner}`, `jarvis-section-${owner}`);
        if (!opened) useEventStore.getState().pushToast("error", t("topbar.detach_failed"));
      }
      return;
    }
    pending.current = true;
    setBusy(true);
    try {
      const view = solo ? windowIdentity(window.location.search, section) : existing ?? section;
      if (solo) {
        // Restore main before closing the last detached window.
        const focus = await fetch("/api/window/focus", { method: "POST" });
        const body = await focus.json() as { ok?: boolean };
        if (!focus.ok || !body.ok) throw new Error("Main window could not be restored");
      }
      const response = await fetch(`/api/window/${solo ? "reattach" : "detach"}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ view }),
      });
      const body = await response.json() as { ok?: boolean };
      if (!response.ok || !body.ok) throw new Error("Window operation was rejected");
    } catch (error) {
      console.warn("Section window operation failed", error);
      useEventStore.getState().pushToast("error", t(solo ? "topbar.reattach_failed" : "topbar.detach_failed"));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  const Icon = solo ? PanelLeftClose : AppWindow;
  return (
    <button type="button" title={hint} aria-label={label} disabled={busy}
      data-testid="detach-view-button" onClick={() => void onClick()}
      className="inline-flex h-8 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
      <Icon aria-hidden className="h-4 w-4 shrink-0" />
      <span>{label}</span>
    </button>
  );
}
