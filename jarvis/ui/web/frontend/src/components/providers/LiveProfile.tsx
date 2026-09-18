import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useT } from "@/i18n";
import { Button } from "@/components/ui/button";

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

async function read<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`GPT-Live: HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export function LiveProfile() {
  const t = useT();
  const queryClient = useQueryClient();
  const profile = useQuery({
    queryKey: ["live-profile"],
    queryFn: () =>
      read<{
        profile: LiveProfileValue;
        key_ready: boolean;
        active: boolean;
        agent_configured: boolean;
      }>("/api/live/profile"),
  });
  const options = useQuery({
    queryKey: ["live-options"],
    queryFn: () =>
      read<{
        models: { id: string; label: string }[];
        voices: string[];
        efforts: string[];
      }>("/api/live/options"),
    staleTime: 300_000,
  });
  const [draft, setDraft] = useState<LiveProfileValue | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const value = draft ?? profile.data?.profile;
  if (!value)
    return <p role="status">{profile.error?.message ?? t("live.loading")}</p>;
  const update = (patch: Partial<LiveProfileValue>) =>
    setDraft({ ...value, ...patch });
  const save = async () => {
    setSaving(true);
    setMessage("");
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
      setDraft(null);
      await queryClient.invalidateQueries();
      setMessage(t("live.saved"));
    } catch (error) {
      setMessage(String(error));
    } finally {
      setSaving(false);
    }
  };
  const useForAgents = async () => {
    setSaving(true);
    try {
      const response = await fetch("/api/live/use-key-for-agent", {
        method: "POST",
      });
      if (!response.ok) throw new Error((await response.json()).detail);
      await queryClient.invalidateQueries();
      setMessage(t("live.agent_saved"));
    } catch (error) {
      setMessage(String(error));
    } finally {
      setSaving(false);
    }
  };
  const field =
    "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-foreground";
  return (
    <section
      className="my-4 space-y-4 rounded-xl border border-border p-5"
      aria-label="GPT-Live"
    >
      <div>
        <h3 className="font-semibold">GPT-Live</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("live.description")}
        </p>
      </div>
      {!profile.data?.key_ready && (
        <p className="text-sm text-warning">{t("live.key_required")}</p>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <label className="text-sm">
          {t("live.voice_model")}
          <input className={field} value={value.model} readOnly />
        </label>
        <label className="text-sm">
          {t("live.voice")}
          <select
            className={field}
            value={value.voice}
            onChange={(e) => update({ voice: e.target.value })}
          >
            {(options.data?.voices ?? [value.voice]).map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          {t("live.thinking_model")}
          <input
            aria-label={t("live.thinking_model")}
            className={field}
            list="live-thinking-models"
            value={value.backend_model}
            placeholder={t("live.choose_model")}
            onChange={(e) => update({ backend_model: e.target.value })}
          />
          <datalist id="live-thinking-models">
            {options.data?.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </datalist>
        </label>
        <label className="text-sm">
          {t("live.reasoning")}
          <select
            className={field}
            value={value.reasoning_effort}
            onChange={(e) => update({ reasoning_effort: e.target.value })}
          >
            {(options.data?.efforts ?? ["", value.reasoning_effort]).map(
              (e) => (
                <option key={e} value={e}>
                  {e || t("live.model_default")}
                </option>
              ),
            )}
          </select>
        </label>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value.web_search}
          onChange={(e) => update({ web_search: e.target.checked })}
        />
        {t("live.web_search")}
      </label>
      <details>
        <summary className="cursor-pointer text-sm">
          {t("live.prompts")}
        </summary>
        <label className="mt-3 block text-sm">
          {t("live.conversation_prompt")}
          <textarea
            className={field}
            value={value.instructions}
            onChange={(e) => update({ instructions: e.target.value })}
          />
        </label>
        <label className="mt-3 block text-sm">
          {t("live.backend_prompt")}
          <textarea
            className={field}
            value={value.backend_instructions}
            onChange={(e) => update({ backend_instructions: e.target.value })}
          />
        </label>
      </details>
      <p className="text-xs text-muted-foreground">{t("live.billing")}</p>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={
            saving || !value.backend_model.trim() || !profile.data?.key_ready
          }
          onClick={() => void save()}
        >
          {t("live.save")}
        </Button>
        {profile.data?.profile.configured && !profile.data.agent_configured && (
          <Button
            variant="outline"
            disabled={saving}
            onClick={() => void useForAgents()}
          >
            {t("live.use_for_agents")}
          </Button>
        )}
      </div>
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
    </section>
  );
}
