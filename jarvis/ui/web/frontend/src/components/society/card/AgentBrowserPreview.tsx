/**
 * The real agent browser, rendered in the Options rail while it runs.
 * Opening the card never launches one; pixels stay out of React state; all
 * manual actions require a control lease.
 */
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Maximize2, Minimize2, RotateCw } from "lucide-react";
import { useLocaleChunk, useT } from "@/i18n";
import { societyDisplayName } from "@/lib/societyDisplayName";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { BrandedSelect } from "@/components/ui/select";
import type { SocietyAgent } from "../data";
import { useAgentBrowserOpen, useBrowserInstallStatus } from "../cardData";
import { useBrowserView } from "./useBrowserView";
import { AgentCursor } from "./AgentCursor";
import { browserPoint } from "./browserInput";
import { googleSignInRejected } from "./browserSignIn";
import { BrowserProfilesButton } from "../browser/BrowserProfilesButton";
import "./agentCard.css";

export function AgentBrowserPreview({ agent }: { agent: SocietyAgent }) {
  const lastHoverAt = useRef(0);
  const t = useT();
  useLocaleChunk("society");
  const assistantName = useEventStore((s) => s.assistantName);
  const displayName = societyDisplayName(agent, assistantName);
  // Viewing launches a Chromium, so the view attaches only to a browser the
  // agent already runs, or after the person asks for it.
  const running = useAgentBrowserOpen(agent.agentId);
  const [wanted, setWanted] = useState(false);
  useEffect(() => { setWanted(false); }, [agent.agentId]);
  useEffect(() => { if (running.data?.open) setWanted(true); }, [running.data?.open]);
  const profileUnavailable = running.data?.mode === "unavailable";
  const live = !profileUnavailable && (wanted || running.data?.open === true);
  const isChrome = running.data?.mode === "chrome";
  const managedRuntime = !isChrome && !profileUnavailable;
  const chromeDisconnected = isChrome && !running.data?.connected;
  const { canvas, state, control, approve } = useBrowserView(agent.agentId, live);
  const rejectedGoogle = managedRuntime && live && googleSignInRejected(state.url);
  const install = useBrowserInstallStatus();
  const [expanded, setExpanded] = useState(false);
  const [address, setAddress] = useState("");
  useEffect(() => setAddress(state.url), [state.url]);
  useEffect(() => { setExpanded(false); }, [agent.agentId]);
  const status = profileUnavailable
    ? t("society.browser_profiles.profile_unavailable")
    : chromeDisconnected
    ? t("society.browser_profiles.chrome_offline")
    : state.loginMode
    ? t("society.browser_profiles.inline_login_active")
    : state.previewPaused
    ? t("society.browser_profiles.sign_in_chrome")
    : !live
    ? t("society.browser_live.off")
    : state.connected && state.ready
    ? t(state.manual ? "society.browser_live.manual" : "society.browser_live.live")
    : t(managedRuntime && install.data && !install.data.installed ? "society.card.browser_setting_up" : "society.card.browser_connecting");
  const buttonClass = "rounded px-2 py-1 text-xs hover:bg-secondary disabled:opacity-40";
  const enterUrl = () => {
    const url = /^https?:\/\//i.test(address) ? address : "https://" + address;
    control("navigate", { url });
  };
  return (
    <div data-testid="agent-browser-preview" className={cn(
      "shrink-0", expanded && "fixed inset-4 z-[60] flex flex-col rounded-xl border border-border bg-background p-3 shadow-xl",
    )}>
      <div className="mb-1 flex items-center justify-between gap-1 text-[10px] text-muted-foreground">
        <div className="min-w-0">
          <div className="truncate">{displayName}{running.data?.profileName ? ` · ${running.data.profileName}` : ""} · {status}</div>
        </div>
        <button className={buttonClass} onClick={() => setExpanded((v) => !v)}
          aria-label={t(expanded ? "society.browser_live.collapse" : "society.browser_live.expand")}>
          {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
      </div>
      {expanded && state.ready && !state.fullWindow && managedRuntime && (
        <div className="mb-2 flex items-center gap-1">
          <button disabled={!state.manual} className={buttonClass} onClick={() => control("back")} aria-label={t("society.browser_live.back")}><ArrowLeft size={16} /></button>
          <button disabled={!state.manual} className={buttonClass} onClick={() => control("forward")} aria-label={t("society.browser_live.forward")}><ArrowRight size={16} /></button>
          <button disabled={!state.manual} className={buttonClass} onClick={() => control("reload")} aria-label={t("society.browser_live.reload")}><RotateCw size={16} /></button>
          <input className="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1 text-sm"
            aria-label={t("society.browser_live.address")} disabled={!state.manual} value={address}
            onChange={(e) => setAddress(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") enterUrl(); }} />
          <BrandedSelect className="max-w-48 text-xs" disabled={!state.manual}
            ariaLabel={t("society.browser_live.tabs")} value={state.target}
            options={[...state.tabs.map((tab) => ({ value: tab.id, label: tab.url || "about:blank" })),
              { value: "new", label: t("society.browser_live.new_tab") }]}
            onValueChange={(target) => control("tab", { target })} />
        </div>
      )}
      <div className={cn("relative flex items-center justify-center overflow-hidden rounded-lg bg-muted",
        expanded ? "min-h-0 flex-1" : "aspect-[16/10]")}>
        <canvas ref={canvas} width={1280} height={800} tabIndex={live && state.manual && !state.previewPaused ? 0 : -1}
          aria-label={t("society.browser_live.screen").replace("{0}", displayName)}
          className="block max-h-full max-w-full object-contain focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          style={{ aspectRatio: "16/10", width: "100%", height: "100%", objectFit: "contain" }}
          onClick={(e) => {
            if (!live || !state.ready) return;
            e.currentTarget.focus();
            const point = browserPoint(e.currentTarget, e.clientX, e.clientY);
            if (point) control("click", { ...point, ...(state.extendedInput && e.detail === 2 ? { count: 2 } : {}) });
          }}
          onContextMenu={(e) => {
            if (!live || !state.ready) return;
            e.preventDefault();
            if (!state.extendedInput) return;
            e.currentTarget.focus();
            const point = browserPoint(e.currentTarget, e.clientX, e.clientY);
            if (point) control("click", { ...point, button: "right" });
          }}
          onAuxClick={(e) => {
            if (!live || !state.ready || e.button !== 1 || !state.extendedInput) return;
            e.preventDefault();
            e.currentTarget.focus();
            const point = browserPoint(e.currentTarget, e.clientX, e.clientY);
            if (point) control("click", { ...point, button: "middle" });
          }}
          onMouseMove={(e) => {
            if (!live || !state.ready || !state.manual || !state.extendedInput || !state.connected) return;
            const now = performance.now();
            if (now - lastHoverAt.current < 33) return;
            const point = browserPoint(e.currentTarget, e.clientX, e.clientY);
            // Older running backends already forward click arguments. This
            // keeps hover compatible while only the browser worker reloads.
            if (point) { lastHoverAt.current = now; control("click", { ...point, move_only: true }); }
          }}
          onWheel={(e) => {
            if (!live || !state.ready || document.activeElement !== e.currentTarget) return;
            const point = browserPoint(e.currentTarget, e.clientX, e.clientY);
            if (point) control("scroll", { ...point, dx: e.deltaX, dy: e.deltaY });
          }}
          onPaste={(e) => {
            if (!live || !state.ready || document.activeElement !== e.currentTarget) return;
            e.preventDefault();
            control("text", { text: e.clipboardData.getData("text/plain") });
          }}
          onKeyDown={(e) => {
            if (!live || !state.ready || (!state.manual && document.activeElement !== e.currentTarget)) return;
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") return;
            e.preventDefault();
            e.stopPropagation();
            if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) control("text", { text: e.key });
            else if (!["Control", "Shift", "Alt", "Meta"].includes(e.key)) {
              const modifiers = [e.ctrlKey ? "Control" : "", e.metaKey ? "Meta" : "",
                e.altKey ? "Alt" : "", e.shiftKey ? "Shift" : ""].filter(Boolean);
              control("key", { key: [...modifiers, e.key].join("+") });
            }
          }} />
        {(!state.fullWindow || state.pointer?.native_window === true) && (
          <AgentCursor pointer={state.ready && state.connected && !state.manual ? state.pointer : undefined} />
        )}
        {!live && <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-muted p-3 text-center text-xs text-muted-foreground">
          <p>{profileUnavailable ? t("society.browser_profiles.profile_unavailable") : chromeDisconnected ? t("society.browser_profiles.disconnected") : t("society.browser_live.off_hint").replace("{0}", displayName)}</p>
          {!profileUnavailable && <button type="button" data-testid="agent-browser-open"
            className="rounded-md border border-border bg-background px-3 py-1 text-xs font-medium text-foreground hover:bg-secondary"
            disabled={chromeDisconnected} onClick={() => setWanted(true)}>
            {t("society.browser_live.open")}
          </button>}
        </div>}
        {live && !state.ready && !state.previewPaused && <div className="absolute inset-0 grid place-items-center bg-muted p-3 text-center text-xs text-muted-foreground">
          {chromeDisconnected ? t("society.browser_profiles.disconnected") : (managedRuntime && install.data?.detail) || status}
          {managedRuntime && install.data?.running && <span>{install.data.percent}%</span>}
        </div>}
        {state.previewPaused && <div className="absolute inset-0 grid place-items-center bg-muted p-4 text-center text-xs text-muted-foreground">
          {t("society.browser_profiles.preview_paused")}
        </div>}
        {state.ready && !state.connected && <div className="absolute bottom-2 rounded bg-background/90 px-2 py-1 text-xs">{status}</div>}
      </div>
      <div className="mt-2 flex flex-wrap justify-center gap-1">
        <BrowserProfilesButton agentId={agent.agentId} />
        {managedRuntime && state.loginAvailable && !state.loginMode && !rejectedGoogle && <button className={buttonClass}
          disabled={!live || !state.connected || state.controlPending}
          onClick={() => { setExpanded(true); control("takeover", { enabled: true, login: true }); }}>
          {t("society.browser_profiles.inline_login")}
        </button>}
        <button className={buttonClass} disabled={!live || !state.connected || (!state.ready && !state.previewPaused && !state.loginMode) || state.controlPending}
          onClick={() => { setExpanded(true); control("takeover", state.loginMode
            ? { enabled: false, login: false } : { enabled: !state.manual }); }}>
          {t(state.loginMode ? "society.browser_profiles.inline_login_finish" : state.manual ? "society.browser_live.return_control" : "society.browser_live.take_control")}
        </button>
        {state.running && <button className={buttonClass} onClick={() => control("cancel")}>{t("society.browser_live.cancel")}</button>}
      </div>
      {state.loginMode && <p role="status" className="mt-2 text-center text-xs text-muted-foreground">
        {t("society.browser_profiles.inline_login_hint")}
      </p>}
      {rejectedGoogle && !state.loginMode && <div role="alert"
        className="mt-3 grid gap-2 rounded-lg border border-border bg-secondary/40 p-3 text-sm text-foreground">
        <strong>{t("society.browser_profiles.google_signin_rejected")}</strong>
        <p>{t("society.browser_profiles.inline_login_recovery")}</p>
        <div className="flex flex-wrap items-center gap-3">
          <button className={buttonClass} disabled={!state.loginAvailable || !state.connected || state.controlPending}
            onClick={() => { setExpanded(true); control("takeover", { enabled: true, login: true }); }}>
            {t("society.browser_profiles.inline_login")}
          </button>
          {!state.loginAvailable && <span className="text-xs text-muted-foreground">{t("society.browser_profiles.inline_login_unavailable")}</span>}
          <a href="https://support.google.com/accounts/answer/7675428" target="_blank" rel="noopener noreferrer"
            className="text-xs underline underline-offset-2">{t("society.browser_profiles.google_signin_help")}</a>
        </div>
      </div>}
      {state.approval && <div className="mt-2 text-xs">
        <p>{t("society.browser_live.approval")} {state.approval.action}</p>
        <button className={buttonClass} onClick={() => void approve(true)}>{t("society.browser_live.allow")}</button>
        <button className={buttonClass} onClick={() => void approve(false)}>{t("society.browser_live.deny")}</button>
      </div>}
      {state.dialog && <div className="mt-2 text-xs">
        <p>{state.dialog.message}</p>
        <button className={buttonClass} onClick={() => control("dialog", { accept: true })}>{t("society.browser_live.allow")}</button>
        <button className={buttonClass} onClick={() => control("dialog", { accept: false })}>{t("society.browser_live.deny")}</button>
      </div>}
      {(state.error || (managedRuntime && install.data?.error)) && <div className="mt-2 text-xs text-destructive" role="status">
        {profileUnavailable ? t("society.browser_profiles.profile_unavailable") : chromeDisconnected ? t("society.browser_profiles.disconnected") : state.error || install.data?.error}
        {managedRuntime && <button className={buttonClass} onClick={() => void fetch("/api/society/browser/repair", { method: "POST" })}>{t("society.browser_live.repair")}</button>}
      </div>}
    </div>
  );
}
export default AgentBrowserPreview;
