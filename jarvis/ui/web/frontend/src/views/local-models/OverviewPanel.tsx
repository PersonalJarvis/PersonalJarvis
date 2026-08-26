/**
 * Overview — the Simple-mode front page of the Local models section, written
 * so it reads like a plan rather than a dashboard.
 *
 * Top to bottom: one status sentence ("Ollama 0.32 running · 16 GB graphics
 * memory · Ollama is not your active brain (OpenRouter answers)"), the
 * actions — "Set up everything" first (the one click that starts the server,
 * fills every role with the best installed download and fetches only what
 * is missing; `useLocalSetup`), then "Browse models" — with the flow's
 * progress line underneath, the headline tiles (downloads on disk, graphics
 * memory, loaded now), the roles as a checklist, and every installed model
 * as one line (`InstalledPanel`), so "what do I have?" never needs the
 * Advanced switch.
 *
 * Props take `providerId` — the id of the card that declares
 * `supports_model_pull` — never a provider name.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Cpu, HardDrive, Layers, Loader2, Search, Sparkles } from "lucide-react";

import {
  SoftButton,
  StatTile,
  StatusDot,
} from "@/components/extensions/primitives";
import { useOverview, type LocalModelRole } from "@/hooks/useLocalModels";
import { switchBrainProvider, useProviders } from "@/hooks/useProviders";
import { fill, useT } from "@/i18n";
import {
  LOCAL_MODELS_MARK_FIRST_DATA,
  markLocalModels,
} from "@/lib/localModelsPerf";
import { cn } from "@/lib/utils";

import { InstalledPanel } from "./InstalledPanel";
import { formatExpiry } from "./localModelsFormat";
import type { SetupStep, SetupSummary } from "./localSetup";
import { RolesPanel, type RolesPanelProps } from "./RolesPanel";
import { useLocalSetup } from "./useLocalSetup";

export interface OverviewPanelProps extends RolesPanelProps {
  /** Opens the catalogue ("Browse models"); the button hides without it. */
  onBrowse?: () => void;
  /** Opens the full installed ledger (Advanced → Models). */
  onManage?: () => void;
  /**
   * Detail level. The switch beside the rail must change what is on screen
   * HERE too, or it reads as a dead control: Simple keeps the roles a
   * checklist, Advanced opens every row to its picker, capabilities and Tune.
   */
  advanced?: boolean;
}

/** Bytes to a short gigabyte figure ("12.4 GB"); "0 GB" below a megabyte. */
export function formatGigabytes(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  if (gb >= 10) return `${gb.toFixed(0)} GB`;
  if (gb >= 0.1) return `${gb.toFixed(1)} GB`;
  return bytes > 0 ? `${(bytes / 1024 ** 2).toFixed(0)} MB` : "0 GB";
}

