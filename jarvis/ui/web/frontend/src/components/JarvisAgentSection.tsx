import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, ChevronDown, FlaskConical, Frame, HardDrive, KeyRound, Lock, LogIn, LogOut, Terminal, type LucideIcon } from "lucide-react";
import { agentBrandNow, useAgentBrand } from "@/lib/agentBrand";
import { PromptWriterCard } from "@/components/PromptWriterCard";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  codexLogout,
  loginAntigravity,
  loginClaude,
  loginGrokBuild,
  logoutAntigravity,
  logoutClaude,
  logoutGrokBuild,
  saveSubagentModel,
  startCodexLogin,
  switchSubagentProvider,
  testAgentCli,
  useProviders,
  type AgentCliTestResult,
  type AntigravityStatus,
  type Billing,
  type ClaudeStatus,
  type CodexStatus,
  type GrokBuildStatus,
} from "@/hooks/useProviders";
import { BrainModelSelector } from "@/components/BrainModelSelector";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { AgentAccountsPanel } from "@/components/AgentAccountsPanel";
import { Button } from "@/components/ui/button";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import {
  LocalModeNotice,
  LocalModelDownloadPanel,
  StateChip,
} from "@/components/providers/ProviderTierSection";
import { filterForLocalMode, useLocalMode } from "@/lib/localMode";

/**
 * Subagent tier for the API-Keys view.
 *
 * Visually a sibling of the provider tiers in `ApiKeysView`: the same row
 * anatomy (brand mark · name · state · detail, the Use control on the right,
 * one editor body open at a time), in three groups — own hardware, coding
 * CLIs, API keys — so the three ways a worker is powered never mix. Each API
 * row carries its own scoped key field. Legacy shared Brain credentials remain
 * a visible compatibility fallback, never the only configuration path.
 *
 * Data source is `GET /api/jarvis-agent/status`; the switch posts to
 * `POST /api/jarvis-agent/switch` (3-layer persist). This is its own section
 * rather than a fourth `ProviderTier` because the status payload differs.
 * The endpoint path and the JSON keys below are the server contract; the
 * user-facing UI brands the agent system with the wake-word-derived assistant
 * name ("Ruben" -> "Ruben-Agent") via agentBrand — never a hardcoded name.
 */

interface SubagentMappingRow {
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
  nvidia: "NVIDIA NIM",
  // Codex is a direct worker (ChatGPT subscription / OpenAI key), not a
  // Jarvis-Agent-routed provider — surfaced as its own selectable subagent row.
  "openai-codex": "OpenAI Codex",
  // Antigravity drives the Google subscription CLI as a direct worker (OAuth, no
  // API key), the Google sibling of Codex.
  antigravity: "Antigravity (Google subscription)",
  "grok-build": "Grok Build",
};

/**
 * The disclosure key of a row. Claude is the one slug that renders TWICE —
 * a subscription row and an API-key row — so its key carries which of the two
 * the live auth mode belongs to; every other slug is its own key.
 */
function rowId(slug: string, claudeMode?: string | null): string {
  if (slug !== "claude-api") return slug;
  return claudeMode === "api_key" ? "claude-api:api" : "claude-api:sub";
}

/**
 * Poll a CLI status endpoint after starting an external login flow, refreshing
 * the section on each tick, until it reports `connected` or a timeout.
 *
 * Why: external CLI logins (Codex `codex login`, Antigravity Google sign-in)
 * complete in a SEPARATE browser/console window AFTER the POST returns, so a
 * single immediate refetch always reads the still-disconnected state. Without
 * this poll the card stays "open" / locked until the user manually reloads —
 * the exact "I connected Codex but can't select it" symptom.
 */
async function pollStatusUntilConnected(
  statusUrl: string,
  onTick: () => void | Promise<void>,
  {
    maxMs = 120_000,
    intervalMs = 2_500,
    isReady,
  }: {
    maxMs?: number;
    intervalMs?: number;
    isReady?: (data: { connected?: boolean; mode?: string }) => boolean;
  } = {},
): Promise<boolean> {
  const ready = isReady ?? ((data) => Boolean(data?.connected));
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, intervalMs));
    await onTick();
    try {
      const res = await fetch(statusUrl);
      if (res.ok) {
        const data = await res.json();
        if (ready(data)) return true;
      }
    } catch {
      // transient (app restarting / network blip) — keep polling
    }
  }
  return false;
}

