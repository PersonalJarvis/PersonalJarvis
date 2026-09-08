import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Save } from "lucide-react";
import { useT } from "@/i18n";
import { BrandedSelect } from "@/components/ui/select";
import { fetchAgentChatCatalog, fetchAgentConnections, fetchProviderModels, type CuratedModel } from "@/lib/agentChatApi";
import { fetchSocietyProviders, type SocietyAgentRow } from "@/lib/societyApi";
import { joinProviderOptions } from "@/store/agentChat";
import { brainSeats, effortsFor, modelsFor } from "../create/brainPicker";
import type { SocietyAgent } from "../data";
import { saveStudioAgent } from "./agentStudioData";

export function AgentStudioModel({ agent, sample, onGuardChange }: {
  agent: SocietyAgent;
  sample: boolean;
  onGuardChange: (dirty: boolean, busy: boolean) => void;
}) {
  const t = useT();
  const client = useQueryClient();
  const [selection, setSelection] = useState({ provider: agent.provider, model: agent.model, effort: agent.effort, account_id: "" });
  const [initial, setInitial] = useState<typeof selection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const catalog = useQuery({
    queryKey: ["society", "studio-model-catalog"],
    queryFn: async () => {
      const [catalog, connections, providers] = await Promise.all([
        fetchAgentChatCatalog("society"), fetchAgentConnections(), fetchSocietyProviders(),
      ]);
      return { catalog, connections, providers };
    },
    enabled: !sample, staleTime: 60_000, retry: false,
  });
  const current = useQuery({
    queryKey: ["society", "studio-model", agent.agentId],
    queryFn: async () => {
      const response = await fetch(`/api/society/agents/${encodeURIComponent(agent.agentId)}`);
      if (!response.ok) throw new Error(`Agent settings ${response.status}`);
      return (await response.json() as { agent: SocietyAgentRow }).agent;
    },
    enabled: !sample, retry: false,
  });
  useEffect(() => {
    if (initial || !current.data) return;
    const { provider, model, effort, account_id } = current.data;
    const next = { provider, model, effort, account_id };
    setInitial(next);
    setSelection(next);
  }, [current.data, initial]);

  const liveIds = useMemo(() => (catalog.data?.catalog.providers ?? [])
    .filter((provider) => provider.models_source === "live" && (provider.keyless || provider.id === selection.provider))
    .map((provider) => provider.id), [catalog.data, selection.provider]);
  const live = useQuery({
    queryKey: ["society", "studio-live-models", liveIds],
    queryFn: async () => Object.fromEntries(await Promise.all(liveIds.map(async (id) => [id,
      (await fetchProviderModels(id)).map((model): CuratedModel => ({ id: model.id, label: model.label ?? model.name ?? model.id })),
    ]))),
    enabled: Boolean(catalog.data), retry: false, staleTime: 60_000,
  });
  const seats = useMemo(() => {
    if (!catalog.data) return [];
    const { catalog: rows, connections, providers } = catalog.data;
    return brainSeats(joinProviderOptions(rows.providers, connections), providers, live.data ?? {}, new Set(connections.map((row) => row.jarvis)));
  }, [catalog.data, live.data]);
  const seat = seats.find((entry) => entry.provider.id === selection.provider) ?? null;
  const models = modelsFor(seat);
  const efforts = effortsFor(seat, selection.model);
  const dirty = Boolean(initial && JSON.stringify(initial) !== JSON.stringify(selection));
  useEffect(() => { onGuardChange(dirty, busy); }, [dirty, busy, onGuardChange]);
  const change = (next: typeof selection) => { setSelection(next); setSaved(false); setError(""); };
  const save = async () => {
    if (busy || !dirty || !seat) return;
    setBusy(true);
    setError("");
    try {
      const row = await saveStudioAgent(agent.agentId, selection, true);
      const next = { provider: row.provider, model: row.model, effort: row.effort, account_id: row.account_id };
      setSelection(next);
      setInitial(next);
      setSaved(true);
      void client.invalidateQueries({ queryKey: ["society", "roster"] });
      void client.invalidateQueries({ queryKey: ["society", "studio-model", agent.agentId] });
      void client.invalidateQueries({ queryKey: ["agent-chat"] });
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };
  const loading = catalog.isLoading || current.isLoading;
  const failed = catalog.isError || current.isError || live.isError;
  const fieldsOff = busy || loading || sample || !initial;
  return <div className="as-stack">
    <p className="as-note">{t("society.studio.model_hint")}</p>
    {sample ? <p className="as-note">{t("society.studio.sample")}</p> : null}
    {loading ? <p role="status">{t("society.chat.models_loading")}</p> : null}
    {failed ? <div role="alert" className="as-error">{t("society.studio.load_error")}
      <button type="button" className="as-text-button" onClick={() => { void catalog.refetch(); void current.refetch(); void live.refetch(); }}>{t("society.studio.retry")}</button>
    </div> : null}
    <fieldset disabled={busy || loading || sample || !initial} className="as-stack">
      <label className="as-field"><span>{t("society.studio.provider")}</span>
        <BrandedSelect
          ariaLabel={t("society.studio.provider")}
          value={selection.provider}
          disabled={fieldsOff}
          onValueChange={(value) => {
            const next = seats.find((entry) => entry.provider.id === value);
            if (!next) return;
            const model = next.provider.default_model;
            const ladder = effortsFor(next, model);
            change({ provider: next.provider.id, model, effort: ladder.includes(next.provider.default_effort) ? next.provider.default_effort : ladder[0] ?? "", account_id: "" });
          }}
          options={[
            ...(!seat ? [{ value: selection.provider, label: selection.provider || t("society.card.default_brain") }] : []),
            ...seats.map((entry) => ({ value: entry.provider.id, label: entry.provider.label })),
          ]}
        />
      </label>
      <label className="as-field"><span>{t("society.chat.model")}</span>
        <input list={`studio-models-${agent.agentId}`} value={selection.model} onChange={(event) => {
          const ladder = effortsFor(seat, event.target.value);
          change({ ...selection, model: event.target.value, effort: ladder.includes(selection.effort) ? selection.effort : ladder[0] ?? "" });
        }} placeholder={t("society.chat.model_provider_default")} />
        <datalist id={`studio-models-${agent.agentId}`}>{models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}</datalist>
      </label>
      <label className="as-field"><span>{t("society.chat.effort")}</span>
        <BrandedSelect
          ariaLabel={t("society.chat.effort")}
          value={selection.effort}
          disabled={fieldsOff}
          onValueChange={(value) => change({ ...selection, effort: value })}
          options={[
            { value: "", label: t("society.chat.effort_default") },
            ...(selection.effort && !efforts.includes(selection.effort) ? [{ value: selection.effort, label: selection.effort }] : []),
            ...efforts.filter(Boolean).map((effort) => ({ value: effort, label: effort })),
          ]}
        />
      </label>
      {seat?.kind === "subscription" ? <label className="as-field"><span>{t("society.studio.account")}</span>
        <BrandedSelect
          ariaLabel={t("society.studio.account")}
          value={selection.account_id}
          disabled={fieldsOff}
          onValueChange={(value) => change({ ...selection, account_id: value })}
          options={[
            { value: "", label: t("society.studio.active_account") },
            ...(selection.account_id && !seat.accounts.some((account) => account.id === selection.account_id)
              ? [{ value: selection.account_id, label: t("society.studio.saved_account") }]
              : []),
            ...seat.accounts.map((account) => ({ value: account.id, label: account.label })),
          ]}
        />
      </label> : null}
    </fieldset>
    {!loading && !failed && !sample && !seat ? <p className="as-note">{t("society.studio.connect_hint")}</p> : null}
    {error ? <p className="as-error" role="alert">{error}</p> : null}
    <div className="as-model-actions">
      <span role="status">{saved ? <><Check size={14} aria-hidden />{t("society.studio.saved")}</> : dirty ? t("society.studio.unsaved") : ""}</span>
      <button type="button" className="as-secondary" disabled={!dirty || busy} onClick={() => { if (initial) change(initial); }}>{t("society.studio.discard")}</button>
      <button type="button" className="as-primary" disabled={!dirty || busy || !seat || failed} onClick={() => void save()}>
        <Save size={15} aria-hidden />{t(busy ? "society.card.saving" : "society.studio.save_model")}
      </button>
    </div>
  </div>;
}
