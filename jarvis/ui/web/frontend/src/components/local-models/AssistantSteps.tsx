/**
 * The assistant's work, readable — and out of the way once it is done.
 *
 * The shared `AgentTimeline` is built for a coding agent: it shows a tool call
 * as its raw name (`lm_inventory`) and a JSON body, which is the right answer
 * when the reader writes code and the wrong one here — this panel is opened by
 * someone who wants to know what the helper is doing to their machine.
 *
 * So every step gets one plain sentence ("Read the installed models"), a mark
 * that carries its state, and its duration. While the turn runs the list is
 * open, because watching it is the point; once the turn is finished it folds
 * into one line ("Checked 5 things · 2.6s") so the answer, not the machinery,
 * is what the finished conversation reads as. The raw call and result stay one
 * click away for the reader who does want them.
 *
 * A question the runner asks that no confirmed plan covers is the one step
 * that can act: it renders its two buttons inline and keeps the group open.
 */
import { useEffect, useState } from "react";
import { Check, ChevronRight, Loader2, X } from "lucide-react";

import type { ReasoningBlock, ToolBlock, TurnBlock } from "@/components/agentchat/reduce";
import { fill, useT } from "@/i18n";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import { cn } from "@/lib/utils";
import "@/components/agentchat/WorkTrace.css";

/** Tool name → i18n key of its sentence. Unknown tools fall back to the name. */
const STEP_KEYS: Record<string, string> = {
  lm_hardware: "hardware",
  lm_server_status: "server_status",
  lm_inventory: "inventory",
  lm_roles: "roles",
  lm_recommendations: "recommendations",
  lm_catalog_search: "catalog_search",
  lm_catalog_tags: "catalog_tags",
  lm_hf_search: "hf_search",
  lm_benchmarks: "benchmarks",
  lm_pull_status: "pull_status",
  lm_server_log: "server_log",
  lm_env_guide: "env_guide",
  lm_suggested_options: "suggested_options",
  lm_test_plan: "test_plan",
  lm_install_ollama: "install_ollama",
  lm_start_server: "start_server",
  lm_stop_server: "stop_server",
  lm_pull: "pull",
  lm_set_role: "set_role",
  lm_set_model_options: "set_options",
  lm_unload: "unload",
  lm_install_voice_server: "install_voice",
  lm_apply_voice_stack: "apply_voice",
  search_web: "search_web",
};

type Tone = "run" | "done" | "fail" | "ask";

function argOf(input: unknown, key: string): string {
  if (!input || typeof input !== "object") return "";
  const value = (input as Record<string, unknown>)[key];
  return typeof value === "string" ? value : "";
}

/** Seconds with one decimal, so a fast step does not read as "0". */
function seconds(ms: number | null): string {
  if (ms === null) return "";
  return `${(ms / 1000).toFixed(1)}s`;
}

function toneOf(block: TurnBlock): Tone {
  if (block.kind === "reasoning") return block.live ? "run" : "done";
  if (block.kind !== "tool") return "done";
  if (block.approval && !block.approval.decision) return "ask";
  if (block.isError) return "fail";
  return block.output === null ? "run" : "done";
}

/** The step's node on the rail (components/agentchat/WorkTrace.css). */
function Mark({ tone }: { tone: Tone }) {
  return (
    <span aria-hidden className={cn("trace-node", tone === "run" && "trace-node-live")}>
      {tone === "run" ? <span className="trace-dot" />
        : tone === "fail" ? <X className="h-3.5 w-3.5 text-destructive" />
        : tone === "ask" ? <span className="trace-dot text-foreground" />
        : <Check className="h-3.5 w-3.5" />}
    </span>
  );
}

function StepRow({
  tone,
  label,
  meta,
  detail,
  children,
}: {
  tone: Tone;
  label: string;
  meta?: string;
  detail?: string;
  children?: React.ReactNode;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(detail && detail.trim());
  return (
    <li className="trace-step group/step pb-0.5" data-testid="assistant-step" data-tone={tone}>
      <div className="flex items-start gap-3 py-1 text-sm leading-6">
        <Mark tone={tone} />
        <span
          className={cn(
            "min-w-0",
            tone === "fail" ? "text-destructive" : "text-foreground-secondary",
          )}
        >
          {tone === "run" ? <span className="trace-shimmer">{label}</span> : label}
        </span>
        {meta && (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {meta}
          </span>
        )}
        {hasDetail && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className={cn(
              "ml-auto shrink-0 rounded px-1 text-xs text-muted-foreground",
              "transition-colors group-hover/step:text-muted-foreground hover:!text-foreground",
              "focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              open && "!text-muted-foreground",
            )}
          >
            {open ? t("local_models.assistant.steps.hide") : t("local_models.assistant.steps.show")}
          </button>
        )}
      </div>
      {open && hasDetail && (
        <pre className="mb-1.5 ml-7 max-h-56 overflow-auto whitespace-pre-wrap rounded-md bg-muted/60 px-3 py-2 font-mono text-xs leading-5 text-foreground-secondary [overflow-wrap:anywhere]">
          {detail}
        </pre>
      )}
      {children}
    </li>
  );
}