export function JarvisAgentSection({
  hideHeader = false,
}: {
  /** Suppress the section header — used inside the API-Keys "Subagents" tab,
   * whose category hero already shows the title (avoids a double label). */
  hideHeader?: boolean;
} = {}) {
  const t = useT();
  const [bridge, setBridge] = useState<SubagentStatus | null>(null);
  const [codexStatus, setCodexStatus] = useState<CodexStatus | null>(null);
  const [antigravityStatus, setAntigravityStatus] =
    useState<AntigravityStatus | null>(null);
  const [claudeStatus, setClaudeStatus] = useState<ClaudeStatus | null>(null);
  const [grokBuildStatus, setGrokBuildStatus] = useState<GrokBuildStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The same per-machine view preference the provider tiers read. The subagent
  // tier used to ignore it, which made Local Mode look broken exactly where a
  // keyless install most needs it: this is the tab that picks the worker.
  const { localMode, setLocalMode } = useLocalMode();
  // One row open at a time, the active worker by default. Anchored once, on
  // the first status that arrives: re-opening on every refetch would yank the
  // list under the pointer the moment a switch lands.
  const [openRow, setOpenRow] = useState<string | null>(null);
  const openRowAnchored = useRef(false);
  const toggleRow = (id: string) => setOpenRow((cur) => (cur === id ? null : id));

  // Re-fetch on brain-switch / subagent-switch / secret-set so the active
  // provider highlight + the per-provider "Key gesetzt" badges track live.
  const reload = useCallback(async () => {
    try {
      const [res, codexRes, antigravityRes, claudeRes, grokBuildRes] = await Promise.all([
        fetch("/api/jarvis-agent/status"),
        fetch("/api/codex/status").catch(() => null),
        fetch("/api/antigravity/status").catch(() => null),
        fetch("/api/claude/status").catch(() => null),
        fetch("/api/grok-build/status").catch(() => null),
      ]);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: SubagentStatus = await res.json();
      setBridge(data);
      const codexData: CodexStatus | null = codexRes?.ok ? await codexRes.json() : null;
      setCodexStatus(codexData);
      if (antigravityRes?.ok) {
        setAntigravityStatus(await antigravityRes.json());
      } else {
        setAntigravityStatus(null);
      }
      const claudeData: ClaudeStatus | null = claudeRes?.ok ? await claudeRes.json() : null;
      setClaudeStatus(claudeData);
      if (grokBuildRes?.ok) {
        setGrokBuildStatus(await grokBuildRes.json());
      } else {
        setGrokBuildStatus(null);
      }
      if (!openRowAnchored.current) {
        openRowAnchored.current = true;
        const active = data.mapping.find((r) => r.is_active_brain);
        setOpenRow(
          active
            ? rowId(active.jarvis, active.jarvis === "claude-api" ? claudeData?.mode : undefined)
            : null,
        );
      }
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void reload();
    const onChange = () => void reload();
    window.addEventListener("jarvis:brain-switched", onChange);
    window.addEventListener("jarvis:agent-switched", onChange);
    window.addEventListener("jarvis:secret-configured", onChange);
    return () => {
      window.removeEventListener("jarvis:brain-switched", onChange);
      window.removeEventListener("jarvis:agent-switched", onChange);
      window.removeEventListener("jarvis:secret-configured", onChange);
    };
  }, [reload]);

  if (error) {
    return (
      <section>
        {!hideHeader && <SectionHeader label={t("apikeys_view.tier_subagent")} />}
        <p className="text-xs text-destructive">Status unavailable: {error}</p>
      </section>
    );
  }

  if (!bridge) return null;

  // Codex + Antigravity are subscription logins (OAuth) rendered as their own
  // connection cards; their "Set active" control now lives ON those cards, so
  // exclude them from the generic provider list to avoid a duplicate card per
  // CLI (the confusing "no select button on the subscription" the user hit).
  // Local Mode applies to EVERY card on this tab, including the three
  // subscription-login cards below — a keyless install has no ChatGPT plan
  // either, and hiding only the API column would have been a half-answer.
  // Same rule as the provider tiers: own-hardware cards stay, plus whichever
  // card is the ACTIVE worker, so the tab can always show what is running.
  const isActiveRow = (r: SubagentMappingRow) => r.is_active_brain;
  const { visible: localVisibleRows, hiddenCount: hiddenByLocalMode } =
    filterForLocalMode(bridge.mapping, localMode, isActiveRow);
  const shown = new Set(localVisibleRows.map((r) => r.jarvis));
  const inLocalMode = (r: SubagentMappingRow | undefined) =>
    r && shown.has(r.jarvis) ? r : undefined;

  const codexRow = inLocalMode(
    bridge.mapping.find((r) => r.jarvis === "openai-codex"),
  );
  const antigravityRow = inLocalMode(
    bridge.mapping.find((r) => r.jarvis === "antigravity"),
  );
  // Claude is dual-billed (Claude Max subscription OR Anthropic API key) — it
  // gets its own connection card showing the signed-in account, like Codex /
  // Antigravity, so exclude it from the generic per-key provider list too.
  const claudeRow = inLocalMode(
    bridge.mapping.find((r) => r.jarvis === "claude-api"),
  );
  const grokBuildRow = inLocalMode(
    bridge.mapping.find((r) => r.jarvis === "grok-build"),
  );
  const providerRows = localVisibleRows.filter(
    (r) =>
      r.jarvis !== "openai-codex" &&
      r.jarvis !== "antigravity" &&
      r.jarvis !== "claude-api" &&
      r.jarvis !== "grok-build",
  );
  // Split the generic providers by access type so each lands in the right
  // group. Splitting on the backend `billing` field keeps a future provider in
  // the correct group instead of the wrong one (AP-21: gate on capability,
  // never a provider name).
  //
  // Own-hardware workers get their OWN group rather than riding along in the
  // subscription column. They were landing there because the split was
  // "anything that is not billed per token", which put Ollama under a heading
  // that reads "sign in with an account" — the opposite of what a keyless card
  // is. Three access types, three headings.
  const localProviderRows = providerRows.filter((r) => r.billing === "local");
  const subProviderRows = providerRows.filter(
    (r) => r.billing !== "api" && r.billing !== "local",
  );
  const apiProviderRows = providerRows.filter((r) => r.billing === "api");
  const hasSubscriptionColumn =
    Boolean(codexRow || antigravityRow || claudeRow || grokBuildRow) ||
    subProviderRows.length > 0;
  const hasApiColumn = Boolean(claudeRow) || apiProviderRows.length > 0;

  const disclosure = (id: string) => ({
    expanded: openRow === id,
    onToggle: () => toggleRow(id),
  });

  return (
    <section className="space-y-4">
      {!hideHeader && <SectionHeader label={t("apikeys_view.tier_subagent")} />}

      {localMode && hiddenByLocalMode > 0 && (
        <LocalModeNotice
          hiddenCount={hiddenByLocalMode}
          keptActiveHosted={localVisibleRows.some(
            (r) => isActiveRow(r) && r.billing !== "local",
          )}
          onDisable={() => setLocalMode(false)}
        />
      )}

      <BridgeStatusStrip status={bridge} />

      {/* The model pin sits right under the status line — it is the setting
          users come back for, so it must not hide below the worker rows. */}
      <SubagentModelCard status={bridge} onSaved={reload} />

      {/* Who writes the Agentic-IDE briefs. Sits next to the model pin because
          it is the same kind of decision — which model does work on the user's
          behalf — and because the groups below are exactly the choice it is
          about: a coding CLI you are signed in to, or an API key. */}
      <PromptWriterCard />

      {/* Three groups, three lists: the three ways a worker is powered never
          mix, and each heading says what the rows under it need. Own-hardware
          workers lead — they are the group with no account and no bill behind
          them, and putting them first is what makes this tab usable at all on
          an install that entered no credentials. */}
      {localProviderRows.length > 0 && (
        <AgentGroup
          icon={HardDrive}
          title="On your own hardware"
          hint="no account, no key"
          testId="agent-group-local"
        >
          {localProviderRows.map((row) => (
            <SubagentProviderCard
              key={row.jarvis}
              row={row}
              onSwitched={reload}
              {...disclosure(row.jarvis)}
            />
          ))}
        </AgentGroup>
      )}

      {hasSubscriptionColumn && (
        <AgentGroup
          icon={Terminal}
          title="Coding CLIs"
          hint="sign in with a subscription — no key"
          testId="agent-group-clis"
        >
          {codexRow && (
            <CodexConnectionCard
              status={codexStatus}
              row={codexRow}
              onChanged={reload}
              {...disclosure("openai-codex")}
            />
          )}
          {antigravityRow && (
            <AntigravityConnectionCard
              status={antigravityStatus}
              row={antigravityRow}
              onChanged={reload}
              {...disclosure("antigravity")}
            />
          )}
          {claudeRow && (
            <ClaudeConnectionCard
              status={claudeStatus}
              row={claudeRow}
              onChanged={reload}
              {...disclosure("claude-api:sub")}
            />
          )}
          {grokBuildRow && (
            <GrokBuildConnectionCard
              status={grokBuildStatus}
              row={grokBuildRow}
              onChanged={reload}
              {...disclosure("grok-build")}
            />
          )}
          {subProviderRows.map((row) => (
            <SubagentProviderCard
              key={row.jarvis}
              row={row}
              onSwitched={reload}
              {...disclosure(row.jarvis)}
            />
          ))}
        </AgentGroup>
      )}

      {hasApiColumn && (
        <AgentGroup
          icon={KeyRound}
          title="API keys"
          hint="billed per token · a dedicated key per worker; shared Brain keys stay a fallback"
          testId="agent-group-api"
        >
          {claudeRow && (
            <ClaudeApiCard
              status={claudeStatus}
              row={claudeRow}
              onChanged={reload}
              {...disclosure("claude-api:api")}
            />
          )}
          {apiProviderRows.map((row) => (
            <SubagentProviderCard
              key={row.jarvis}
              row={row}
              onSwitched={reload}
              {...disclosure(row.jarvis)}
            />
          ))}
        </AgentGroup>
      )}

      {/* Below the groups rather than inside one: this is a second axis.
          The rows above answer "which provider powers the agent"; this answers
          "which of YOUR seats on that provider", and a panel holding two Claude
          Max logins does not belong in a list of one-login-per-provider rows. */}
      <AgentAccountsPanel />
    </section>
  );
}

/**
 * The dedicated subagent LLM model pin. Mirrors the Wiki card's
 * "model (optional)" pattern: empty means the active provider's deep
 * (frontier) model — shown in the hint via `model_resolved` — and a concrete
 * id overrides it for every heavy-task worker spawn.
 */
/**
 * The dedicated subagent LLM model pin — the SAME dropdown as the brain cards,
 * showing the active subagent provider's catalog (``brain_primary``) and saving
 * through the subagent endpoint (POST /api/subagent/model) instead of the
 * per-provider model route. Empty selection = the provider's deep/frontier model
 * (shown in the hint via ``model_resolved``).
 */
function SubagentModelCard({
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
    <div className="space-y-3 rounded-surface border border-border bg-card/50 p-3.5">
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

function SectionHeader({ label }: { label: string }) {
  return (
    <h3 className="mb-3 inline-flex items-center gap-2 text-[11px] uppercase tracking-wider text-muted-foreground">
      <Bot className="h-3.5 w-3.5" /> {label}
    </h3>
  );
}

/**
 * The bridge status — one line, not a box. A status dot + plain-language
 * state, then the read-only worker / model / limits as dim text separated by
 * middots, and the one action (open the run visuals) on the right. Engine
 * internals (binary path etc.) are reduced to "installed" / "not installed";
 * the concrete path is developer noise and is not surfaced.
 */
function BridgeStatusStrip({ status }: { status: SubagentStatus }) {
  const t = useT();
  const activeRow = status.mapping.find((row) => row.is_active_brain);
  const live = Boolean(activeRow?.key_set);
  const worker = PROVIDER_LABELS[status.brain_primary] ?? status.brain_primary;
  const model = status.model_resolved ?? status.sub_model_override ?? null;
  /*
   * The one place in this section that talks to another one.
   *
   * This strip describes the worker that RUNS; whatever it drew ends up in the
   * run archive, and until now the only way to that picture was to remember the
   * filename and go looking for it in Outputs. `requestVisual` hands the stage a
   * target and moves the app there in a single state update — see
   * VisualStageRequest in store/events.ts for why the section is asked rather
   * than reached into.
   */
  const requestVisual = useEventStore((s) => s.requestVisual);

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 py-1 text-xs">
      <span className="inline-flex shrink-0 items-center gap-2">
        <span
          aria-hidden="true"
          className={cn(
            "h-[7px] w-[7px] shrink-0 rounded-full",
            live
              ? "bg-emerald-500 shadow-[0_0_0_3px_rgb(16_185_129/0.18)]"
              : "bg-amber-500",
          )}
        />
        <span className="font-medium text-foreground">
          {live ? "Agent provider configured" : "Agent provider needs setup"}
        </span>
      </span>
      <span aria-hidden="true" className="text-border">·</span>
      <span className="min-w-0 truncate text-muted-foreground">
        {live ? "Credential or subscription available" : "Add a key or connect a login"}
        {" · worker "}
        <span className="font-medium text-foreground">{worker}</span>
        {model && (
          <>
            {" · "}
            <span className="font-mono">{model}</span>
          </>
        )}
        {status.time_cap_min !== null && (
          <>{` · ${status.time_cap_min} min · max ${status.concurrency} parallel`}</>
        )}
        {status.version_pin && (
          <>
            {" · pin "}
            <span className="font-mono">{status.version_pin}</span>
          </>
        )}
      </span>
      <button
        type="button"
        onClick={() => requestVisual()}
        data-testid="agent-show-visuals"
        title={t("visualization.open_from_agent_hint")}
        className="ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-control text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Frame className="h-3.5 w-3.5" aria-hidden />
        {t("visualization.open_from_agent")}
      </button>
    </div>
  );
}

/**
 * One of the three worker groups — own hardware, coding CLIs, API keys — as a
 * kicker line over its own bordered list. Three headings, three lists, no
 * colour-coding: the grouping IS the distinction the old violet/sky/emerald
 * stripes tried to draw, and it reads without a legend.
 */
function AgentGroup({
  icon: Icon,
  title,
  hint,
  testId,
  children,
}: {
  icon: LucideIcon;
  title: string;
  hint: string;
  testId?: string;
  children: React.ReactNode;
}) {
  return (
    <div data-testid={testId} className="space-y-2">
      <div className="flex min-w-0 items-center gap-2 px-0.5 text-xs">
        <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="font-display text-[13px] font-semibold tracking-tight">{title}</span>
        <span aria-hidden="true" className="text-border">·</span>
        <span className="truncate text-muted-foreground">{hint}</span>
      </div>
      <ul className="divide-y divide-border/70 overflow-hidden rounded-surface border border-border bg-card/50">
        {children}
      </ul>
    </div>
  );
}

/**
 * The one shared row every worker is built from — the same anatomy as the
 * provider rows on the other tabs, so the whole screen reads as one list
 * system: logo · name · state · one-line detail on the left, the action and
 * the Use control on the right, a chevron when there is a body to open.
 *
 * The body (key field, install hint, CLI test) mounts only while open; the
 * section opens one row at a time, the active worker by default.
 */
function AgentRow({
  label,
  slug,
  title,
  state,
  subtitle,
  actions,
  body,
  active = false,
  expanded = false,
  onToggle,
  tooltip,
  testId,
}: {
  label: string;
  /** Worker slug driving the brand mark (the shared ProviderLogo family map). */
  slug?: string;
  title: React.ReactNode;
  /** The row's state word. "active" draws no chip (the edge rule and the Use
   *  control say it); anything else is a dot + word beside the name. */
  state: "active" | AgentState;
  subtitle?: React.ReactNode;
  /** Header-right controls: Connect / Disconnect and the Use radio. */
  actions?: React.ReactNode;
  /** Editor body under the row; absent means the row has nothing to open. */
  body?: React.ReactNode;
  active?: boolean;
  expanded?: boolean;
  onToggle?: () => void;
  /** Native hover tooltip for the row header. */
  tooltip?: string;
  testId?: string;
}) {
  const collapsible = Boolean(body && onToggle);

  function handleRowClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!collapsible) return;
    const target = e.target as HTMLElement | null;
    if (
      target?.closest(
        "input, label, button, a, select, textarea, [data-agent-card-control]",
      )
    ) {
      return;
    }
    onToggle?.();
  }

  return (
    <li
      data-testid={testId}
      data-expanded={expanded ? "true" : "false"}
      className={cn(
        "relative",
        // Said once, at the edge: a 3 px gold rule and a faint wash mark the
        // worker that runs, instead of a gold frame plus glow plus chip.
        active &&
          "bg-primary/[0.035] before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-r before:bg-primary",
      )}
    >
      <div
        role={collapsible ? "button" : undefined}
        tabIndex={collapsible ? 0 : undefined}
        aria-expanded={collapsible ? expanded : undefined}
        onClick={handleRowClick}
        onKeyDown={(e) => {
          if (!collapsible || e.target !== e.currentTarget) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle?.();
          }
        }}
        title={tooltip}
        className={cn(
          "flex items-center gap-3 px-3.5 py-3 outline-none",
          collapsible &&
            "cursor-pointer hover:bg-secondary/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        )}
      >
        <ProviderLogo providerId={slug ?? label} label={label} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <span className="truncate font-display text-[13.5px] font-semibold tracking-tight">
              {title}
            </span>
            {/* The active row says so on its left edge and in the Use control;
                the other two states are a dot and a word beside the name. */}
            {state !== "active" && (
              <StateChip tone={state.tone} title={state.title}>
                {state.label}
              </StateChip>
            )}
          </div>
          {subtitle && (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">{subtitle}</p>
          )}
        </div>
        {actions && (
          <div
            data-agent-card-control
            className="flex shrink-0 items-center gap-2"
            onClick={(e) => e.stopPropagation()}
          >
            {actions}
          </div>
        )}
        {collapsible && (
          <ChevronDown
            aria-hidden="true"
            className={cn(
              "h-4 w-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none",
              expanded && "rotate-180",
            )}
          />
        )}
      </div>
      {body && expanded && (
        <div
          data-agent-card-control
          className="space-y-3 border-t border-border/60 bg-background/30 px-3.5 pb-3.5 pl-[3.75rem] pt-3"
        >
          {body}
        </div>
      )}
    </li>
  );
}

/** A small amber hint line (install / locked) shown inside a row body. */
function CardHint({
  icon: Icon,
  children,
}: {
  icon: LucideIcon;
  children: React.ReactNode;
}) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
      <Icon className="mt-0.5 h-3 w-3 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

/** Connect (primary) action shared by the CLI-login rows. */
function ConnectButton({
  onClick,
  disabled,
}: {
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <Button size="sm" onClick={onClick} disabled={disabled} className="h-7 gap-1.5 rounded-control px-2.5 text-xs">
      <LogIn className="h-3.5 w-3.5" />
      Connect
    </Button>
  );
}

/** Disconnect (quiet) action shared by the CLI-login rows. */
function DisconnectButton({
  onClick,
  disabled,
}: {
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={onClick}
      disabled={disabled}
      className="h-7 gap-1.5 rounded-control px-2 text-xs text-muted-foreground hover:text-foreground"
    >
      <LogOut className="h-3.5 w-3.5" />
      Disconnect
    </Button>
  );
}

/**
 * "Test" button + inline result shared by the CLI rows.
 *
 * POSTs the row's live-test endpoint (the backend re-augments PATH, clears
 * the version caches, and spawns the real binary), then reports found /
 * not-found with the binary path, version and login state. A miss lists the
 * PATH directories that were searched — making "installed in the shell but
 * invisible to the app" (the macOS GUI-PATH trap) diagnosable on the row.
 */
function CliTestControl({
  endpoint,
  onChanged,
}: {
  endpoint: string;
  onChanged?: () => void | Promise<void>;
}) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AgentCliTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      setResult(await testAgentCli(endpoint));
      // The test may have just discovered a freshly-installed CLI (PATH was
      // re-augmented) — refresh the section so the row state follows.
      await onChanged?.();
    } catch (e) {
      setResult(null);
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-2 border-t border-border/60 pt-2.5" data-agent-card-control>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          size="sm"
          variant="ghost"
          onClick={run}
          disabled={running}
          className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
        >
          <FlaskConical className={cn("h-3.5 w-3.5", running && "animate-pulse")} />
          {running ? "Testing…" : "Test"}
        </Button>
        {result && !error && (
          <span
            className={cn(
              "inline-flex items-center gap-1.5 text-xs",
              result.ok ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400",
            )}
          >
            <span
              aria-hidden="true"
              className={cn("h-[7px] w-[7px] rounded-full", result.ok ? "bg-emerald-500" : "bg-amber-500")}
            />
            {result.message}
          </span>
        )}
        {error && <span className="text-xs text-destructive">Test failed: {error}</span>}
      </div>
      {result && !error && result.installed && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {result.binary_path && (
            <>
              Binary: <code className="break-all">{result.binary_path}</code>
              <br />
            </>
          )}
          {result.version && <>Version: {result.version} · </>}
          Login: {result.connected ? `connected (${result.auth_mode})` : "not connected"}
          {result.account ? ` · ${result.account}` : ""}
        </p>
      )}
      {result && !error && !result.installed && result.searched_path.length > 0 && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">
            Searched {result.searched_path.length} PATH directories
          </summary>
          <ul className="mt-1 max-h-32 overflow-y-auto">
            {result.searched_path.map((p) => (
              <li key={p} className="break-all">
                {p}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/**
 * The honest state vocabulary of a worker row. Each word says exactly what
 * the app KNOWS: a CLI that reports its login is "connected" (green); a key
 * that is merely stored is "key saved" (grey, not green — nothing has proven
 * it answers); "no key" / "not installed" name the missing piece. The old
 * "ready" for a stored key claimed more than anyone had checked.
 */
interface AgentState {
  label: string;
  tone: "ready" | "neutral" | "missing";
  title?: string;
}
const STATE_CONNECTED: AgentState = { label: "connected", tone: "ready" };
const STATE_NOT_CONNECTED: AgentState = { label: "not connected", tone: "neutral" };
const STATE_NOT_INSTALLED: AgentState = { label: "not installed", tone: "missing" };
const STATE_KEY_SAVED: AgentState = {
  label: "key saved",
  tone: "neutral",
  title: "A key is stored. Whether it works is proven only by a run.",
};
const STATE_SHARED_KEY: AgentState = {
  label: "shared key",
  tone: "neutral",
  title: "No dedicated key — the shared Brain key is used as a fallback.",
};
const STATE_NO_KEY: AgentState = { label: "no key", tone: "neutral" };
const STATE_LOCAL: AgentState = { label: "no key needed", tone: "neutral" };

/** The expand/collapse plumbing every row card receives from the section. */
interface RowDisclosure {
  expanded: boolean;
  onToggle: () => void;
}

function CodexConnectionCard({
  status,
  row,
  onChanged,
  expanded,
  onToggle,
}: {
  status: CodexStatus | null;
  row: SubagentMappingRow | undefined;
  onChanged: () => void | Promise<void>;
} & RowDisclosure) {
  const pushToast = useEventStore((s) => s.pushToast);
  const [pending, setPending] = useState(false);
  const { activating, activate } = useSubagentActivate(row, onChanged);
  const connected = Boolean(status?.connected);
  const installed = status?.installed ?? false;
  const isActive = Boolean(row?.is_active_brain);
  const email =
    status?.user_email ??
    status?.account_label ??
    status?.accountLabel ??
    null;
  const detail = connected
    ? email
      ? `Connected as ${email}`
      : status?.message || "Connected via ChatGPT"
    : status?.message || "ChatGPT login not connected";

  async function connect() {
    setPending(true);
    try {
      await startCodexLogin();
      pushToast("info", "Codex login started — finish it in the browser window");
      await onChanged();
      // The login finishes asynchronously in the spawned console/browser, so
      // poll until the CLI reports connected; then the row flips to selectable
      // on its own — no manual reload needed.
      void pollStatusUntilConnected("/api/codex/status", onChanged).then((ok) => {
        if (ok) pushToast("success", `Codex connected — now selectable as a ${agentBrandNow()}`);
      });
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function disconnect() {
    setPending(true);
    try {
      await codexLogout();
      pushToast("info", "Codex login disconnected");
      await onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  const selectable = connected || (installed && Boolean(row?.api_key_set));
  return (
    <AgentRow
      testId="agent-row-openai-codex"
      label="OpenAI Codex"
      slug={row?.jarvis}
      title="OpenAI Codex"
      active={isActive}
      expanded={expanded}
      onToggle={onToggle}
      state={
        isActive
          ? "active"
          : connected
            ? STATE_CONNECTED
            : !installed
              ? STATE_NOT_INSTALLED
              : row?.api_key_set
                ? STATE_KEY_SAVED
                : STATE_NOT_CONNECTED
      }
      subtitle={detail}
      actions={
        <>
          {connected ? (
            <DisconnectButton onClick={disconnect} disabled={pending} />
          ) : (
            <ConnectButton onClick={connect} disabled={pending || !installed} />
          )}
          {/* When connected, the same Use control as every other row — so
              this subscription login is selectable right here. */}
          {selectable && row && (
            <SubagentActiveControl
              row={row}
              activating={activating}
              onActivate={activate}
            />
          )}
        </>
      }
      body={
        <>
          {/* The OpenAI key is the ALTERNATIVE way in (per-token billing), not
              a requirement of the ChatGPT login. Showing it unconditionally on
              this subscription row read as "the subscription needs an OpenAI
              API key" (user report 2026-07-17), so render it only while the
              login is absent — or when a key is actually stored, so it stays
              replaceable/deletable. */}
          {row?.secret_key && (!connected || row.dedicated_key_set) && (
            <ApiKeyForm
              secretKey={row.secret_key}
              dashboardUrl={row.dashboard_url}
              configured={row.dedicated_key_set}
              credentialHelp={row.credential_help}
              onChanged={onChanged}
            />
          )}
          {!installed && (
            <CardHint icon={Terminal}>Install Codex before activating it.</CardHint>
          )}
          <CliTestControl endpoint="/api/codex/test" onChanged={onChanged} />
        </>
      }
    />
  );
}

function AntigravityConnectionCard({
  status,
  row,
  onChanged,
  expanded,
  onToggle,
}: {
  status: AntigravityStatus | null;
  row: SubagentMappingRow | undefined;
  onChanged: () => void | Promise<void>;
} & RowDisclosure) {
  const pushToast = useEventStore((s) => s.pushToast);
  const brand = useAgentBrand();
  const [pending, setPending] = useState(false);
  const { activating, activate } = useSubagentActivate(row, onChanged);
  const connected = Boolean(status?.connected && status.mode === "oauth-personal");
  const installed = status?.installed ?? false;
  const apiKeyReady = Boolean(installed && row?.api_key_set);
  // `/api/jarvis-agent/status.key_set` is the canonical readiness decision.
  // Keep the independent live status gate as defense against two parallel
  // responses straddling an install/uninstall. A Gemini key alone belongs to
  // the separate Google Gemini row and must never make this CLI row ready.
  const usable = Boolean(installed && row?.key_set);
  const isActive = Boolean(usable && row?.is_active_brain);
  const detail = connected
    ? status?.user_email
      ? `Connected as ${status.user_email}`
      : status?.message || "Connected"
    : apiKeyReady
      ? `Gemini ${brand} API key ready (billed per token)`
    : status?.message || "Google login not connected";

  async function connect() {
    setPending(true);
    try {
      await loginAntigravity();
      pushToast("info", "Google login started — finish it in the browser window");
      await onChanged();
      // Same as Codex: the Google sign-in completes asynchronously, so poll
      // until agy reports connected and the row unlocks on its own.
      void pollStatusUntilConnected("/api/antigravity/status", onChanged).then((ok) => {
        if (ok) {
          pushToast("success", `Antigravity connected — now selectable as a ${agentBrandNow()}`);
        }
      });
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function disconnect() {
    setPending(true);
    try {
      await logoutAntigravity();
      pushToast("info", "Google login disconnected");
      await onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <AgentRow
      testId="agent-row-antigravity"
      label="Antigravity"
      slug={row?.jarvis}
      title="Antigravity"
      active={isActive}
      expanded={expanded}
      onToggle={onToggle}
      state={
        isActive
          ? "active"
          : connected
            ? STATE_CONNECTED
            : !installed
              ? STATE_NOT_INSTALLED
              : apiKeyReady
                ? STATE_KEY_SAVED
                : STATE_NOT_CONNECTED
      }
      subtitle={detail}
      actions={
        <>
          {connected ? (
            <DisconnectButton onClick={disconnect} disabled={pending} />
          ) : (
            <ConnectButton onClick={connect} disabled={pending || !installed} />
          )}
          {usable && row && (
            <SubagentActiveControl
              row={row}
              activating={activating}
              onActivate={activate}
            />
          )}
        </>
      }
      body={
        <>
          {!installed && (
            <CardHint icon={Terminal}>
              Install Antigravity or the Gemini CLI before connecting.
            </CardHint>
          )}
          <CliTestControl endpoint="/api/antigravity/test" onChanged={onChanged} />
        </>
      }
    />
  );
}

function GrokBuildConnectionCard({
  status,
  row,
  onChanged,
  expanded,
  onToggle,
}: {
  status: GrokBuildStatus | null;
  row: SubagentMappingRow | undefined;
  onChanged: () => void | Promise<void>;
} & RowDisclosure) {
  const pushToast = useEventStore((s) => s.pushToast);
  const [pending, setPending] = useState(false);
  const { activating, activate } = useSubagentActivate(row, onChanged);
  const connected = Boolean(status?.connected && status.mode === "subscription");
  const installed = status?.installed ?? false;
  const usable = Boolean(installed && row?.key_set);
  const isActive = Boolean(usable && row?.is_active_brain);
  const detail = connected
    ? status?.user_email
      ? `Connected as ${status.user_email}`
      : status?.message || "Connected via SuperGrok / X Premium+"
    : status?.message || "Grok Build login not connected";

  async function connect() {
    setPending(true);
    try {
      await loginGrokBuild();
      pushToast("info", "Grok Build login started — finish it in the browser window");
      await onChanged();
      void pollStatusUntilConnected("/api/grok-build/status", onChanged, {
        isReady: (data) =>
          Boolean(data?.connected && data?.mode === "subscription"),
      }).then((ok) => {
        if (ok) {
          pushToast("success", `Grok Build connected — now selectable as a ${agentBrandNow()}`);
        }
      });
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function disconnect() {
    setPending(true);
    try {
      await logoutGrokBuild();
      pushToast("info", "Grok Build login disconnected");
      await onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <AgentRow
      testId="agent-row-grok-build"
      label="Grok Build"
      slug={row?.jarvis}
      title="Grok Build"
      active={isActive}
      expanded={expanded}
      onToggle={onToggle}
      state={
        isActive
          ? "active"
          : connected
            ? STATE_CONNECTED
            : !installed
              ? STATE_NOT_INSTALLED
              : STATE_NOT_CONNECTED
      }
      subtitle={detail}
      actions={
        <>
          {connected ? (
            <DisconnectButton onClick={disconnect} disabled={pending} />
          ) : (
            <ConnectButton onClick={connect} disabled={pending || !installed} />
          )}
          {usable && row && (
            <SubagentActiveControl
              row={row}
              activating={activating}
              onActivate={activate}
            />
          )}
        </>
      }
      body={
        <>
          {!installed && (
            <CardHint icon={Terminal}>
              {status?.message || "Install Grok Build before connecting."}
            </CardHint>
          )}
          <CliTestControl endpoint="/api/grok-build/test" onChanged={onChanged} />
        </>
      }
    />
  );
}

function ClaudeConnectionCard({
  status,
  row,
  onChanged,
  expanded,
  onToggle,
}: {
  status: ClaudeStatus | null;
  row: SubagentMappingRow | undefined;
  onChanged: () => void | Promise<void>;
} & RowDisclosure) {
  const pushToast = useEventStore((s) => s.pushToast);
  const [pending, setPending] = useState(false);
  const { activating, activate } = useSubagentActivate(row, onChanged);
  const connected = Boolean(status?.connected && status.mode === "subscription");
  const installed = status?.installed ?? false;
  // Claude has ONE subagent slug (claude-api) reached by EITHER the Claude Max
  // OAuth login OR an Anthropic API key — split into two sibling rows (mirror
  // of Codex/OpenAI + Antigravity/Gemini). This subscription row lights up only
  // when claude-api is the active worker AND it is running over the OAuth login
  // (mode != "api_key"); the API key row owns the api_key mode. So exactly one
  // of the two ever shows "active".
  const isActive = Boolean(row?.is_active_brain) && status?.mode === "subscription";
  // A subscription login shows the signed-in account + tier ("Connected as
  // ruben@… · Claude Max"); not connected shows how to sign in. The API-key
  // alternative lives on its own row in the API-keys group.
  const detail = connected
    ? status?.user_email
      ? `Connected as ${status.user_email}${
          status.account_label ? ` · ${status.account_label}` : ""
        }`
      : status?.message || "Connected"
    : status?.message || "Claude login not connected";

  async function connect() {
    setPending(true);
    try {
      await loginClaude();
      pushToast("info", "Claude login started — finish it in the terminal window");
      await onChanged();
      // The sign-in finishes asynchronously in the spawned console, so poll
      // until the CLI reports connected; the row then unlocks on its own.
      void pollStatusUntilConnected("/api/claude/status", onChanged).then((ok) => {
        if (ok) pushToast("success", `Claude connected — now selectable as a ${agentBrandNow()}`);
      });
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function disconnect() {
    setPending(true);
    try {
      await logoutClaude();
      pushToast("info", "Claude subscription disconnected");
      await onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <AgentRow
      testId="agent-row-claude-subscription"
      label="Anthropic Claude"
      slug={row?.jarvis}
      title="Anthropic Claude"
      active={isActive}
      expanded={expanded}
      onToggle={onToggle}
      state={
        isActive
          ? "active"
          : connected
            ? STATE_CONNECTED
            : !installed
              ? STATE_NOT_INSTALLED
              : STATE_NOT_CONNECTED
      }
      subtitle={detail}
      actions={
        <>
          {connected ? (
            <DisconnectButton onClick={disconnect} disabled={pending} />
          ) : (
            <ConnectButton onClick={connect} disabled={pending || !installed} />
          )}
          {connected && row && (
            <SubagentActiveControl
              row={row}
              active={isActive}
              activating={activating}
              onActivate={activate}
            />
          )}
        </>
      }
      body={
        <>
          {!installed && (
            <CardHint icon={Terminal}>Install the Claude CLI before connecting.</CardHint>
          )}
          <CliTestControl endpoint="/api/claude/test" onChanged={onChanged} />
        </>
      }
    />
  );
}

/**
 * The Anthropic Claude (API) row — the per-token sibling of the subscription
 * row above, built to behave EXACTLY like the OpenAI / Google Gemini subagent
 * rows (`SubagentProviderCard`). The dedicated key is entered here; the row
 * reflects whether it is set (`row.api_key_set`) and lets the user pick
 * Claude-on-the-key as the heavy-task worker — locked with a pointer until the
 * key exists, exactly like OpenAI/Gemini.
 *
 * Claude has ONE subagent slug (claude-api) reached by either auth, so this row
 * and the subscription row both drive `useSubagentActivate(row)`. To keep only
 * one of the two lit, "active" here means claude-api is the active worker AND it
 * is running over the API key (status mode === "api_key"); the subscription row
 * owns every other mode.
 */
function ClaudeApiCard({
  status,
  row,
  onChanged,
  expanded,
  onToggle,
}: {
  status: ClaudeStatus | null;
  row: SubagentMappingRow | undefined;
  onChanged: () => void | Promise<void>;
} & RowDisclosure) {
  const { activating, activate } = useSubagentActivate(row, onChanged);
  const brand = useAgentBrand();
  // OAuth unlocks the subscription sibling only. This API row reflects an
  // actual API key, so a Claude Max login cannot paint it falsely ready.
  const keySet = Boolean(row?.api_key_set);
  const isActive = Boolean(row?.is_active_brain) && status?.mode === "api_key";

  return (
    <AgentRow
      testId="agent-row-claude-api"
      label="Anthropic Claude"
      slug={row?.jarvis}
      title="Anthropic Claude"
      active={isActive}
      expanded={expanded}
      onToggle={onToggle}
      tooltip={
        isActive
          ? `This ${brand} provider is active`
          : keySet
            ? `Activate this ${brand} provider`
            : `Save a dedicated Claude ${brand} key first`
      }
      state={isActive ? "active" : keySet ? STATE_KEY_SAVED : STATE_NO_KEY}
      subtitle="API key · billed per token"
      actions={
        row && (
          <SubagentActiveControl
            row={row}
            active={isActive}
            activating={activating}
            onActivate={activate}
          />
        )
      }
      body={
        <>
          {row?.secret_key && (
            <ApiKeyForm
              secretKey={row.secret_key}
              dashboardUrl={row.dashboard_url}
              configured={row.dedicated_key_set}
              credentialHelp={row.credential_help}
              onChanged={onChanged}
            />
          )}
          {!keySet && (
            <CardHint icon={Lock}>
              Locked &mdash; save a dedicated <strong>Claude API key</strong> above.
            </CardHint>
          )}
        </>
      }
    />
  );
}

/**
 * Shared "activate this subagent provider" action (POST /api/subagent/switch,
 * 3-layer persist). Used by the generic provider rows AND the CLI-login rows,
 * so every selectable row behaves identically.
 */
function useSubagentActivate(
  row: SubagentMappingRow | undefined,
  onSwitched: () => void | Promise<void>,
) {
  const [activating, setActivating] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);

  const activate = useCallback(async () => {
    if (!row || row.is_active_brain || activating) return;
    const label = PROVIDER_LABELS[row.jarvis] ?? row.jarvis;
    if (!row.key_set) {
      pushToast(
        "warning",
        row.jarvis === "openai-codex"
          ? `${label}: connect the ChatGPT login first.`
          : row.jarvis === "antigravity"
            ? `${label}: connect the Google login first.`
            : `${label}: save a key on its ${agentBrandNow()} card first.`,
      );
      return;
    }
    setActivating(true);
    window.dispatchEvent(
      new CustomEvent("jarvis:provider-selection-pending", {
        detail: { section: "subagents", provider: row.jarvis },
      }),
    );
    try {
      const result = await switchSubagentProvider(row.jarvis);
      const note = result.restart_required ? " (active from next restart)" : "";
      pushToast("success", `${agentBrandNow()} → ${label}${note}`);
      window.dispatchEvent(new CustomEvent("jarvis:agent-switched"));
      await onSwitched();
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("jarvis:provider-switch-failed", {
          detail: { section: "subagents", provider: row.jarvis },
        }),
      );
      pushToast("error", (e as Error).message);
    } finally {
      setActivating(false);
    }
  }, [row, activating, pushToast, onSwitched]);

  return { activating, activate };
}

/**
 * One sub-agent-capable provider as a row. The Use control switches the
 * heavy-task subagent provider via `POST /api/subagent/switch` (3-layer
 * persist); the key itself lives in the row body. A provider with no key
 * cannot be activated (warning toast instead of a silent no-op).
 */
function SubagentProviderCard({
  row,
  onSwitched,
  expanded,
  onToggle,
}: {
  row: SubagentMappingRow;
  onSwitched: () => void | Promise<void>;
} & RowDisclosure) {
  const label = row.label ?? PROVIDER_LABELS[row.jarvis] ?? row.jarvis;
  const brand = useAgentBrand();
  const { activating, activate } = useSubagentActivate(row, onSwitched);

  return (
    <AgentRow
      testId={`agent-row-${row.jarvis}`}
      label={label}
      slug={row.jarvis}
      title={label}
      active={row.is_active_brain}
      expanded={expanded}
      onToggle={onToggle}
      tooltip={
        row.is_active_brain
          ? `This ${brand} provider is active`
          : row.key_set
            ? `Activate this ${brand} provider`
            : "Set an API key first"
      }
      // A keyless row has no key to wait for: it is ready as soon as it is
      // listed, and every credential line below has to say so.
      state={
        row.is_active_brain
          ? "active"
          : row.keyless
            ? STATE_LOCAL
            : row.dedicated_key_set
              ? STATE_KEY_SAVED
              : row.shared_key_set
                ? STATE_SHARED_KEY
                : STATE_NO_KEY
      }
      subtitle={
        row.keyless
          ? "Runs on this machine — no key needed"
          : row.dedicated_key_set
            ? `Dedicated ${brand} key configured`
            : row.shared_key_set
              ? "Using the shared Brain key as a compatibility fallback"
              : `Set a dedicated key for this ${brand}`
      }
      actions={
        <SubagentActiveControl
          row={row}
          activating={activating}
          onActivate={activate}
        />
      }
      body={
        row.secret_key || row.credential_help || !row.key_set ? (
          <>
            {row.secret_key && (
              <ApiKeyForm
                secretKey={row.secret_key}
                dashboardUrl={row.dashboard_url}
                configured={row.dedicated_key_set}
                credentialHelp={row.credential_help}
                onChanged={onSwitched}
              />
            )}
            {row.keyless && row.credential_help && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {row.credential_help}
              </p>
            )}
            {!row.key_set && (
              <CardHint icon={Lock}>
                Locked &mdash; save a dedicated <strong>{label}</strong> key on this row.
              </CardHint>
            )}
          </>
        ) : undefined
      }
    />
  );
}

/**
 * The Use control, mirroring `ActiveControl` on the provider rows: visually a
 * state word ("Active") or a quiet action ("Set active"), semantically still a
 * radio in the `active-subagent` group so browsers, screen readers and the
 * tests keep native single-select. Source of truth stays `row.is_active_brain`
 * from `/api/jarvis-agent/status`; the radio reflects the server state and is
 * not held locally. It is NOT disabled when the key is missing — `activate()`
 * routes a warning toast so every click gets a reaction.
 */
function SubagentActiveControl({
  row,
  activating,
  onActivate,
  active,
}: {
  row: SubagentMappingRow;
  activating: boolean;
  onActivate: () => void;
  /**
   * Explicit active state, overriding `row.is_active_brain`. The two Claude
   * rows share ONE slug (claude-api), so the raw row flag would light BOTH
   * radios at once. Each Claude row passes its mode-split flag here so only the
   * row matching the live auth (subscription vs API key) shows "Active". Other
   * rows omit it and fall back to the row flag.
   */
  active?: boolean;
}) {
  const brand = useAgentBrand();
  const isActive = active ?? row.is_active_brain;
  const labelTitle = isActive
    ? `This ${brand} provider is active`
    : row.key_set
      ? `Activate this ${brand} provider`
      : "Set an API key first";

  return (
    <label
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      className={cn(
        "inline-flex h-7 shrink-0 cursor-pointer select-none items-center gap-2 whitespace-nowrap rounded-control px-2.5 text-xs transition-colors focus-within:ring-2 focus-within:ring-ring",
        isActive
          ? "font-medium text-foreground"
          : row.key_set
            ? "border border-border bg-background/60 text-muted-foreground hover:border-primary/50 hover:text-foreground"
            : "border border-dashed border-border/80 text-muted-foreground/80 hover:text-foreground",
      )}
      title={labelTitle}
    >
      <input
        type="radio"
        name="active-subagent"
        checked={isActive}
        onChange={() => onActivate()}
        disabled={activating}
        className="sr-only"
      />
      {isActive && (
        <span
          aria-hidden="true"
          className={cn(
            "h-[7px] w-[7px] rounded-full bg-primary",
            activating && "animate-pulse motion-reduce:animate-none",
          )}
        />
      )}
      {activating ? "Activating…" : isActive ? "Active" : "Set active"}
    </label>
  );
}
