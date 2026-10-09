import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, ChevronRight, Loader2, RefreshCw, Search, Star, Users } from "lucide-react";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { brainValue, effortLabel } from "@/components/agentchat/AgentComposer";
import { RailButton, useFavoriteModels } from "@/components/agentchat/ComposerBrainPicker";
import { useAgentChat } from "@/components/agentchat/AgentChatStoreContext";
import { useT } from "@/i18n";
import type { CuratedModel } from "@/lib/agentChatApi";
import { cn } from "@/lib/utils";
import { effortsFor, type BrainSeat } from "../create/brainPicker";
import { RuntimeProviderUnsupported, useUpdateAgentModel, type SocietyAgent } from "../data";
import { rankModels } from "@/lib/modelRanking";
import { orderBy, useProviderOrder } from "@/lib/providerOrder";
import { collapsibleModels, matchesModel, modelEffort, modelGroupOrder, modelSeats, providerTitle, runtimeSeats, seatBlocked, visibleModels } from "./modelChoices";
import { blockedReasonKey } from "../create/seatChoice";

import { useModelMenuData } from "./useModelMenuData";
import { RuntimeStatusRow, useAgentRuntimes } from "../card/RuntimePicker";
import { providerChoices, API_KEY_ACCOUNT, SUBSCRIPTION_ACCOUNT } from "../create/seatChoice";
import { modelAccessFamily, preferredModelAccess, useModelAccess } from "@/lib/modelAccess";
import { ModelAccessSwitch } from "@/components/providers/ModelAccessSwitch";

type Submenu = { provider: string; model?: CuratedModel; anchor: DOMRect };
type PickerSeat = BrainSeat & { key: string; family: string; accessAccount: string };
const menuRow = "flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] text-popover-foreground hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none disabled:opacity-45";

const FAVORITES = "favorites";

