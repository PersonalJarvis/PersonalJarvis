/**
 * Roles — the four model slots Jarvis fills from the local server, one row
 * each, one primary action each.
 *
 * Chat & voice, Tools & screen, Deep & coding and Embeddings come from
 * `GET roles`; every row shows the configured pick (or the discovery
 * sentence), the capabilities the slot requires, a picker over the installed
 * models that qualify, and "Use recommended" — which downloads the pick when
 * it is missing, assigns it, then writes the suggested options silently and
 * reads back one sentence. The read-only advanced roles hide under
 * "More roles". When nothing is installed at all, the ledger collapses into a
 * single "Set up the recommended models" button.
 *
 * Two variants: `ledger` (the full rows: picker, badges, Tune) and
 * `checklist` — one compact line per writable role (dot, label, model or
 * "not set", ONE trailing action: "Use recommended" when the shortlist has a
 * better pick, otherwise "Change"), where Change expands that line into the
 * full ledger row in place. The Overview uses the checklist.
 *
 * Props take `providerId` (the pull-capable card's id) rather than the whole
 * descriptor so the panel can be mounted from anywhere the id is known. The
 * "Tune" button only calls `onTune(model)` — the sheet belongs to the caller.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";

import {
  Panel,
  PanelHeader,
  SoftButton,
  StatusDot,
} from "@/components/extensions/primitives";
import {
  getSuggestedOptions,
  modelPullStatus,
  saveModelOptions,
  setRole,
  startModelPull,
  useInvalidateLocalModels,
  useOverview,
  useSetRole,
  type LocalModelRole,
  type RoleRow,
  type SuggestedOptionsResponse,
} from "@/hooks/useLocalModels";
import { useProviders } from "@/hooks/useProviders";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

export interface RolesPanelProps {
  /** Id of the pull-capable provider card (found via `supports_model_pull`). */
  providerId: string;
  /** Opens the Tune sheet for one installed model; the button hides without it. */
  onTune?: (model: string) => void;
  /** Navigates to the API Keys section; shown when another brain is active. */
  onOpenApiKeys?: () => void;
  /** `ledger` = full rows (default); `checklist` = compact rows that expand. */
  variant?: "ledger" | "checklist";
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
const POLL_MS = 2000;

/** "qwen3.5" and "qwen3.5:latest" are the same download. */
function canonical(name: string): string {
  return name.endsWith(":latest") ? name.slice(0, -":latest".length) : name;
}

/** Poll one download until the server reports it done; throws on error. */
async function waitForPull(
  providerId: string,
  model: string,
  onProgress: (p: { percent?: number; message: string }) => void,
  alive: () => boolean,
): Promise<void> {
  await startModelPull(providerId, model);
  for (;;) {
    if (!alive()) return;
    const pull = await modelPullStatus(providerId, model);
    if (pull.state === "done") return;
    if (pull.state === "error")
      throw new Error(pull.message || "download failed");
    onProgress({ percent: pull.percent, message: pull.message });
    await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
  }
}

/** Human window size: 16384 -> "16k". */
function contextLabel(numCtx: number): string {
  return numCtx >= 1024 ? `${Math.round(numCtx / 1024)}k` : String(numCtx);
}

