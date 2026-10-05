import { useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronRight, Loader2 } from "lucide-react";
import { useT } from "@/i18n";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { SettingsRow, SettingsSection } from "@/views/apikeys/settingsUi";
import { fetchAgentAccounts } from "@/lib/agentAccountsApi";
import { LiveSubscriptionAccount } from "./LiveSubscriptionAccount";
import { useRestartApp } from "@/hooks/useRestartApp";

export type LiveAuthMode = "api_key" | "chatgpt_subscription";

export interface LiveProfileValue {
  auth_mode?: LiveAuthMode;
  subscription_account_id?: string;
  subscription_voice?: string;
  subscription_backend_model?: string;
  subscription_reasoning_effort?: string;
  model: string;
  voice: string;
  backend_model: string;
  reasoning_effort: string;
  web_search: boolean;
  instructions: string;
  backend_instructions: string;
  configured: boolean;
}

export interface LiveProfileState {
  profile: LiveProfileValue;
  key_ready: boolean;
  active: boolean;
  agent_configured: boolean;
  subscription?: {
    account_id: string;
    account_connected: boolean;
    voice_status: string;
    reason?: string;
  };
}

async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { cache: "no-store", signal });
  if (!response.ok) throw new Error(`GPT-Live: HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

/** Shared by the profile form and the provider list, so both read one cache. */
export const liveProfileQuery = {
  queryKey: ["live-profile"],
  queryFn: ({ signal }: { signal: AbortSignal }) =>
    read<LiveProfileState>("/api/live/profile", signal),
} as const;

/** Whether the saved profile can start a call as it stands. */
export function liveProfileReady(state: LiveProfileState | undefined, mode = state?.profile.auth_mode ?? "api_key"): boolean {
  if (mode === "chatgpt_subscription") {
    return Boolean(state?.profile.auth_mode !== undefined && state.profile.configured && state.subscription?.account_connected &&
      state.profile.subscription_backend_model?.trim());
  }
  return Boolean(
    state?.key_ready &&
      state.profile.configured &&
      state.profile.backend_model.trim(),
  );
}

/** PUT the profile; the backend also makes GPT-Live the realtime voice. */
export async function saveLiveProfile(profile: LiveProfileValue): Promise<void> {
  const response = await fetch("/api/live/profile", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...profile, configured: true }),
  });
  if (!response.ok)
    throw new Error((await response.json()).detail ?? `HTTP ${response.status}`);
}

const capitalize = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);

/**
 * GPT-Live's own settings: how it is paid for (the ChatGPT subscription or an
 * OpenAI key), the conversation voice and the thinking model that answers with
 * tools — the Voice tab of the API Keys page, in the page's compact row
 * grammar.
 */
export function LiveProfile({ onSaved, onAuthModeChange, selectedAuthMode, keyField }: {
  onSaved?: () => void;
  /** The API-key row, shown under the access choice while the key route is picked. */
  keyField?: ReactNode;
  onAuthModeChange?: (mode: LiveAuthMode, pending: boolean) => void;
  /** A dedicated provider row fixes its own access without changing the other row. */
  selectedAuthMode?: LiveAuthMode;
} = {}) {
  const t = useT();
  const queryClient = useQueryClient();
  const restart = useRestartApp();
  const profile = useQuery(liveProfileQuery);
  const [draft, setDraft] = useState<LiveProfileValue | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const storedValue = draft ?? profile.data?.profile;
  const authMode = selectedAuthMode ?? storedValue?.auth_mode ?? "api_key";
  const supportsSubscription = profile.data?.profile.auth_mode !== undefined && profile.data.subscription !== undefined;
  // An older backend rejects unknown fields. Keep API saves on its original
  // shape until the backend has advertised the new subscription contract.
  const value = storedValue && selectedAuthMode && (supportsSubscription || selectedAuthMode === "chatgpt_subscription")
    ? { ...storedValue, auth_mode: selectedAuthMode } : storedValue;
  const subscription = authMode === "chatgpt_subscription";
  const accounts = useQuery({
    queryKey: ["live-subscription-accounts"],
    queryFn: fetchAgentAccounts,
    enabled: subscription && supportsSubscription,
    staleTime: 30_000,
    retry: false,
  });
  const group = accounts.data?.platforms.find((entry) => entry.platform === "codex");
  const accountId = value?.subscription_account_id || group?.active_account || "";
  const account = group?.accounts.find((entry) => entry.id === accountId);
  const options = useQuery({
    queryKey: subscription ? ["live-options", authMode, accountId] : ["live-options"],
    queryFn: ({ signal }) =>
      read<{
        models: { id: string; label: string; efforts?: string[]; default_effort?: string }[];
        voices: string[];
        efforts: string[];
      }>(subscription
        ? `/api/live/options?auth_mode=chatgpt_subscription&account_id=${encodeURIComponent(accountId)}`
        : "/api/live/options", signal),
    enabled: Boolean(value) && (!subscription || (supportsSubscription && Boolean(account?.connected && account.mode === "subscription"))),
    staleTime: 300_000,
    retry: false,
  });
  const profileLoaded = Boolean(value);
  const billingPending = authMode !== (profile.data?.profile.auth_mode ?? "api_key");
  useEffect(() => {
    if (profileLoaded) onAuthModeChange?.(authMode, billingPending);
  }, [authMode, billingPending, onAuthModeChange, profileLoaded]);
  useEffect(() => {
    const credentialsChanged = () => {
      void queryClient.invalidateQueries({ queryKey: ["live-profile"] });
    };
    window.addEventListener("jarvis:secret-configured", credentialsChanged);
    return () => window.removeEventListener("jarvis:secret-configured", credentialsChanged);
  }, [queryClient]);
  if (!value)
    return (
      <p role="status" className="px-1 text-sm text-muted-foreground">
        {profile.error?.message ?? t("live.loading")}
      </p>
    );
  const update = (patch: Partial<LiveProfileValue>) => {
    setDraft({ ...value, ...patch });
    setMessage("");
    setError("");
  };
  const saved = profile.data?.profile;
  const dirty = saved && JSON.stringify(value) !== JSON.stringify(saved);
  const sameCredentials = authMode === (saved?.auth_mode ?? "api_key") &&
    (!subscription || (value.subscription_account_id ?? "") === (saved?.subscription_account_id ?? ""));
  const ready = Boolean(profile.data?.active && saved?.configured && sameCredentials);
  const credentialReady = subscription
    ? Boolean(supportsSubscription && account?.connected && account.mode === "subscription" && !accounts.isError)
    : Boolean(profile.data?.key_ready);
  const voice = subscription ? value.subscription_voice ?? "cove" : value.voice;
  const backendModel = subscription ? value.subscription_backend_model ?? "" : value.backend_model;
  const reasoningEffort = subscription ? value.subscription_reasoning_effort ?? "medium" : value.reasoning_effort;
  const models = options.data?.models ?? [];
  const selectedModel = models.find((model) => model.id === backendModel);
  const efforts = subscription && selectedModel?.efforts
    ? ["", ...selectedModel.efforts.filter(Boolean)]
    : options.data?.efforts ?? ["", reasoningEffort];
  const effortAvailable = !subscription || efforts.includes(reasoningEffort);
  const modelOptions = models.map((model) => ({
    value: model.id,
    label: model.label,
    hint: model.label === model.id ? undefined : model.id,
  }));
  if (
    !subscription && backendModel &&
    !modelOptions.some((model) => model.value === backendModel)
  ) {
    modelOptions.unshift({
      value: backendModel,
      label: backendModel,
      hint: "",
    });
  }
  async function refresh() {
    // A settings save must not re-fetch every background query in the app.
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["live-profile"] }),
      queryClient.invalidateQueries({ queryKey: ["voice-mode"] }),
    ]);
    window.dispatchEvent(new CustomEvent("jarvis:realtime-switched"));
    onSaved?.();
  }
  // Once Live is set up, a picker change applies at once: a changed dropdown
  // that silently reverts when the page closes left users billed for a model
  // they believed they had replaced. Free-text prompts still use the button.
  const choose = (patch: Partial<LiveProfileValue>) => {
    const next = { ...value, ...patch };
    setDraft(next);
    if (ready && credentialReady && !saving && (effortAvailable || patch.subscription_reasoning_effort !== undefined)) void save(next);
  };
  const chooseModel = (selected: string) => {
    if (!subscription) { choose({ backend_model: selected }); return; }
    const model = models.find((entry) => entry.id === selected);
    const nextEffort = reasoningEffort && model?.efforts && !model.efforts.includes(reasoningEffort)
      ? model.default_effort && model.efforts.includes(model.default_effort) ? model.default_effort : ""
      : reasoningEffort;
    choose({ subscription_backend_model: selected, subscription_reasoning_effort: nextEffort });
  };
  async function save(next?: LiveProfileValue) {
    const chosen = next ?? value;
    if (!chosen) return;
    if (chosen.auth_mode === "chatgpt_subscription" && !supportsSubscription) return;
    setSaving(true);
    setMessage("");
    setError("");
    try {
      await saveLiveProfile(chosen);
      await refresh();
      setDraft(null);
      setMessage(t("live.saved"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }
  async function useForAgents() {
    setSaving(true);
    setError("");
    try {
      const response = await fetch("/api/live/use-key-for-agent", {
        method: "POST",
      });
      if (!response.ok) throw new Error((await response.json()).detail);
      window.dispatchEvent(new CustomEvent("jarvis:subagent-switched"));
      await refresh();
      setMessage(t("live.agent_saved"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }
  // The save button only exists while there is something to save. A greyed
  // "Saved & active" button read as a broken control; the saved state is a
  // quiet line instead.
  const showSave = !ready || dirty;
  return (
    <section aria-label="GPT-Live" className="flex flex-col gap-8" data-testid="live-profile">
      <SettingsSection
        title={t("live.billing_method")}
        headerAction={billingPending
          ? <span className="text-xs text-muted-foreground">{t("live.billing_pending")}</span>
          : ready && !dirty ? (
            <span data-testid="live-profile-saved" className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <Check aria-hidden="true" className="h-3.5 w-3.5 text-accent" />
              {t("live.ready")}
            </span>
          ) : undefined}
      >
        <SettingsRow
          title={t("live.billing_method")}
          description={t(subscription ? "live.subscription_billing" : "live.billing")}
          control={selectedAuthMode
            ? <span className="text-sm font-medium text-foreground">{subscription ? "GPT Subscription" : t("live.api_key_mode")}</span>
            : <BrandedSelect className="w-56" value={authMode} disabled={saving}
                ariaLabel={t("live.billing_method")}
                onValueChange={(mode) => update({ auth_mode: mode as LiveAuthMode })}
                options={[
                  { value: "chatgpt_subscription", label: t("live.subscription_mode") },
                  { value: "api_key", label: t("live.api_key_mode") },
                ]} />}
        />
        {!subscription && keyField}
        {subscription && !supportsSubscription ? (
          <SettingsRow
            title={t("live.subscription_mode")}
            description={t("settings_view.wake_word.restart_required")}
            control={<Button size="sm" disabled={restart.restarting} onClick={() => void restart.restart()}>{restart.buttonLabel}</Button>}
          />
        ) : null}
        {subscription && supportsSubscription ? (
          <SettingsRow title={t("live.subscription_mode")}>
            <LiveSubscriptionAccount group={group} accountId={value.subscription_account_id ?? ""}
              loading={accounts.isPending} disabled={saving}
              voiceStatus={profile.data?.subscription?.account_id === accountId
                ? profile.data.subscription.voice_status : "unverified"}
              onAccountChange={(subscription_account_id) => update({ subscription_account_id })}
              onConnected={() => {
                void queryClient.invalidateQueries({ queryKey: ["live-subscription-accounts"] });
                void queryClient.invalidateQueries({ queryKey: ["live-profile"] });
                void queryClient.invalidateQueries({ queryKey: ["live-options", "chatgpt_subscription"] });
              }} />
            {accounts.isError ? <p role="alert" className="mt-2 text-xs text-destructive">{t("live.subscription_accounts_failed")}</p> : null}
          </SettingsRow>
        ) : null}
      </SettingsSection>

      <SettingsSection title={t("live.conversation_heading")}>
        <SettingsRow
          title={t("live.voice_model")}
          description={t("live.conversation_help")}
          control={<span className="text-sm font-medium text-foreground">{subscription ? "GPT-Live · ChatGPT" : "GPT-Live 1"}</span>}
        />
        <SettingsRow
          title={t("live.voice")}
          control={
            <BrandedSelect
              className="w-56"
              value={voice}
              disabled={saving || (subscription && !options.data)}
              onValueChange={(selected) => choose(subscription ? { subscription_voice: selected } : { voice: selected })}
              ariaLabel={t("live.voice")}
              options={(options.data?.voices ?? (subscription ? [] : [voice])).map((voice) => ({
                value: voice,
                label: capitalize(voice),
              }))}
            />
          }
        />
      </SettingsSection>

      <SettingsSection title={t("live.thinking_heading")}>
        <SettingsRow
          title={t("live.thinking_model")}
          description={t(subscription ? "live.subscription_thinking_help" : "live.thinking_help")}
          control={
            <BrandedSelect
              className="w-56"
              value={backendModel}
              disabled={saving || (subscription && !options.data)}
              onValueChange={chooseModel}
              ariaLabel={t("live.thinking_model")}
              placeholder={t("live.choose_model")}
              searchPlaceholder={t("live.search_models")}
              options={modelOptions}
            />
          }
        />
        <SettingsRow
          title={t("live.reasoning")}
          control={
            <BrandedSelect
              className="w-56"
              value={reasoningEffort}
              disabled={saving}
              onValueChange={(selected) => choose(subscription ? { subscription_reasoning_effort: selected } : { reasoning_effort: selected })}
              ariaLabel={t("live.reasoning")}
              options={efforts.map((effort) => ({
                value: effort,
                label: effort ? capitalize(effort) : t("live.model_default"),
              }))}
            />
          }
        />
        {!subscription && <SettingsRow
          title={<label htmlFor="live-web-search">{t("live.web_search")}</label>}
          description={t("live.web_search_help")}
          control={
            <Switch
              id="live-web-search"
              checked={value.web_search}
              disabled={saving}
              onCheckedChange={(web_search) => choose({ web_search })}
              aria-label={t("live.web_search")}
            />
          }
        />}
        <details className="group">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-foreground transition-colors hover:bg-secondary/50 [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden="true"
              className="h-4 w-4 text-muted-foreground transition-transform group-open:rotate-90 motion-reduce:transition-none"
            />
            {t("live.prompts")}
          </summary>
          <div className="grid gap-4 px-4 pb-4 md:grid-cols-2">
            <label className="space-y-1.5">
              <span className="block text-xs font-medium text-muted-foreground">{t("live.conversation_prompt")}</span>
              <Textarea rows={3} value={value.instructions} onChange={(event) => update({ instructions: event.target.value })} />
            </label>
            <label className="space-y-1.5">
              <span className="block text-xs font-medium text-muted-foreground">{t("live.backend_prompt")}</span>
              <Textarea rows={3} value={value.backend_instructions} onChange={(event) => update({ backend_instructions: event.target.value })} />
            </label>
            {!subscription ? <label className="space-y-1.5 md:col-span-2">
              <span className="block text-xs font-medium text-muted-foreground">{t("live.custom_model")}</span>
              <Input value={value.backend_model} onChange={(event) => update({ backend_model: event.target.value })} />
            </label> : null}
          </div>
        </details>
      </SettingsSection>

      <div className="flex flex-col gap-3 px-1">
        {options.isError ? <p role="alert" className="text-xs text-destructive">{t("live.options_failed")}</p> : null}
        {!subscription && !profile.data?.key_ready && (
          <p role="status" className="text-xs text-warning">{t("live.key_required")}</p>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-md text-xs text-muted-foreground">{t("live.agents_separate")}</p>
          <div className="flex items-center gap-2">
            {!subscription && sameCredentials && saved?.configured && !profile.data?.agent_configured && (
              <Button variant="outline" size="sm" disabled={saving} onClick={() => void useForAgents()}>
                {t("live.use_for_agents")}
              </Button>
            )}
            {showSave ? (
              <Button
                size="sm"
                disabled={saving || !backendModel.trim() || !credentialReady || !effortAvailable ||
                  (subscription && (!options.data || options.isError || !models.some((model) => model.id === backendModel)))}
                onClick={() => void save()}
              >
                {saving && <Loader2 className="animate-spin" />}
                {ready ? t("live.save_changes") : t("live.save")}
              </Button>
            ) : null}
          </div>
        </div>
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        {message && <p role="status" className="text-xs text-muted-foreground">{message}</p>}
      </div>
    </section>
  );
}
