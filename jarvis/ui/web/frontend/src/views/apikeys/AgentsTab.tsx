import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Check, FlaskConical, Loader2, LogIn, LogOut } from "lucide-react";
import { AgentAccountsPanel } from "@/components/AgentAccountsPanel";
import { PromptWriterCard } from "@/components/PromptWriterCard";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { ProviderTestControl } from "@/components/providers/ProviderTierSection";
import { SubagentModelCard, type SubagentStatus } from "@/components/SubagentModelCard";
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
import { familyState, type FamilyState } from "./providers/familyState";
import { DetailHeader, ListRow, MasterDetail, SettingsRow, SettingsSection } from "./settingsUi";

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

/**
 * Switch the agents' worker. One worker runs them at a time, so turning one
 * on turns the previous one off — the switches in both lists move together.
 */
function useAgentSwitch(onChanged: () => void | Promise<void>) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [switching, setSwitching] = useState<string | null>(null);

  async function use(row: AgentRowStatus, label: string) {
    if (row.is_active_brain || switching) return;
    setSwitching(row.jarvis);
    window.dispatchEvent(
      new CustomEvent("jarvis:provider-selection-pending", { detail: { section: "subagents", provider: row.jarvis } }),
    );
    try {
      const result = await switchSubagentProvider(row.jarvis);
      pushToast(
        "success",
        t(result.restart_required ? "providers_page.agents_switched_restart" : "providers_page.agents_switched").replace("{0}", label),
      );
      window.dispatchEvent(new CustomEvent("jarvis:agent-switched"));
      await onChanged();
    } catch (cause) {
      window.dispatchEvent(
        new CustomEvent("jarvis:provider-switch-failed", { detail: { section: "subagents", provider: row.jarvis } }),
      );
      pushToast("error", (cause as Error).message);
    } finally {
      setSwitching(null);
    }
  }

  return { switching, use };
}

/**
 * The Agents tab: which subscription or key runs the assistant's background
 * agents. Two lists in the same shape — the subscription logins, then the API
 * keys — each beside the selected entry's settings. The switch on a row is
 * "the agents run on this"; exactly one is on.
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
  const agentSwitch = useAgentSwitch(refresh);
  const states = useMemo<Record<string, FamilyState>>(() => {
    const ctx = { providers, subscriptions, agents, health };
    return Object.fromEntries((families ?? []).map((f) => [f.id, familyState(f, ctx)]));
  }, [families, providers, subscriptions, agents, health]);

  const rowsOf = (family: ProviderFamily) =>
    (agents?.mapping ?? []).filter((row) => family.agent_ids.includes(row.jarvis));
  const subscriptionFamilies = (families ?? []).filter((f) => f.subscription);
  // A key list entry is a company that can run the agents on a key, plus the
  // machine itself (agents on a local model need no key at all).
  const keyFamilies = (families ?? []).filter(
    (f) => rowsOf(f).some((row) => row.billing !== "subscription") && (f.key_slot || f.local),
  );

  return (
    <div className="flex flex-col gap-10">
      <SubscriptionsSection
        families={subscriptionFamilies}
        states={states}
        rowsOf={rowsOf}
        agentSwitch={agentSwitch}
        onChanged={refresh}
      />
      <ApiKeysSection
        families={keyFamilies}
        states={states}
        rowsOf={rowsOf}
        agentSwitch={agentSwitch}
        onChanged={refresh}
      />
      <AgentSettings />
      <SettingsSection title={t("providers_page.accounts_title")} plain>
        <AgentAccountsPanel />
      </SettingsSection>
    </div>
  );
}

type AgentSwitch = ReturnType<typeof useAgentSwitch>;

/** The worker row a subscription login runs the agents through. */
function subscriptionRow(_family: ProviderFamily, rows: AgentRowStatus[]): AgentRowStatus | undefined {
  return rows.find((row) => row.billing === "subscription" || row.billing === "subscription_or_api");
}

/** The worker row a company key runs the agents through. */
function keyRow(_family: ProviderFamily, rows: AgentRowStatus[]): AgentRowStatus | undefined {
  return rows.find((row) => row.billing === "api" || row.billing === "local") ?? rows.find((row) => row.billing === "subscription_or_api");
}

