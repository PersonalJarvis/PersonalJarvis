/**
 * The reasoning steps of one turn, the way the Claude desktop app shows
 * them: a muted "Thought for 10s" line that folds and unfolds the list, rows
 * hung on a thin vertical connector, and tool calls as rows carrying the
 * service's brand mark in a small tile. Not the model's thinking text — the
 * maintainer asked for the steps and the tool calls, nothing more.
 *
 * Two modes share one renderer:
 *   live     — the turn is still running; the list is open, the active row
 *              spins, the header counts the seconds.
 *   finished — folded to the header by default, BUT the tool rows stay
 *              visible under it: what the assistant reached for is the part
 *              worth seeing without a click. Unfolding shows every row.
 *
 * Presentational only: steps come in as props (the store keeps the live
 * list and the per-message snapshots), time comes in as `durationMs` — the
 * caller ticks. That keeps this usable from the chat column and the voice
 * lane alike, and testable without a store.
 */
import { useId, useState } from "react";
import {
  Bot,
  Brain,
  ChevronRight,
  CircleAlert,
  Clock,
  Info,
  MonitorDot,
  type LucideIcon,
} from "lucide-react";

import type { ThinkingStep, ThinkingStepKind } from "@/lib/thinkingSteps";
import { resolveToolBrand } from "@/lib/toolBrand";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

export interface TurnStepsProps {
  steps: ThinkingStep[];
  /** The turn is still running — the last active step shows a live spinner. */
  live?: boolean;
  /** Total thinking time (finished: of the turn; live: elapsed so far), for the header. */
  durationMs?: number;
  /** Tighter rows (the voice lane). */
  compact?: boolean;
  /** Finished traces start folded; live ones start open. */
  defaultOpen?: boolean;
  className?: string;
}

