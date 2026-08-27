/**
 * Jobs — which download answers for which job, one row each.
 *
 * Chat & voice, Tools & screen, Deep & coding and Embeddings come from
 * `GET roles`; every row shows the configured pick (or the discovery
 * sentence), a picker over the installed models (`RolePicker`) and, when the
 * shortlist has a better one, "Use recommended" — which downloads that pick
 * when it is missing, assigns it, then writes the suggested options silently
 * and reads back one sentence. The read-only advanced roles hide under
 * "More roles".
 *
 * The picker is on screen for every row at every detail level. It used to be
 * folded away behind a "Change" button on the Simple page, which — together
 * with a shortlist that could come back empty — is what made the section
 * read as "it keeps resetting my model" (BUG-188): the one control that
 * changes a role was two clicks away and then offered nothing to pick.
 * `advanced` now adds detail (capability badges, Tune, the read-only roles),
 * it never takes the control away.
 *
 * The panel draws no frame of its own — the caller's `StepCard` is the frame.
 * Props take `providerId` (the pull-capable card's id) rather than the whole
 * descriptor so the panel can be mounted from anywhere the id is known.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Loader2, SlidersHorizontal, Sparkles } from "lucide-react";

import { SoftButton, StatusDot } from "@/components/extensions/primitives";
import {
  setRole,
  useInvalidateLocalModels,
  useOverview,
  useSetRole,
  type LocalModelRole,
  type LocalModelRow,
  type RoleRow,
} from "@/hooks/useLocalModels";
import { useProviders } from "@/hooks/useProviders";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { canonical, waitForPull } from "./localSetup";
import { RolePicker } from "./RolePicker";
import { useSilentTune } from "./useLocalSetup";

export interface RolesPanelProps {
  /** Id of the pull-capable provider card (found via `supports_model_pull`). */
  providerId: string;
  /** Opens the Tune sheet for one installed model; the button hides without it. */
  onTune?: (model: string) => void;
  /** Navigates to the API Keys section; shown when another brain is active. */
  onOpenApiKeys?: () => void;
  /** Adds capability badges, Tune and the read-only roles. Never removes the picker. */
  advanced?: boolean;
  /** Runs the overview's "Set up everything" flow (the empty state's button). */
  onSetup?: () => void;
  /** That flow is underway: the empty state's button waits. */
  setupBusy?: boolean;
}

type Phase = "idle" | "pulling" | "assigning" | "tuning" | "done" | "error";

interface Progress {
  phase: Phase;
  percent?: number;
  message?: string;
  /** The one-sentence readback after the silent tune. */
  readback?: string;
}

const WRITABLE: readonly LocalModelRole[] = [
  "chat",
  "voice",
  "tools_screen",
  "deep",
  "embedding",
];

/** Human window size: 16384 -> "16k". */
function contextLabel(numCtx: number): string {
  return numCtx >= 1024 ? `${Math.round(numCtx / 1024)}k` : String(numCtx);
}

