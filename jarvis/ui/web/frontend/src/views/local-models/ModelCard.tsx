/**
 * One job, one card, one model.
 *
 * A local setup is not a sequence of steps — chat, tools, deep and
 * embeddings are four peers, and numbering them would claim an order that
 * does not exist. So the page is a grid of equals, and this is one tile:
 * what the job is, in a sentence a non-developer recognises; the model doing
 * it right now; and the picker that changes it.
 *
 * The card's signature is the memory bar. Every other model picker in the
 * world shows a list of names; the question that actually decides a local
 * setup is "does this fit on my graphics card". The bar answers it before
 * the download starts: the model's size against the accelerator this machine
 * reported, amber once the model is larger than the card can hold.
 *
 * `state` is derived, never passed: a card is `ready` when its model is set
 * AND on disk, `missing` when a tag is configured that is not installed,
 * `empty` when nothing is set. The border carries it, so a glance across the
 * grid says what is left to do without reading a word.
 */
import { Download, Loader2, SlidersHorizontal } from "lucide-react";

import { SoftButton } from "@/components/extensions/primitives";
import type { LocalModelRow, RoleRow } from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { formatGb } from "./localModelsFormat";
import { RolePicker } from "./RolePicker";
import type { RoleProgress } from "./useRoleActions";

export type ModelCardState = "ready" | "missing" | "empty" | "blocked";

export interface ModelCardProps {
  row: RoleRow;
  /** Every download the inventory knows; empty while the server is silent. */
  models: LocalModelRow[];
  /** Graphics memory this machine reported, in GB; 0 = unknown. */
  acceleratorGb: number;
  progress: RoleProgress;
  busy: boolean;
  onPick: (model: string) => void;
  onUseRecommended: () => void;
  /** Advanced only: opens the Tune sheet for the model on this card. */
  onTune?: (model: string) => void;
  /** Adds the required-capability badges and the Tune button. */
  advanced?: boolean;
}

/** Human window size: 16384 -> "16k". */
function contextLabel(numCtx: number): string {
  return numCtx >= 1024 ? `${Math.round(numCtx / 1024)}k` : String(numCtx);
}

/** The capabilities a job is gated on, as one short phrase. */
function capabilityLine(row: LocalModelRow | null): string {
  if (!row || !row.probed) return "";
  return row.capabilities.filter((c) => c !== "completion").join(" · ");
}

export function cardState(row: RoleRow): ModelCardState {
  // A job served by something other than Ollama, or whose own server is not
  // installed yet, says so and offers nothing: a picker whose write is going
  // to be refused is worse than a sentence naming what is missing.
  if (!row.writable || (row.note !== "" && row.current === "")) return "blocked";
  if (!row.current) return "empty";
  return row.installed ? "ready" : "missing";
}

const RING: Record<ModelCardState, string> = {
  ready: "border-border",
  // Amber and emerald carry meanings the token set has no name for, and each
  // needs its own value per theme — the same pairing StatTile uses.
  missing: "border-amber-500/50",
  empty: "border-dashed border-border",
  blocked: "border-border/60",
};

