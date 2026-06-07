import { useCallback, useEffect, useState } from "react";
import { Bot, CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { switchSubagentProvider } from "@/hooks/useProviders";

/**
 * Subagent tier for the API-Keys view.
 *
 * Visually a sibling of the brain/tts/stt tiers in `ApiKeysView`: the same
 * tier header + `card-outline` cards + identical `StatusBadge` styling. Each
 * provider card carries the same "Als aktiv" radio as the API-key tiers, so
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
        <p className="text-xs text-destructive">Status nicht ladbar: {error}</p>
      </section>
    );
  }

  if (!bridge) return null;

  return (
    <section>
      <SectionHeader label={t("apikeys_view.tier_subagent")} />
      <ul className="space-y-3">
        <li>
          <BridgeCard status={bridge} />
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
 * (binary path etc.) are reduced to an "installiert"/"nicht installiert"
 * status — the concrete path is developer noise and is not surfaced.
 */
function BridgeCard({ status }: { status: SubagentStatus }) {
  const installed = Boolean(status.binary_detected);
  const live = status.enabled && installed;
  const stateLabel = !status.configured
    ? "nicht konfiguriert"
    : !installed
      ? "Engine nicht installiert"
      : status.enabled
        ? "aktiviert"
        : "deaktiviert (enabled = false)";

  const modelLabel =
    status.model_resolved ??
    (status.provider_slug ? "(folgt brain.primary, kein Modell aufloesbar)" : "—");

  return (
    <div className="card-outline space-y-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">Subagent-Bridge</span>
            {live ? (
              <span className="chip-yellow">aktiv</span>
            ) : (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                inaktiv
              </span>
            )}
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            Externer Subagent fuer Heavy-Tasks (lies Repo, baue Feature,
            reproduziere Bug). Nutzt dieselben Brain-Provider-Keys wie unten
            &mdash; kein separates Eingabefeld noetig. Alle registrierten
            MCP-Server werden beim Pre-Boot mit Mission-isoliertem State-Dir an
            den Subagenten weitergegeben.
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
        <dd className="font-mono">{installed ? "installiert" : "nicht installiert"}</dd>

        <dt className="text-muted-foreground">Pin-Version</dt>
        <dd className="font-mono">{status.version_pin ?? "—"}</dd>

        <dt className="text-muted-foreground">Aktiver Worker</dt>
        <dd className="font-mono">
          <strong>{PROVIDER_LABELS[status.brain_primary] ?? status.brain_primary}</strong>
        </dd>

        <dt className="text-muted-foreground">Modell</dt>
        <dd className="break-all font-mono">
          {modelLabel}
          {status.model_override && (
            <span className="ml-2 text-muted-foreground">(Override aus Config)</span>
          )}
        </dd>

        {status.time_cap_min !== null && (
          <>
            <dt className="text-muted-foreground">Time-Cap</dt>
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
 * "Als aktiv" radio). Clicking the card or the radio switches the Heavy-Task
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
        `${label}: erst den API-Key oben beim Brain-Provider setzen.`,
      );
      return;
    }
    setActivating(true);
    try {
      const result = await switchSubagentProvider(row.jarvis);
      const note = result.restart_required
        ? " (aktiv ab nächstem Neustart)"
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
          ? "Dieser Subagent-Provider ist aktiv"
          : row.key_set
            ? "Diesen Subagent-Provider aktivieren"
            : "Erst API-Key setzen"
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
        <p className="text-[11px] text-muted-foreground">
          Key fehlt &mdash; oben beim Brain-Provider setzen, dann ist dieser
          Subagent einsatzbereit.
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
    ? "Dieser Subagent-Provider ist aktiv"
    : row.key_set
      ? "Diesen Subagent-Provider aktivieren"
      : "Erst API-Key setzen";

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
      {activating ? "Aktiviere…" : "Als aktiv"}
    </label>
  );
}

/**
 * Mirrors the three StatusBadge variants from `ApiKeysView` (chip-yellow
 * "aktiv" / emerald "eingerichtet" / muted "offen") so the subagent cards are
 * visually indistinguishable from the API-key cards above.
 */
function SubagentStatusBadge({ row }: { row: SubagentMappingRow }) {
  if (row.is_active_brain) return <span className="chip-yellow">aktiv</span>;
  if (row.key_set) {
    return (
      <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">
        eingerichtet
      </span>
    );
  }
  return (
    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
      offen
    </span>
  );
}