/**
 * A searchable provider catalog, with account and effort submenus. A rail on
 * the left picks which provider's models are listed (plus a starred tab);
 * typing searches every provider at once.
 */
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
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // Rows are prepared while closed, so start on the agent's own provider.
  const [section, setSection] = useState(agent.provider);
  const [access, rememberAccess] = useModelAccess();
  const [favorites, toggleFavorite] = useFavoriteModels();
  const [position, setPosition] = useState<CSSProperties>({});
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const sidePanel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const inFlight = useRef(false);
  const [host, setHost] = useState<HTMLElement | null>(null);

  useLayoutEffect(() => {
    setHost(trigger.current?.closest<HTMLElement>('[role="dialog"]') ?? document.body);
  }, []);

  const chatCatalog = useAgentChat((state) => state.surface === "society" ? state.catalog : null);
  const chatConnections = useAgentChat((state) => state.connections);
  const { options, providers, live, modelErrors, loading: catalogLoading, refreshing: catalogRefreshing, failed: catalogFailed, refresh: refreshData } = useModelMenuData(chatCatalog, chatConnections,
    { [agent.provider]: agent.accountId ?? "", ...Object.fromEntries(Object.entries(accounts).map(([key, account]) => [key.slice(0, key.lastIndexOf(":")), account])) });
  const defaultModelLabel = t("agent_chat.model_default");
  const runtimes = useAgentRuntimes();
  const external = Boolean(agent.runtime && agent.runtime !== "jarvis");
  const loading = catalogLoading || (external && runtimes.isPending);
  const refreshing = catalogRefreshing || (external && runtimes.isFetching);
  const failed = catalogFailed || (external && runtimes.isError);
  const supportedKey = (Array.isArray(runtimes.data?.supported_providers) ? runtimes.data.supported_providers : []).join(",");
  const gatewayKey = (Array.isArray(runtimes.data?.subscription_providers) ? runtimes.data.subscription_providers : []).join(",");
  const loginKey = (Array.isArray(runtimes.data?.login_providers) ? runtimes.data.login_providers : []).join(",");
  const seats = useMemo(() => {
    const all = modelSeats(options, providers ?? [], live, defaultModelLabel);
    // Hermes / OpenClaw run on an API key, a local model or a subscription
    // Jarvis' model gateway serves; never a subscription CLI's own loop.
    if (!external) return all;
    const list = (key: string) => key.split(",").filter(Boolean);
    return runtimeSeats(all, list(supportedKey), list(gatewayKey), list(loginKey));
  }, [options, providers, live, defaultModelLabel, external, supportedKey, gatewayKey, loginKey]);
  const accessKey = JSON.stringify(runtimes.data?.access ?? {});
  const blockedKey = JSON.stringify(external ? runtimes.data?.access_blocked ?? {} : {});
  const choices = useMemo(() => providerChoices(seats, JSON.parse(accessKey), external, JSON.parse(blockedKey)).sort((a, b) =>
    Math.min(...a.options.map((option) => modelGroupOrder(option.seat))) - Math.min(...b.options.map((option) => modelGroupOrder(option.seat)))), [seats, accessKey, blockedKey, external]);
  const accessSeats = useMemo<PickerSeat[]>(() => choices.flatMap((choice) => choice.options.map((option) => ({
    ...option.seat, key: `${option.seat.provider.id}:${option.kind}`, family: choice.id, accessAccount: option.accountId,
  }))), [choices]);
  const currentAccount = (seat: PickerSeat) => {
    if (seat.kind !== "subscription") return seat.accessAccount || (agent.provider === seat.provider.id ? agent.accountId ?? "" : "");
    const stored = agent.provider === seat.provider.id ? agent.accountId ?? "" : "";
    return accounts[seat.key] ?? (stored && stored !== API_KEY_ACCOUNT && stored !== SUBSCRIPTION_ACCOUNT ? stored : seat.accessAccount);
  };
  const blockedFor = (seat: PickerSeat) => external ? seatBlocked(seat, runtimes.data?.access_blocked, currentAccount(seat)) : "";
  const preferredEffort = (seat: BrainSeat, model: CuratedModel) => modelEffort(seat, model.id, seat.provider.id === agent.provider ? agent.effort : seat.provider.default_effort);
  // useT returns a new function each render; memoize by its actual labels.
  const titleKey = JSON.stringify(accessSeats.map((seat) => providerTitle(seat, t)));
  const groups = useMemo(() => {
    const titles = JSON.parse(titleKey) as string[];
    return accessSeats.map((seat, index) => {
      const matched = seat.provider.curated_models.filter((model) => matchesModel(seat, model, search, titles[index]));
      // Newest of each model line first; earlier versions fold away. Catalogs
      // with their own fold (OpenCode, OpenRouter) keep their order.
      const ranked = collapsibleModels(seat) ? { current: matched, older: [] } : rankModels(matched);
      return { seat, title: titles[index], models: [...ranked.current, ...ranked.older], older: new Set(ranked.older) };
    }).filter((group) => group.models.length > 0).sort((a, b) =>
      modelGroupOrder(a.seat) - modelGroupOrder(b.seat) || a.title.localeCompare(b.title));
  }, [accessSeats, search, titleKey]);
  const sideSeat = accessSeats.find((seat) => seat.key === submenu?.provider);
  const browsing = !search.trim();
  const activeChoice = choices.find((choice) => choice.id === section || choice.options.some((option) => option.seat.provider.id === section)) ?? choices[0];
  const activeOption = activeChoice ? preferredModelAccess(activeChoice.options, access[activeChoice.id]) : undefined;
  const showFavorites = browsing && section === FAVORITES;
  const shownGroups = browsing ? groups.filter((group) => group.seat.family === activeChoice?.id && group.seat.kind === activeOption?.kind) : groups;
  const [providerOrder, moveProvider] = useProviderOrder();
  const railChoices = orderBy(choices, (choice) => choice.id, providerOrder.map(modelAccessFamily));
  const railIds = railChoices.map((choice) => choice.id);
  const starred = useMemo(() => favorites.flatMap((value) => {
    for (const seat of accessSeats) {
      const sameProvider = accessSeats.filter((entry) => entry.provider.id === seat.provider.id);
      if (preferredModelAccess(sameProvider, access[seat.family]) !== seat) continue;
      const model = seat.provider.curated_models.find((entry) => brainValue(seat.provider.id, entry.id) === value);
      if (model) return [{ seat, model }];
    }
    return [];
  }), [favorites, accessSeats, access]);
  const pickSection = (id: string) => { setSubmenu(null); setSearch(""); setSection(id); input.current?.focus({ preventScroll: true }); };

  function close() {
    if (inFlight.current) return;
    setOpen(false); setSubmenu(null); setSearch(""); trigger.current?.focus();
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
    let frame = 0;
    const place = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const above = rect.top > window.innerHeight - rect.bottom;
      const width = Math.min(380, window.innerWidth - 16);
      const next: CSSProperties = { position: "fixed", width, left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
        maxHeight: Math.min(560, Math.max(120, (above ? rect.top : window.innerHeight - rect.bottom) - 16)),
        ...(above ? { bottom: window.innerHeight - rect.top + 6 } : { top: rect.bottom + 6 }),
      };
      setPosition((previous) => previous.left === next.left && previous.top === next.top && previous.bottom === next.bottom && previous.width === next.width && previous.maxHeight === next.maxHeight ? previous : next);

    };
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; place(); });
    };
    const scroll = (event: Event) => {
      if (!panel.current?.contains(event.target as Node)) schedule();
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    // Resizing the composer or its ancestors can move an unchanged trigger.
    for (let node: HTMLElement | null = trigger.current; node; node = node.parentElement) observer?.observe(node);
    // Prepare geometry while hidden; opening must not force a page layout.
    place();
    window.addEventListener("resize", schedule);
    window.addEventListener("scroll", scroll, { capture: true, passive: true });
    const dialog = trigger.current?.closest('[role="dialog"]');
    dialog?.addEventListener("animationend", schedule);
    return () => {
      cancelAnimationFrame(frame); observer?.disconnect();
      window.removeEventListener("resize", schedule);
      window.removeEventListener("scroll", scroll, true);
      dialog?.removeEventListener("animationend", schedule);
    };
  }, []);

  useLayoutEffect(() => {
    if (open) input.current?.focus({ preventScroll: true });
  }, [open]);

  useEffect(() => {
    if (submenu) sidePanel.current?.querySelector<HTMLButtonElement>("button[data-menu-choice]")?.focus();
  }, [submenu]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      const node = event.target as Node;
      if (!trigger.current?.contains(node) && !panel.current?.contains(node) && !sidePanel.current?.contains(node) && !inFlight.current) {
        setOpen(false); setSubmenu(null); setSearch("");
      }
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  async function save(seat: PickerSeat, model: CuratedModel, effort = preferredEffort(seat, model)) {
    if (busy || inFlight.current || blockedFor(seat)) return;
    inFlight.current = true; setSaving(true); onSavingChange(true); setError(null);
    try {
      await update(agent.agentId, { provider: seat.provider.id, model: model.id, effort, account_id: currentAccount(seat) });
      rememberAccess(seat.family, seat.kind);
      setOpen(false); setSubmenu(null); trigger.current?.focus();
    } catch (err) {
      setError(err instanceof RuntimeProviderUnsupported
        ? t("society.runtime.provider_unsupported")
        : `${t("society.chat.model_save_failed")} (${err instanceof Error ? err.message : String(err)})`);
    } finally {
      inFlight.current = false; setSaving(false); onSavingChange(false);
    }
  }

  async function refresh() {
    setSubmenu(null);
    await Promise.all([refreshData(), runtimes.refetch()]);
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

  const sideStyle: CSSProperties = submenu ? { position: "fixed", width: 210,
    left: Math.max(8, submenu.anchor.right + 210 < window.innerWidth - 8 ? submenu.anchor.right + 4 : submenu.anchor.left - 214),
    top: Math.max(8, Math.min(submenu.anchor.top, window.innerHeight - 300)), maxHeight: Math.min(290, window.innerHeight - 16),
  } : {};

  /** One model row; `owner` adds the provider's name under it (the starred tab mixes providers). */
  const row = (seat: PickerSeat, model: CuratedModel, owner = false) => {
    const selected = agent.provider === seat.provider.id && agent.model === model.id
      && (agent.accountId ?? "") === currentAccount(seat);
    const effort = preferredEffort(seat, model);
    const value = brainValue(seat.provider.id, model.id);
    const isFavorite = favorites.includes(value);
    const starLabel = t(isFavorite ? "agent_chat.favorite_remove" : "agent_chat.favorite_add");
    return <div key={value} className={cn("group flex items-center", selected && "bg-secondary/70")}>
      <button type="button" role="menuitemradio" aria-checked={selected} disabled={busy || saving || Boolean(blockedFor(seat))} data-menu-choice
        onKeyDown={(event) => {
          if (event.key === "ArrowRight" && effortsFor(seat, model.id).length) {
            event.preventDefault(); event.stopPropagation();
            setSubmenu({ provider: seat.key, model, anchor: event.currentTarget.getBoundingClientRect() });
          }
        }}
        onClick={() => void save(seat, model)} className={cn(menuRow, "min-w-0 flex-1")} title={model.id}>
        <span className="flex min-w-0 flex-col">
          <span className="min-w-0 truncate">{model.label || model.id}<span className="ml-1 text-muted-foreground">{effort ? effortLabel(effort, t) : ""}</span></span>
          {owner ? <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
            <ProviderLogo providerId={seat.provider.id} label={seat.provider.label} size="sm" /><span className="truncate">{providerTitle(seat, t)}</span>
          </span> : null}
        </span>
        {selected ? <Check className="ml-auto h-3.5 w-3.5 shrink-0" aria-hidden /> : null}
      </button>
      <button type="button" aria-label={starLabel} aria-pressed={isFavorite} title={starLabel} data-testid="agent-model-star"
        onClick={() => toggleFavorite(value)}
        className={cn("rounded p-1.5 hover:bg-secondary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          isFavorite ? "text-warning" : "text-faint-foreground hover:text-foreground")}>
        <Star className={cn("h-3 w-3", isFavorite && "fill-current")} aria-hidden />
      </button>
      {effortsFor(seat, model.id).length ? <button type="button" disabled={busy || saving} aria-label={`${t("society.chat.effort")}: ${model.label}`} aria-haspopup="menu"
        onClick={(event) => setSubmenu({ provider: seat.key, model, anchor: event.currentTarget.getBoundingClientRect() })}
        className="mr-1 rounded p-1.5 text-muted-foreground opacity-60 hover:bg-secondary hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
        <ChevronRight className="h-3 w-3" aria-hidden />
      </button> : null}
    </div>;
  };

  return <>
    <button ref={trigger} data-chat-model-trigger type="button" aria-label={t("society.chat.model")} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
      disabled={busy || saving} title={busy ? t("society.chat.model_busy") : t("society.chat.model")}
      onClick={() => { if (open) close(); else { setSearch(""); setAccounts({}); setExpanded({}); setError(null); setSection(agent.provider || ""); setOpen(true); } }}
      className="flex max-w-full items-center gap-1.5 rounded-full px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
      {external ? <ProviderLogo providerId={agent.runtime!} label={t(`society.runtime.${agent.runtime}`)} size="sm" /> : null}
      {agent.provider ? <ProviderLogo providerId={agent.provider} label={agent.providerLabel} size="sm" /> : null}
      <span className="truncate">{agent.model || agent.providerLabel || t("society.chat.model_default")}</span>
      {agent.effort ? <span className="shrink-0 opacity-70">{effortLabel(agent.effort, t)}</span> : null}
      <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
    </button>
    {host && (open || !loading) ? createPortal(<>
      <div ref={panel} id={menuId} aria-hidden={!open} style={{ ...position, visibility: open ? "visible" : "hidden", contain: "layout paint style" }} className="fixed z-[80] flex overflow-hidden rounded-md border border-border bg-popover text-popover-foreground shadow-float"
        onKeyDown={(event) => moveFocus(event, panel.current)}>
        {!loading && !failed && railChoices.length ? <div role="toolbar" aria-orientation="vertical" aria-label={t("agent_chat.pick_provider")} data-testid="agent-model-rail"
          className="scrollbar-jarvis flex w-12 shrink-0 flex-col gap-1 overflow-y-auto border-r border-border bg-sheen/[0.03] p-1.5">
          <RailButton active={showFavorites} label={t("agent_chat.favorites")} onSelect={() => pickSection(FAVORITES)} testId="agent-model-rail-favorites">
            <Star className="h-4 w-4 fill-current" aria-hidden />
          </RailButton>
          <span className="mx-1 my-0.5 border-b border-border" aria-hidden />
          {railChoices.map((choice, index) => <RailButton key={choice.id} active={browsing && !showFavorites && activeChoice?.id === choice.id}
            label={choice.options.length > 1 ? choice.label : providerTitle(choice.options[0].seat, t)}
            reorder={{ id: choice.id, onMove: (dragged, target) => moveProvider(railIds, dragged, target),
              onStep: (step) => { const target = railIds[index + step]; if (target) moveProvider(railIds, choice.id, target); } }}
            onSelect={() => pickSection(choice.id)} testId={`agent-model-rail-${choice.options.length === 1 ? choice.options[0].seat.provider.id : choice.id}`}>
            <span className="inline-flex scale-[1.3]"><ProviderLogo providerId={choice.logo} label={choice.label} size="sm" /></span>
          </RailButton>)}
        </div> : null}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {agent.tier !== "lead" ? (
          <div className="shrink-0 border-b border-border px-3 py-2.5">
            <RuntimeStatusRow runtime={agent.runtime ?? "jarvis"} />
          </div>
        ) : null}
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2.5">
          <Search className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
          <input ref={input} aria-label={t("society.chat.model_search")} placeholder={t("society.chat.model_search")}
            value={search} onChange={(event) => { setSearch(event.target.value); setSubmenu(null); }}
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground" />
          {refreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-label={t("society.chat.models_loading")} /> : null}
        </div>
        {browsing && !showFavorites && activeChoice && activeOption ? <ModelAccessSwitch
          options={activeChoice.options} value={activeOption.kind} disabled={busy || saving}
          onChange={(kind) => { setSubmenu(null); rememberAccess(activeChoice.id, kind); }} /> : null}
        <div role="menu" aria-label={t("society.chat.model")} aria-busy={saving} className="min-h-0 overflow-y-auto overscroll-contain py-1" onScroll={() => setSubmenu(null)}>
          {loading ? <p role="status" className="px-3 py-3 text-xs text-muted-foreground">{t("society.create.catalog_loading")}</p> : null}
          {failed ? <p role="alert" className="px-3 py-3 text-xs text-destructive">{t("society.chat.model_load_failed")}</p> : null}
          {browsing && !showFavorites && activeOption && modelErrors.includes(activeOption.seat.provider.id)
            ? <p role="alert" className="px-3 py-3 text-xs text-destructive">{t("society.chat.model_load_failed")}</p> : null}
          {!loading && !failed && groups.length === 0 ? <p className="px-3 py-3 text-xs text-muted-foreground">{t(refreshing ? "society.chat.models_loading" : "society.chat.model_no_matches")}</p> : null}
          {!loading && !failed && showFavorites ? (starred.length
            ? starred.map(({ seat, model }) => row(seat, model, true))
            : <p className="px-3 py-3 text-xs text-muted-foreground">{t("agent_chat.favorites_empty")}</p>) : null}
          {!loading && !failed && !showFavorites ? shownGroups.map(({ seat, title, models, older }) => {
            const isExpanded = expanded[seat.key] ?? false;
            const shown = visibleModels(seat, models, isExpanded, search);
            const foldable = collapsibleModels(seat) && !search.trim();
            const isRouter = seat.provider.family === "openrouter";
            const choicesId = `${menuId}-${seat.key}-models`;
            const toggle = () => { setSubmenu(null); setExpanded((previous) => ({ ...previous, [seat.key]: !isExpanded })); };
            const olderKey = `older:${seat.key}`;
            const folded = search.trim() ? [] : shown.filter((model) => older.has(model));
            const lineup = folded.length ? shown.filter((model) => !older.has(model)) : shown;
            const olderOpen = expanded[olderKey] ?? folded.some((model) => agent.provider === seat.provider.id && agent.model === model.id);
            const toggleOlder = () => { setSubmenu(null); setExpanded((previous) => ({ ...previous, [olderKey]: !olderOpen })); };
            const toRow = (model: CuratedModel) => row(seat, model);
            return <div key={seat.key} role="group" aria-label={title}>
            <div className="sticky top-0 z-10 flex items-center gap-1 bg-popover px-3 pb-1 pt-3">
              {foldable && isRouter ? <button type="button" data-menu-choice disabled={busy || saving}
                onClick={toggle} aria-label={title} aria-expanded={isExpanded} aria-controls={choicesId}
                className="flex min-w-0 flex-1 items-center gap-1 rounded text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
                {isExpanded ? <ChevronDown className="h-3 w-3 shrink-0" aria-hidden /> : <ChevronRight className="h-3 w-3 shrink-0" aria-hidden />}
                <span className="truncate">{title}</span><span className="ml-auto">{models.length}</span>
              </button> : <span className="min-w-0 flex-1 truncate text-[11px] font-semibold uppercase tracking-wide text-muted-foreground" title={title}>{title}</span>}
              {seat.accounts.length ? <button type="button" disabled={busy || saving} data-menu-choice
                aria-label={`${t("society.chat.model_account")}: ${title}`} aria-haspopup="menu" aria-expanded={submenu?.provider === seat.key && !submenu.model}
                onClick={(event) => setSubmenu({ provider: seat.key, anchor: event.currentTarget.getBoundingClientRect() })}
                className="flex max-w-[140px] items-center gap-1 rounded px-1 py-0.5 text-[10px] text-muted-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
                <Users className="h-3 w-3 shrink-0" aria-hidden /><span className="truncate">{seat.accounts.find((account) => account.id === currentAccount(seat))?.label ?? t("society.chat.model_active_account")}</span><ChevronDown className="h-2.5 w-2.5 shrink-0" aria-hidden />
              </button> : null}
            </div>
            {blockedFor(seat) ? <p role="note" className="px-3 pb-1 text-xs text-warning" data-testid={`agent-model-blocked-${seat.provider.id}`}>
              {t(blockedReasonKey(blockedFor(seat)))}
            </p> : null}
            <div id={choicesId}>
              {lineup.map(toRow)}
              {folded.length ? <button type="button" data-menu-choice disabled={busy || saving} aria-expanded={olderOpen} onClick={toggleOlder}
                className={cn(menuRow, "text-muted-foreground")}>
                <span className="min-w-0 flex-1 truncate">{t("agent_chat.older_models")}</span>
                <span className="text-xs">{folded.length}</span>
                <ChevronRight className={cn("h-3 w-3 shrink-0 transition-transform", olderOpen && "rotate-90")} aria-hidden />
              </button> : null}
              {olderOpen ? folded.map(toRow) : null}
            </div>
            {foldable && !isRouter && (isExpanded || shown.length < models.length) ? <button type="button" data-menu-choice
              disabled={busy || saving} aria-expanded={isExpanded} aria-controls={choicesId} onClick={toggle}
              className={cn(menuRow, "text-muted-foreground")}>
              {isExpanded ? <ChevronDown className="h-3 w-3" aria-hidden /> : <ChevronRight className="h-3 w-3" aria-hidden />}
              {t(isExpanded ? "society.chat.model_show_fewer" : "society.chat.model_show_more")}
              {!isExpanded ? <span className="ml-auto text-xs">{models.length - shown.length}</span> : null}
            </button> : null}
          </div>; }) : null}
        </div>
        {error ? <p role="alert" className="shrink-0 border-t border-border px-3 py-2 text-xs text-destructive">{error}</p> : null}
        <div className="shrink-0 border-t border-border py-1">
          <button type="button" onClick={() => void refresh()} disabled={refreshing || saving} className={cn(menuRow, "text-muted-foreground")}>
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} aria-hidden />{t("society.chat.model_refresh")}
          </button>
        </div>
        </div>
      </div>
      {open && submenu && sideSeat ? <div ref={sidePanel} style={sideStyle} role="menu" aria-label={t(submenu.model ? "society.chat.effort" : "society.chat.model_account")}
        className="z-[81] overflow-y-auto rounded-md border border-border bg-popover py-1 shadow-float" onKeyDown={(event) => moveFocus(event, sidePanel.current)}>
        <p className="px-3 py-2 text-[11px] font-semibold uppercase text-muted-foreground">{submenu.model ? t("society.chat.effort") : t("society.chat.model_account")}</p>
        {submenu.model ? effortsFor(sideSeat, submenu.model.id).map((effort) => <button key={effort} type="button" role="menuitemradio" data-menu-choice
          aria-checked={effort === preferredEffort(sideSeat, submenu.model!)} disabled={busy || saving}
          onClick={() => void save(sideSeat, submenu.model!, effort)} className={menuRow}>
          {effortLabel(effort, t)}{effort === preferredEffort(sideSeat, submenu.model!) ? <Check className="ml-auto h-3.5 w-3.5" aria-hidden /> : null}
        </button>) : [{ id: sideSeat.accessAccount, label: t("society.chat.model_active_account") }, ...sideSeat.accounts].map((account) => <button key={account.id} type="button" role="menuitemradio" data-menu-choice
          aria-checked={account.id === currentAccount(sideSeat)} disabled={busy || saving}
          onClick={() => { setAccounts((previous) => ({ ...previous, [sideSeat.key]: account.id || sideSeat.accessAccount })); setSubmenu(null); input.current?.focus(); }} className={menuRow}>
          <span className="truncate">{account.label}</span>{account.id === currentAccount(sideSeat) ? <Check className="ml-auto h-3.5 w-3.5" aria-hidden /> : null}
        </button>)}
      </div> : null}
    </>, host) : null}
  </>;
}
