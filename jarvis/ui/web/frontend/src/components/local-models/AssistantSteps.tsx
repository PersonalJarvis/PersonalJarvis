/**
 * The assistant's work, readable.
 *
 * The shared `AgentTimeline` is built for a coding agent: it shows a tool call
 * as its raw name (`lm_inventory`) and a JSON body, which is the right answer
 * when the reader writes code and the wrong one here — this panel is opened by
 * someone who wants to know what the helper is doing to their machine.
 *
 * So every step gets one plain sentence ("Read the installed models"), a dot
 * that carries its state, and its duration; the raw call and result stay one
 * click away for the reader who does want them. Thinking is one quiet line.
 * A question the runner asks (an approval that is not covered by a confirmed
 * plan) is the one step that can act: it renders its two buttons inline.
 */
import { useState } from "react";
import { Check, ChevronRight, Loader2, X } from "lucide-react";

import type { ReasoningBlock, ToolBlock, TurnBlock } from "@/components/agentchat/reduce";
import { fill, useT } from "@/i18n";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import { cn } from "@/lib/utils";

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

function StepShell({
  tone,
  icon,
  label,
  meta,
  detail,
  children,
}: {
  tone: "run" | "done" | "fail" | "ask";
  icon: React.ReactNode;
  label: string;
  meta?: string;
  detail?: string;
  children?: React.ReactNode;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(detail && detail.trim());
  return (
    <li className="relative pl-6" data-testid="assistant-step" data-tone={tone}>
      {/* The rail: one continuous line through every step of a turn. */}
      <span
        aria-hidden="true"
        className="absolute left-[7px] top-5 bottom-[-6px] w-px bg-border last:hidden"
      />
      <span
        aria-hidden="true"
        className={cn(
          "absolute left-0 top-[3px] flex h-3.5 w-3.5 items-center justify-center rounded-full",
          tone === "done" && "text-emerald-600 dark:text-emerald-400",
          tone === "run" && "text-primary",
          tone === "fail" && "text-destructive",
          tone === "ask" && "text-amber-600 dark:text-amber-400",
        )}
      >
        {icon}
      </span>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className={cn("text-[13.5px]", tone === "fail" ? "text-destructive" : "text-foreground")}>
          {label}
        </span>
        {meta && <span className="font-mono text-[11px] tabular-nums text-muted-foreground">{meta}</span>}
        {hasDetail && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="inline-flex items-center gap-0.5 rounded text-[11px] text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
            {open ? t("local_models.assistant.steps.hide") : t("local_models.assistant.steps.show")}
          </button>
        )}
      </div>
      {open && hasDetail && (
        <pre className="mt-1.5 max-h-48 overflow-auto rounded-md border border-border bg-muted/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
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
      }).replace(/\s{2,}/g, " ").trim()
    : block.name;

  const waiting = Boolean(block.approval && !block.approval.decision);
  const tone: "run" | "done" | "fail" | "ask" = waiting
    ? "ask"
    : block.isError
      ? "fail"
      : block.output === null
        ? "run"
        : "done";
  const icon =
    tone === "run" ? (
      <Loader2 className="h-3 w-3 animate-spin" />
    ) : tone === "fail" ? (
      <X className="h-3 w-3" />
    ) : tone === "ask" ? (
      <span className="h-2 w-2 rounded-full bg-current" />
    ) : (
      <Check className="h-3 w-3" />
    );

  const detail = [
    block.input && Object.keys(block.input as object).length
      ? JSON.stringify(block.input, null, 1)
      : "",
    block.output ?? "",
  ]
    .filter(Boolean)
    .join("\n\n");

  return (
    <StepShell
      tone={tone}
      icon={icon}
      label={label}
      meta={seconds(block.durationMs)}
      detail={detail}
    >
      {waiting && block.approval && onDecide && (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-2">
          <span className="text-[13px] text-foreground">{block.approval.summary}</span>
          <div className="ml-auto flex gap-1.5">
            <button
              type="button"
              onClick={() => onDecide(block.approval!.approvalId, "allow")}
              className="rounded-md bg-primary px-2.5 py-1 text-[12px] font-medium text-primary-foreground hover:opacity-90 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              {t("local_models.assistant.steps.allow")}
            </button>
            <button
              type="button"
              onClick={() => onDecide(block.approval!.approvalId, "deny")}
              className="rounded-md border border-border px-2.5 py-1 text-[12px] hover:bg-muted focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              {t("local_models.assistant.steps.deny")}
            </button>
          </div>
        </div>
      )}
    </StepShell>
  );
}

function ThinkingStep({ block }: { block: ReasoningBlock }) {
  const t = useT();
  const label = block.live
    ? t("local_models.assistant.steps.thinking_live")
    : fill(t("local_models.assistant.steps.thinking"), { s: seconds(block.durationMs) || "—" });
  return (
    <StepShell
      tone={block.live ? "run" : "done"}
      icon={
        block.live ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <span className="h-1.5 w-1.5 rounded-full bg-current" />
        )
      }
      label={label}
      detail={block.text}
    />
  );
}

/**
 * The steps of one turn. Text blocks are NOT rendered here — the answer is the
 * panel's own paragraph, so the steps stay a list of work and the words stay
 * prose.
 */
export function AssistantSteps({
  blocks,
  onDecide,
}: {
  blocks: readonly TurnBlock[];
  onDecide?: (approvalId: string, decision: ApprovalDecision) => void;
}) {
  const steps = blocks.filter((b) => b.kind !== "text");
  if (steps.length === 0) return null;
  return (
    <ul className="flex flex-col gap-2.5" data-testid="assistant-steps">
      {steps.map((block) =>
        block.kind === "tool" ? (
          <ToolStep key={block.callId} block={block} onDecide={onDecide} />
        ) : (
          <ThinkingStep key={block.id} block={block as ReasoningBlock} />
        ),
      )}
    </ul>
  );
}
