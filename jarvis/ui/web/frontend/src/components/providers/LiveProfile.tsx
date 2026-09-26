import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Check, Loader2, Waves } from "lucide-react";
import { useT } from "@/i18n";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

export interface LiveProfileValue {
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

export function LiveProfile({ onSaved }: { onSaved?: () => void } = {}) {
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
      }>("/api/live/profile", signal),
  });
  const options = useQuery({
    queryKey: ["live-options"],
    queryFn: ({ signal }) =>
      read<{
        models: { id: string; label: string }[];
        voices: string[];
        efforts: string[];
      }>("/api/live/options", signal),
    staleTime: 300_000,
  });
  const [draft, setDraft] = useState<LiveProfileValue | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const credentialsChanged = () => {
      void queryClient.invalidateQueries({ queryKey: ["live-profile"] });
    };
    window.addEventListener("jarvis:secret-configured", credentialsChanged);
    return () => window.removeEventListener("jarvis:secret-configured", credentialsChanged);
  }, [queryClient]);
  const value = draft ?? profile.data?.profile;
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
  const ready = Boolean(profile.data?.active && saved?.configured);
  const models = options.data?.models ?? [];
  const modelOptions = models.map((model) => ({
    value: model.id,
    label: model.label,
    hint: model.label === model.id ? undefined : model.id,
  }));
  if (
    value.backend_model &&
    !modelOptions.some((model) => model.value === value.backend_model)
  ) {
    modelOptions.unshift({
      value: value.backend_model,
      label: value.backend_model,
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
  async function save() {
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const response = await fetch("/api/live/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...value, configured: true }),
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
      {!profile.data?.key_ready && (
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
            <span className="font-medium">GPT-Live 1</span>
          </div>
          <label className="block space-y-2 text-sm">
            <span>{t("live.voice")}</span>
            <BrandedSelect
              className={field}
              value={value.voice}
              onValueChange={(voice) => update({ voice })}
              ariaLabel={t("live.voice")}
              options={(options.data?.voices ?? [value.voice]).map((voice) => ({
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
            {t("live.thinking_help")}
          </p>
          <label className="block space-y-2 text-sm">
            <span>{t("live.thinking_model")}</span>
            <BrandedSelect
              className={field}
              value={value.backend_model}
              onValueChange={(backend_model) => update({ backend_model })}
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
              value={value.reasoning_effort}
              onValueChange={(reasoning_effort) => update({ reasoning_effort })}
              ariaLabel={t("live.reasoning")}
              options={(
                options.data?.efforts ?? ["", value.reasoning_effort]
              ).map((effort) => ({
                value: effort,
                    label: effort ? effort.charAt(0).toUpperCase() + effort.slice(1) : t("live.model_default"),
              }))}
            />
          </label>
        </div>
      </div>
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
          onCheckedChange={(web_search) => update({ web_search })}
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
          <label className="space-y-2 text-sm md:col-span-2">
            <span>{t("live.custom_model")}</span>
            <input
              className={field}
              value={value.backend_model}
              onChange={(event) =>
                update({ backend_model: event.target.value })
              }
            />
          </label>
        </div>
      </details>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <p className="max-w-md text-xs leading-relaxed text-muted-foreground">
          {t("live.billing")}
        </p>
        <Button
          disabled={
            saving ||
            !value.backend_model.trim() ||
            !profile.data?.key_ready ||
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
      {saved?.configured && !profile.data?.agent_configured && (
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
