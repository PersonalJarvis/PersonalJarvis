import { useCallback, useEffect, useState } from "react";
import { ArrowUp, Bot, CheckCircle2, Lock, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { saveSubagentModel, switchSubagentProvider } from "@/hooks/useProviders";

/**
 * Subagent tier for the API-Keys view.
 *
 * Visually a sibling of the brain/tts/stt tiers in `ApiKeysView`: the same
 * tier header + `card-outline` cards + identical `StatusBadge` styling. Each
 * provider card carries the same "Set active" radio as the API-key tiers, so
 * the user can switch the Heavy-Task subagent provider seamlessly instead of
 * being stuck on one. There is no key input — sub-agent workers reuse the
 * brain-provider keys entered above (`key_set` mirrors that state).
 *
 * Data source is `GET /api/openclaw/status`; the switch posts to
 * `POST /api/subagent/switch` (3-layer persist). This is its own section
 * rather than a fourth `ProviderTier` because the status payload differs.
 * The endpoint path and the JSON keys below are the server contract; the
 * user-facing UI never says anything but "Subagent".
 */

interface SubagentMappingRow {
  jarvis: string;
  /** Server contract: the engine-side provider slug (e.g. "google"). */
  openclaw: string;
  env_var: string;
  env_fallback: string | null;
  key_set: boolean;
  is_active_brain: boolean;
}

interface SubagentStatus {
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

// Human-readable card titles for the known sub-agent providers; falls back to
// the raw jarvis slug for anything not listed.
const PROVIDER_LABELS: Record<string, string> = {
  gemini: "Google Gemini",
  "claude-api": "Anthropic Claude",
  openai: "OpenAI",
  openrouter: "OpenRouter",
  grok: "xAI Grok",
  // Codex is a direct worker (ChatGPT subscription / OpenAI key), not an
  // OpenClaw-routed provider — surfaced as its own selectable subagent row.
  "openai-codex": "OpenAI Codex",
};

export function SubagentSection() {
  const t = useT();
  const [bridge, setBridge] = useState<SubagentStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-fetch on brain-switch / subagent-switch / secret-set so the active
  // provider highlight + the per-provider "Key gesetzt" badges track live.
  const reload = useCallback(async () => {
    try {
      const res = await fetch("/api/openclaw/status");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: SubagentStatus = await res.json();
      setBridge(data);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void reload();
    const onChange = () => void reload();
    window.addEventListener("jarvis:brain-switched", onChange);
    window.addEventListener("jarvis:subagent-switched", onChange);
    window.addEventListener("jarvis:secret-configured", onChange);
    return () => {
      window.removeEventListener("jarvis:brain-switched", onChange);
      window.removeEventListener("jarvis:subagent-switched", onChange);
      window.removeEventListener("jarvis:secret-configured", onChange);
    };
  }, [reload]);

  if (error) {
    return (
      <section>
        <SectionHeader label={t("apikeys_view.tier_subagent")} />
        <p className="text-xs text-destructive">Status unavailable: {error}</p>
      </section>
    );
  }

  if (!bridge) return null;

  return (
    <section>
      <SectionHeader label={t("apikeys_view.tier_subagent")} />
      {/* The coupling is non-obvious: subagent providers have no key field of
          their own — they reuse the Brain-provider keys set above. Spell it out
          so users know where to add the key instead of looking for an input
          that isn't here. */}
      <p className="mb-3 flex items-start gap-2 rounded-md border border-primary/25 bg-primary/[0.04] px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
        <ArrowUp className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
        <span>
          Subagents reuse the API keys from the{" "}
          <strong className="text-foreground">Brain</strong> section above — there
          is no key field here. Add a key to a brain provider first, then pick
          which one runs your heavy background tasks.
        </span>
      </p>
      <ul className="space-y-3">
        <li>
          <BridgeCard status={bridge} />
        </li>
        <li>
          <SubagentModelCard status={bridge} onSaved={reload} />
        </li>
        {bridge.mapping.map((row) => (
          <li key={row.jarvis}>
            <SubagentProviderCard row={row} onSwitched={reload} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The dedicated subagent LLM model pin. Mirrors the Wiki card's
 * "model (optional)" pattern: empty means the active provider's deep
 * (frontier) model — shown in the hint via `model_resolved` — and a concrete
 * id overrides it for every heavy-task worker spawn.
 */
function SubagentModelCard({
  status,
  onSaved,
}: {
  status: SubagentStatus;
  onSaved: () => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [model, setModel] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Controlled value falls back to the server state until the user edits it.
  const value = model ?? status.sub_model_override ?? "";

  async function handleApply() {
    setPending(true);
    try {
      const next = await saveSubagentModel(value.trim());
      setModel(null);
      pushToast(
        "success",
        next.restart_required
          ? t("subagent_model.saved_restart")
          : t("subagent_model.saved"),
      );
      window.dispatchEvent(new Event("jarvis:subagent-switched"));
      onSaved();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card-outline space-y-3 p-4">
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        {t("subagent_model.description")}
      </p>
      <label className="block">
        <span className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
          {t("subagent_model.model_label")}
        </span>
        <input
          type="text"
          aria-label={t("subagent_model.model_label")}
          value={value}
          onChange={(e) => setModel(e.target.value)}
          placeholder={t("subagent_model.model_placeholder")}
          className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
        />
        <span className="mt-1 block text-[11px] text-muted-foreground">
          {t("subagent_model.model_hint")}
          {status.model_resolved ? ` (${status.model_resolved})` : ""}
        </span>
      </label>
      <button
        type="button"
        onClick={handleApply}
        disabled={pending}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {pending ? t("subagent_model.applying") : t("subagent_model.apply")}
      </button>
    </div>
  );
}

function SectionHeader({ label }: { label: string }) {
  return (
    <h3 className="mb-3 inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
      <Bot className="h-3.5 w-3.5" /> {label}
    </h3>
  );
}

/**
 * The subagent bridge meta-card — read-only configuration (engine state, pin,
 * active worker, model, time-cap). Shares the active-card highlight with
 * the provider cards so the section reads as one system. Engine internals
 * (binary path etc.) are reduced to an "installed"/"not installed"
 * status — the concrete path is developer noise and is not surfaced.
 */
function BridgeCard({ status }: { status: SubagentStatus }) {
  const installed = Boolean(status.binary_detected);
  const live = status.enabled && installed;
  const stateLabel = !status.configured
    ? "not configured"
    : !installed
      ? "engine not installed"
      : status.enabled
        ? "enabled"
        : "disabled (enabled = false)";

  const modelLabel =
    status.model_resolved ??
    (status.provider_slug ? "(follows brain.primary, no model resolvable)" : "—");

  return (
    <div className="card-outline space-y-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">Subagent bridge</span>
            {live ? (
              <span className="chip-yellow">active</span>
            ) : (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                inactive
              </span>
            )}
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            External subagent for heavy tasks (read a repo, build a feature,
            reproduce a bug). It reuses the same brain-provider keys you set
            above &mdash; no separate input field needed. Every registered MCP
            server is handed to the subagent at pre-boot with a mission-isolated
            state directory.
          </p>
        </div>
      </div>

      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Bridge</dt>
        <dd className="font-mono">
          {live ? (
            <CheckCircle2 className="mr-1 inline h-3 w-3 text-emerald-500" />
          ) : (
            <XCircle className="mr-1 inline h-3 w-3 text-muted-foreground" />
          )}
          {stateLabel}
        </dd>

        <dt className="text-muted-foreground">Engine</dt>
        <dd className="font-mono">{installed ? "installed" : "not installed"}</dd>

        <dt className="text-muted-foreground">Pin version</dt>
        <dd className="font-mono">{status.version_pin ?? "—"}</dd>

        <dt className="text-muted-foreground">Active worker</dt>
        <dd className="font-mono">
          <strong>{PROVIDER_LABELS[status.brain_primary] ?? status.brain_primary}</strong>
        </dd>

        <dt className="text-muted-foreground">Model</dt>
        <dd className="break-all font-mono">
          {modelLabel}
          {status.model_override && (
            <span className="ml-2 text-muted-foreground">(override from config)</span>
          )}
        </dd>

        {status.time_cap_min !== null && (
          <>
            <dt className="text-muted-foreground">Time cap</dt>
            <dd className="font-mono">
              {status.time_cap_min} min · max. {status.concurrency} parallel
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}

/**
 * One sub-agent-capable provider, styled to match the `ProviderCard` in
 * `ApiKeysView` (header + badge + id·auth sub-line + active highlight + the
 * "Set active" radio). Clicking the card or the radio switches the Heavy-Task
 * subagent provider via `POST /api/subagent/switch` (3-layer persist). The key
 * itself is managed by the brain-provider card above; a provider with no key
 * cannot be activated (warning toast instead of a silent no-op).
 */
function SubagentProviderCard({
  row,
  onSwitched,
}: {
  row: SubagentMappingRow;
  onSwitched: () => void | Promise<void>;
}) {
  const label = PROVIDER_LABELS[row.jarvis] ?? row.jarvis;
  const [activating, setActivating] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);

  async function activate() {
    if (row.is_active_brain || activating) return;
    if (!row.key_set) {
      pushToast(
        "warning",
        `${label}: set the API key on the brain provider above first.`,
      );
      return;
    }
    setActivating(true);
    try {
      const result = await switchSubagentProvider(row.jarvis);
      const note = result.restart_required
        ? " (active from next restart)"
        : "";
      pushToast("success", `Subagent → ${label}${note}`);
      window.dispatchEvent(new CustomEvent("jarvis:subagent-switched"));
      await onSwitched();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setActivating(false);
    }
  }

  // Click anywhere on the card activates — except on the radio/label (which
  // has its own handler) so a single user click never fires activate() twice.
  function handleCardActivate(e: React.MouseEvent<HTMLDivElement>) {
    const target = e.target as HTMLElement | null;
    if (target && (target.closest("input") || target.closest("label"))) {
      return;
    }
    void activate();
  }

  return (
    <div
      onClick={handleCardActivate}
      onDoubleClick={handleCardActivate}
      title={
        row.is_active_brain
          ? "This subagent provider is active"
          : row.key_set
            ? "Activate this subagent provider"
            : "Set an API key first"
      }
      className={cn(
        "card-outline space-y-2 p-4 transition-colors",
        row.is_active_brain
          ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30"
          : row.key_set
            ? "cursor-pointer hover:border-primary/40 hover:bg-primary/[0.02]"
            : "opacity-95",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{label}</span>
            <SubagentStatusBadge row={row} />
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            <code className="font-mono">
              {row.jarvis} → {row.openclaw}
            </code>
            {" · "}
            <span className="font-mono">
              {row.env_var}
              {row.env_fallback && ` / ${row.env_fallback}`}
            </span>
          </p>
        </div>

        <SubagentActiveControl
          row={row}
          activating={activating}
          onActivate={activate}
        />
      </div>

      {!row.key_set && (
        <p className="flex items-center gap-1.5 text-[11px] text-amber-600">
          <Lock className="h-3 w-3 shrink-0" />
          <span>
            Locked &mdash; add the <strong>{label}</strong> key in the Brain
            section above to unlock it.
          </span>
        </p>
      )}
    </div>
  );
}

/**
 * Radio-based active toggle, mirroring `ActiveControl` in `ApiKeysView`.
 * Source of truth stays `row.is_active_brain` from `/api/openclaw/status`;
 * the radio reflects the server state and is not held locally.
 * `name="active-subagent"` gives native single-select across the cards.
 * The radio is NOT disabled when the key is missing — instead `activate()`
 * routes a warning toast so every click gets a reaction.
 */
function SubagentActiveControl({
  row,
  activating,
  onActivate,
}: {
  row: SubagentMappingRow;
  activating: boolean;
  onActivate: () => void;
}) {
  const labelTitle = row.is_active_brain
    ? "This subagent provider is active"
    : row.key_set
      ? "Activate this subagent provider"
      : "Set an API key first";

  return (
    <label
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      className={cn(
        "inline-flex shrink-0 cursor-pointer select-none items-center gap-1.5 text-xs",
        row.is_active_brain
          ? "font-medium text-primary"
          : row.key_set
            ? "text-muted-foreground hover:text-foreground"
            : "text-muted-foreground/70",
      )}
      title={labelTitle}
    >
      <input
        type="radio"
        name="active-subagent"
        checked={row.is_active_brain}
        onChange={() => onActivate()}
        disabled={activating}
        className="accent-primary"
      />
      {activating ? "Activating…" : "Set active"}
    </label>
  );
}

/**
 * Mirrors the three StatusBadge variants from `ApiKeysView` (chip-yellow
 * "active" / emerald "ready" / muted "open") so the subagent cards are
 * visually indistinguishable from the API-key cards above.
 */
function SubagentStatusBadge({ row }: { row: SubagentMappingRow }) {
  if (row.is_active_brain) return <span className="chip-yellow">active</span>;
  if (row.key_set) {
    return (
      <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">
        ready
      </span>
    );
  }
  return (
    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
      open
    </span>
  );
}
