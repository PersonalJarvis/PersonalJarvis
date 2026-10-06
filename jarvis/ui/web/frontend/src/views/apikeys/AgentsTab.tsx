import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FlaskConical, Loader2, LogIn, LogOut } from "lucide-react";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { ProviderTestControl } from "@/components/providers/ProviderTierSection";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  codexLogout,
  deleteSecret,
  loginAntigravity,
  loginClaude,
  loginGrokBuild,
  logoutAntigravity,
  logoutClaude,
  logoutGrokBuild,
  setCodexBinaryPath,
  startCodexLogin,
  switchSubagentProvider,
  testAgentCli,
  type AgentCliTestResult,
  type ProviderDescriptor,
  type SectionHealth,
} from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { fetchAgentChatCatalog, fetchProviderModels, type CuratedModel } from "@/lib/agentChatApi";
import {
  AGENT_PROVIDER_PREFS_KEY,
  EMPTY_AGENT_PROVIDER_PREFS,
  fetchAgentProviderPrefs,
  saveAgentProviderPrefs,
  toggled,
  type AgentProviderPrefs,
} from "@/lib/agentProviderPrefs";
import {
  pollUntilConnected,
  subscriptionStatusUrl,
  type AgentRowStatus,
  type ProviderFamily,
  type SubscriptionKind,
  type useProviderFamilies,
} from "@/lib/providerFamilies";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { KeyField } from "./KeyField";
import { SubscriptionAccounts } from "./SubscriptionAccounts";
import { accessPatch, familyAccess, nextDefault, onPatch, withPatch, type Access, type FamilyAccess } from "./agentAccess";
import { familyState, type FamilyState } from "./providers/familyState";
import {
  DetailHeader,
  ListRow,
  MasterDetail,
  ModelList,
  ModelListRow,
  Segmented,
  SettingsRow,
  SettingsSection,
} from "./settingsUi";

type FamiliesData = ReturnType<typeof useProviderFamilies>;

/** Each subscription login: its short name and the vendor CLI's own flows. */
const CLI: Record<
  SubscriptionKind,
  { name: string; login: () => Promise<void>; logout: () => Promise<void>; test: string; where: "browser" | "terminal" }
> = {
  codex: { name: "Codex", login: () => startCodexLogin(), logout: () => codexLogout(), test: "/api/codex/test", where: "browser" },
  claude_cli: { name: "Claude", login: loginClaude, logout: logoutClaude, test: "/api/claude/test", where: "terminal" },
  antigravity: { name: "Antigravity", login: loginAntigravity, logout: logoutAntigravity, test: "/api/antigravity/test", where: "browser" },
  grok_build: { name: "Grok", login: loginGrokBuild, logout: logoutGrokBuild, test: "/api/grok-build/test", where: "browser" },
};

/** "codex-cli 0.160.0" → "v0.160.0"; anything without a version number as given. */
export function shortVersion(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const match = raw.match(/\d+\.\d+(?:\.\d+)?/);
  return match ? `v${match[0]}` : raw;
}

const SOCIETY_CATALOG_KEY = ["agent-chat", "catalog", "society"] as const;

/**
 * The agents' provider choices and the one action that changes them: save a
 * patch, refresh every picker that reads them, and keep the default worker
 * on a company that is still on.
 */