export function RolesPanel({
  providerId,
  onTune,
  onOpenApiKeys,
  variant = "ledger",
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
  const [setup, setSetup] = useState<Progress>({ phase: "idle" });
  const [moreOpen, setMoreOpen] = useState(false);
  // Checklist rows the user expanded into the full ledger row.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const toggleExpanded = useCallback((roleId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(roleId)) next.delete(roleId);
      else next.add(roleId);
      return next;
    });
  }, []);

  // Async flows outlive a tab switch; nothing is written to state afterwards.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const alive = useCallback(() => mounted.current, []);

  const installed = useMemo(() => {
    const names = new Set<string>();
    for (const m of inventory.data?.models ?? []) names.add(canonical(m.name));
    return names;
  }, [inventory.data]);
  const isInstalled = useCallback(
    (name: string) => installed.has(canonical(name)),
    [installed],
  );

  const rows = roles.data?.roles ?? [];
  const primary = rows.filter((r) => !r.advanced);
  const advanced = rows.filter((r) => r.advanced);

  // The brain switch stays a user-only decision; the panel only says so.
  const activeBrain =
    providers.find((p) => p.tier === "brain" && p.active) ?? null;
  const otherBrainActive =
    activeBrain !== null && activeBrain.id !== providerId;

  const readbackFor = useCallback(
    (s: SuggestedOptionsResponse): string => {
      const ctx = s.options.num_ctx;
      if (typeof ctx === "number") {
        const key =
          s.options.num_gpu != null
            ? "local_models.roles.readback_gpu"
            : "local_models.roles.readback_ctx";
        return fill(t(key), { ctx: contextLabel(ctx) });
      }
      return s.reasons[0] ?? t("local_models.roles.readback_defaults");
    },
    [t],
  );

  const patch = useCallback((roleId: string, next: Progress) => {
    if (!mounted.current) return;
    setProgress((prev) => ({ ...prev, [roleId]: next }));
  }, []);

  /** Suggested options in, one sentence out. Never fails the flow. */
  const tuneSilently = useCallback(
    async (model: string): Promise<string> => {
      try {
        const suggested = await getSuggestedOptions(providerId, model);
        await saveModelOptions(providerId, model, suggested.options);
        return readbackFor(suggested);
      } catch (err) {
        // The slot is already assigned; a failed tune only loses the readback.
        console.warn(
          "[local-models] suggested options not applied",
          model,
          err,
        );
        return t("local_models.roles.tune_skipped");
      }
    },
    [providerId, readbackFor, t],
  );

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

  /** Empty state: the chat pick and the embedding pick, then all four rows. */
  const setupRecommended = useCallback(async () => {
    const byId = new Map(primary.map((r) => [r.id, r]));
    const chatPick = byId.get("chat")?.recommended ?? "";
    const embedPick = byId.get("embedding")?.recommended ?? "";
    const pulls = Array.from(new Set([chatPick, embedPick].filter(Boolean)));
    if (pulls.length === 0) return;
    const setSafe = (next: Progress) => {
      if (mounted.current) setSetup(next);
    };
    try {
      for (const model of pulls) {
        if (isInstalled(model)) continue;
        setSafe({ phase: "pulling", percent: 0, message: model });
        await waitForPull(
          providerId,
          model,
          (p) =>
            setSafe({
              phase: "pulling",
              percent: p.percent,
              message: `${model} ${p.message}`.trim(),
            }),
          alive,
        );
        if (!alive()) return;
      }
      setSafe({ phase: "assigning" });
      const readbacks: string[] = [];
      for (const roleId of WRITABLE) {
        const row = byId.get(roleId);
        if (!row) continue;
        const fallback = roleId === "embedding" ? embedPick : chatPick;
        const model =
          row.recommended && pulls.includes(row.recommended)
            ? row.recommended
            : fallback;
        if (!model) continue;
        await setRole(providerId, roleId, model);
      }
      setSafe({ phase: "tuning" });
      for (const model of pulls)
        readbacks.push(`${model}: ${await tuneSilently(model)}`);
      setSafe({ phase: "done", readback: readbacks.join(" ") });
    } catch (err) {
      setSafe({
        phase: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      invalidate();
    }
  }, [alive, invalidate, isInstalled, primary, providerId, tuneSilently]);

  const nothingInstalled =
    inventory.data !== undefined &&
    !inventory.data.error &&
    inventory.data.models.length === 0;
  const busy =
    setup.phase === "pulling" ||
    setup.phase === "assigning" ||
    setup.phase === "tuning";

  return (
    <Panel className="p-4">
      <div className="space-y-3" data-testid="local-models-roles">
        <PanelHeader
          title={t("local_models.roles.title")}
          subtitle={t("local_models.roles.subtitle")}
        />

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

        {nothingInstalled && rows.length > 0 && (
          <div
            className="rounded-lg border border-dashed border-border px-6 py-8 text-center"
            data-testid="roles-empty"
          >
            <p className="text-sm text-muted-foreground">
              {t("local_models.roles.empty_body")}
            </p>
            <div className="mt-4 flex justify-center">
              <SoftButton
                primary
                onClick={() => void setupRecommended()}
                disabled={busy}
              >
                {busy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" />
                )}
                {t("local_models.roles.setup_button")}
              </SoftButton>
            </div>
            <ProgressLine
              progress={setup}
              t={t}
              className="mt-3 justify-center"
            />
          </div>
        )}

        {primary.length > 0 && (
          <div
            className="divide-y divide-border/70 overflow-hidden rounded-xl border border-border bg-card/60"
            data-testid="roles-ledger"
          >
            {primary.map((row) => {
              const rowProgress = progress[row.id] ?? { phase: "idle" };
              const saving =
                setRoleMutation.isPending &&
                setRoleMutation.variables?.role === row.id;
              const onPick = (model: string) =>
                setRoleMutation.mutate({
                  role: row.id as LocalModelRole,
                  model,
                });
              if (variant === "checklist" && !expanded.has(row.id)) {
                return (
                  <RoleChecklistRow
                    key={row.id}
                    row={row}
                    progress={rowProgress}
                    installed={isInstalled}
                    saving={saving}
                    onUseRecommended={() => void useRecommended(row)}
                    onExpand={() => toggleExpanded(row.id)}
                    t={t}
                  />
                );
              }
              return (
                <RoleLedgerRow
                  key={row.id}
                  row={row}
                  progress={rowProgress}
                  installed={isInstalled}
                  saving={saving}
                  onPick={onPick}
                  onUseRecommended={() => void useRecommended(row)}
                  onTune={onTune}
                  onCollapse={
                    variant === "checklist"
                      ? () => toggleExpanded(row.id)
                      : undefined
                  }
                  t={t}
                />
              );
            })}
          </div>
        )}

        {setRoleMutation.isError && (
          <p className="text-sm text-destructive">
            {setRoleMutation.error instanceof Error
              ? setRoleMutation.error.message
              : String(setRoleMutation.error)}
          </p>
        )}

        {advanced.length > 0 && (
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
                className="mt-2 divide-y divide-border/70 overflow-hidden rounded-xl border border-border bg-card/60"
                data-testid="roles-more"
              >
                {advanced.map((row) => (
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
    </Panel>
  );
}

function RoleLedgerRow({
  row,
  progress,
  installed,
  saving,
  onPick,
  onUseRecommended,
  onTune,
  onCollapse,
  t,
}: {
  row: RoleRow;
  progress: Progress;
  installed: (name: string) => boolean;
  saving: boolean;
  onPick: (model: string) => void;
  onUseRecommended: () => void;
  onTune?: (model: string) => void;
  /** Checklist only: folds the row back into its compact line. */
  onCollapse?: () => void;
  t: (key: string) => string;
}) {
  const running =
    progress.phase === "pulling" ||
    progress.phase === "assigning" ||
    progress.phase === "tuning";
  const currentMissing = row.current !== "" && !row.installed;
  const canUseRecommended =
    row.writable && row.recommended !== "" && row.recommended !== row.current;
  // The picker lists every qualifying download, plus the configured one when
  // it is gone, so the gap is visible rather than silently replaced.
  const choices = useMemo(() => {
    const list = [...row.qualifying];
    if (row.current && !list.includes(row.current)) list.unshift(row.current);
    return list;
  }, [row.current, row.qualifying]);

  return (
    <div
      className="grid gap-3 px-3.5 py-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_auto] lg:items-center"
      data-testid={`role-row-${row.id}`}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">
            {t(row.label_key)}
          </span>
          {row.required.map((cap) => (
            <span
              key={cap}
              className="rounded-md border border-border px-1.5 py-0.5 text-[11px] uppercase tracking-[0.18em] text-muted-foreground"
            >
              {cap}
            </span>
          ))}
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
        <ProgressLine progress={progress} t={t} className="mt-1.5" />
      </div>

      <div className="min-w-0">
        {row.writable ? (
          <select
            aria-label={fill(t("local_models.roles.pick_label"), {
              role: t(row.label_key),
            })}
            value={row.current}
            disabled={saving || running}
            onChange={(e) => onPick(e.target.value)}
            className="h-8 w-full rounded-md border border-border bg-background/60 px-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          >
            {row.id !== "embedding" && (
              <option value="">{t("local_models.roles.pick_discovery")}</option>
            )}
            {row.id === "embedding" && !row.current && (
              <option value="">{t("local_models.roles.pick_none")}</option>
            )}
            {choices.map((name) => (
              <option key={name} value={name}>
                {name}
                {installed(name)
                  ? ""
                  : ` ${t("local_models.roles.pick_missing_suffix")}`}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-xs text-muted-foreground">
            {t("local_models.roles.read_only")}
          </span>
        )}
      </div>

      <div className="flex items-center gap-1.5 lg:justify-end">
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
        {onTune && row.current && row.installed && (
          <SoftButton onClick={() => onTune(row.current)} disabled={running}>
            <SlidersHorizontal className="h-3.5 w-3.5" />
            {t("local_models.roles.tune")}
          </SoftButton>
        )}
        {onCollapse && (
          <SoftButton onClick={onCollapse} ariaExpanded>
            <ChevronDown className="h-3.5 w-3.5" />
            {t("local_models.roles.checklist_done")}
          </SoftButton>
        )}
      </div>
    </div>
  );
}

/**
 * The compact checklist line: dot + label + model (or "not set") + ONE
 * trailing action. The line itself is a button that expands to the full row;
 * the trailing action is "Use recommended" when the shortlist has a better
 * pick, otherwise "Change" (which expands too).
 */
function RoleChecklistRow({
  row,
  progress,
  installed,
  saving,
  onUseRecommended,
  onExpand,
  t,
}: {
  row: RoleRow;
  progress: Progress;
  installed: (name: string) => boolean;
  saving: boolean;
  onUseRecommended: () => void;
  onExpand: () => void;
  t: (key: string) => string;
}) {
  const running =
    progress.phase === "pulling" ||
    progress.phase === "assigning" ||
    progress.phase === "tuning";
  const currentMissing = row.current !== "" && !row.installed;
  const canUseRecommended =
    row.writable && row.recommended !== "" && row.recommended !== row.current;
  const tone: "ok" | "warn" | "off" = row.current
    ? currentMissing
      ? "warn"
      : "ok"
    : "off";
  return (
    <div
      className="grid gap-2 px-3.5 py-2.5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
      data-testid={`role-row-${row.id}`}
      data-variant="checklist"
    >
      <button
        type="button"
        onClick={onExpand}
        aria-expanded={false}
        className="flex min-w-0 items-center gap-2 text-left"
      >
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="text-sm font-medium text-foreground">
          {t(row.label_key)}
        </span>
        <StatusDot
          tone={tone}
          label={
            row.current ? (
              <span className="font-mono text-xs text-foreground/85">
                {row.current}
                {currentMissing && (
                  <span className="ml-2 font-sans text-muted-foreground">
                    {t("local_models.roles.not_installed")}
                  </span>
                )}
              </span>
            ) : (
              <span className="text-xs">
                {t("local_models.roles.checklist_not_set")}
              </span>
            )
          }
        />
      </button>
      <div className="flex items-center gap-1.5 sm:justify-end">
        {canUseRecommended ? (
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
        ) : (
          <SoftButton onClick={onExpand} disabled={running || saving}>
            {t("local_models.roles.checklist_change")}
          </SoftButton>
        )}
      </div>
      <ProgressLine progress={progress} t={t} className="sm:col-span-2" />
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
