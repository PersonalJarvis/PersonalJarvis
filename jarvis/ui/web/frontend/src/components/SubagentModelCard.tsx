import { BrainModelSelector } from "@/components/BrainModelSelector";
import { LocalModelDownloadPanel } from "@/components/providers/ProviderTierSection";
import { saveSubagentModel, useProviders, type Billing } from "@/hooks/useProviders";
import { useT } from "@/i18n";

/** Mirror of `GET /api/jarvis-agent/status` — the agents' worker bridge. */
export interface SubagentMappingRow {
  jarvis: string;
  /** Server contract: the worker-harness provider slug (e.g. "google"). */
  worker_slug: string;
  env_var: string;
  env_fallback: string | null;
  key_set: boolean;
  api_key_set: boolean;
  dedicated_key_set: boolean;
  shared_key_set: boolean;
  oauth_connected: boolean;
  credential_source: string;
  secret_key: string | null;
  dashboard_url: string | null;
  credential_help: string | null;
  is_active_brain: boolean;
  /** Runs on the user's own hardware and has no credential at all. Such a card
   * is ready the moment it exists — asking it for a key locked a local-only
   * install out of picking any worker. */
  keyless?: boolean;
  /** The card's own display name from the provider spec, so the picker stops
   * showing raw ids ("local-openai") next to proper labels ("xAI Grok"). */
  label?: string | null;
  /** How this subagent is billed — "api" (per token) / "subscription" /
   * "subscription_or_api". Drives the billing badge so the API-vs-subscription
   * distinction is visible right on the subagent cards. */
  billing: Billing;
}

export interface SubagentStatus {
  configured: boolean;
  enabled: boolean;
  binary_path: string;
  binary_detected: string | null;
  version_pin: string | null;
  time_cap_min: number | null;
  concurrency: number | null;
  state_dir_root: string | null;
  brain_primary: string;
  provider_slug: string | null;
  model_override: string | null;
  /** The dedicated subagent LLM pin ([brain.sub_jarvis].model); empty/null
   * means "the active provider's deep model" (shown via model_resolved). */
  sub_model_override: string | null;
  model_resolved: string | null;
  mapping: SubagentMappingRow[];
}

/**
 * The dedicated subagent LLM model pin — the SAME dropdown as the brain cards,
 * showing the active subagent provider's catalog (``brain_primary``) and saving
 * through the subagent endpoint (POST /api/subagent/model) instead of the
 * per-provider model route. Empty selection = the provider's deep/frontier model
 * (shown in the hint via ``model_resolved``).
 */
export function SubagentModelCard({
  status,
  onSaved,
}: {
  status: SubagentStatus;
  onSaved: () => void;
}) {
  const t = useT();
  // The subagent worker slug → the catalog provider id (Codex's worker slug
  // "openai-codex" maps to the catalog's "codex"; all others match 1:1).
  const catalogProvider =
    status.brain_primary === "openai-codex"
      ? "codex"
      : status.brain_primary === "grok-build"
        ? "grok-build"
        : status.brain_primary;
  // Whether the ACTIVE worker's server can be told to download a model. Read
  // off the provider catalog rather than a provider name, so a second local
  // server type that ships a puller lights this up on its own (AP-21).
  const { providers } = useProviders();
  const pullable = providers.find(
    (p) => p.id === catalogProvider && p.supports_model_pull,
  );
  return (
    <div className="space-y-3 rounded-surface border border-border bg-card p-3.5">
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t("subagent_model.description")}
      </p>
      {catalogProvider ? (
        <BrainModelSelector
          providerId={catalogProvider}
          currentModel={status.sub_model_override ?? ""}
          healthSection="subagents"
          healthActive
          onSave={async (model) => {
            const r = await saveSubagentModel(model);
            window.dispatchEvent(new Event("jarvis:agent-switched"));
            onSaved();
            return {
              ok: true,
              provider: status.brain_primary,
              model,
              persisted: r.persisted,
              applied_live: false,
              restart_required: r.restart_required,
              probe: null,
            };
          }}
        />
      ) : (
        <p className="text-xs text-muted-foreground">{t("subagent_model.model_hint")}</p>
      )}
      <p className="text-xs text-muted-foreground">
        {t("subagent_model.model_hint")}
        {status.model_resolved ? ` (${status.model_resolved})` : ""}
      </p>

      {/* The download panel, for a worker whose server can be told to fetch a
          model. Without it this tab dead-ended a local-only install: the picker
          above lists what the server HOLDS, so on a fresh Ollama it is empty,
          and the one screen that could fix that was three tabs away under
          Brain. Same panel, same endpoints — the capability flag decides. */}
      {pullable && <LocalModelDownloadPanel descriptor={pullable} onChanged={onSaved} />}
    </div>
  );
}