export function RolesPanel({
  providerId,
  onTune,
  onOpenApiKeys,
  advanced = false,
  onSetup,
  setupBusy = false,
}: RolesPanelProps) {
  const t = useT();
  // Roles and inventory come from the one overview round-trip (painted
  // first from the snapshot); the shapes below keep the rest of the panel
  // reading as before.
  const overview = useOverview(providerId);
  const roles = {
    data: overview.data?.roles,
    isLoading: overview.isLoading,
    isError: overview.isError,
    error: overview.error,
  };
  const inventory = { data: overview.data?.inventory };
  const { providers } = useProviders();
  const setRoleMutation = useSetRole(providerId);
  const invalidate = useInvalidateLocalModels(providerId);

  const [progress, setProgress] = useState<Record<string, Progress>>({});
  const [moreOpen, setMoreOpen] = useState(false);

  // Async flows outlive a tab switch; nothing is written to state afterwards.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const alive = useCallback(() => mounted.current, []);

  const models: LocalModelRow[] = useMemo(
    () => inventory.data?.models ?? [],
    [inventory.data],
  );
  const installed = useMemo(() => {
    const names = new Set<string>();
    for (const m of models) names.add(canonical(m.name));
    return names;
  }, [models]);
  const isInstalled = useCallback(
    (name: string) => installed.has(canonical(name)),
    [installed],
  );

  const rows = roles.data?.roles ?? [];
  const primary = rows.filter((r) => !r.advanced);
  const advancedRows = rows.filter((r) => r.advanced);

  // The brain switch stays a user-only decision; the panel only says so.
  const activeBrain =
    providers.find((p) => p.tier === "brain" && p.active) ?? null;
  const otherBrainActive =
    activeBrain !== null && activeBrain.id !== providerId;

  const patch = useCallback((roleId: string, next: Progress) => {
    if (!mounted.current) return;
    setProgress((prev) => ({ ...prev, [roleId]: next }));
  }, []);

  /** Suggested options in, one sentence out. Never fails the flow. */
  const tuneSilently = useSilentTune(providerId);

  const useRecommended = useCallback(
    async (row: RoleRow) => {
      const model = row.recommended;
      if (!model || !WRITABLE.includes(row.id as LocalModelRole)) return;
      try {
        if (!isInstalled(model)) {
          patch(row.id, { phase: "pulling", percent: 0, message: "" });
          await waitForPull(
            providerId,
            model,
            (p) => patch(row.id, { phase: "pulling", ...p }),
            alive,
          );
          if (!alive()) return;
        }
        patch(row.id, { phase: "assigning" });
        await setRole(providerId, row.id as LocalModelRole, model);
        patch(row.id, { phase: "tuning" });
        const readback = await tuneSilently(model);
        patch(row.id, { phase: "done", readback });
      } catch (err) {
        patch(row.id, {
          phase: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      } finally {
        invalidate();
      }
    },
    [alive, invalidate, isInstalled, patch, providerId, tuneSilently],
  );

  // "Nothing is installed" is only true when the server actually answered.
  // While it is silent the inventory is empty for a different reason, and
  // saying "download something" there sends the user after the wrong problem.
  const serverSilent = Boolean(inventory.data?.error);
  const nothingInstalled =
    inventory.data !== undefined && !serverSilent && models.length === 0;

  return (
    <div className="space-y-3" data-testid="local-models-roles">
      {otherBrainActive && (
        <p
          className="text-sm text-amber-700 dark:text-amber-400"
          data-testid="roles-other-brain"
        >
          {fill(t("local_models.roles.other_brain"), {
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

      {serverSilent && (
        <p
          className="text-sm text-amber-700 dark:text-amber-400"
          data-testid="roles-server-silent"
        >
          {t("local_models.roles.server_silent")}
        </p>
      )}
      {roles.data?.error && (
        <p className="text-sm text-amber-700 dark:text-amber-400">
          {roles.data.error}
        </p>
      )}
      {roles.isError && (
        <p className="text-sm text-destructive">
          {roles.error instanceof Error
            ? roles.error.message
            : String(roles.error)}
        </p>
      )}
      {roles.isLoading && (
        <p className="text-sm text-muted-foreground">
          {t("local_models.roles.loading")}
        </p>
      )}

      {nothingInstalled && rows.length > 0 && onSetup && (
        <div
          className="rounded-lg border border-dashed border-border px-6 py-8 text-center"
          data-testid="roles-empty"
        >
          <p className="text-sm text-muted-foreground">
            {t("local_models.roles.empty_body")}
          </p>
          <div className="mt-4 flex justify-center">
            <SoftButton primary onClick={onSetup} disabled={setupBusy}>
              {setupBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="h-3.5 w-3.5" />
              )}
              {t("local_models.roles.setup_button")}
            </SoftButton>
          </div>
        </div>
      )}

      {primary.length > 0 && (
        <div
          className="divide-y divide-border/70 overflow-hidden rounded-lg border border-border/70"
          data-testid="roles-ledger"
        >
          {primary.map((row) => (
            <RoleRowView
              key={row.id}
              row={row}
              models={models}
              progress={progress[row.id] ?? { phase: "idle" }}
              installed={isInstalled}
              saving={
                setRoleMutation.isPending &&
                setRoleMutation.variables?.role === row.id
              }
              advanced={advanced}
              onPick={(model) =>
                setRoleMutation.mutate({
                  role: row.id as LocalModelRole,
                  model,
                })
              }
              onUseRecommended={() => void useRecommended(row)}
              onTune={onTune}
              t={t}
            />
          ))}
        </div>
      )}

      {setRoleMutation.isError && (
        <p className="text-sm text-destructive">
          {setRoleMutation.error instanceof Error
            ? setRoleMutation.error.message
            : String(setRoleMutation.error)}
        </p>
      )}

      {advanced && advancedRows.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setMoreOpen((v) => !v)}
            aria-expanded={moreOpen}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            <ChevronDown
              className={cn(
                "h-3.5 w-3.5 transition-transform",
                moreOpen && "rotate-180",
              )}
            />
            {t("local_models.roles.more_roles")}
          </button>
          {moreOpen && (
            <div
              className="mt-2 divide-y divide-border/70 overflow-hidden rounded-lg border border-border/70"
              data-testid="roles-more"
            >
              {advancedRows.map((row) => (
                <div
                  key={row.id}
                  className="grid gap-1 px-3.5 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] sm:items-center"
                >
                  <div className="text-sm text-foreground">
                    {t(row.label_key)}
                  </div>
                  <div className="text-sm text-muted-foreground">
                    {row.current ? (
                      <span className="font-mono text-xs text-foreground/85">
                        {row.current}
                      </span>
                    ) : (
                      row.note || t("local_models.roles.follows_chat")
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * One job: what it is, what answers it now, and the picker that changes it.
 *
 * Three columns on a wide pane — name and state, picker, actions — so the
 * pickers line up as a column a user can run down. Below `lg` they stack in
 * the same order.
 */
function RoleRowView({
  row,
  models,
  progress,
  installed,
  saving,
  advanced,
  onPick,
  onUseRecommended,
  onTune,
  t,
}: {
  row: RoleRow;
  models: LocalModelRow[];
  progress: Progress;
  installed: (name: string) => boolean;
  saving: boolean;
  advanced: boolean;
  onPick: (model: string) => void;
  onUseRecommended: () => void;
  onTune?: (model: string) => void;
  t: (key: string) => string;
}) {
  const running =
    progress.phase === "pulling" ||
    progress.phase === "assigning" ||
    progress.phase === "tuning";
  const currentMissing = row.current !== "" && !row.installed;
  const canUseRecommended =
    row.writable && row.recommended !== "" && row.recommended !== row.current;

  return (
    <div
      className="grid gap-3 px-3.5 py-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-start"
      data-testid={`role-row-${row.id}`}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">
            {t(row.label_key)}
          </span>
          {advanced &&
            row.required.map((cap) => (
              <span
                key={cap}
                className="rounded-md border border-border px-1.5 py-0.5 text-[11px] uppercase tracking-[0.18em] text-muted-foreground"
              >
                {cap}
              </span>
            ))}
        </div>
        <div className="mt-1 text-sm">
          {row.current ? (
            <StatusDot
              tone={currentMissing ? "warn" : "ok"}
              label={
                <span className="font-mono text-xs text-foreground/85">
                  {row.current}
                  {currentMissing && (
                    <span className="ml-2 font-sans text-muted-foreground">
                      {t("local_models.roles.not_installed")}
                    </span>
                  )}
                </span>
              }
            />
          ) : (
            <span className="text-muted-foreground">
              {row.note || t("local_models.roles.discovery")}
            </span>
          )}
        </div>
        {row.id === "voice" && row.context_tokens ? (
          <p
            className="mt-1 text-xs text-muted-foreground"
            data-testid="voice-context"
          >
            {fill(
              t(
                row.context_source === "manual"
                  ? "local_models.roles.voice_context_manual"
                  : "local_models.roles.voice_context_auto",
              ),
              { context: contextLabel(row.context_tokens) },
            )}
          </p>
        ) : null}
        <ProgressLine progress={progress} t={t} className="mt-1.5" />
      </div>

      <div className="min-w-0">
        {row.writable ? (
          <RolePicker
            row={row}
            models={models}
            disabled={saving || running}
            onPick={onPick}
          />
        ) : (
          <span className="text-xs text-muted-foreground">
            {t("local_models.roles.read_only")}
          </span>
        )}
        {canUseRecommended && row.recommended_reason && (
          <p
            className="mt-1.5 text-xs text-muted-foreground"
            data-testid="role-recommended-reason"
          >
            <span className="font-mono text-foreground/85">
              {row.recommended}
            </span>
            {" — "}
            {row.recommended_reason}
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 lg:justify-end">
        {canUseRecommended && (
          <SoftButton
            primary
            onClick={onUseRecommended}
            disabled={running || saving}
          >
            {running ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Sparkles className="h-3.5 w-3.5" />
            )}
            {installed(row.recommended)
              ? t("local_models.roles.use_recommended")
              : fill(t("local_models.roles.download_recommended"), {
                  model: row.recommended,
                })}
          </SoftButton>
        )}
        {advanced && onTune && row.current && row.installed && (
          <SoftButton onClick={() => onTune(row.current)} disabled={running}>
            <SlidersHorizontal className="h-3.5 w-3.5" />
            {t("local_models.roles.tune")}
          </SoftButton>
        )}
      </div>
    </div>
  );
}

/** The inline progress / readback line under a row or the setup button. */
function ProgressLine({
  progress,
  t,
  className,
}: {
  progress: Progress;
  t: (key: string) => string;
  className?: string;
}) {
  if (progress.phase === "idle") return null;
  let text: string;
  let tone: "busy" | "ok" | "error" = "busy";
  switch (progress.phase) {
    case "pulling": {
      const pct =
        typeof progress.percent === "number"
          ? `${Math.round(progress.percent)} %`
          : "";
      text =
        `${t("local_models.roles.progress_pulling")} ${pct} ${progress.message ?? ""}`.trim();
      break;
    }
    case "assigning":
      text = t("local_models.roles.progress_assigning");
      break;
    case "tuning":
      text = t("local_models.roles.progress_tuning");
      break;
    case "done":
      tone = "ok";
      text = progress.readback ?? t("local_models.roles.progress_done");
      break;
    default:
      tone = "error";
      text =
        `${t("local_models.roles.progress_error")} ${progress.message ?? ""}`.trim();
  }
  return (
    <div
      className={cn("flex items-center text-xs", className)}
      data-testid="role-progress"
    >
      <StatusDot tone={tone} pulse={tone === "busy"} label={text} />
    </div>
  );
}
