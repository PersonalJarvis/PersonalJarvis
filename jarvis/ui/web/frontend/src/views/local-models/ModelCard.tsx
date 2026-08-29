/**
 * One job, one card, one model — and an honest verdict on the pairing.
 *
 * A local setup is not a sequence of steps — chat, voice, tools & screen
 * and deep are peers, and numbering them would claim an order that does
 * not exist. So the page is a grid of equals, and this is one tile: what
 * the job is, in a sentence a non-developer recognises; the model doing it
 * right now, by its readable name with the tag small beside it; and one
 * line saying whether that model can actually do the job.
 *
 * That line is the card's point. The old card knew ready / missing / empty
 * and said "ready" while the tools & screen job sat on a model without
 * vision. Now the backend's verdict on the current pick (`current_fit`,
 * one rule for every surface) becomes the card's state: `unfit` is a
 * warning ring and a sentence naming what is missing, `slow` names the
 * cost, and the recommendation button appears only when there is a reason
 * for it — never as a standing nag to switch away from a fine choice.
 *
 * The memory row stays the signature ("does this fit on my graphics
 * card"), but it now counts what the model actually costs loaded: the
 * weights plus the context window — plus, for the voice brain, the memory
 * the local speech stack keeps free beside it.
 */
import { Download, Loader2, SlidersHorizontal } from "lucide-react";

import { SoftButton } from "@/components/extensions/primitives";
import type { LocalModelRow, RoleRow } from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { estimateContextGb, formatContext, formatGb } from "./localModelsFormat";
import { capabilityChips, findModel, labelFor, modelLabel } from "./modelNames";
import { RolePicker } from "./RolePicker";
import type { RoleProgress } from "./useRoleActions";

export type ModelCardState =
  | "ready"
  | "slow"
  | "unfit"
  | "unknown"
  | "missing"
  | "empty"
  | "blocked";

export interface ModelCardProps {
  row: RoleRow;
  /** Every download the inventory knows; empty while the server is silent. */
  models: LocalModelRow[];
  /** Graphics memory this machine reported, in GB; 0 = unknown. */
  acceleratorGb: number;
  /** Memory the local speech stack keeps free beside this job's model (voice). */
  reserveGb?: number;
  /** Labels of the other jobs served by the same download. */
  sharedWith?: string[];
  progress: RoleProgress;
  busy: boolean;
  onPick: (model: string) => void;
  onUseRecommended: () => void;
  /** Downloads a shortlist pick and assigns it to this job. */
  onInstall?: (model: string) => void;
  /** Opens the Tune sheet for the model on this card. */
  onTune?: (model: string) => void;
}

const GIB = 1024 ** 3;
/** The window an unloaded, untuned model is assumed to open with. */
const DEFAULT_CONTEXT_TOKENS = 8192;

export function cardState(row: RoleRow): ModelCardState {
  // A job served by something other than Ollama, or whose own server is not
  // installed yet, says so and offers nothing: a picker whose write is going
  // to be refused is worse than a sentence naming what is missing.
  if (!row.writable || (row.note !== "" && row.current === "")) return "blocked";
  if (!row.current) return "empty";
  if (!row.installed || row.current_fit === "absent") return "missing";
  switch (row.current_fit) {
    case "unfit":
      return "unfit";
    case "slow":
      return "slow";
    case "unknown":
      return "unknown";
    default:
      return "ready";
  }
}

const RING: Record<ModelCardState, string> = {
  ready: "border-border",
  unknown: "border-border",
  // Amber and red carry meanings the token set has no name for; the accent
  // stays for the memory bar and the primary button.
  slow: "border-foreground/40",
  unfit: "border-destructive/50",
  missing: "border-foreground/50",
  empty: "border-dashed border-border",
  blocked: "border-border/60",
};

const VERDICT_TONE: Record<ModelCardState, string> = {
  ready: "text-muted-foreground",
  unknown: "text-muted-foreground",
  slow: "text-foreground",
  unfit: "text-destructive",
  missing: "text-foreground",
  empty: "text-muted-foreground",
  blocked: "text-muted-foreground",
};

