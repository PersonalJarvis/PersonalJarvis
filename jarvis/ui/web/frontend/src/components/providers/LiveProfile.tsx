import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Check, Loader2, Waves } from "lucide-react";
import { useT } from "@/i18n";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { fetchAgentAccounts } from "@/lib/agentAccountsApi";
import { LiveSubscriptionAccount } from "./LiveSubscriptionAccount";

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

async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { cache: "no-store", signal });
  if (!response.ok) throw new Error(`GPT-Live: HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export function LiveProfile({ onSaved, onAuthModeChange }: {
  onSaved?: () => void;
  onAuthModeChange?: (mode: LiveAuthMode, pending: boolean) => void;
} = {}) {
  const t = useT();
  const queryClient = useQueryClient();
  const profile = useQuery({
    queryKey: ["live-profile"],
    queryFn: ({ signal }) =>
      read<{
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
      }>("/api/live/profile", signal),
  });
  const [draft, setDraft] = useState<LiveProfileValue | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const value = draft ?? profile.data?.profile;
  const authMode = value?.auth_mode ?? "api_key";
  const subscription = authMode === "chatgpt_subscription";
  const accounts = useQuery({
    queryKey: ["live-subscription-accounts"],
    queryFn: fetchAgentAccounts,
    enabled: subscription,
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
    enabled: Boolean(value) && (!subscription || Boolean(account?.connected && account.mode === "subscription")),
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
      <p role="status" className="py-4 text-sm text-muted-foreground">
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
    ? Boolean(account?.connected && account.mode === "subscription" && !accounts.isError)
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
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const response = await fetch("/api/live/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...chosen, configured: true }),
      });
      if (!response.ok)
        throw new Error(
          (await response.json()).detail ?? `HTTP ${response.status}`,
        );
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
  const field =
    "w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground";
  return (
    <section aria-label="GPT-Live" className="space-y-5 py-2">
      <label className="block space-y-2 text-sm">
        <span>{t("live.billing_method")}</span>
        <BrandedSelect
          value={authMode}
          disabled={saving}
          ariaLabel={t("live.billing_method")}
          onValueChange={(mode) => update({ auth_mode: mode as LiveAuthMode })}
          options={[
            { value: "api_key", label: t("live.api_key_mode") },
            { value: "chatgpt_subscription", label: t("live.subscription_mode") },
          ]}
        />
      </label>
      {subscription ? (
        <>
          <LiveSubscriptionAccount
            group={group}
            accountId={value.subscription_account_id ?? ""}
            loading={accounts.isPending}
            disabled={saving}
            voiceStatus={profile.data?.subscription?.account_id === accountId
              ? profile.data.subscription.voice_status : "unverified"}
            onAccountChange={(subscription_account_id) => update({ subscription_account_id })}
            onConnected={() => {
              void queryClient.invalidateQueries({ queryKey: ["live-subscription-accounts"] });
              void queryClient.invalidateQueries({ queryKey: ["live-profile"] });
              void queryClient.invalidateQueries({ queryKey: ["live-options", "chatgpt_subscription"] });
            }}
          />
          {accounts.isError ? <p role="alert" className="text-sm text-destructive">{t("live.subscription_accounts_failed")}</p> : null}
        </>
      ) : null}
      {!subscription && !profile.data?.key_ready && (
        <p role="status" className="text-sm text-warning">
          {t("live.key_required")}
        </p>
      )}
      <div className="grid gap-6 md:grid-cols-2">
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Waves className="h-4 w-4 text-muted-foreground" />
            {t("live.conversation_heading")}
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("live.conversation_help")}
          </p>
          <div className="flex items-center justify-between rounded-lg bg-secondary/50 px-3 py-2.5 text-sm">
            <span className="text-muted-foreground">
              {t("live.voice_model")}
            </span>
            <span className="font-medium">{subscription ? "GPT-Live · ChatGPT" : "GPT-Live 1"}</span>
          </div>
          <label className="block space-y-2 text-sm">
            <span>{t("live.voice")}</span>
            <BrandedSelect
              className={field}
              value={voice}
              disabled={saving || (subscription && !options.data)}
              onValueChange={(selected) => choose(subscription ? { subscription_voice: selected } : { voice: selected })}
              ariaLabel={t("live.voice")}
              options={(options.data?.voices ?? (subscription ? [] : [voice])).map((voice) => ({
                value: voice,
                label: voice.charAt(0).toUpperCase() + voice.slice(1),
              }))}
            />
          </label>
        </div>
        <div className="space-y-4 md:border-l md:border-border md:pl-6">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Brain className="h-4 w-4 text-muted-foreground" />
            {t("live.thinking_heading")}
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t(subscription ? "live.subscription_thinking_help" : "live.thinking_help")}
          </p>
          <label className="block space-y-2 text-sm">
            <span>{t("live.thinking_model")}</span>
            <BrandedSelect
              className={field}
              value={backendModel}
              disabled={saving || (subscription && !options.data)}
              onValueChange={chooseModel}
              ariaLabel={t("live.thinking_model")}
              placeholder={t("live.choose_model")}
              searchPlaceholder={t("live.search_models")}
              options={modelOptions}
            />
          </label>
          <label className="block space-y-2 text-sm">
            <span>{t("live.reasoning")}</span>
            <BrandedSelect
              className={field}
              value={reasoningEffort}
              disabled={saving}
              onValueChange={(selected) => choose(subscription ? { subscription_reasoning_effort: selected } : { reasoning_effort: selected })}
              ariaLabel={t("live.reasoning")}
              options={efforts.map((effort) => ({
                value: effort,
                label: effort ? effort.charAt(0).toUpperCase() + effort.slice(1) : t("live.model_default"),
              }))}
            />
          </label>
        </div>
      </div>
      {options.isError ? <p role="alert" className="text-sm text-destructive">{t("live.options_failed")}</p> : null}
      <div className="flex items-center justify-between gap-4 border-t border-border pt-4">
        <div>
          <label htmlFor="live-web-search" className="text-sm font-medium">
            {t("live.web_search")}
          </label>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("live.web_search_help")}
          </p>
        </div>
        <Switch
          id="live-web-search"
          checked={value.web_search}
          disabled={saving}
          onCheckedChange={(web_search) => choose({ web_search })}
          aria-label={t("live.web_search")}
        />
      </div>
      <details className="border-t border-border pt-4">
        <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
          {t("live.prompts")}
        </summary>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span>{t("live.conversation_prompt")}</span>
            <textarea
              className={field}
              rows={3}
              value={value.instructions}
              onChange={(event) => update({ instructions: event.target.value })}
            />
          </label>
          <label className="space-y-2 text-sm">
            <span>{t("live.backend_prompt")}</span>
            <textarea
              className={field}
              rows={3}
              value={value.backend_instructions}
              onChange={(event) =>
                update({ backend_instructions: event.target.value })
              }
            />
          </label>
          {!subscription ? <label className="space-y-2 text-sm md:col-span-2">
            <span>{t("live.custom_model")}</span>
            <input
              className={field}
              value={value.backend_model}
              onChange={(event) =>
                update({ backend_model: event.target.value })
              }
            />
          </label> : null}
        </div>
      </details>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <p className="max-w-md text-xs leading-relaxed text-muted-foreground">
          {t(subscription ? "live.subscription_billing" : "live.billing")}
        </p>
        <Button
          disabled={
            saving ||
            !backendModel.trim() ||
            !credentialReady ||
            !effortAvailable ||
            (subscription && (!options.data || options.isError || !models.some((model) => model.id === backendModel))) ||
            (ready && !dirty)
          }
          onClick={() => void save()}
        >
          {saving ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : ready && !dirty ? (
            <Check className="mr-2 h-4 w-4" />
          ) : null}
          {ready && !dirty
            ? t("live.ready")
            : ready
              ? t("live.save_changes")
              : t("live.save")}
        </Button>
      </div>
      {!subscription && saved?.configured && !profile.data?.agent_configured && (
        <Button
          variant="outline"
          disabled={saving}
          onClick={() => void useForAgents()}
        >
          {t("live.use_for_agents")}
        </Button>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm text-muted-foreground">
          {message}
        </p>
      )}
    </section>
  );
}
