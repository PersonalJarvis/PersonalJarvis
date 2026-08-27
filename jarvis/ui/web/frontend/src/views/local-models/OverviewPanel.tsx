/**
 * Local models, front page: what sits in memory, the jobs, the server.
 *
 * The page answers one question — "which model does what on my machine,
 * and does it actually work" — and it answers it the way the machine is
 * actually shaped. Chat, voice, tools & screen and deep & coding are PEERS,
 * not steps: you can fill them in any order, and a number beside each would
 * claim a sequence that does not exist. So they are a grid of equal cards
 * (`ModelCard`), each carrying its own model, its own verdict and its own
 * picker.
 *
 * Which roles are cards, which one is the quiet row below (embeddings:
 * picked once, then forgotten) and which are footnotes (the read-only
 * consumers) is the backend's `layout` per role — the page iterates what
 * the server sends instead of keeping a second list of role ids to sync by
 * hand, so a role added there lands here by construction.
 *
 * Above the grid, `MemoryStrip` adds up what every job costs loaded at
 * once — the question the per-card bar cannot answer. Under the grid,
 * `ServerLaunch` offers the one button that puts a working, tested,
 * autostarting server behind the picks.
 *
 * There is no Simple/Advanced switch any more: every control is on the
 * card at every time (the picker, Tune), and the plumbing sits behind a
 * "Details" fold on each card. Advanced never removed a control (BUG-188);
 * now nothing hides one either.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

import { SoftButton, StatusDot } from "@/components/extensions/primitives";
import {
  useOverview,
  type LocalModelRole,
  type LocalModelRow,
  type RoleRow,
} from "@/hooks/useLocalModels";
import { switchBrainProvider, useProviders } from "@/hooks/useProviders";
import { fill, useT } from "@/i18n";
import {
  LOCAL_MODELS_MARK_FIRST_DATA,
  markLocalModels,
} from "@/lib/localModelsPerf";
import { cn } from "@/lib/utils";

import { InstalledPanel } from "./InstalledPanel";
import { canonical } from "./localSetup";
import type { SetupStep, SetupSummary } from "./localSetup";
import { MemoryStrip } from "./MemoryStrip";
import { ModelCard } from "./ModelCard";
import { labelFor, modelLabel, findModel } from "./modelNames";
import { RolePicker } from "./RolePicker";
import { ServerLaunch } from "./ServerLaunch";
import { useLocalSetup } from "./useLocalSetup";
import { useRoleActions, IDLE } from "./useRoleActions";
import { verifyLines } from "./verifyLines";

export interface OverviewPanelProps {
  /** Id of the pull-capable provider card (found via `supports_model_pull`). */
  providerId: string;
  /** Opens the Tune sheet for one installed model; the button hides without it. */
  onTune?: (model: string) => void;
  /** Navigates to the API Keys section; shown when another brain is active. */
  onOpenApiKeys?: () => void;
  /** Opens the catalogue ("Browse models"); the link hides without it. */
  onBrowse?: () => void;
  /** Opens the full installed ledger (Models tab). */
  onManage?: () => void;
}