function ToolStep({
  block,
  onDecide,
}: {
  block: ToolBlock;
  onDecide?: (approvalId: string, decision: ApprovalDecision) => void;
}) {
  const t = useT();
  const key = STEP_KEYS[block.name];
  const label = key
    ? fill(t(`local_models.assistant.steps.${key}`), {
        model: argOf(block.input, "model") || argOf(block.input, "name"),
        role: argOf(block.input, "role"),
        query: argOf(block.input, "q") || argOf(block.input, "query"),
      })
        .replace(/\s{2,}/g, " ")
        .trim()
    : block.name;

  const tone = toneOf(block);
  const detail = [
    block.input && Object.keys(block.input as object).length
      ? JSON.stringify(block.input, null, 1)
      : "",
    block.output ?? "",
  ]
    .filter(Boolean)
    .join("\n\n");

  return (
    <StepRow tone={tone} label={label} meta={seconds(block.durationMs)} detail={detail}>
      {tone === "ask" && block.approval && onDecide && (
        <div className="mb-1.5 ml-7 mt-1 flex flex-wrap items-center gap-3">
          <span className="text-meta text-foreground">{block.approval.summary}</span>
          <div className="ml-auto flex gap-1.5">
            <button
              type="button"
              onClick={() => onDecide(block.approval!.approvalId, "allow")}
              className="rounded-md bg-primary px-2.5 py-1 text-meta font-medium text-primary-foreground hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t("local_models.assistant.steps.allow")}
            </button>
            <button
              type="button"
              onClick={() => onDecide(block.approval!.approvalId, "deny")}
              className="rounded-md border border-border px-2.5 py-1 text-meta hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t("local_models.assistant.steps.deny")}
            </button>
          </div>
        </div>
      )}
    </StepRow>
  );
}

function ThinkingStep({ block }: { block: ReasoningBlock }) {
  const t = useT();
  const label = block.live
    ? t("local_models.assistant.steps.thinking_live")
    : fill(t("local_models.assistant.steps.thinking"), { s: seconds(block.durationMs) || "—" });
  return <StepRow tone={block.live ? "run" : "done"} label={label} detail={block.text} />;
}

/**
 * The work of one turn: open while it happens, one summary line once it is
 * done. Text blocks are NOT rendered here — the answer is the panel's own
 * prose, so the steps stay a list of work and the words stay words.
 */
export function AssistantSteps({
  blocks,
  live,
  onDecide,
}: {
  blocks: readonly TurnBlock[];
  /** The turn is still running: the group stays open and cannot be folded. */
  live?: boolean;
  onDecide?: (approvalId: string, decision: ApprovalDecision) => void;
}) {
  const t = useT();
  const steps = blocks.filter((b) => b.kind !== "text");
  const asking = steps.some((b) => toneOf(b) === "ask");
  const failed = steps.some((b) => toneOf(b) === "fail");
  const [open, setOpen] = useState(true);

  // Fold when the turn ends — unless something still wants the reader's eye.
  useEffect(() => {
    if (!live && !asking && !failed) setOpen(false);
    if (live || asking || failed) setOpen(true);
  }, [live, asking, failed]);

  if (steps.length === 0) return null;

  const total = steps.reduce((ms, b) => ms + (b.durationMs ?? 0), 0);
  const pinned = Boolean(live || asking || failed);
  const summary = fill(t("local_models.assistant.steps.summary"), {
    n: String(steps.length),
    s: seconds(total) || "—",
  });

  return (
    <div data-testid="assistant-steps" data-open={open ? "yes" : "no"}>
      {/* Collapsed, this is one quiet line the eye can skip — the answer is
          what the finished conversation is for. Open, the rail carries the
          work so it reads as a sequence rather than a paragraph of nouns. */}
      <button
        type="button"
        onClick={() => !pinned && setOpen((v) => !v)}
        aria-expanded={open}
        disabled={pinned}
        className={cn(
          "inline-flex items-center gap-2 rounded-full border border-border py-1 pl-2.5 pr-2",
          "text-xs leading-5 text-muted-foreground transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          !pinned && "hover:border-border-strong hover:bg-secondary hover:text-foreground",
        )}
      >
        {live ? (
          <Loader2 className="h-3 w-3 shrink-0 text-accent motion-safe:animate-spin" />
        ) : (
          <span aria-hidden className="trace-dot shrink-0" />
        )}
        <span className={live ? "trace-shimmer" : undefined}>
          {live ? t("local_models.assistant.steps.working") : summary}
        </span>
        <ChevronRight
          className={cn(
            "h-3 w-3 shrink-0 opacity-60 transition-transform",
            open && "rotate-90",
            pinned && "hidden",
          )}
        />
      </button>
      {open && (
        <ul className="trace-rail mt-1.5 pl-[5px]">
          {steps.map((block) =>
            block.kind === "tool" ? (
              <ToolStep key={block.callId} block={block} onDecide={onDecide} />
            ) : (
              <ThinkingStep key={block.id} block={block as ReasoningBlock} />
            ),
          )}
        </ul>
      )}
    </div>
  );
}
