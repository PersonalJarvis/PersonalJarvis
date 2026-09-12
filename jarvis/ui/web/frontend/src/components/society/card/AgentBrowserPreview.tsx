/**
 * The real agent browser, continuously rendered in the Options rail.
 * Pixels stay out of React state; all manual actions require a control lease.
 */
import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Maximize2, Minimize2, RotateCw } from "lucide-react";
import { useLocaleChunk, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { BrandedSelect } from "@/components/ui/select";
import type { SocietyAgent } from "../data";
import { useBrowserInstallStatus } from "../cardData";
import { useBrowserView } from "./useBrowserView";
import "./agentCard.css";

export function AgentBrowserPreview({ agent }: { agent: SocietyAgent }) {
  const t = useT();
  useLocaleChunk("society");
  const { canvas, state, control, approve } = useBrowserView(agent.agentId);
  const install = useBrowserInstallStatus();
  const [expanded, setExpanded] = useState(false);
  const [address, setAddress] = useState("");
  useEffect(() => setAddress(state.url), [state.url]);
  useEffect(() => { setExpanded(false); }, [agent.agentId]);
  const status = !install.data?.installed ? t("society.card.browser_setting_up")
    : !state.connected || !state.ready || state.controlPending ? t("society.card.browser_connecting")
    : state.manual ? t("society.browser_live.manual") : t("society.browser_live.live");
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
          <div className="font-medium text-foreground">Personal Jarvis</div>
          <div className="truncate">{agent.name} · {status}</div>
        </div>
        <button className={buttonClass} onClick={() => setExpanded((v) => !v)}
          aria-label={t(expanded ? "society.browser_live.collapse" : "society.browser_live.expand")}>
          {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
      </div>
      {expanded && (
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
        <canvas ref={canvas} width={1280} height={800} tabIndex={state.manual ? 0 : -1}
          aria-label={t("society.browser_live.screen").replace("{0}", agent.name)}
          className="block max-h-full max-w-full object-contain focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          style={{ aspectRatio: "16/10", width: "100%", height: "100%", objectFit: "contain" }}
          onClick={(e) => {
            if (!state.manual) return;
            e.currentTarget.focus();
            const box = e.currentTarget.getBoundingClientRect();
            const scale = Math.min(box.width / 1280, box.height / 800);
            const x = (e.clientX - box.left - (box.width - 1280 * scale) / 2) / scale;
            const y = (e.clientY - box.top - (box.height - 800 * scale) / 2) / scale;
            if (x >= 0 && y >= 0 && x <= 1280 && y <= 800) control("click", { x, y });
          }}
          onWheel={(e) => { if (state.manual) control("scroll", { dx: e.deltaX, dy: e.deltaY }); }}
          onPaste={(e) => {
            if (!state.manual) return;
            e.preventDefault();
            control("text", { text: e.clipboardData.getData("text/plain") });
          }}
          onKeyDown={(e) => {
            if (!state.manual) return;
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
        {!state.ready && <div className="absolute inset-0 grid place-items-center bg-muted p-3 text-center text-xs text-muted-foreground">
          {install.data?.detail || status}
          {install.data?.running && <span>{install.data.percent}%</span>}
        </div>}
        {state.ready && !state.connected && <div className="absolute bottom-2 rounded bg-background/90 px-2 py-1 text-xs">{status}</div>}
      </div>
      <div className="mt-2 flex flex-wrap justify-center gap-1">
        <button className={buttonClass} disabled={!state.connected || !state.ready || state.controlPending}
          onClick={() => { setExpanded(true); control("takeover", { enabled: !state.manual }); }}>
          {t(state.manual ? "society.browser_live.return_control" : "society.browser_live.take_control")}
        </button>
        {state.running && <button className={buttonClass} onClick={() => control("cancel")}>{t("society.browser_live.cancel")}</button>}
      </div>
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
      {(state.error || install.data?.error) && <div className="mt-2 text-xs text-destructive" role="status">
        {state.error || install.data?.error}
        <button className={buttonClass} onClick={() => void fetch("/api/society/browser/repair", { method: "POST" })}>{t("society.browser_live.repair")}</button>
      </div>}
    </div>
  );
}
export default AgentBrowserPreview;
