/**
 * The reasoning steps of one turn, the way the Claude desktop app shows
 * them: a muted "Thought for 10s" line that folds and unfolds the list, rows
 * hung on a thin vertical connector, and every step as a line a person can
 * read — "Looking in Wiki · Urlaub", "Creating artifact · Sales deck",
 * "Running skill · daily-brief" — with the feature's own mark in front of
 * it, or the service's brand logo for Gmail, Spotify, an MCP server…
 *
 * Row kinds:
 *   thought  — the sentence the model wrote next to a tool call (its own
 *              words for why it reaches for the tool); muted, italic.
 *   tool     — the call: verb + detail, a spinner while it runs, the time
 *              it took, red with "failed" when it did. Click a finished row
 *              to unfold what went in (arguments) and what came back.
 *   brain / computer / worker / note — the rest of the turn.
 *
 * Two modes share one renderer:
 *   live     — the turn is still running; the list is open, the active row
 *              spins, the header counts the seconds.
 *   finished — folded to the header by default, BUT the tool rows stay
 *              visible under it: what the assistant reached for is the part
 *              worth seeing without a click. Unfolding shows every row and,
 *              when the turn said, which model answered.
 *
 * Presentational only: steps come in as props (the store keeps the live
 * list and the per-message snapshots), time comes in as `durationMs` — the
 * caller ticks. That keeps this usable from the chat column, the voice lane
 * and the history alike, and testable without a store.
 */
import { useId, useState } from "react";
import {
  AppWindow,
  BookMarked,
  BookOpen,
  Bookmark,
  Bot,
  Brain,
  ChevronRight,
  CircleAlert,
  Clock,
  Compass,
  Contact,
  Cpu,
  FilePlus2,
  Globe,
  Info,
  MonitorDot,
  MousePointerClick,
  Phone,
  Plug,
  Puzzle,
  ScanEye,
  Settings2,
  ShieldCheck,
  Sparkles,
  Terminal,
  UserRound,
  Wand2,
  type LucideIcon,
} from "lucide-react";

import type { ThinkingStep, ThinkingStepKind } from "@/lib/thinkingSteps";
import { describeToolStep, type ToolFamily } from "@/lib/toolStepLabel";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