export function OverviewPanel({
  providerId,
  onTune,
  onOpenApiKeys,
  onBrowse,
  onManage,
  advanced = false,
}: OverviewPanelProps) {
  const t = useT();
  const overview = useOverview(providerId);
  const { providers, refetch: refetchProviders } = useProviders();
  const setup = useLocalSetup(providerId);

  // One round-trip (or the painted snapshot) feeds every tile.
  const s = overview.data?.server;
  const shortlist = { data: overview.data?.recommended };
  const inventory = { data: overview.data?.inventory };
  const server = { isLoading: overview.isLoading, isError: overview.isError };
  const checking = overview.isFetching && overview.data?.source === "cache";

  const marked = useRef(false);
  useEffect(() => {
    if (marked.current || !overview.data) return;
    marked.current = true;
    markLocalModels(LOCAL_MODELS_MARK_FIRST_DATA);
  }, [overview.data]);
  const remote = s?.host_kind === "remote";
  const serverLabel =
    providers.find((p) => p.id === providerId)?.label ||
    t("local_models.overview.status_generic_server");

  // --- status sentence --------------------------------------------------
  let serverClause = t("local_models.overview.status_checking");
  let serverTone: "ok" | "warn" | "error" | "busy" = "busy";
  if (s) {
    if (s.running) {
      serverClause = s.version
        ? fill(t("local_models.overview.status_running"), {
            server: serverLabel,
            version: s.version,
          })
        : fill(t("local_models.overview.status_running_noversion"), {
            server: serverLabel,
          });
      if (remote)
        serverClause = fill(t("local_models.overview.status_remote"), {
          server: serverLabel,
          url: s.base_url,
        });
      serverTone = "ok";
    } else if (remote) {
      serverClause = fill(t("local_models.overview.status_unreachable"), {
        server: serverLabel,
      });
      serverTone = "warn";
    } else if (!s.installed) {
      serverClause = fill(t("local_models.overview.status_not_installed"), {
        server: serverLabel,
      });
      serverTone = "warn";
    } else {
      serverClause = fill(t("local_models.overview.status_stopped"), {
        server: serverLabel,
      });
      serverTone = "warn";
    }
  } else if (server.isError) {
    serverClause = fill(t("local_models.overview.status_unreachable"), {
      server: serverLabel,
    });
    serverTone = "error";
  }

  const acceleratorGb = shortlist.data?.accelerator_gb ?? 0;
  const acceleratorSource = shortlist.data?.accelerator_source ?? "";
  const gpuClause =
    acceleratorGb > 0
      ? fill(t("local_models.overview.status_gpu"), {
          gb: acceleratorGb.toFixed(1),
        })
      : shortlist.data
        ? t("local_models.overview.status_gpu_unknown")
        : "";

  // The third clause only renders once the provider list is here — no gate.
  const activeBrain =
    providers.find((p) => p.tier === "brain" && p.active) ?? null;
  const otherBrainActive =
    activeBrain !== null && activeBrain.id !== providerId;
  const brainClause = activeBrain
    ? activeBrain.id === providerId
      ? fill(t("local_models.overview.status_brain_active"), {
          server: serverLabel,
        })
      : fill(t("local_models.overview.status_brain_other"), {
          server: serverLabel,
          brain: activeBrain.label,
        })
    : "";

  // --- tiles ------------------------------------------------------------
  const gpuValue =
    acceleratorGb > 0
      ? `${acceleratorGb.toFixed(1)} GB`
      : t("local_models.overview.gpu_unknown");
  const loadedVram = s?.loaded_vram_bytes ?? 0;
  const gpuHint =
    acceleratorGb > 0
      ? loadedVram > 0
        ? fill(t("local_models.overview.gpu_in_use"), {
            gb: formatGigabytes(loadedVram),
          })
        : t("local_models.overview.gpu_in_use_none")
      : acceleratorSource
        ? fill(t("local_models.overview.gpu_source"), {
            source: acceleratorSource,
          })
        : t("local_models.overview.gpu_unknown_hint");

  const modelCount = inventory.data?.models.length ?? null;
  const diskHint = [
    modelCount === null
      ? ""
      : modelCount === 1
        ? t("local_models.overview.disk_model_one")
        : fill(t("local_models.overview.disk_models"), { count: modelCount }),
    s?.models_dir ?? "",
  ]
    .filter(Boolean)
    .join(" · ");

  const loaded = s?.running_models ?? [];
  const firstExpiry = loaded.length > 0 ? formatExpiry(loaded[0].expires_at) : "";
  const loadedHint =
    loaded.length > 0
      ? [
          loaded.map((m) => m.name).join(", "),
          firstExpiry
            ? fill(t("local_models.overview.loaded_expires"), {
                when: firstExpiry,
              })
            : "",
        ]
          .filter(Boolean)
          .join(" · ")
      : t("local_models.overview.loaded_none");

  const dotClass = {
    ok: "bg-emerald-500",
    warn: "bg-amber-500",
    error: "bg-destructive",
    busy: "bg-primary animate-pulse",
  }[serverTone];

  // --- actions ----------------------------------------------------------
  // A server that is not installed gets a button that NAMES the install:
  // the click is the consent the dangerous-flagged install route asks for.
  const needsInstall = Boolean(s && !s.installed && !remote);
  const setupLabel = needsInstall
    ? fill(t("local_models.overview.action_setup_install"), {
        server: serverLabel,
      })
    : t("local_models.overview.action_setup");
  const setupHint = needsInstall
    ? t("local_models.overview.action_setup_install_hint")
    : t("local_models.overview.action_setup_hint");

  const roleRows = overview.data?.roles.roles ?? [];
  const roleLabel = useCallback(
    (role: LocalModelRole) => {
      const row = roleRows.find((r) => r.id === role);
      return t(row?.label_key ?? `local_models.role_${role}`);
    },
    [roleRows, t],
  );

  return (
    <div className="space-y-4" data-testid="local-models-overview-panel">
      <p
        className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-foreground"
        data-testid="overview-status"
      >
        <span className={cn("h-2 w-2 shrink-0 rounded-full", dotClass)} />
        <span>{serverClause}</span>
        {gpuClause && (
          <>
            <span className="text-muted-foreground">·</span>
            <span className="text-muted-foreground">{gpuClause}</span>
          </>
        )}
        {brainClause && (
          <>
            <span className="text-muted-foreground">·</span>
            <span
              className={cn(
                activeBrain?.id === providerId
                  ? "text-muted-foreground"
                  : "text-amber-700 dark:text-amber-400",
              )}
              data-testid="overview-brain-clause"
            >
              {brainClause}
            </span>
          </>
        )}
        {checking && (
          <span className="ml-1" data-testid="overview-checking">
            <StatusDot
              tone="busy"
              pulse
              label={t("local_models.overview.checking")}
            />
          </span>
        )}
      </p>

      <div
        className="flex flex-wrap gap-2"
        role="group"
        aria-label={t("local_models.overview.actions_label")}
        data-testid="overview-actions"
      >
        <ActionButton
          primary
          icon={
            setup.busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )
          }
          label={setupLabel}
          hint={setupHint}
          onClick={setup.run}
          disabled={setup.busy}
          testId="overview-setup"
        />
        {onBrowse && (
          <ActionButton
            icon={<Search className="h-4 w-4" />}
            label={t("local_models.overview.action_browse")}
            hint={t("local_models.overview.action_browse_hint")}
            onClick={onBrowse}
          />
        )}
      </div>

      <SetupProgress
        step={setup.step}
        serverLabel={serverLabel}
        roleLabel={roleLabel}
        otherBrain={otherBrainActive ? (activeBrain?.label ?? "") : ""}
        onSwitchBrain={async () => {
          await switchBrainProvider(providerId);
          await refetchProviders();
        }}
        t={t}
      />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatTile
          icon={<HardDrive className="h-3.5 w-3.5" />}
          label={t("local_models.overview.disk")}
          value={formatGigabytes(s?.disk_bytes ?? 0)}
          hint={diskHint}
          tone="primary"
          loading={server.isLoading}
        />
        <StatTile
          icon={<Cpu className="h-3.5 w-3.5" />}
          label={t("local_models.overview.gpu")}
          value={gpuValue}
          hint={gpuHint}
          tone="primary"
          loading={server.isLoading}
        />
        <StatTile
          icon={<Layers className="h-3.5 w-3.5" />}
          label={t("local_models.overview.loaded")}
          value={String(loaded.length)}
          hint={loadedHint}
          tone="primary"
          loading={server.isLoading}
        />
      </div>

      <RolesPanel
        providerId={providerId}
        onTune={onTune}
        onOpenApiKeys={onOpenApiKeys}
        variant={advanced ? "ledger" : "checklist"}
        onSetup={setup.run}
        setupBusy={setup.busy}
      />

      <InstalledPanel
        models={inventory.data?.models ?? []}
        roles={roleRows}
        diskBytes={inventory.data?.disk_bytes ?? s?.disk_bytes ?? 0}
        loading={overview.isLoading}
        error={inventory.data?.error ?? null}
        onManage={onManage}
      />
    </div>
  );
}