function SubscriptionsSection({
  families,
  states,
  rowsOf,
  agentSwitch,
  onChanged,
}: {
  families: ProviderFamily[];
  states: Record<string, FamilyState>;
  rowsOf: (family: ProviderFamily) => AgentRowStatus[];
  agentSwitch: AgentSwitch;
  onChanged: () => Promise<void>;
}) {
  const t = useT();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => {
    if (selectedId || !families.length) return;
    const active = families.find((f) => subscriptionRow(f, rowsOf(f))?.is_active_brain && states[f.id]?.subscriptionOn);
    setSelectedId((active ?? families.find((f) => states[f.id]?.subscriptionOn) ?? families[0]).id);
  }, [families, rowsOf, selectedId, states]);
  const selected = families.find((f) => f.id === selectedId) ?? null;

  return (
    <SettingsSection title={t("providers_page.subscriptions_title")} plain data-testid="agents-subscriptions">
      <MasterDetail
        testId="subscription-list"
        list={families.map((family) => {
          const state = states[family.id];
          const kind = family.subscription!.kind;
          const row = subscriptionRow(family, rowsOf(family));
          const on = Boolean(row?.is_active_brain && state?.subscriptionOn);
          return (
            <ListRow
              key={family.id}
              testId={`subscription-${kind}`}
              icon={<ProviderLogo providerId={family.subscription!.provider_id} label={CLI[kind].name} size="sm" className="h-5 w-5" />}
              name={CLI[kind].name}
              version={shortVersion(state?.subscription?.version)}
              status={subscriptionStatus(state, t)}
              dot={state?.failing ? "error" : null}
              selected={family.id === selectedId}
              dimmed={!state?.subscriptionOn}
              onSelect={() => setSelectedId(family.id)}
              trailing={
                <Switch
                  checked={on}
                  disabled={!row || !state?.subscriptionOn || on || agentSwitch.switching !== null}
                  onCheckedChange={(checked) => checked && row && void agentSwitch.use(row, CLI[kind].name)}
                  aria-label={t("providers_page.agents_use_for").replace("{0}", CLI[kind].name)}
                  title={t("providers_page.agents_use_for").replace("{0}", CLI[kind].name)}
                />
              }
            />
          );
        })}
        detail={
          selected && states[selected.id] ? (
            <SubscriptionDetail
              key={selected.id}
              family={selected}
              state={states[selected.id]}
              row={subscriptionRow(selected, rowsOf(selected))}
              agentSwitch={agentSwitch}
              onChanged={onChanged}
            />
          ) : null
        }
      />
    </SettingsSection>
  );
}

function subscriptionStatus(state: FamilyState | undefined, t: (key: string) => string): string {
  if (!state?.subscription) return t("providers_page.subscription_unknown");
  if (state.subscriptionOn) {
    return state.account
      ? t("providers_page.authenticated_as").replace("{0}", state.account)
      : t("providers_page.authenticated");
  }
  if (!state.subscription.installed) return t("providers_page.not_installed");
  return t("providers_page.not_signed_in");
}