export interface TurnStepsProps {
  steps: ThinkingStep[];
  /** The turn is still running — the last active step shows a live spinner. */
  live?: boolean;
  /** Total thinking time (finished: of the turn; live: elapsed so far), for the header. */
  durationMs?: number;
  /** "provider · model" that answered, shown in the unfolded header of a finished turn. */
  model?: string;
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

const KIND_ICON: Record<Exclude<ThinkingStepKind, "tool" | "thought">, LucideIcon> = {
  brain: Brain,
  computer: MonitorDot,
  worker: Bot,
  note: Info,
};

/** The feature's own mark for a tool row that has no brand logo. */
const FAMILY_ICON: Record<ToolFamily, LucideIcon> = {
  wiki: BookOpen,
  wiki_write: BookMarked,
  artifact: FilePlus2,
  skill: Wand2,
  skill_create: Sparkles,
  web: Globe,
  screen: ScanEye,
  screen_recall: ScanEye,
  control: MousePointerClick,
  navigate: Compass,
  app: AppWindow,
  memory: Bookmark,
  profile: UserRound,
  contact: Contact,
  call: Phone,
  worker: Bot,
  shell: Terminal,
  model: Cpu,
  mcp_admin: Plug,
  settings: Settings2,
  verify: ShieldCheck,
  mcp: Plug,
  service: Puzzle,
  other: Puzzle,
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

/** The gutter box every mark sits in: opaque, so it masks the connector line. */
function markBox(compact: boolean): string {
  return cn(
    "relative z-10 grid shrink-0 place-items-center bg-background",
    compact ? "h-[18px] w-[18px]" : "h-5 w-5",
  );
}

/** The tile at the left of a tool row: the brand SVG, or the family's icon. */
function ToolMark({
  logoUrl,
  Icon,
  label,
  active,
  error,
  compact,
}: {
  logoUrl?: string;
  Icon: LucideIcon;
  label: string;
  active: boolean;
  error: boolean;
  compact: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const showLogo = Boolean(logoUrl) && !failed;
  const size = compact ? "h-3 w-3" : "h-3.5 w-3.5";
  if (showLogo) {
    return (
      <span
        data-testid="turn-step-brand"
        data-brand-tier="logo"
        title={label}
        className={cn(markBox(compact), "overflow-hidden rounded-md")}
      >
        <span aria-hidden className="absolute inset-0 rounded-md border border-sheen/10 bg-sheen/[0.07]" />
        <img
          src={logoUrl}
          alt=""
          className={cn("relative", size)}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      </span>
    );
  }
  return (
    <span data-testid="turn-step-brand" data-brand-tier="icon" title={label} className={markBox(compact)}>
      {active ? (
        <Spinner className={size} />
      ) : (
        <Icon
          aria-hidden
          className={cn(size, error ? "text-destructive" : "text-muted-foreground/80")}
        />
      )}
    </span>
  );
}

/** The gutter mark of a non-tool row: spinner while active, the kind's icon otherwise. */
function KindMark({ step, compact }: { step: ThinkingStep; compact: boolean }) {
  const size = compact ? "h-3 w-3" : "h-3.5 w-3.5";
  const box = markBox(compact);
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
  const Icon = KIND_ICON[step.kind as Exclude<ThinkingStepKind, "tool" | "thought">] ?? Info;
  return (
    <span className={box}>
      <Icon aria-hidden className={cn(size, "text-muted-foreground/70")} />
    </span>
  );
}

/** A muted, italic line in the model's own words — no mark, a quiet dot in the gutter. */
function ThoughtRow({ step, compact }: { step: ThinkingStep; compact: boolean }) {
  return (
    <li
      data-testid="turn-step"
      data-kind="thought"
      data-status={step.status}
      className={cn("relative flex min-w-0 items-start", compact ? "gap-2 py-[3px]" : "gap-2.5 py-1")}
    >
      <span className={markBox(compact)}>
        <span aria-hidden className="h-1 w-1 rounded-full bg-muted-foreground/50" />
      </span>
      <span
        className={cn(
          "min-w-0 flex-1 italic leading-5 text-muted-foreground/85",
          compact ? "text-xs" : "text-[13px]",
        )}
      >
        {step.detail}
      </span>
    </li>
  );
}

/** Arguments / result / error of a finished tool call, shown when a row is unfolded. */
function ToolDetails({ step, compact }: { step: ThinkingStep; compact: boolean }) {
  const t = useT();
  const args = step.args ? Object.entries(step.args) : [];
  return (
    <div
      data-testid="turn-step-details"
      className={cn(
        "mb-1 ml-[30px] mr-1 flex flex-col gap-1 rounded-md border border-border/70 bg-secondary/40 px-2.5 py-2",
        compact ? "text-[11px]" : "text-xs",
      )}
    >
      {args.length > 0 && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5">
          {args.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="font-mono text-muted-foreground/70">{k}</dt>
              <dd className="min-w-0 break-words text-foreground/85">{formatArg(v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {step.result && (
        <div className="flex min-w-0 gap-2">
          <span className="shrink-0 text-muted-foreground/70">{t("turn_steps.result")}</span>
          <span className="min-w-0 break-words text-foreground/85">{step.result}</span>
        </div>
      )}
      {step.error && (
        <div className="flex min-w-0 gap-2">
          <span className="shrink-0 text-destructive/80">{t("turn_steps.error")}</span>
          <span className="min-w-0 break-words text-destructive">{step.error}</span>
        </div>
      )}
      {args.length === 0 && !step.result && !step.error && (
        <span className="text-muted-foreground/70">{t("turn_steps.no_details")}</span>
      )}
    </div>
  );
}

function formatArg(v: unknown): string {
  if (typeof v === "string") return v;
  if (v === null || v === undefined) return "—";
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function ToolRow({ step, compact }: { step: ThinkingStep; compact: boolean }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const active = step.status === "active";
  const error = step.status === "error";
  const view = describeToolStep(step.detail ?? "", step.args);
  const line = view.labelKey ? t(view.labelKey) : view.label;
  const hasDetails = !active && (Boolean(step.args) || Boolean(step.result) || Boolean(step.error));
  const Icon = FAMILY_ICON[view.family];
  const detailsId = useId();

  const body = (
    <>
      <ToolMark
        logoUrl={view.brand?.logoUrl}
        Icon={Icon}
        label={line}
        active={active}
        error={error}
        compact={compact}
      />
      <span
        className={cn(
          "min-w-0 flex-1 truncate leading-5",
          compact ? "text-xs" : "text-[13px]",
          error ? "text-destructive" : "text-foreground/85",
          active && "thinking-shimmer font-medium",
        )}
      >
        {line}
        {view.detail && (
          <span className={cn("ml-1.5 font-normal text-muted-foreground/80")}>· {view.detail}</span>
        )}
      </span>
      {active && <Spinner className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />}
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
      {hasDetails && (
        <ChevronRight
          aria-hidden
          className={cn(
            "h-3 w-3 shrink-0 opacity-0 transition-transform duration-200 group-hover/row:opacity-60 motion-reduce:transition-none",
            open && "rotate-90 opacity-60",
          )}
        />
      )}
    </>
  );

  return (
    <li
      data-testid="turn-step"
      data-kind="tool"
      data-family={view.family}
      data-status={step.status}
      className="relative flex min-w-0 flex-col"
    >
      {hasDetails ? (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={detailsId}
          data-testid="turn-step-toggle"
          className={cn(
            "group/row -mx-1 flex min-w-0 items-center rounded-md px-1 text-left hover:bg-secondary/50",
            compact ? "gap-2 py-[3px]" : "gap-2.5 py-1",
          )}
        >
          {body}
        </button>
      ) : (
        <div className={cn("flex min-w-0 items-center", compact ? "gap-2 py-[3px]" : "gap-2.5 py-1")}>
          {body}
        </div>
      )}
      {hasDetails && open && (
        <div id={detailsId}>
          <ToolDetails step={step} compact={compact} />
        </div>
      )}
    </li>
  );
}

function StepRow({ step, compact }: { step: ThinkingStep; compact: boolean }) {
  const t = useT();
  if (step.kind === "tool") return <ToolRow step={step} compact={compact} />;
  if (step.kind === "thought") return <ThoughtRow step={step} compact={compact} />;
  const active = step.status === "active";
  const error = step.status === "error";

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
      <KindMark step={step} compact={compact} />
      <span
        className={cn(
          "min-w-0 flex-1 truncate leading-5",
          compact ? "text-xs" : "text-[13px]",
          error ? "text-destructive" : "text-muted-foreground",
          active && "thinking-shimmer font-medium",
        )}
      >
        {t(step.labelKey)}
        {step.detail && (
          <span className="ml-1.5 font-normal text-muted-foreground/80">{step.detail}</span>
        )}
        {error && step.error && (
          <span className="ml-1.5 font-normal text-destructive/80">{step.error}</span>
        )}
      </span>
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

/**
 * Is a finished trace worth a line on screen?
 *
 * A turn that only saw the brain call come and go within a second — no tool,
 * no worker, no computer use — has nothing to show, and "Thought for 0s"
 * above an answer is noise, not information (maintainer, 2026-08-23). Live
 * traces are always shown: the person should see the assistant is working.
 */
export function traceWorthShowing(
  steps: ThinkingStep[],
  durationMs: number | undefined,
  live: boolean,
): boolean {
  if (live) return true;
  if (steps.length === 0) return false;
  const substantial = steps.some((s) => s.kind !== "brain" && s.kind !== "note");
  return substantial || (durationMs ?? 0) >= 1000;
}

export function TurnSteps({
  steps,
  live = false,
  durationMs,
  model,
  compact = false,
  defaultOpen,
  className,
}: TurnStepsProps) {
  const t = useT();
  const listId = useId();
  const [open, setOpen] = useState<boolean>(defaultOpen ?? live);

  // A finished turn with nothing observed has nothing to say; a live one
  // still shows its header so the person sees the assistant is working.
  if (!traceWorthShowing(steps, durationMs, live)) return null;

  const toolRows = steps.filter((s) => s.kind === "tool");
  const visible = open ? steps : toolRows;
  const headerText = live
    ? t("thinking.label")
    : thoughtFor(t("turn_steps.thought_for"), formatThoughtDuration(durationMs ?? 0));
  const elapsed = live && durationMs !== undefined ? formatThoughtDuration(durationMs) : null;
  const modelLine = !live && open && model ? model : null;

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
        {modelLine && (
          <span
            data-testid="turn-steps-model"
            className="truncate font-mono text-[10px] text-muted-foreground/60"
          >
            · {modelLine}
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