/**
 * The set-up flow's one line under the actions: what is happening now, or
 * — once it ended — what was done, in the user's words (role → model), plus
 * the one decision the flow leaves to the user: making this server the
 * active brain when another one answers right now.
 */
function SetupProgress({
  step,
  serverLabel,
  roleLabel,
  otherBrain,
  onSwitchBrain,
  t,
}: {
  step: SetupStep | null;
  serverLabel: string;
  roleLabel: (role: LocalModelRole) => string;
  /** The label of the brain that answers instead of this server; "" = none. */
  otherBrain: string;
  onSwitchBrain: () => Promise<void>;
  t: (key: string) => string;
}) {
  const [brain, setBrain] = useState<
    { state: "idle" } | { state: "switching" } | { state: "done" } | { state: "error"; message: string }
  >({ state: "idle" });
  // A new run resets the brain line.
  useEffect(() => {
    if (step && step.phase !== "done") setBrain({ state: "idle" });
  }, [step]);
  if (!step) return null;
  const k = (key: string) => t(`local_models.overview.${key}`);

  let tone: "busy" | "ok" | "error" = "busy";
  let text = "";
  let summary: SetupSummary | undefined;
  switch (step.phase) {
    case "planning":
      text = k("setup_planning");
      break;
    case "server": {
      const pct =
        typeof step.percent === "number" && step.action === "installing"
          ? ` ${Math.round(step.percent)} %`
          : "";
      text =
        `${fill(
          k(
            step.action === "starting"
              ? "setup_server_starting"
              : "setup_server_installing",
          ),
          { server: serverLabel },
        )}${pct} ${step.detail ?? ""}`.trim();
      break;
    }
    case "pulling": {
      const pct =
        typeof step.percent === "number" ? `${Math.round(step.percent)} %` : "";
      text = `${fill(k("setup_pulling"), { model: step.model })} ${pct} ${
        step.message ?? ""
      }`.trim();
      break;
    }
    case "assigning":
      text = fill(k("setup_assigning"), {
        role: roleLabel(step.role),
        model: step.model,
      });
      break;
    case "tuning":
      text = fill(k("setup_tuning"), { model: step.model });
      break;
    case "error":
      tone = "error";
      text = fill(k("setup_error"), { message: step.message });
      summary = step.summary;
      break;
    case "done":
      tone = "ok";
      summary = step.summary;
      text =
        summary.assigned.length === 0 &&
        summary.pulled.length === 0 &&
        !summary.serverStarted
          ? k("setup_done_nothing")
          : k("setup_done");
      break;
  }

  const lines: string[] = [];
  if (summary) {
    if (summary.assigned.length > 0)
      lines.push(
        summary.assigned
          .map((a) =>
            fill(k("setup_assigned"), {
              role: roleLabel(a.role),
              model: a.model,
            }),
          )
          .join(" · "),
      );
    if (summary.kept.length === 1) lines.push(k("setup_kept_one"));
    else if (summary.kept.length > 1)
      lines.push(fill(k("setup_kept"), { count: summary.kept.length }));
    for (const skip of summary.skipped)
      lines.push(
        fill(k("setup_skipped"), {
          role: roleLabel(skip.role),
          reason: skip.note || k("setup_skipped_no_pick"),
        }),
      );
    for (const [model, readback] of Object.entries(summary.readbacks))
      lines.push(`${model}: ${readback}`);
  }

  return (
    <div className="space-y-1 text-xs" data-testid="setup-progress">
      <StatusDot tone={tone} pulse={tone === "busy"} label={text} />
      {lines.map((line, i) => (
        <p key={i} className="ml-4 text-muted-foreground">
          {line}
        </p>
      ))}
      {step.phase === "done" && otherBrain && brain.state !== "done" && (
        <div
          className="ml-4 flex flex-wrap items-center gap-2 text-amber-700 dark:text-amber-400"
          data-testid="setup-brain"
        >
          <span>
            {fill(k("setup_brain_hint"), {
              server: serverLabel,
              brain: otherBrain,
            })}
          </span>
          <SoftButton
            primary
            disabled={brain.state === "switching"}
            onClick={() => {
              setBrain({ state: "switching" });
              onSwitchBrain().then(
                () => setBrain({ state: "done" }),
                (err: unknown) =>
                  setBrain({
                    state: "error",
                    message: err instanceof Error ? err.message : String(err),
                  }),
              );
            }}
          >
            {brain.state === "switching" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            {fill(k("setup_brain_button"), { server: serverLabel })}
          </SoftButton>
          {brain.state === "error" && (
            <span className="text-destructive">
              {fill(k("setup_brain_failed"), { message: brain.message })}
            </span>
          )}
        </div>
      )}
      {step.phase === "done" && brain.state === "done" && (
        <p className="ml-4 text-emerald-600 dark:text-emerald-400" data-testid="setup-brain-done">
          {fill(k("setup_brain_switched"), { server: serverLabel })}
        </p>
      )}
    </div>
  );
}

/** A 44 px SoftButton with icon, label and a one-line hint underneath. */
function ActionButton({
  icon,
  label,
  hint,
  onClick,
  primary,
  disabled,
  testId,
}: {
  icon: ReactNode;
  label: string;
  hint: string;
  onClick: () => void;
  primary?: boolean;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <span data-testid={testId}>
      <SoftButton
        primary={primary}
        onClick={onClick}
        disabled={disabled}
        ariaLabel={label}
        className="h-11 px-3.5 text-left"
      >
        <span className="shrink-0">{icon}</span>
        <span className="flex min-w-0 flex-col leading-tight">
          <span className="text-xs font-medium">{label}</span>
          <span
            className={cn(
              "truncate text-[11px] font-normal",
              primary ? "text-primary-foreground/80" : "text-muted-foreground",
            )}
          >
            {hint}
          </span>
        </span>
      </SoftButton>
    </span>
  );
}