const VERDICT_MARK: Record<ModelCardState, string> = {
  ready: "✓",
  unknown: "?",
  slow: "!",
  unfit: "✕",
  missing: "!",
  empty: "·",
  blocked: "·",
};

function Chip({ children, on = false }: { children: React.ReactNode; on?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded border px-1.5 py-px text-[10px] tabular-nums",
        on ? "border-border text-foreground/90" : "border-border/70 text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}

export function ModelCard({
  row,
  models,
  acceleratorGb,
  reserveGb = 0,
  sharedWith = [],
  progress,
  busy,
  onPick,
  onUseRecommended,
  onInstall,
  onTune,
}: ModelCardProps) {
  const t = useT();
  const state = cardState(row);
  const current = findModel(models, row.current);
  const weightsBytes = current?.size_bytes ?? 0;
  const weightsGb = weightsBytes / GIB;

  // The context the model runs with: the live figure when loaded, the voice
  // brain's sized window, the tuned/default window otherwise.
  const contextTokens =
    (current?.loaded ? current.running_context_length : null) ??
    row.context_tokens ??
    DEFAULT_CONTEXT_TOKENS;
  const contextGb =
    current?.loaded && (current.loaded_size_bytes ?? 0) > weightsBytes
      ? ((current.loaded_size_bytes ?? 0) - weightsBytes) / GIB
      : weightsGb > 0
        ? estimateContextGb(contextTokens, weightsGb)
        : 0;
  const totalGb = weightsGb + contextGb + reserveGb;
  const share = acceleratorGb > 0 && totalGb > 0 ? totalGb / acceleratorGb : 0;
  const overBudget = share > 1;

  const recommendedLabel = labelFor(models, row.recommended);
  const recommendedInstalled = findModel(models, row.recommended) !== null;
  const wantsRecommendation =
    row.writable &&
    row.recommended !== "" &&
    row.recommended !== row.current &&
    (state === "empty" || state === "missing" || state === "unfit" || state === "slow");

  const verdict = (() => {
    switch (state) {
      case "ready":
        return sharedWith.length > 0
          ? `${t("local_models.jobs.fit_fits")} ${fill(t("local_models.jobs.shared_with"), {
              roles: sharedWith.join(", "),
            })}`
          : t("local_models.jobs.fit_fits");
      case "slow":
        return fill(t("local_models.jobs.fit_slow"), { reason: row.current_reason ?? "" });
      case "unfit": {
        const base = fill(t("local_models.jobs.fit_unfit"), { reason: row.current_reason ?? "" });
        return wantsRecommendation
          ? `${base} ${fill(t("local_models.jobs.fit_alternative"), { model: recommendedLabel })}`
          : base;
      }
      case "unknown":
        return t("local_models.jobs.fit_unknown");
      case "missing":
        return t("local_models.jobs.not_on_disk");
      case "empty":
        return t("local_models.jobs.empty");
      case "blocked":
        return row.note || t("local_models.roles.read_only");
    }
  })();

  const chips: Array<{ text: string; on?: boolean }> = [];
  if (current) {
    if (current.params_label) chips.push({ text: current.params_label, on: true });
    if (current.quant_label || current.quantization_level)
      chips.push({ text: current.quant_label || current.quantization_level });
    chips.push({ text: formatGb(current.size_bytes) });
    if (current.context_length) {
      chips.push({
        text:
          row.id === "voice" && row.context_tokens
            ? fill(t("local_models.jobs.context_of"), {
                context: formatContext(row.context_tokens),
                native: formatContext(current.context_length),
              })
            : `${formatContext(current.context_length)} ctx`,
        on: row.id === "voice",
      });
    }
    for (const cap of capabilityChips(current)) chips.push({ text: cap, on: true });
    if (current.source) chips.push({ text: current.source });
    if (current.loaded) chips.push({ text: t("local_models.jobs.loaded_now"), on: true });
  }

  const memoryText = (() => {
    if (share <= 0) return t("local_models.jobs.memory_unknown");
    if (overBudget)
      return fill(t("local_models.jobs.memory_over"), { gb: acceleratorGb.toFixed(1) });
    const values = {
      weights: formatGb(weightsBytes),
      context: formatGb(contextGb * GIB),
      reserve: formatGb(reserveGb * GIB),
      percent: Math.round(share * 100),
      gb: acceleratorGb.toFixed(1),
    };
    return fill(
      t(reserveGb > 0 ? "local_models.jobs.memory_line_voice" : "local_models.jobs.memory_line"),
      values,
    );
  })();

  return (
    <article
      className={cn(
        "flex flex-col rounded-2xl border bg-card/50 p-4 transition-colors",
        RING[state],
      )}
      data-testid={`model-card-${row.id}`}
      data-state={state}
      aria-label={t(row.label_key)}
    >
      {/* The job: an eyebrow, because the job is the category and the model
          below it is the value — not the other way round. */}
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
          {t(row.label_key)}
        </p>
        <p className="truncate text-[11px] text-muted-foreground/80">
          {t(`local_models.jobs.${row.id}_purpose`)}
        </p>
      </div>

      {/* The model: the largest thing on the card, by a name a person can
          read; the tag small beside it, so the address is never lost. */}
      <div className="mt-3 min-h-[5.25rem]">
        {row.current ? (
          <>
            <p className="flex flex-wrap items-baseline gap-x-2">
              <span
                className="font-display text-lg font-semibold tracking-tight text-foreground"
                data-testid={`model-card-current-${row.id}`}
              >
                {modelLabel(current, row.current)}
              </span>
              <span
                className="truncate font-mono text-[11px] text-muted-foreground"
                title={row.current}
                data-testid={`model-card-tag-${row.id}`}
              >
                {row.current}
              </span>
            </p>
            {chips.length > 0 ? (
              <p className="mt-1.5 flex flex-wrap gap-1" data-testid={`model-card-chips-${row.id}`}>
                {chips.map((chip) => (
                  <Chip key={chip.text} on={chip.on}>
                    {chip.text}
                  </Chip>
                ))}
              </p>
            ) : (
              <p className="mt-1 text-[11px] text-muted-foreground">
                {state === "missing"
                  ? t("local_models.jobs.not_on_disk")
                  : t("local_models.jobs.no_facts")}
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">{verdict}</p>
        )}
      </div>

      {/* The verdict: one line, one rule, the same the picker uses. */}
      {row.current && (
        <p
          className={cn("mt-2 flex gap-1.5 text-xs leading-snug", VERDICT_TONE[state])}
          data-testid={`model-card-verdict-${row.id}`}
        >
          <span aria-hidden className="w-3 shrink-0 text-center font-semibold">
            {VERDICT_MARK[state]}
          </span>
          <span>{verdict}</span>
        </p>
      )}

      {/* The memory bar: this model, loaded, against this machine's card. */}
      {state !== "blocked" && state !== "empty" && (
        <div className="mt-2.5" data-testid={`model-card-memory-${row.id}`}>
          <div
            className="flex h-1.5 overflow-hidden rounded-full bg-sheen/[0.08]"
            role="img"
            aria-label={
              share > 0
                ? fill(t("local_models.jobs.memory_aria"), { percent: Math.round(share * 100) })
                : t("local_models.jobs.memory_unknown")
            }
          >
            <div
              className={cn(
                "h-full transition-[width] duration-500 ease-out",
                overBudget ? "bg-foreground" : "bg-foreground/70",
              )}
              style={{
                width: `${Math.min(100, Math.round((weightsGb / Math.max(acceleratorGb, totalGb, 0.01)) * 100))}%`,
              }}
              data-testid={`model-card-memory-fill-${row.id}`}
            />
            <div
              className={cn("h-full", overBudget ? "bg-foreground/50" : "bg-primary/40")}
              style={{
                width: `${Math.min(100, Math.round((contextGb / Math.max(acceleratorGb, totalGb, 0.01)) * 100))}%`,
              }}
            />
            {reserveGb > 0 && (
              <div
                className="h-full bg-sheen/25"
                style={{
                  width: `${Math.min(100, Math.round((reserveGb / Math.max(acceleratorGb, totalGb, 0.01)) * 100))}%`,
                }}
              />
            )}
          </div>
          <p className="mt-1 text-[10px] tabular-nums text-muted-foreground">{memoryText}</p>
        </div>
      )}

      {/* The controls. Always the same shape, always present. */}
      <div className="mt-3.5 flex flex-col gap-2">
        {state !== "blocked" && (
          <div className="flex gap-2">
            <RolePicker
              row={row}
              models={models}
              disabled={busy}
              onPick={onPick}
              onInstall={onInstall}
              className="min-w-0 flex-1"
            />
            {onTune && row.current && row.installed && (
              <SoftButton
                onClick={() => onTune(row.current)}
                disabled={busy}
                ariaLabel={fill(t("local_models.tune.aria"), { model: row.current })}
                className="h-9 shrink-0"
              >
                <SlidersHorizontal className="h-3.5 w-3.5" />
                {t("local_models.roles.tune")}
              </SoftButton>
            )}
          </div>
        )}

        {wantsRecommendation && (
          <SoftButton
            primary={state === "unfit" || state === "empty"}
            onClick={onUseRecommended}
            disabled={busy}
            className="h-8 w-full justify-center"
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            {recommendedInstalled
              ? fill(t("local_models.jobs.switch_to"), { model: recommendedLabel })
              : fill(t("local_models.roles.download_recommended"), { model: recommendedLabel })}
          </SoftButton>
        )}
      </div>

      <CardProgress progress={progress} t={t} />

      {/* The plumbing, one click away: where the pick lives, what the job
          asks for, what the recommendation would be and why. */}
      <details className="mt-3 border-t border-dashed border-border/70 pt-2 text-[11px] text-muted-foreground">
        <summary className="cursor-pointer select-none">{t("local_models.jobs.details")}</summary>
        <dl className="mt-1.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
          <dt>{t("local_models.jobs.details_config")}</dt>
          <dd className="truncate font-mono">{row.config_key}</dd>
          {row.required.filter((c) => c !== "completion").length > 0 && (
            <>
              <dt>{t("local_models.jobs.details_required")}</dt>
              <dd className="flex flex-wrap gap-1">
                {row.required
                  .filter((c) => c !== "completion")
                  .map((cap) => (
                    <Chip key={cap}>{cap}</Chip>
                  ))}
              </dd>
            </>
          )}
          {current?.loaded && current.loaded_as && current.loaded_as !== current.name && (
            <>
              <dt>{t("local_models.jobs.details_loaded_as")}</dt>
              <dd className="truncate font-mono">{current.loaded_as}</dd>
            </>
          )}
          {row.recommended && row.recommended_reason && (
            <>
              <dt>{t("local_models.jobs.details_recommended")}</dt>
              <dd>
                {recommendedLabel} — {row.recommended_reason}
              </dd>
            </>
          )}
        </dl>
      </details>
    </article>
  );
}

/** What the card is doing, or what it just did. One line, no chrome. */
function CardProgress({
  progress,
  t,
}: {
  progress: RoleProgress;
  t: (key: string) => string;
}) {
  if (progress.phase === "idle") return null;
  let text: string;
  let tone = "text-muted-foreground";
  switch (progress.phase) {
    case "pulling": {
      const pct =
        typeof progress.percent === "number" ? `${Math.round(progress.percent)} %` : "";
      text = `${t("local_models.roles.progress_pulling")} ${pct} ${progress.message ?? ""}`.trim();
      break;
    }
    case "assigning":
      text = t("local_models.roles.progress_assigning");
      break;
    case "tuning":
      text = t("local_models.roles.progress_tuning");
      break;
    case "done":
      tone = "text-muted-foreground";
      text = progress.readback ?? t("local_models.roles.progress_done");
      break;
    default:
      tone = "text-destructive";
      text = `${t("local_models.roles.progress_error")} ${progress.message ?? ""}`.trim();
  }
  return (
    <p className={cn("mt-2.5 text-[11px] leading-snug", tone)} data-testid="role-progress">
      {text}
    </p>
  );
}