export function ModelCard({
  row,
  models,
  acceleratorGb,
  progress,
  busy,
  onPick,
  onUseRecommended,
  onTune,
  advanced = false,
}: ModelCardProps) {
  const t = useT();
  const state = cardState(row);
  const current = models.find((m) => m.name === row.current) ?? null;
  const sizeBytes = current?.size_bytes ?? 0;
  const budgetBytes = acceleratorGb * 1024 ** 3;
  const share = budgetBytes > 0 && sizeBytes > 0 ? sizeBytes / budgetBytes : 0;
  const overBudget = share > 1;
  const caps = capabilityLine(current);

  const canUseRecommended =
    row.writable && row.recommended !== "" && row.recommended !== row.current;
  const recommendedInstalled = models.some((m) => m.name === row.recommended);

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
      {/* The job. An eyebrow, because the job is the category and the model
          below it is the value — not the other way round. */}
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
          {t(row.label_key)}
        </p>
        {advanced && row.required.length > 0 && (
          <div className="flex shrink-0 flex-wrap justify-end gap-1">
            {row.required.map((cap) => (
              <span
                key={cap}
                className="rounded border border-border px-1 py-px text-[10px] uppercase tracking-wider text-muted-foreground"
              >
                {cap}
              </span>
            ))}
          </div>
        )}
      </div>
      {/* Two lines reserved: a sentence that wraps on a narrow card must
          not push this card's picker below its neighbours'. */}
      <p className="mt-1 min-h-[2.5rem] text-xs leading-relaxed text-muted-foreground">
        {t(`local_models.jobs.${row.id}_purpose`)}
      </p>

      {/* The model. The largest thing on the card, because it is the answer
          the card exists to give. Fixed height: the pickers below are scanned
          as a column and must line up across cards that carry a fact more. */}
      <div className="mt-3.5 min-h-[4.5rem]">
        {row.current ? (
          <>
            <p
              className="truncate font-mono text-[15px] font-medium text-foreground"
              title={row.current}
              data-testid={`model-card-current-${row.id}`}
            >
              {row.current}
            </p>
            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
              {state === "missing"
                ? t("local_models.jobs.not_on_disk")
                : [sizeBytes ? formatGb(sizeBytes) : "", caps]
                    .filter(Boolean)
                    .join(" · ") || t("local_models.jobs.no_facts")}
            </p>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            {state === "blocked"
              ? row.note || t("local_models.roles.read_only")
              : t("local_models.jobs.empty")}
          </p>
        )}
        {/* Speech runs with a window sized for this machine; a call is
            judged by its first word, so that number belongs on the card. */}
        {row.id === "voice" && row.context_tokens ? (
          <p
            className="mt-1 text-[11px] leading-snug text-muted-foreground"
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
      </div>

      {/* The memory bar: this model against this machine's graphics memory. */}
      {state !== "blocked" && (
        <div className="mt-2.5" data-testid={`model-card-memory-${row.id}`}>
          <div
            className="h-1.5 overflow-hidden rounded-full bg-sheen/[0.08]"
            role="img"
            aria-label={
              share > 0
                ? fill(t("local_models.jobs.memory_aria"), {
                    percent: Math.round(share * 100),
                  })
                : t("local_models.jobs.memory_unknown")
            }
          >
            <div
              className={cn(
                "h-full rounded-full transition-[width] duration-500 ease-out",
                overBudget ? "bg-amber-500" : "bg-primary",
              )}
              style={{ width: `${Math.min(100, Math.round(share * 100))}%` }}
              data-testid={`model-card-memory-fill-${row.id}`}
            />
          </div>
          <p className="mt-1 text-[10px] tabular-nums text-muted-foreground">
            {share > 0
              ? overBudget
                ? fill(t("local_models.jobs.memory_over"), {
                    gb: acceleratorGb.toFixed(1),
                  })
                : fill(t("local_models.jobs.memory_share"), {
                    percent: Math.round(share * 100),
                    gb: acceleratorGb.toFixed(1),
                  })
              : t("local_models.jobs.memory_unknown")}
          </p>
        </div>
      )}

      {/* The control. Always present, always the same shape. */}
      <div className="mt-3.5 space-y-2">
        {state !== "blocked" ? (
          <RolePicker
            row={row}
            models={models}
            disabled={busy}
            onPick={onPick}
          />
        ) : null}

        {canUseRecommended && (
          <SoftButton
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
              ? fill(t("local_models.jobs.switch_to"), {
                  model: row.recommended,
                })
              : fill(t("local_models.roles.download_recommended"), {
                  model: row.recommended,
                })}
          </SoftButton>
        )}

        {advanced && onTune && row.current && row.installed && (
          <SoftButton
            onClick={() => onTune(row.current)}
            disabled={busy}
            className="h-8 w-full justify-center"
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
            {t("local_models.roles.tune")}
          </SoftButton>
        )}
      </div>

      <CardProgress progress={progress} t={t} />
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
      tone = "text-emerald-600 dark:text-emerald-400";
      text = progress.readback ?? t("local_models.roles.progress_done");
      break;
    default:
      tone = "text-destructive";
      text =
        `${t("local_models.roles.progress_error")} ${progress.message ?? ""}`.trim();
  }
  return (
    <p
      className={cn("mt-2.5 text-[11px] leading-snug", tone)}
      data-testid="role-progress"
    >
      {text}
    </p>
  );
}