/** Whole seconds like the Claude app: "10s", "1m 05s"; never "0s" for a real turn. */
export function formatThoughtDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  if (total < 60) return `${Math.max(total, ms > 0 ? 1 : 0)}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

/** Per-row durations keep tenths under ten seconds — a 0.4s tool call is not "0s". */
function formatStepDuration(ms: number): string {
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  return formatThoughtDuration(ms);
}

const KIND_ICON: Record<Exclude<ThinkingStepKind, "tool">, LucideIcon> = {
  brain: Brain,
  computer: MonitorDot,
  worker: Bot,
  note: Info,
};

/**
 * The small spinner shown on an active row. A plain ring instead of an
 * animated SVG so it stays crisp at 14px; under reduced motion it stands
 * still as a ring, which still reads as "open".
 */
function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      data-testid="turn-step-spinner"
      className={cn(
        "inline-block rounded-full border-2 border-primary/25 border-t-primary motion-safe:animate-spin",
        className,
      )}
    />
  );
}

/** The tile at the left of a tool row: the brand SVG, or a two-letter monogram. */
function BrandTile({
  logoUrl,
  monogram,
  label,
  compact,
}: {
  logoUrl?: string;
  monogram: string;
  label: string;
  compact: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const showLogo = Boolean(logoUrl) && !failed;
  return (
    <span
      data-testid="turn-step-brand"
      data-brand-tier={showLogo ? "logo" : "monogram"}
      title={label}
      className={cn(
        // Opaque page background first, so the tile masks the connector line
        // behind it; the tint is a translucent plate on top of that.
        "relative z-10 grid shrink-0 place-items-center overflow-hidden rounded-md bg-background",
        compact ? "h-[18px] w-[18px]" : "h-5 w-5",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-0 rounded-md border",
          // A full-colour mark sits on a light plate (its own colours carry the
          // brand); the monogram sits on the page's own secondary surface.
          showLogo ? "border-sheen/10 bg-sheen/[0.07]" : "border-border/70 bg-secondary",
        )}
      />
      {showLogo ? (
        <img
          src={logoUrl}
          alt=""
          className={cn("relative", compact ? "h-3 w-3" : "h-3.5 w-3.5")}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="relative font-mono text-[8px] font-semibold leading-none text-muted-foreground">
          {monogram}
        </span>
      )}
    </span>
  );
}

/** The gutter mark of a non-tool row: spinner while active, the kind's icon otherwise. */
function KindMark({ step, compact }: { step: ThinkingStep; compact: boolean }) {
  const size = compact ? "h-3 w-3" : "h-3.5 w-3.5";
  const box = cn(
    "relative z-10 grid shrink-0 place-items-center bg-background",
    compact ? "h-[18px] w-[18px]" : "h-5 w-5",
  );
  if (step.status === "active") {
    return (
      <span className={box}>
        <Spinner className={size} />
      </span>
    );
  }
  if (step.status === "error") {
    return (
      <span className={box}>
        <CircleAlert aria-hidden className={cn(size, "text-destructive")} />
      </span>
    );
  }
  const Icon = KIND_ICON[step.kind as Exclude<ThinkingStepKind, "tool">] ?? Info;
  return (
    <span className={box}>
      <Icon aria-hidden className={cn(size, "text-muted-foreground/70")} />
    </span>
  );
}

function StepRow({ step, compact }: { step: ThinkingStep; compact: boolean }) {
  const t = useT();
  const isTool = step.kind === "tool";
  const active = step.status === "active";
  const error = step.status === "error";
  const brand = isTool ? resolveToolBrand(step.detail ?? "") : null;

  return (
    <li
      data-testid="turn-step"
      data-kind={step.kind}
      data-status={step.status}
      className={cn(
        "relative flex min-w-0 items-center",
        compact ? "gap-2 py-[3px]" : "gap-2.5 py-1",
      )}
    >
      {isTool && brand ? (
        <BrandTile
          logoUrl={brand.logoUrl}
          monogram={brand.monogram}
          label={brand.label}
          compact={compact}
        />
      ) : (
        <KindMark step={step} compact={compact} />
      )}

      <span
        className={cn(
          "min-w-0 flex-1 truncate leading-5",
          compact ? "text-xs" : "text-[13px]",
          error ? "text-destructive" : isTool ? "text-foreground/85" : "text-muted-foreground",
          active && !isTool && "thinking-shimmer font-medium",
        )}
      >
        {isTool && brand ? (
          <>
            {brand.label}
            {step.detail && (
              <span className="ml-2 font-mono text-[10px] text-muted-foreground/70">
                {step.detail}
              </span>
            )}
          </>
        ) : (
          <>
            {t(step.labelKey)}
            {step.detail && (
              <span className="ml-1.5 font-normal text-muted-foreground/80">{step.detail}</span>
            )}
          </>
        )}
      </span>

      {isTool && active && <Spinner className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />}
      {error && (
        <span className="shrink-0 text-[10px] uppercase tracking-[0.08em] text-destructive/80">
          {t("turn_steps.failed")}
        </span>
      )}
      {!active && step.durationMs !== undefined && step.durationMs > 0 && (
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/60">
          {formatStepDuration(step.durationMs)}
        </span>
      )}
    </li>
  );
}

export function TurnSteps({
  steps,
  live = false,
  durationMs,
  compact = false,
  defaultOpen,
  className,
}: TurnStepsProps) {
  const t = useT();
  const listId = useId();
  const [open, setOpen] = useState<boolean>(defaultOpen ?? live);

  // A finished turn with nothing observed has nothing to say; a live one
  // still shows its header so the person sees the assistant is working.
  if (!live && steps.length === 0) return null;

  const toolRows = steps.filter((s) => s.kind === "tool");
  const visible = open ? steps : toolRows;
  const headerText = live
    ? t("thinking.label")
    : thoughtFor(t("turn_steps.thought_for"), formatThoughtDuration(durationMs ?? 0));
  const elapsed = live && durationMs !== undefined ? formatThoughtDuration(durationMs) : null;

  return (
    <div
      data-testid="turn-steps"
      data-live={live ? "true" : "false"}
      data-open={open ? "true" : "false"}
      className={cn("flex min-w-0 flex-col", className)}
      role={live ? "status" : undefined}
      aria-live={live ? "polite" : undefined}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={listId}
        data-testid="turn-steps-toggle"
        className={cn(
          "group -ml-1 flex w-fit max-w-full items-center gap-1.5 rounded-md px-1 text-left text-muted-foreground transition-colors hover:text-foreground",
          compact ? "py-0.5 text-xs" : "py-1 text-[13px]",
        )}
      >
        {live ? (
          <Spinner className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />
        ) : (
          <Clock aria-hidden className={cn("shrink-0", compact ? "h-3 w-3" : "h-3.5 w-3.5")} />
        )}
        <span className={cn("truncate", live && "thinking-shimmer font-medium")}>{headerText}</span>
        {elapsed && (
          <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground/70">
            {elapsed}
          </span>
        )}
        <ChevronRight
          aria-hidden
          className={cn(
            "h-3 w-3 shrink-0 opacity-60 transition-transform duration-200 motion-reduce:transition-none group-hover:opacity-100",
            open && "rotate-90",
          )}
        />
      </button>

      {visible.length > 0 && (
        <ol
          id={listId}
          data-testid="turn-steps-list"
          data-folded={open ? "false" : "true"}
          className={cn("relative", compact ? "ml-0.5 mt-0.5" : "ml-0.5 mt-1")}
        >
          {/* The connector: one thin line behind the gutter marks, which mask
              it with their own background so it reads as joining them. */}
          <span
            aria-hidden
            className={cn(
              "absolute bottom-2 top-2 w-px bg-border",
              compact ? "left-[8.5px]" : "left-[9.5px]",
            )}
          />
          {visible.map((s) => (
            <StepRow key={s.id} step={s} compact={compact} />
          ))}
        </ol>
      )}
    </div>
  );
}

/**
 * "Thought for {duration}" — the locale decides the word order. Should a
 * locale (or an un-hydrated test) hand back a template without the token,
 * the duration is still appended so the number is never lost.
 */
function thoughtFor(template: string, duration: string): string {
  return template.includes("{duration}")
    ? fill(template, { duration })
    : `${template} ${duration}`;
}