function SubscriptionDetail({
  family,
  state,
  row,
  agentSwitch,
  onChanged,
}: {
  family: ProviderFamily;
  state: FamilyState;
  row: AgentRowStatus | undefined;
  agentSwitch: AgentSwitch;
  onChanged: () => Promise<void>;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const kind = family.subscription!.kind;
  const cli = CLI[kind];
  const status = state.subscription;
  const installed = status?.installed ?? false;
  const binary = status && "binary_path" in status ? status.binary_path ?? "" : "";
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

  return (
    <div data-testid={`subscription-detail-${kind}`} className="space-y-6">
      <div className="space-y-2.5">
        <DetailHeader
          icon={<ProviderLogo providerId={family.subscription!.provider_id} label={cli.name} size="sm" className="h-5 w-5" />}
          name={cli.name}
          version={shortVersion(status?.version)}
        />
        <SettingsSectionCard>
          <SettingsRow
            title={t("providers_page.account")}
            status={
              pending === "waiting"
                ? t(cli.where === "browser" ? "providers_page.login_waiting_browser" : "providers_page.login_waiting_terminal")
                : !installed && status
                  ? card?.install_hint || t("providers_page.subscription_install")
                  : subscriptionStatus(state, t)
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
          <UseForAgentsRow
            row={row}
            label={cli.name}
            ready={state.subscriptionOn}
            hint={t("providers_page.agents_needs_login")}
            agentSwitch={agentSwitch}
          />
        </SettingsSectionCard>
      </div>

      {installed && (
        <SettingsSection title={t("providers_page.runtime")}>
          <BinaryPathRow kind={kind} path={binary} onChanged={onChanged} />
          <SettingsRow
            title={t("providers_page.cli_check")}
            description={t("providers_page.cli_check_hint")}
            control={<CliTest endpoint={cli.test} onChanged={onChanged} />}
          />
        </SettingsSection>
      )}
    </div>
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
      description={t("providers_page.binary_path_hint")}
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

function UseForAgentsRow({
  row,
  label,
  ready,
  hint,
  agentSwitch,
}: {
  row: AgentRowStatus | undefined;
  label: string;
  ready: boolean;
  hint: string;
  agentSwitch: AgentSwitch;
}) {
  const t = useT();
  if (!row) return null;
  const active = row.is_active_brain && ready;
  return (
    <SettingsRow
      data-testid={`agent-use-row-${row.jarvis}`}
      title={t("providers_page.agents_title")}
      description={t("providers_page.agents_desc")}
      status={!ready ? hint : undefined}
      control={
        active ? (
          <span className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground">
            <Check aria-hidden="true" className="h-4 w-4 text-accent" />
            {t("providers_page.in_use")}
          </span>
        ) : (
          <Button
            size="sm"
            variant="outline"
            data-testid={`provider-agent-use-${row.jarvis}`}
            disabled={!ready || agentSwitch.switching !== null}
            onClick={() => void agentSwitch.use(row, label)}
          >
            {agentSwitch.switching === row.jarvis && <Loader2 className="animate-spin" />}
            {t("providers_page.agents_use")}
          </Button>
        )
      }
    />
  );
}

function ApiKeysSection({
  families,
  states,
  rowsOf,
  agentSwitch,
  onChanged,
}: {
  families: ProviderFamily[];
  states: Record<string, FamilyState>;
  rowsOf: (family: ProviderFamily) => AgentRowStatus[];
  agentSwitch: AgentSwitch;
  onChanged: () => Promise<void>;
}) {
  const t = useT();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => {
    if (selectedId || !families.length) return;
    const active = families.find((f) => {
      const row = keyRow(f, rowsOf(f));
      return row?.is_active_brain && (f.key_present || f.local);
    });
    setSelectedId((active ?? families.find((f) => f.key_present) ?? families[0]).id);
  }, [families, rowsOf, selectedId]);
  const selected = families.find((f) => f.id === selectedId) ?? null;

  const keyStatus = (family: ProviderFamily) => {
    const state = states[family.id];
    if (family.local) return t("providers_page.status_local");
    if (family.key_present) return t("providers_page.key_saved_short");
    if (state?.viaProject) return t("providers_page.status_project");
    return t("providers_page.key_missing_short");
  };

  return (
    <SettingsSection title={t("providers_page.keys_title")} plain data-testid="agents-api-keys">
      <MasterDetail
        testId="api-key-list"
        list={families.map((family) => {
          const row = keyRow(family, rowsOf(family));
          const ready = family.local || family.key_present || Boolean(states[family.id]?.viaProject);
          const on = Boolean(row?.is_active_brain) && ready && !(row?.billing === "subscription_or_api" && states[family.id]?.subscriptionOn);
          return (
            <ListRow
              key={family.id}
              testId={`provider-family-${family.id}`}
              icon={<ProviderLogo providerId={family.logo_id} label={family.label} size="sm" className="h-5 w-5" />}
              name={family.label}
              status={keyStatus(family)}
              dot={states[family.id]?.failing ? "error" : null}
              selected={family.id === selectedId}
              dimmed={!ready}
              onSelect={() => setSelectedId(family.id)}
              trailing={
                <Switch
                  checked={on}
                  disabled={!row || !ready || on || agentSwitch.switching !== null}
                  onCheckedChange={(checked) => checked && row && void agentSwitch.use(row, family.label)}
                  aria-label={t("providers_page.agents_use_for").replace("{0}", family.label)}
                  title={t("providers_page.agents_use_for").replace("{0}", family.label)}
                />
              }
            />
          );
        })}
        detail={
          selected && states[selected.id] ? (
            <KeyDetail
              key={selected.id}
              family={selected}
              state={states[selected.id]}
              rows={rowsOf(selected)}
              agentSwitch={agentSwitch}
              onChanged={onChanged}
            />
          ) : null
        }
      />
    </SettingsSection>
  );
}

const TEST_TIER_ORDER: ProviderDescriptor["tier"][] = ["brain", "realtime", "tts", "stt", "dictation"];

function KeyDetail({
  family,
  state,
  rows,
  agentSwitch,
  onChanged,
}: {
  family: ProviderFamily;
  state: FamilyState;
  rows: AgentRowStatus[];
  agentSwitch: AgentSwitch;
  onChanged: () => Promise<void>;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [merging, setMerging] = useState<string | null>(null);
  const testCard = TEST_TIER_ORDER.map((tier) => state.members.find((m) => m.tier === tier && m.auth_mode === "api_key")).find(Boolean);
  const ready = family.key_present || state.viaProject || family.local;
  // The machine itself can run the agents on more than one local server.
  const workerRows = family.local ? rows.filter((row) => row.billing === "local") : [keyRow(family, rows)].filter(Boolean) as AgentRowStatus[];

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
    <div data-testid={`provider-family-detail-${family.id}`} className="space-y-6">
      <div className="space-y-2.5">
        <DetailHeader icon={<ProviderLogo providerId={family.logo_id} label={family.label} size="sm" className="h-5 w-5" />} name={family.label} />
        <SettingsSectionCard>
          {family.key_slot && (
            <KeyField
              slot={family.key_slot}
              present={family.key_present}
              providerLabel={family.label}
              dashboardUrl={family.dashboard_url}
              description={state.viaProject && !family.key_present ? t("providers_page.key_project_hint") : t("providers_page.key_desc_short")}
              onChanged={onChanged}
            />
          )}
          {workerRows.map((row) => (
            <UseForAgentsRow
              key={row.jarvis}
              row={row}
              label={family.local ? row.label ?? family.label : family.label}
              ready={ready && !(row.billing === "subscription_or_api" && state.subscriptionOn && !family.key_present)}
              hint={t("providers_page.agents_needs_key")}
              agentSwitch={agentSwitch}
            />
          ))}
          {family.key_present && testCard && (
            <SettingsRow
              title={t("providers_page.key_test_label")}
              description={t("providers_page.key_test_hint")}
              control={<ProviderTestControl providerId={testCard.id} providerLabel={testCard.label} section={testCard.tier} active={testCard.active} />}
            />
          )}
        </SettingsSectionCard>
      </div>

      {family.separate_keys.length > 0 && (
        <SettingsSection title={t("providers_page.separate_title")}>
          {family.separate_keys.map((entry) => (
            <SettingsRow
              key={entry.slot}
              data-testid={`provider-separate-key-${entry.slot}`}
              title={t(`providers_page.separate_${entry.surface}`)}
              description={t("providers_page.separate_hint")}
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
      )}
    </div>
  );
}

/**
 * Settings that belong to the agents rather than to one provider: the model
 * a worker thinks with and who writes the task briefs.
 */
function AgentSettings() {
  const t = useT();
  const [status, setStatus] = useState<SubagentStatus | null>(null);
  const reload = async () => {
    try {
      const res = await fetch("/api/jarvis-agent/status", { cache: "no-store" });
      if (res.ok) setStatus((await res.json()) as SubagentStatus);
    } catch (cause) {
      // The model card stays hidden; the lists above report the same
      // endpoint's failure.
      console.debug("agent status unavailable", cause);
    }
  };
  useEffect(() => {
    void reload();
    const onChange = () => void reload();
    window.addEventListener("jarvis:agent-switched", onChange);
    return () => window.removeEventListener("jarvis:agent-switched", onChange);
  }, []);

  return (
    <SettingsSection title={t("providers_page.agent_settings_title")} plain data-testid="provider-agent-settings">
      <div className="grid gap-4 xl:grid-cols-2">
        {status && <SubagentModelCard status={status} onSaved={() => void reload()} />}
        <PromptWriterCard />
      </div>
    </SettingsSection>
  );
}