/** Where a role lands when an older payload carries no `layout`. */
function layoutOf(row: RoleRow): "card" | "row" | "footnote" {
  if (row.layout === "card" || row.layout === "row" || row.layout === "footnote")
    return row.layout;
  if (!row.writable || row.advanced) return "footnote";
  return row.id === "embedding" ? "row" : "card";
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
}: OverviewPanelProps) {
  const t = useT();
  const overview = useOverview(providerId);
  const { providers, refetch: refetchProviders } = useProviders();
  const setup = useLocalSetup(providerId);

  const s = overview.data?.server;
  const inventory = overview.data?.inventory;
  const models = useMemo(() => inventory?.models ?? [], [inventory]);
  const acceleratorGb = overview.data?.recommended?.accelerator_gb ?? 0;
  const resident = overview.data?.roles.resident;
  const serverSilent = Boolean(inventory?.error);

  const marked = useRef(false);
  useEffect(() => {
    if (marked.current || !overview.data) return;
    marked.current = true;
    markLocalModels(LOCAL_MODELS_MARK_FIRST_DATA);
  }, [overview.data]);

  const installedNames = useMemo(() => {
    const names = new Set<string>();
    for (const m of models) names.add(canonical(m.name));
    return names;
  }, [models]);
  const actions = useRoleActions(providerId, installedNames);

  const allRows = useMemo(() => overview.data?.roles.roles ?? [], [overview.data]);
  const cardRows = useMemo(() => allRows.filter((r) => layoutOf(r) === "card"), [allRows]);
  const sideRows = useMemo(() => allRows.filter((r) => layoutOf(r) === "row"), [allRows]);
  const footnoteRows = useMemo(
    () => allRows.filter((r) => layoutOf(r) === "footnote"),
    [allRows],
  );

  const serverLabel =
    providers.find((p) => p.id === providerId)?.label ||
    t("local_models.overview.status_generic_server");
  const activeBrain =
    providers.find((p) => p.tier === "brain" && p.active) ?? null;
  const otherBrainActive =
    activeBrain !== null && activeBrain.id !== providerId;

  const roleLabel = useCallback(
    (role: LocalModelRole) =>
      t(allRows.find((r) => r.id === role)?.label_key ?? `local_models.role_${role}`),
    [allRows, t],
  );

  // Which other cards share a card's download — loaded once, said once.
  const sharedWith = useCallback(
    (row: RoleRow) =>
      row.current
        ? cardRows
            .filter((r) => r.id !== row.id && r.current && canonical(r.current) === canonical(row.current))
            .map((r) => t(r.label_key))
        : [],
    [cardRows, t],
  );

  const checking = overview.isFetching && overview.data?.source === "cache";

  return (
    <div className="space-y-4" data-testid="local-models-overview-panel">
      {/* One line of context, never a banner: what is off, if anything is. */}
      {(otherBrainActive || serverSilent || overview.isError || checking) && (
        <div className="space-y-1.5" data-testid="overview-notices">
          {checking && (
            <span data-testid="overview-checking">
              <StatusDot
                tone="busy"
                pulse
                label={t("local_models.overview.checking")}
              />
            </span>
          )}
          {serverSilent && (
            <p
              className="text-sm text-amber-700 dark:text-amber-400"
              data-testid="roles-server-silent"
            >
              {t("local_models.roles.server_silent")}
            </p>
          )}
          {overview.isError && (
            <p className="text-sm text-destructive">
              {overview.error instanceof Error
                ? overview.error.message
                : String(overview.error)}
            </p>
          )}
          {otherBrainActive && (
            <p
              className="text-sm text-amber-700 dark:text-amber-400"
              data-testid="overview-brain-clause"
            >
              {fill(t("local_models.overview.status_brain_other"), {
                server: serverLabel,
                brain: activeBrain?.label ?? "",
              })}{" "}
              {onOpenApiKeys && (
                <button
                  type="button"
                  onClick={onOpenApiKeys}
                  className="font-medium text-primary hover:underline"
                >
                  {t("local_models.roles.other_brain_link")}
                </button>
              )}
            </p>
          )}
        </div>
      )}

      <MemoryStrip resident={resident} roleLabel={roleLabel} />

      {/* The grid. Peers, two columns on a wide pane. */}
      <div
        className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4"
        data-testid="model-card-grid"
      >
        {cardRows.length === 0 && overview.isLoading
          ? ["chat", "voice", "tools_screen", "deep"].map((id) => <CardSkeleton key={id} />)
          : cardRows.map((row) => (
              <ModelCard
                key={row.id}
                row={row}
                models={models}
                acceleratorGb={acceleratorGb}
                reserveGb={row.id === "voice" ? (resident?.reserve_gb ?? 0) : 0}
                sharedWith={sharedWith(row)}
                progress={actions.progress[row.id] ?? IDLE}
                busy={actions.isBusy(row.id)}
                onPick={(model) => actions.pick(row.id, model)}
                onUseRecommended={() => actions.useRecommended(row)}
                onInstall={(model) => actions.install(row, model)}
                onTune={onTune}
              />
            ))}
      </div>

      {actions.writeError && (
        <p className="text-sm text-destructive" data-testid="role-write-error">
          {actions.writeError}
        </p>
      )}

      {/* Set here, but not part of the conversation. */}
      {sideRows.map((row) => (
        <SideJobRow
          key={row.id}
          row={row}
          models={models}
          busy={actions.isBusy(row.id)}
          onPick={(model) => actions.pick(row.id, model)}
          onInstall={(model) => actions.install(row, model)}
          onTune={onTune}
          t={t}
        />
      ))}

      {footnoteRows.length > 0 && (
        <FootnoteJobs rows={footnoteRows} models={models} t={t} />
      )}

      <ServerLaunch
        rows={[...cardRows, ...sideRows]}
        models={models}
        acceleratorGb={acceleratorGb}
        server={s}
        serverLabel={serverLabel}
        running={Boolean(s?.running)}
        busy={setup.busy}
        onRun={setup.run}
      >
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
      </ServerLaunch>

      <InstalledPanel
        models={models}
        roles={allRows}
        diskBytes={inventory?.disk_bytes ?? s?.disk_bytes ?? 0}
        loading={overview.isLoading}
        error={inventory?.error ?? null}
        onManage={onManage}
        onBrowse={onBrowse}
      />
    </div>
  );
}

