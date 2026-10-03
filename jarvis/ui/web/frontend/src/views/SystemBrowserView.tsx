import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, Globe, RefreshCw, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { inDesktopShell } from "@/lib/nativeDrop";
import { browserBounds, systemBrowser, type BrowserState } from "@/lib/systemBrowser";
import { useEventStore } from "@/store/events";

export function SystemBrowserView() {
  const t = useT();
  const desktop = inDesktopShell();
  const active = useEventStore((s) => s.activeSection === "browser");
  const area = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);
  const lease = useRef<string | null>(null);
  const generation = useRef(0);
  const [state, setState] = useState<BrowserState>({});
  const [busy, setBusy] = useState(false);
  const [docked, setDocked] = useState(false);
  const [message, setMessage] = useState("");

  const detach = useCallback(() => {
    generation.current += 1;
    const current = lease.current;
    lease.current = null;
    if (mounted.current) setDocked(false);
    if (current) void systemBrowser("detach", { lease: current }).catch(() => {
      // The native presence lease also restores the window after a lost request.
      if (mounted.current) setMessage("system_browser.connection_lost");
    });
  }, []);

  useEffect(() => {
    mounted.current = true;
    if (desktop) void systemBrowser("status").then((data) => {
      if (mounted.current) setState(data);
    }).catch(() => {
      if (mounted.current) setMessage("system_browser.failed");
    });
    return () => { mounted.current = false; detach(); };
  }, [desktop, detach]);

  useEffect(() => {
    if (!active) detach();
  }, [active, detach]);

  useEffect(() => {
    if (!docked || !area.current) return;
    let cancelled = false;
    let inFlight = false;
    const update = async () => {
      if (cancelled || inFlight || !lease.current || !area.current) return;
      const bounds = browserBounds(area.current);
      if (!bounds || document.hidden) { detach(); return; }
      inFlight = true;
      try {
        const result = await systemBrowser("present", { lease: lease.current, bounds });
        if (!cancelled && !result.ok) {
          detach();
          setMessage("system_browser.session_ended");
        }
      } catch {
        if (!cancelled) { detach(); setMessage("system_browser.connection_lost"); }
      } finally { inFlight = false; }
    };
    const observer = new ResizeObserver(() => { void update(); });
    observer.observe(area.current);
    // Stop docking when a global dialog needs the same native screen area.
    const overlays = new MutationObserver(() => {
      if (document.querySelector('[role="dialog"], [role="alertdialog"]')) detach();
    });
    overlays.observe(document.body, { childList: true, subtree: true });
    const timer = window.setInterval(() => { void update(); }, 2000);
    window.addEventListener("resize", update);
    window.addEventListener("pagehide", detach);
    document.addEventListener("visibilitychange", update);
    void update();
    return () => {
      cancelled = true;
      observer.disconnect(); overlays.disconnect(); window.clearInterval(timer);
      window.removeEventListener("resize", update);
      window.removeEventListener("pagehide", detach);
      document.removeEventListener("visibilitychange", update);
    };
  }, [docked, detach]);

  async function run(action: "open" | "windows") {
    setBusy(true); setMessage("");
    try {
      const data = await systemBrowser(action, action === "open" ? {} : undefined);
      if (!mounted.current) return;
      if (data.ok === false) setMessage("system_browser.failed");
      else if (action === "windows") setState(data);
      else setMessage("system_browser.choose_profile");
    } catch {
      if (mounted.current) setMessage("system_browser.failed");
    } finally { if (mounted.current) setBusy(false); }
  }

  async function attach(windowId: string) {
    const bounds = area.current && browserBounds(area.current);
    if (!bounds) { setMessage("system_browser.too_small"); return; }
    const attempt = ++generation.current;
    setBusy(true); setMessage("");
    try {
      const data = await systemBrowser("attach", { window_id: windowId, bounds });
      if (!mounted.current || generation.current !== attempt) {
        if (data.lease) await systemBrowser("detach", { lease: data.lease });
        return;
      }
      if (data.ok && data.lease) { lease.current = data.lease; setDocked(true); }
      else setMessage("system_browser.failed");
    } catch {
      if (mounted.current) setMessage("system_browser.failed");
    } finally { if (mounted.current) setBusy(false); }
  }

  return <section className="flex h-full min-h-0 flex-col gap-3 p-4" aria-label={t("system_browser.title")}>
    <header className="flex shrink-0 flex-wrap items-center gap-3">
      <Globe className="h-5 w-5 text-muted-foreground" aria-hidden />
      <h1 className="text-lg font-semibold">{t("system_browser.title")}</h1>
      <span className="text-sm text-muted-foreground">{t(docked ? "system_browser.docked" : "system_browser.subtitle")}</span>
      <div className="ml-auto flex gap-2">
        {docked ? <Button variant="outline" onClick={detach}><Unplug className="mr-2 h-4 w-4" />{t("system_browser.detach")}</Button> : <>
          <Button variant="outline" disabled={!desktop || !state.available || busy} onClick={() => void run("open")}>
            <ExternalLink className="mr-2 h-4 w-4" />{t("system_browser.open")}
          </Button>
          <Button variant="outline" disabled={!desktop || !state.can_dock || busy} onClick={() => void run("windows")}>
            <RefreshCw className="mr-2 h-4 w-4" />{t("system_browser.windows")}
          </Button>
        </>}
      </div>
    </header>
    {message && <p role="status" className="shrink-0 text-sm text-muted-foreground">{t(message)}</p>}
    <div ref={area} data-testid="browser-area" className="min-h-0 flex-1 overflow-auto rounded-lg border border-border bg-background">
      {!docked && <div className="mx-auto flex max-w-xl flex-col gap-4 p-8">
        <h2 className="text-base font-medium">{t("system_browser.heading")}</h2>
        <p className="text-sm leading-relaxed text-muted-foreground">{t("system_browser.description")}</p>
        {!desktop || state.can_dock === false ? <p className="text-sm text-muted-foreground">{t("system_browser.unsupported")}</p> : null}
        {state.windows?.length === 0 && <p className="text-sm text-muted-foreground">{t("system_browser.no_windows")}</p>}
        {state.windows?.map((candidate) => <Button key={candidate.id} variant="outline" disabled={busy}
          className="h-auto justify-start whitespace-normal py-3 text-left" onClick={() => void attach(candidate.id)}>
          <Globe className="mr-3 h-4 w-4 shrink-0" /><span className="break-all">{candidate.title}</span>
        </Button>)}
        <p className="text-xs leading-relaxed text-muted-foreground">{t("system_browser.lifecycle")}</p>
      </div>}
    </div>
  </section>;
}