function useAgentProviders(entriesFor: (prefs: AgentProviderPrefs) => FamilyAccess[], primary: string, onChanged: () => Promise<void>) {
  const t = useT();
  const client = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const prefsQuery = useQuery({ queryKey: AGENT_PROVIDER_PREFS_KEY, queryFn: fetchAgentProviderPrefs, staleTime: 30_000 });
  const prefs = prefsQuery.data ?? EMPTY_AGENT_PROVIDER_PREFS;
  const [busy, setBusy] = useState<string | null>(null);

  async function makeDefault(row: AgentRowStatus, label: string, quiet = false) {
    window.dispatchEvent(new CustomEvent("jarvis:provider-selection-pending", { detail: { section: "subagents", provider: row.jarvis } }));
    try {
      const result = await switchSubagentProvider(row.jarvis);
      if (!quiet || result.restart_required) {
        pushToast(
          "success",
          t(result.restart_required ? "providers_page.default_switched_restart" : "providers_page.default_switched").replace("{0}", label),
        );
      }
      window.dispatchEvent(new CustomEvent("jarvis:agent-switched"));
    } catch (cause) {
      window.dispatchEvent(new CustomEvent("jarvis:provider-switch-failed", { detail: { section: "subagents", provider: row.jarvis } }));
      pushToast("error", (cause as Error).message);
    }
  }

  async function save(familyId: string, patch: Partial<AgentProviderPrefs>) {
    if (busy) return;
    setBusy(familyId);
    try {
      const saved = await saveAgentProviderPrefs(patch);
      client.setQueryData(AGENT_PROVIDER_PREFS_KEY, saved);
      void client.invalidateQueries({ queryKey: SOCIETY_CATALOG_KEY });
      void client.invalidateQueries({ queryKey: ["society", "providers"] });
      const next = nextDefault(entriesFor(withPatch(prefs, saved)), primary, familyId);
      if (next) await makeDefault(next, next.label ?? next.jarvis, true);
      await onChanged();
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function chooseDefault(row: AgentRowStatus, label: string) {
    if (busy) return;
    setBusy(row.jarvis);
    try {
      await makeDefault(row, label);
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  return { prefs, ready: prefsQuery.isSuccess, busy, save, chooseDefault };
}

type AgentProviders = ReturnType<typeof useAgentProviders>;

/**
 * The Agents tab: every company the assistant's agents can run on, as one
 * list beside the selected company's settings. Any number of companies are
 * on at once — each one connected is one more seat an agent can sit on.
 * Inside a company the person picks how it is reached (subscription or API
 * key) and which of its models the agents are offered.
 */
export function AgentsTab({
  data,
  providers,
  health,
  onProvidersChanged,
}: {
  data: FamiliesData;
  providers: ProviderDescriptor[];
  health: Record<string, SectionHealth | undefined>;
  onProvidersChanged: () => void;
}) {
  const t = useT();
  const { families, subscriptions, agents, reload } = data;
  const refresh = async () => {
    onProvidersChanged();
    await reload();
  };
  const states = useMemo<Record<string, FamilyState>>(() => {
    const ctx = { providers, subscriptions, agents, health };
    return Object.fromEntries((families ?? []).map((f) => [f.id, familyState(f, ctx)]));
  }, [families, providers, subscriptions, agents, health]);
  const mapping = agents?.mapping ?? [];
  // Companies that can run an agent, subscriptions first (a plan already paid
  // for beats a metered key), this computer last.
  const listed = useMemo(() => {
    const rank = (f: ProviderFamily) => (f.local ? 2 : f.subscription ? 0 : 1);
    return (families ?? [])
      .filter((f) => mapping.some((row) => f.agent_ids.includes(row.jarvis)) && (f.subscription || f.key_slot || f.local))
      .sort((a, b) => rank(a) - rank(b));
  }, [families, mapping]);
  const entriesFor = (prefs: AgentProviderPrefs) => listed.map((f) => familyAccess(f, mapping, prefs, states[f.id]));
  const control = useAgentProviders(entriesFor, agents?.brain_primary ?? "", refresh);
  const entries = entriesFor(control.prefs);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => {
    if (selectedId || !entries.length) return;
    const primary = entries.find((fa) => fa.rows.some((row) => row.is_active_brain));
    setSelectedId((primary ?? entries.find((fa) => fa.on) ?? entries[0]).family.id);
  }, [entries, selectedId]);
  const selected = entries.find((fa) => fa.family.id === selectedId) ?? null;

  return (
    <div className="flex flex-col gap-10" data-testid="apikeys-agents">
      <SettingsSection title={t("providers_page.agent_providers_title")} plain data-testid="agents-providers">
        <MasterDetail
          testId="agent-provider-list"
          list={entries.map((fa) => (
            <ProviderListRow
              key={fa.family.id}
              entry={fa}
              state={states[fa.family.id]}
              selected={fa.family.id === selectedId}
              onSelect={() => setSelectedId(fa.family.id)}
              control={control}
            />
          ))}
          detail={
            selected && states[selected.family.id] ? (
              <ProviderDetail
                key={selected.family.id}
                entry={selected}
                state={states[selected.family.id]}
                control={control}
                onChanged={refresh}
              />
            ) : null
          }
        />
      </SettingsSection>
    </div>
  );
}

/** One entry per company, named and marked as the company, whichever access it uses. */
function displayName(family: ProviderFamily): string {
  return family.label;
}

function logoOf(family: ProviderFamily): string {
  return family.logo_id;
}

/** The line under a company's name: how it is reached, or what it still needs. */
function entryStatus(entry: FamilyAccess, state: FamilyState | undefined, t: (key: string) => string): string {
  if (entry.access === "local") return t("providers_page.status_local");
  if (entry.access === "subscription") {
    if (!state?.subscription) return t("providers_page.subscription_unknown");
    if (state.subscriptionOn) {
      return state.account
        ? t("providers_page.status_subscription_as").replace("{0}", state.account)
        : t("providers_page.status_subscription");
    }
    if (!state.subscription.installed) return t("providers_page.not_installed");
    return t("providers_page.not_signed_in");
  }
  if (entry.family.key_present) return t("providers_page.status_key");
  if (state?.viaProject) return t("providers_page.status_project");
  return t("providers_page.key_missing_short");
}

function ProviderListRow({
  entry,
  state,
  selected,
  onSelect,
  control,
}: {
  entry: FamilyAccess;
  state: FamilyState | undefined;
  selected: boolean;
  onSelect: () => void;
  control: AgentProviders;
}) {
  const t = useT();
  const family = entry.family;
  const name = displayName(family);
  const label = t("providers_page.agents_use_for").replace("{0}", name);
  return (
    <ListRow
      testId={`agent-provider-${family.id}`}
      icon={<ProviderLogo providerId={logoOf(family)} label={name} size="sm" className="h-5 w-5" />}
      name={name}
      version={entry.access === "subscription" ? shortVersion(state?.subscription?.version) : null}
      status={entryStatus(entry, state, t)}
      dot={state?.failing ? "error" : null}
      selected={selected}
      dimmed={!entry.on}
      onSelect={onSelect}
      trailing={
        <Switch
          checked={entry.on}
          disabled={!entry.ready || !control.ready || control.busy !== null}
          onCheckedChange={(on) => void control.save(family.id, onPatch(entry, on, control.prefs))}
          aria-label={label}
          title={entry.ready ? label : t("providers_page.agents_needs_access")}
        />
      }
    />
  );
}

function ProviderDetail({
  entry,
  state,
  control,
  onChanged,
}: {
  entry: FamilyAccess;
  state: FamilyState;
  control: AgentProviders;
  onChanged: () => Promise<void>;
}) {
  const t = useT();
  const family = entry.family;
  const name = displayName(family);
  const subscription = entry.access === "subscription";
  const keyed = entry.access === "api_key";
  const testCard = keyed
    ? TEST_TIER_ORDER.map((tier) => state.members.find((m) => m.tier === tier && m.auth_mode === "api_key")).find(Boolean)
    : undefined;

  return (
    <div data-testid={`agent-provider-detail-${family.id}`} className="space-y-6">
      <div className="space-y-2.5">
        <DetailHeader
          icon={<ProviderLogo providerId={logoOf(family)} label={name} size="sm" className="h-5 w-5" />}
          name={name}
          version={subscription ? shortVersion(state.subscription?.version) : null}
        />
        <SettingsSectionCard>
          {entry.choices.length > 1 && (
            <SettingsRow
              title={t("providers_page.access_title")}
              control={
                <Segmented<Access>
                  size="xs"
                  testId="agent-provider-access"
                  label={t("providers_page.access_title")}
                  value={entry.access}
                  disabled={!control.ready || control.busy !== null}
                  onChange={(access) => void control.save(family.id, accessPatch(entry, access, control.prefs))}
                  options={entry.choices.map((choice) => ({ value: choice, label: t(`providers_page.access_${choice}`) }))}
                />
              }
            />
          )}
          {subscription && family.subscription && <SubscriptionAccountRow family={family} state={state} onChanged={onChanged} />}
          {keyed && family.key_slot && (
            <KeyField
              slot={family.key_slot}
              present={family.key_present}
              providerLabel={family.label}
              dashboardUrl={family.dashboard_url}
              description={state.viaProject && !family.key_present ? t("providers_page.status_project") : undefined}
              onChanged={onChanged}
            />
          )}
          {entry.access === "local" && (
            <SettingsRow title={t("providers_page.local_title")} />
          )}
          {entry.rows.map((row) => (
            <DefaultRow
              key={row.jarvis}
              row={row}
              label={family.local ? row.label ?? family.label : name}
              entry={entry}
              control={control}
            />
          ))}
          {keyed && family.key_present && testCard && (
            <SettingsRow
              title={t("providers_page.key_test_label")}
              control={<ProviderTestControl providerId={testCard.id} providerLabel={testCard.label} section={testCard.tier} active={testCard.active} />}
            />
          )}
        </SettingsSectionCard>
      </div>

      {entry.rows.map((row) => (
        <AgentModels
          key={row.jarvis}
          providerId={row.jarvis}
          title={entry.rows.length > 1 ? `${t("providers_page.models_title")} · ${row.label ?? row.jarvis}` : t("providers_page.models_title")}
          control={control}
          familyId={family.id}
        />
      ))}

      {subscription && family.subscription && state.subscription?.installed && (
        <SettingsSection title={t("providers_page.runtime")}>
          <BinaryPathRow
            kind={family.subscription.kind}
            path={"binary_path" in state.subscription ? state.subscription.binary_path ?? "" : ""}
            onChanged={onChanged}
          />
          <SettingsRow
            title={t("providers_page.cli_check")}
            control={<CliTest endpoint={CLI[family.subscription.kind].test} onChanged={onChanged} />}
          />
        </SettingsSection>
      )}

      {subscription && family.subscription && <SubscriptionAccounts kind={family.subscription.kind} />}

      {keyed && <SeparateKeys family={family} onChanged={onChanged} />}
    </div>
  );
}

function SubscriptionAccountRow({ family, state, onChanged }: { family: ProviderFamily; state: FamilyState; onChanged: () => Promise<void> }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const kind = family.subscription!.kind;
  const cli = CLI[kind];
  const status = state.subscription;
  const installed = status?.installed ?? false;
  const card = state.members.find((m) => m.id === family.subscription!.provider_id);
  const [pending, setPending] = useState<"login" | "waiting" | "logout" | null>(null);

  async function connect() {
    setPending("login");
    try {
      await cli.login();
      pushToast("info", t(cli.where === "browser" ? "providers_page.login_started_browser" : "providers_page.login_started_terminal"));
      setPending("waiting");
      await onChanged();
      if (await pollUntilConnected(subscriptionStatusUrl(kind), onChanged)) {
        pushToast("success", t("providers_page.login_done").replace("{0}", cli.name));
      }
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function disconnect() {
    setPending("logout");
    try {
      await cli.logout();
      pushToast("info", t("providers_page.logout_done").replace("{0}", cli.name));
      await onChanged();
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setPending(null);
    }
  }

  const line = !state.subscription
    ? t("providers_page.subscription_unknown")
    : state.subscriptionOn
      ? state.account
        ? t("providers_page.authenticated_as").replace("{0}", state.account)
        : t("providers_page.authenticated")
      : !installed
        ? card?.install_hint || t("providers_page.subscription_install")
        : t("providers_page.not_signed_in");

  return (
    <SettingsRow
      data-testid={`subscription-account-${kind}`}
      title={t("providers_page.account")}
      status={
        pending === "waiting"
          ? t(cli.where === "browser" ? "providers_page.login_waiting_browser" : "providers_page.login_waiting_terminal")
          : line
      }
      control={
        state.subscriptionOn ? (
          <Button size="sm" variant="outline" data-testid="subscription-disconnect" disabled={pending !== null} onClick={() => void disconnect()}>
            {pending === "logout" ? <Loader2 className="animate-spin" /> : <LogOut />}
            {t("providers_page.disconnect")}
          </Button>
        ) : (
          <Button size="sm" data-testid="subscription-connect" disabled={pending !== null || !installed} onClick={() => void connect()}>
            {pending ? <Loader2 className="animate-spin" /> : <LogIn />}
            {t("providers_page.connect")}
          </Button>
        )
      }
    />
  );
}

/**
 * Whether the tasks the assistant hands off by itself go here. Every company
 * that is on can run an agent; one of them takes the tasks nobody assigned.
 */
function DefaultRow({ row, label, entry, control }: { row: AgentRowStatus; label: string; entry: FamilyAccess; control: AgentProviders }) {
  const t = useT();
  const isDefault = row.is_active_brain && entry.on;
  return (
    <SettingsRow
      data-testid={`agent-default-row-${row.jarvis}`}
      title={t("providers_page.default_title")}
      status={!entry.ready ? t("providers_page.agents_needs_access") : undefined}
      control={
        isDefault ? (
          <span className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground">
            <Check aria-hidden="true" className="h-4 w-4 text-accent" />
            {t("providers_page.default_on")}
          </span>
        ) : (
          <Button
            size="sm"
            variant="outline"
            data-testid={`agent-make-default-${row.jarvis}`}
            disabled={!entry.on || control.busy !== null}
            onClick={() => void control.chooseDefault(row, label)}
          >
            {control.busy === row.jarvis && <Loader2 className="animate-spin" />}
            {t("providers_page.default_make")}
          </Button>
        )
      }
    />
  );
}

/**
 * The models a provider offers the agents, each with a switch: a model
 * switched off is left out of every agent's model picker.
 */
function AgentModels({ providerId, title, control, familyId }: { providerId: string; title: string; control: AgentProviders; familyId: string }) {
  const t = useT();
  const catalog = useQuery({ queryKey: SOCIETY_CATALOG_KEY, queryFn: () => fetchAgentChatCatalog("society"), staleTime: 60_000 });
  const row = catalog.data?.providers.find((p) => p.id === providerId);
  const live = useQuery({
    queryKey: ["society", "model-menu", "live", providerId],
    queryFn: async (): Promise<CuratedModel[]> =>
      (await fetchProviderModels(providerId)).map((model) => ({ id: model.id, label: model.label ?? model.name ?? model.id })),
    enabled: row?.models_source === "live",
    staleTime: 10 * 60_000,
    retry: false,
  });
  if (!row) return null;
  const models = [...new Map((row.models_source === "live" && live.data?.length ? live.data : row.curated_models)
    .filter((model) => model.id)
    .map((model) => [model.id, model])).values()];
  const hidden = new Set(control.prefs.hidden_models[providerId] ?? []);
  const shown = models.filter((model) => !hidden.has(model.id)).length;
  const loading = row.models_source === "live" && live.isPending;
  const setHidden = (ids: string[]) =>
    void control.save(familyId, { hidden_models: { ...control.prefs.hidden_models, [providerId]: ids } });
  const busy = !control.ready || control.busy !== null;

  return (
    <SettingsSection title={title} plain data-testid={`agent-models-${providerId}`}>
      {models.length === 0 ? (
        <div className="rounded-xl border border-border/60 bg-card/40 px-4 py-3 text-xs text-muted-foreground">
          {loading ? t("providers_page.models_loading") : t("providers_page.models_none")}
        </div>
      ) : (
        <ModelList
          header={t("providers_page.models_count").replace("{0}", String(shown)).replace("{1}", String(models.length))}
          action={
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs"
              disabled={busy}
              onClick={() => setHidden(shown === 0 ? [] : models.map((model) => model.id))}
            >
              {shown === 0 ? t("providers_page.models_show_all") : t("providers_page.models_hide_all")}
            </Button>
          }
        >
          {models.map((model) => (
            <ModelListRow
              key={model.id}
              name={model.label || model.id}
              id={model.id}
              note={model.note ?? (model.efforts?.length ? t("providers_page.models_reasoning") : null)}
              muted={hidden.has(model.id)}
              control={
                <Switch
                  checked={!hidden.has(model.id)}
                  disabled={busy}
                  onCheckedChange={(on) => setHidden(toggled([...hidden], [model.id], on))}
                  aria-label={model.label || model.id}
                />
              }
            />
          ))}
        </ModelList>
      )}
    </SettingsSection>
  );
}

/** A plain card (no section title) for the rows under a detail header. */
function SettingsSectionCard({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border/60 bg-card/40 [&>*+*]:border-t [&>*+*]:border-border/50">
      {children}
    </div>
  );
}

/** Where the CLI binary lives. Codex lets the user point at another one. */
function BinaryPathRow({ kind, path, onChanged }: { kind: SubscriptionKind; path: string; onChanged: () => Promise<void> }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const editable = kind === "codex";
  const [draft, setDraft] = useState(path);
  useEffect(() => setDraft(path), [path]);

  async function commit() {
    const next = draft.trim();
    if (!editable || next === path) return;
    try {
      await setCodexBinaryPath(next);
      await onChanged();
    } catch (cause) {
      pushToast("error", (cause as Error).message);
      setDraft(path);
    }
  }

  return (
    <SettingsRow
      title={t("providers_page.binary_path")}
      control={
        <Input
          value={draft}
          readOnly={!editable}
          spellCheck={false}
          aria-label={t("providers_page.binary_path")}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => void commit()}
          onKeyDown={(event) => {
            if (event.key === "Enter") void commit();
          }}
          className={cn("h-8 font-mono text-sm sm:w-72", !editable && "text-muted-foreground")}
        />
      }
    />
  );
}

/** A live check of the CLI: found where, which version, signed in as whom. */
function CliTest({ endpoint, onChanged }: { endpoint: string; onChanged: () => Promise<void> }) {
  const t = useT();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AgentCliTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      setResult(await testAgentCli(endpoint));
      await onChanged();
    } catch (cause) {
      setResult(null);
      setError((cause as Error).message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <span className="flex min-w-0 items-center gap-3">
      {(result || error) && (
        <span
          role="status"
          title={error ?? result?.message}
          className={cn("max-w-[16rem] truncate text-xs", error || (result && !result.ok) ? "text-destructive" : "text-muted-foreground")}
        >
          {error ?? result?.message}
        </span>
      )}
      <Button size="sm" variant="outline" disabled={running} onClick={() => void run()}>
        {running ? <Loader2 className="animate-spin" /> : <FlaskConical />}
        {t("providers_page.test")}
      </Button>
    </span>
  );
}

const TEST_TIER_ORDER: ProviderDescriptor["tier"][] = ["brain", "realtime", "tts", "stt", "dictation"];

/** Features that still hold a key of their own, each with a way back onto the main key. */
function SeparateKeys({ family, onChanged }: { family: ProviderFamily; onChanged: () => Promise<void> }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [merging, setMerging] = useState<string | null>(null);
  if (family.separate_keys.length === 0) return null;

  async function merge(slot: string) {
    setMerging(slot);
    try {
      await deleteSecret(slot);
      window.dispatchEvent(new CustomEvent("jarvis:secret-configured", { detail: { key: slot, action: "delete" } }));
      pushToast("info", t("providers_page.separate_merged_toast").replace("{0}", family.label));
      await onChanged();
    } catch (cause) {
      pushToast("error", (cause as Error).message);
    } finally {
      setMerging(null);
    }
  }

  return (
    <SettingsSection title={t("providers_page.separate_title")}>
      {family.separate_keys.map((entry) => (
        <SettingsRow
          key={entry.slot}
          data-testid={`provider-separate-key-${entry.slot}`}
          title={t(`providers_page.separate_${entry.surface}`)}
          control={
            <Button
              size="sm"
              variant="outline"
              disabled={merging !== null || !family.key_present}
              title={family.key_present ? undefined : t("providers_page.separate_needs_main")}
              onClick={() => void merge(entry.slot)}
            >
              {merging === entry.slot && <Loader2 className="animate-spin" />}
              {t("providers_page.separate_merge")}
            </Button>
          }
        />
      ))}
    </SettingsSection>
  );
}