/** A card-shaped placeholder so the grid does not jump when data lands. */
function CardSkeleton() {
  return (
    <div
      className="h-[17rem] animate-pulse rounded-2xl border border-dashed border-border bg-card/30"
      data-testid="model-card-skeleton"
    />
  );
}

/**
 * A job that is set here but is not part of the conversation — today just
 * embeddings, which indexes the wiki in the background.
 *
 * One line, not a card: it carries the same picker (dropping it would take a
 * setting away with nowhere else to reach it) but none of the card's weight,
 * because it is picked once and then forgotten.
 */
function SideJobRow({
  row,
  models,
  busy,
  onPick,
  onInstall,
  onTune,
  t,
}: {
  row: RoleRow;
  models: LocalModelRow[];
  busy: boolean;
  onPick: (model: string) => void;
  onInstall?: (model: string) => void;
  onTune?: (model: string) => void;
  t: (key: string) => string;
}) {
  const missing = row.current !== "" && !row.installed;
  const blocked = !row.writable || (row.note !== "" && row.current === "");
  const current = findModel(models, row.current);
  return (
    <div
      className="grid gap-3 rounded-xl border border-border/70 bg-card/40 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)_auto] lg:items-center"
      data-testid={`side-job-${row.id}`}
    >
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
          {t(row.label_key)}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {row.note || t(`local_models.jobs.${row.id}_purpose`)}
          {current && (
            <span className="ml-2 text-foreground/80">
              {modelLabel(current)} · {formatGigabytes(current.size_bytes)}
            </span>
          )}
        </p>
      </div>
      <div className="min-w-0">
        {blocked ? (
          <span className="text-xs text-muted-foreground">
            {t("local_models.roles.read_only")}
          </span>
        ) : (
          <RolePicker
            row={row}
            models={models}
            disabled={busy}
            onPick={onPick}
            onInstall={onInstall}
          />
        )}
      </div>
      <div className="flex items-center gap-2 lg:justify-end">
        {missing && (
          <span className="text-xs text-amber-700 dark:text-amber-400">
            {t("local_models.jobs.not_on_disk")}
          </span>
        )}
        {row.current_fit === "unfit" && row.current_reason && (
          <span className="text-xs text-destructive">{row.current_reason}</span>
        )}
        {onTune && row.current && row.installed && (
          <SoftButton onClick={() => onTune(row.current)} className="h-8">
            {t("local_models.roles.tune")}
          </SoftButton>
        )}
      </div>
    </div>
  );
}

/** The jobs that follow another pick or run elsewhere; read-only, one line each. */
function FootnoteJobs({
  rows,
  models,
  t,
}: {
  rows: RoleRow[];
  models: LocalModelRow[];
  t: (key: string) => string;
}) {
  return (
    <p className="text-xs text-muted-foreground" data-testid="roles-more">
      {rows.map((row, i) => (
        <span key={row.id}>
          {i > 0 && " · "}
          <span className="text-foreground/80">{t(row.label_key)}</span>
          {": "}
          {row.current ? (
            <span title={row.current}>{labelFor(models, row.current)}</span>
          ) : (
            row.note || t("local_models.roles.follows_chat")
          )}
        </span>
      ))}
    </p>
  );
}

/**
 * The set-up flow's line inside the launch bar: what is happening now, or
 * — once it ended — what was done, in the user's words (job → model), plus
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
    case "verifying":
      text = k("setup_verifying");
      break;
    case "saving":
      text = k("setup_saving");
      break;
    case "error":
      tone = "error";
      text = fill(k("setup_error"), { message: step.message });
      summary = step.summary;
      break;
    case "done": {
      summary = step.summary;
      const changed =
        summary.assigned.length > 0 ||
        summary.pulled.length > 0 ||
        summary.serverStarted;
      if (summary.verify && !summary.verify.ok) {
        // Set up, yes — but the proof failed, and that is the headline.
        tone = "error";
        text = fill(k("setup_done_problem"), { reason: summary.verify.reason });
      } else {
        tone = "ok";
        text = changed ? k("setup_done") : k("setup_done_nothing");
      }
      break;
    }
  }

  const lines: string[] = [];
  if (summary) {
    for (const line of verifyLines(summary.verify, t)) lines.push(line);
    if (summary.autostart) lines.push(k("setup_autostart_on"));
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
    <div
      className="mt-3 space-y-1 border-t border-border/70 pt-3 text-xs"
      data-testid="setup-progress"
    >
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
            <span className={cn("text-destructive")}>
              {fill(k("setup_brain_failed"), { message: brain.message })}
            </span>
          )}
        </div>
      )}
      {step.phase === "done" && brain.state === "done" && (
        <p
          className="ml-4 text-emerald-600 dark:text-emerald-400"
          data-testid="setup-brain-done"
        >
          {fill(k("setup_brain_switched"), { server: serverLabel })}
        </p>
      )}
    </div>
  );
}
