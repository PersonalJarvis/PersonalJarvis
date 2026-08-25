/**
 * Overview — the Simple-mode front page of the Local models section, written
 * so it reads like a plan rather than a dashboard.
 *
 * Top to bottom: one status sentence ("Ollama 0.32 running · 16 GB graphics
 * memory · Ollama is not your active brain (OpenRouter answers)"), a row of
 * three primary actions (Browse models / Help me set up / Something is not
 * working), the headline tiles (downloads on disk, graphics memory, loaded
 * now), and the roles as a checklist. Nothing here writes; the roles part is
 * `RolesPanel`, mounted underneath so the integration wires one component.
 *
 * The setup assistant is NOT rendered here: "Help me set up" and "Something is
 * not working" switch the section to the helper's own area, so the overview
 * never becomes a page thousands of pixels tall with the roles at the bottom.
 *
 * Props take `providerId` — the id of the card that declares
 * `supports_model_pull` — never a provider name.
 */
import { useEffect, useRef, type ReactNode } from "react";
import {
  AlertCircle,
  Cpu,
  HardDrive,
  Layers,
  Search,
  Sparkles,
} from "lucide-react";

import {
  SoftButton,
  StatTile,
  StatusDot,
} from "@/components/extensions/primitives";
import { useOverview } from "@/hooks/useLocalModels";
import { useProviders } from "@/hooks/useProviders";
import { fill, useT } from "@/i18n";
import {
  LOCAL_MODELS_MARK_FIRST_DATA,
  markLocalModels,
} from "@/lib/localModelsPerf";
import { cn } from "@/lib/utils";

import { formatExpiry } from "./localModelsFormat";
import { RolesPanel, type RolesPanelProps } from "./RolesPanel";

export interface OverviewPanelProps extends RolesPanelProps {
  /** Opens the catalogue ("Browse models"); the button hides without it. */
  onBrowse?: () => void;
  /** Opens the setup assistant — its own area, not a block inside this one. */
  onOpenAssistant?: () => void;
  /** "Something is not working" — opens the assistant in diagnose mode. */
  onReportProblem?: () => void;
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
  onOpenAssistant,
  onReportProblem,
  advanced = false,
}: OverviewPanelProps) {
  const t = useT();
  const overview = useOverview(providerId);
  const { providers } = useProviders();

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

      {(onBrowse || onOpenAssistant || onReportProblem) && (
        <div
          className="flex flex-wrap gap-2"
          role="group"
          aria-label={t("local_models.overview.actions_label")}
          data-testid="overview-actions"
        >
          {onBrowse && (
            <ActionButton
              primary
              icon={<Search className="h-4 w-4" />}
              label={t("local_models.overview.action_browse")}
              hint={t("local_models.overview.action_browse_hint")}
              onClick={onBrowse}
            />
          )}
          {onOpenAssistant && (
            <ActionButton
              icon={<Sparkles className="h-4 w-4" />}
              label={t("local_models.overview.action_setup")}
              hint={t("local_models.overview.action_setup_hint")}
              onClick={onOpenAssistant}
            />
          )}
          {onReportProblem && (
            <ActionButton
              quiet
              icon={<AlertCircle className="h-4 w-4" />}
              label={t("local_models.overview.action_broken")}
              hint={t("local_models.overview.action_broken_hint")}
              onClick={onReportProblem}
            />
          )}
        </div>
      )}

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
      />
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
  quiet,
}: {
  icon: ReactNode;
  label: string;
  hint: string;
  onClick: () => void;
  primary?: boolean;
  quiet?: boolean;
}) {
  return (
    <SoftButton
      primary={primary}
      onClick={onClick}
      ariaLabel={label}
      className={cn(
        "h-11 px-3.5 text-left",
        quiet && "bg-transparent text-muted-foreground hover:text-foreground",
      )}
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
  );
}
