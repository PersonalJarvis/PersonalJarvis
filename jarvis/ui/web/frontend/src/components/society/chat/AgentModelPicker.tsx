import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronRight, Loader2, RefreshCw, Search, Users } from "lucide-react";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { effortLabel } from "@/components/agentchat/AgentComposer";
import { useT } from "@/i18n";
import { fetchAgentChatCatalog, fetchAgentConnections, fetchProviderModels, type CuratedModel } from "@/lib/agentChatApi";
import { fetchSocietyProviders } from "@/lib/societyApi";
import { cn } from "@/lib/utils";
import { joinProviderOptions } from "@/store/agentChat";
import { effortsFor, type BrainSeat } from "../create/brainPicker";
import { useUpdateAgentModel, type SocietyAgent } from "../data";
import { matchesModel, modelEffort, modelSeats, providerTitle } from "./modelChoices";

type Submenu = { provider: string; model?: CuratedModel; anchor: DOMRect };
const menuRow = "flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] text-popover-foreground hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none disabled:opacity-45";

/** A searchable provider catalog, with account and effort submenus. */
export function AgentModelPicker({ agent, busy, onSavingChange }: {
  agent: SocietyAgent; busy: boolean; onSavingChange: (saving: boolean) => void;
}) {
  const t = useT();
  const update = useUpdateAgentModel();
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<Record<string, string>>({});
  const [submenu, setSubmenu] = useState<Submenu | null>(null);
  const [position, setPosition] = useState<CSSProperties>({});
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const sidePanel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const inFlight = useRef(false);

  const catalog = useQuery({ queryKey: ["agent-chat", "catalog", "society"], queryFn: () => fetchAgentChatCatalog("society"), enabled: open, staleTime: 60_000 });
  const connections = useQuery({ queryKey: ["agent-chat", "connections"], queryFn: fetchAgentConnections, enabled: open, staleTime: 60_000 });
  const providers = useQuery({ queryKey: ["society", "providers"], queryFn: fetchSocietyProviders, enabled: open, staleTime: 60_000 });
  const options = useMemo(() => joinProviderOptions(catalog.data?.providers ?? [], connections.data ?? []), [catalog.data, connections.data]);
  const liveProviders = options.filter((option) => option.connected && option.models_source === "live");
  const liveQueries = useQueries({ queries: liveProviders.map((provider) => ({
    queryKey: ["society", "model-menu", "live", provider.id],
    queryFn: async (): Promise<CuratedModel[]> => (await fetchProviderModels(provider.id)).map((model) => ({ id: model.id, label: model.label ?? model.name ?? model.id })),
    enabled: open && connections.isSuccess, staleTime: 60_000, retry: false,
  })) });
  const live: Record<string, CuratedModel[]> = {};
  liveProviders.forEach((provider, index) => { if (liveQueries[index].data) live[provider.id] = liveQueries[index].data!; });
  const seats = modelSeats(options, providers.data ?? [], live);
  const loading = catalog.isLoading || connections.isLoading || providers.isLoading;
  const refreshing = catalog.isFetching || connections.isFetching || providers.isFetching || liveQueries.some((query) => query.isFetching);
  const failed = catalog.isError || connections.isError || providers.isError;
  const currentAccount = (seat: BrainSeat) => accounts[seat.provider.id] ?? (agent.provider === seat.provider.id ? agent.accountId ?? "" : "");
  const preferredEffort = (seat: BrainSeat, model: CuratedModel) => modelEffort(seat, model.id, seat.provider.id === agent.provider ? agent.effort : seat.provider.default_effort);
  const groups = seats.map((seat) => ({ seat, title: providerTitle(seat, t),
    models: seat.provider.curated_models.filter((model) => matchesModel(seat, model, search, providerTitle(seat, t))),
  })).filter((group) => group.models.length > 0).sort((a, b) => {
    const order = { subscription: 0, api: 1, local: 2 };
    return order[a.seat.kind] - order[b.seat.kind] || a.title.localeCompare(b.title);
  });
  const sideSeat = seats.find((seat) => seat.provider.id === submenu?.provider);

  function close() {
    if (inFlight.current) return;
    setOpen(false); setSubmenu(null); trigger.current?.focus();
  }

  useEffect(() => {
    if (!open) return;
    // Radix listens on document capture: the nested menu must handle Escape first.
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault(); event.stopPropagation();
      if (submenu) { setSubmenu(null); input.current?.focus(); } else close();
    };
    window.addEventListener("keydown", escape, true);
    return () => window.removeEventListener("keydown", escape, true);
  }, [open, submenu]);

  useLayoutEffect(() => {
    if (!open) return;
    let frame = 0;
    const place = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const above = rect.top > window.innerHeight - rect.bottom;
      const width = Math.min(320, window.innerWidth - 16);
      const next: CSSProperties = { position: "fixed", width, left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
        maxHeight: Math.min(560, Math.max(120, (above ? rect.top : window.innerHeight - rect.bottom) - 16)),
        ...(above ? { bottom: window.innerHeight - rect.top + 6 } : { top: rect.bottom + 6 }),
      };
      setPosition((previous) => previous.left === next.left && previous.top === next.top && previous.bottom === next.bottom && previous.width === next.width && previous.maxHeight === next.maxHeight ? previous : next);
      // Dialog content can settle after the menu opens without a window resize.
      frame = requestAnimationFrame(place);
    };
    place(); input.current?.focus();
    return () => cancelAnimationFrame(frame);
  }, [open]);

  useEffect(() => {
    if (submenu) sidePanel.current?.querySelector<HTMLButtonElement>("button[data-menu-choice]")?.focus();
  }, [submenu]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      const node = event.target as Node;
      if (!trigger.current?.contains(node) && !panel.current?.contains(node) && !sidePanel.current?.contains(node) && !inFlight.current) {
        setOpen(false); setSubmenu(null);
      }
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  async function save(seat: BrainSeat, model: CuratedModel, effort = preferredEffort(seat, model)) {
    if (busy || inFlight.current) return;
    inFlight.current = true; setSaving(true); onSavingChange(true); setError(null);
    try {
      await update(agent.agentId, { provider: seat.provider.id, model: model.id, effort, account_id: currentAccount(seat) });
      setOpen(false); setSubmenu(null); trigger.current?.focus();
    } catch (err) {
      setError(`${t("society.chat.model_save_failed")} (${err instanceof Error ? err.message : String(err)})`);
    } finally {
      inFlight.current = false; setSaving(false); onSavingChange(false);
    }
  }

  async function refresh() {
    setSubmenu(null);
    await Promise.all([catalog.refetch(), connections.refetch(), providers.refetch(), ...liveQueries.map((query) => query.refetch())]);
  }

  function moveFocus(event: KeyboardEvent, container: HTMLElement | null) {
    if (event.key === "Escape" || (event.key === "ArrowLeft" && submenu)) {
      event.preventDefault(); event.stopPropagation();
      if (submenu) { setSubmenu(null); input.current?.focus(); } else close();
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    if (event.target === input.current && ["Home", "End"].includes(event.key)) return;
    const buttons = Array.from(container?.querySelectorAll<HTMLButtonElement>("button[data-menu-choice]:not(:disabled)") ?? []);
    if (!buttons.length) return;
    event.preventDefault(); event.stopPropagation();
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const index = event.key === "Home" ? 0 : event.key === "End" || (current === -1 && event.key === "ArrowUp") ? buttons.length - 1
      : (current + (event.key === "ArrowUp" ? -1 : 1) + buttons.length) % buttons.length;
    buttons[index].focus(); buttons[index].scrollIntoView?.({ block: "nearest" });
  }

  const host = trigger.current?.closest<HTMLElement>('[role="dialog"]') ?? document.body;
  const sideStyle: CSSProperties = submenu ? { position: "fixed", width: 210,
    left: Math.max(8, submenu.anchor.right + 210 < window.innerWidth - 8 ? submenu.anchor.right + 4 : submenu.anchor.left - 214),
    top: Math.max(8, Math.min(submenu.anchor.top, window.innerHeight - 300)), maxHeight: Math.min(290, window.innerHeight - 16),
  } : {};

  return <>
    <button ref={trigger} type="button" aria-label={t("society.chat.model")} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
      disabled={busy || saving} title={busy ? t("society.chat.model_busy") : t("society.chat.model")}
      onClick={() => { if (open) close(); else { setSearch(""); setAccounts({}); setError(null); setOpen(true); } }}
      className="flex max-w-full items-center gap-1.5 rounded-full px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
      {agent.provider ? <ProviderLogo providerId={agent.provider} label={agent.providerLabel} size="sm" /> : null}
      <span className="truncate">{agent.model || agent.providerLabel || t("society.chat.model_default")}</span>
      {agent.effort ? <span className="shrink-0 opacity-70">{effortLabel(agent.effort, t)}</span> : null}
      <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
    </button>
    {open ? createPortal(<>
      <div ref={panel} id={menuId} style={position} className="z-[80] flex flex-col overflow-hidden rounded-md border border-border bg-popover text-popover-foreground shadow-float"
        onKeyDown={(event) => moveFocus(event, panel.current)}>
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2.5">
          <Search className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
          <input ref={input} aria-label={t("society.chat.model_search")} placeholder={t("society.chat.model_search")}
            value={search} onChange={(event) => { setSearch(event.target.value); setSubmenu(null); }}
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground" />
          {refreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-label={t("society.chat.models_loading")} /> : null}
        </div>
        <div role="menu" aria-label={t("society.chat.model")} aria-busy={saving} className="min-h-0 overflow-y-auto overscroll-contain py-1" onScroll={() => setSubmenu(null)}>
          {loading ? <p role="status" className="px-3 py-3 text-xs text-muted-foreground">{t("society.create.catalog_loading")}</p> : null}
          {failed ? <p role="alert" className="px-3 py-3 text-xs text-destructive">{t("society.chat.model_load_failed")}</p> : null}
          {!loading && !failed && groups.length === 0 ? <p className="px-3 py-3 text-xs text-muted-foreground">{t(refreshing ? "society.chat.models_loading" : "society.chat.model_no_matches")}</p> : null}
          {!loading && !failed ? groups.map(({ seat, title, models }) => <div key={seat.provider.id} role="group" aria-label={title}>
            <div className="sticky top-0 z-10 flex items-center gap-1 bg-popover px-3 pb-1 pt-3">
              <span className="min-w-0 flex-1 truncate text-[11px] font-semibold uppercase tracking-wide text-muted-foreground" title={title}>{title}</span>
              {seat.accounts.length ? <button type="button" disabled={busy || saving} data-menu-choice
                aria-label={`${t("society.chat.model_account")}: ${title}`} aria-haspopup="menu" aria-expanded={submenu?.provider === seat.provider.id && !submenu.model}
                onClick={(event) => setSubmenu({ provider: seat.provider.id, anchor: event.currentTarget.getBoundingClientRect() })}
                className="flex max-w-[140px] items-center gap-1 rounded px-1 py-0.5 text-[10px] text-muted-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
                <Users className="h-3 w-3 shrink-0" aria-hidden /><span className="truncate">{seat.accounts.find((account) => account.id === currentAccount(seat))?.label ?? t("society.chat.model_active_account")}</span><ChevronDown className="h-2.5 w-2.5 shrink-0" aria-hidden />
              </button> : null}
            </div>
            {models.map((model) => {
              const selected = agent.provider === seat.provider.id && agent.model === model.id && (agent.accountId ?? "") === currentAccount(seat);
              const effort = preferredEffort(seat, model);
              return <div key={model.id} className={cn("group flex items-center", selected && "bg-secondary/70")}>
                <button type="button" role="menuitemradio" aria-checked={selected} disabled={busy || saving} data-menu-choice
                  onKeyDown={(event) => {
                    if (event.key === "ArrowRight" && effortsFor(seat, model.id).length) {
                      event.preventDefault(); event.stopPropagation();
                      setSubmenu({ provider: seat.provider.id, model, anchor: event.currentTarget.getBoundingClientRect() });
                    }
                  }}
                  onClick={() => void save(seat, model)} className={cn(menuRow, "min-w-0 flex-1")} title={model.id}>
                  <span className="min-w-0 truncate">{model.label || model.id}<span className="ml-1 text-muted-foreground">{effort ? effortLabel(effort, t) : ""}</span></span>
                  {selected ? <Check className="ml-auto h-3.5 w-3.5 shrink-0" aria-hidden /> : null}
                </button>
                {effortsFor(seat, model.id).length ? <button type="button" disabled={busy || saving} aria-label={`${t("society.chat.effort")}: ${model.label}`} aria-haspopup="menu"
                  onClick={(event) => setSubmenu({ provider: seat.provider.id, model, anchor: event.currentTarget.getBoundingClientRect() })}
                  className="mr-1 rounded p-1.5 text-muted-foreground opacity-60 hover:bg-secondary hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
                  <ChevronRight className="h-3 w-3" aria-hidden />
                </button> : null}
              </div>;
            })}
          </div>) : null}
        </div>
        {error ? <p role="alert" className="shrink-0 border-t border-border px-3 py-2 text-xs text-destructive">{error}</p> : null}
        <div className="shrink-0 border-t border-border py-1">
          <button type="button" onClick={() => void refresh()} disabled={refreshing || saving} className={cn(menuRow, "text-muted-foreground")}>
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} aria-hidden />{t("society.chat.model_refresh")}
          </button>
        </div>
      </div>
      {submenu && sideSeat ? <div ref={sidePanel} style={sideStyle} role="menu" aria-label={t(submenu.model ? "society.chat.effort" : "society.chat.model_account")}
        className="z-[81] overflow-y-auto rounded-md border border-border bg-popover py-1 shadow-float" onKeyDown={(event) => moveFocus(event, sidePanel.current)}>
        <p className="px-3 py-2 text-[11px] font-semibold uppercase text-muted-foreground">{submenu.model ? t("society.chat.effort") : t("society.chat.model_account")}</p>
        {submenu.model ? effortsFor(sideSeat, submenu.model.id).map((effort) => <button key={effort} type="button" role="menuitemradio" data-menu-choice
          aria-checked={effort === preferredEffort(sideSeat, submenu.model!)} disabled={busy || saving}
          onClick={() => void save(sideSeat, submenu.model!, effort)} className={menuRow}>
          {effortLabel(effort, t)}{effort === preferredEffort(sideSeat, submenu.model!) ? <Check className="ml-auto h-3.5 w-3.5" aria-hidden /> : null}
        </button>) : [{ id: "", label: t("society.chat.model_active_account") }, ...sideSeat.accounts].map((account) => <button key={account.id} type="button" role="menuitemradio" data-menu-choice
          aria-checked={account.id === currentAccount(sideSeat)} disabled={busy || saving}
          onClick={() => { setAccounts((previous) => ({ ...previous, [sideSeat.provider.id]: account.id })); setSubmenu(null); input.current?.focus(); }} className={menuRow}>
          <span className="truncate">{account.label}</span>{account.id === currentAccount(sideSeat) ? <Check className="ml-auto h-3.5 w-3.5" aria-hidden /> : null}
        </button>)}
      </div> : null}
    </>, host) : null}
  </>;
}
