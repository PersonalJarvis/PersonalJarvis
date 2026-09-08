import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";

import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { effortLabel } from "@/components/agentchat/AgentComposer";
import { useT } from "@/i18n";
import { fetchAgentChatCatalog, fetchAgentConnections, fetchProviderModels, type CuratedModel } from "@/lib/agentChatApi";
import { fetchSocietyProviders } from "@/lib/societyApi";
import { joinProviderOptions } from "@/store/agentChat";
import { brainSeats, effortsFor, modelsFor, type BrainSeat } from "../create/brainPicker";
import { useUpdateAgentModel, type SocietyAgent } from "../data";

const selectClass = "mt-1 w-full min-w-0 rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

async function modelLists(ids: string[]): Promise<Record<string, CuratedModel[]>> {
  return Object.fromEntries(await Promise.all(ids.map(async (id) => {
    // An unavailable local endpoint cannot offer an installed model.
    const models = await fetchProviderModels(id).catch(() => []);
    return [id, models.map((m) => ({ id: m.id, label: m.label ?? m.name ?? m.id }))];
  })));
}

/** A per-agent choice: the roster and its chat move together through the model route. */
export function AgentModelPicker({ agent, busy, onSavingChange }: {
  agent: SocietyAgent;
  busy: boolean;
  onSavingChange: (saving: boolean) => void;
}) {
  const t = useT();
  const update = useUpdateAgentModel();
  const [open, setOpen] = useState(false);
  const [providerId, setProviderId] = useState(agent.provider);
  const [model, setModel] = useState(agent.model);
  const [effort, setEffort] = useState(agent.effort);
  const [accountId, setAccountId] = useState(agent.accountId ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const inFlight = useRef(false);

  const catalog = useQuery({ queryKey: ["agent-chat", "catalog", "society"], queryFn: () => fetchAgentChatCatalog("society"), enabled: open, staleTime: 60_000 });
  const connections = useQuery({ queryKey: ["agent-chat", "connections"], queryFn: fetchAgentConnections, enabled: open, staleTime: 60_000 });
  const providers = useQuery({ queryKey: ["society", "providers"], queryFn: fetchSocietyProviders, enabled: open, staleTime: 60_000 });
  const keylessIds = useMemo(() => (catalog.data?.providers ?? []).filter((p) => p.keyless && p.models_source === "live").map((p) => p.id), [catalog.data]);
  const localModels = useQuery({ queryKey: ["agent-chat", "live-models", "keyless", keylessIds], queryFn: () => modelLists(keylessIds), enabled: open && catalog.isSuccess, staleTime: 60_000 });
  const chosenProvider = catalog.data?.providers.find((p) => p.id === providerId);
  const pickedModels = useQuery({ queryKey: ["agent-chat", "live-models", providerId], queryFn: () => modelLists([providerId]), enabled: open && chosenProvider?.models_source === "live" && !chosenProvider.keyless, staleTime: 60_000 });
  const seats = useMemo(() => brainSeats(
    joinProviderOptions(catalog.data?.providers ?? [], connections.data ?? []),
    providers.data ?? [],
    { ...localModels.data, ...pickedModels.data },
    new Set(connections.data?.map((c) => c.jarvis)),
  ), [catalog.data, connections.data, providers.data, localModels.data, pickedModels.data]);
  const seat = seats.find((s) => s.provider.id === providerId) ?? null;
  const models = modelsFor(seat);
  const efforts = effortsFor(seat, model);
  const validEffort = efforts.includes(effort) ? effort : efforts.includes(seat?.provider.default_effort ?? "") ? seat!.provider.default_effort : efforts[0] ?? "";
  const loading = catalog.isLoading || connections.isLoading || providers.isLoading || localModels.isLoading;
  const failed = catalog.isError || connections.isError || providers.isError;
  const modelValid = Boolean(model.trim()) && (!models.length || models.some((m) => m.id === model));

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node) && !inFlight.current) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  function pickProvider(next: BrainSeat | undefined) {
    if (!next) return;
    setProviderId(next.provider.id);
    const options = modelsFor(next);
    setModel(options.find((m) => m.id === next.provider.default_model)?.id ?? options[0]?.id ?? next.provider.default_model);
    setEffort(next.provider.default_effort);
    setAccountId("");
  }

  async function save() {
    if (!seat || !modelValid || busy || inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    onSavingChange(true);
    setError(null);
    try {
      await update(agent.agentId, { provider: providerId, model: model.trim(), effort: validEffort, account_id: accountId });
      setOpen(false);
      trigger.current?.focus();
    } catch (err) {
      setError(`${t("society.chat.model_save_failed")} (${err instanceof Error ? err.message : String(err)})`);
    } finally {
      inFlight.current = false;
      setSaving(false);
      onSavingChange(false);
    }
  }

  return <div ref={root} className="relative w-fit max-w-full" onKeyDown={(event) => {
    if (open && event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (!saving) { setOpen(false); trigger.current?.focus(); }
    }
  }}>
    <button ref={trigger} type="button" aria-label={t("society.chat.model")} aria-expanded={open}
      disabled={busy || saving} title={busy ? t("society.chat.model_busy") : t("society.chat.model")}
      onClick={() => {
        if (!open) { setProviderId(agent.provider); setModel(agent.model); setEffort(agent.effort); setAccountId(agent.accountId ?? ""); setError(null); }
        setOpen(!open);
      }} className="flex max-w-full items-center gap-1.5 rounded-full px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
      {agent.provider ? <ProviderLogo providerId={agent.provider} label={agent.providerLabel} size="sm" /> : null}
      <span className="truncate">{agent.model || agent.providerLabel || t("society.chat.model_default")}</span>
      {agent.effort ? <span className="shrink-0 opacity-70">{agent.effort}</span> : null}
      <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
    </button>
    {open ? <div role="group" aria-label={t("society.chat.model")} className="absolute bottom-full left-0 z-30 mb-2 w-80 max-w-[calc(100vw-4rem)] rounded-lg border border-border bg-popover p-3 text-popover-foreground shadow-float">
      <p className="mb-3 text-xs text-muted-foreground">{t("society.chat.model_scope")}</p>
      {loading ? <p role="status" className="text-xs">{t("society.create.catalog_loading")}</p> : failed ? <p role="alert" className="text-xs text-destructive">{t("society.chat.model_load_failed")}</p> : <>
        <fieldset disabled={saving || busy} className="space-y-3">
          <label className="block text-xs">{t("society.chat.model_provider")}
            <select aria-label={t("society.chat.model_provider")} className={selectClass} value={providerId} onChange={(e) => pickProvider(seats.find((s) => s.provider.id === e.target.value))}>
              {!seat ? <option value={providerId}>{t("society.chat.model_choose_provider")}</option> : null}
              {(["subscription", "api", "local"] as const).map((kind) => <optgroup key={kind} label={t(`society.create.kind_${kind}`)}>
                {seats.filter((s) => s.kind === kind).map((s) => <option key={s.provider.id} value={s.provider.id}>{s.provider.label}</option>)}
              </optgroup>)}
            </select>
          </label>
          {seat ? <>
            <label className="block text-xs">{t("society.create.model")}
              {models.length ? <select aria-label={t("society.create.model")} className={selectClass} value={model} onChange={(e) => setModel(e.target.value)}>
                {!models.some((m) => m.id === model) ? <option value={model} disabled>{model || t("society.create.model")}</option> : null}
                {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select> : <input aria-label={t("society.create.model")} className={selectClass} value={model} onChange={(e) => setModel(e.target.value)} />}
            </label>
            {efforts.length ? <label className="block text-xs">{t("society.create.effort")}
              <select aria-label={t("society.create.effort")} className={selectClass} value={validEffort} onChange={(e) => setEffort(e.target.value)}>
                {efforts.map((level) => <option key={level} value={level}>{effortLabel(level, t)}</option>)}
              </select>
            </label> : null}
            {seat.accounts.length > 1 || accountId ? <label className="block text-xs">{t("society.chat.model_account")}
              <select aria-label={t("society.chat.model_account")} className={selectClass} value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                <option value="">{t("society.chat.model_active_account")}</option>
                {accountId && !seat.accounts.some((a) => a.id === accountId) ? <option value={accountId}>{accountId}</option> : null}
                {seat.accounts.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
              </select>
            </label> : null}
          </> : null}
        </fieldset>
        <p className="mt-3 text-xs text-muted-foreground">{t("society.chat.model_connections")}</p>
        {error ? <p role="alert" className="mt-2 text-xs text-destructive">{error}</p> : null}
        <button type="button" disabled={busy || saving || !seat || !modelValid || pickedModels.isFetching}
          onClick={() => void save()} className="mt-3 w-full rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50">
          {t(saving ? "society.chat.model_saving" : "society.chat.model_apply")}
        </button>
      </>}
    </div> : null}
  </div>;
}
