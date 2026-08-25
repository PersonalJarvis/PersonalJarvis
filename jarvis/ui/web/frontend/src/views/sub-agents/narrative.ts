/**
 * The one paragraph that explains a run to a person.
 *
 * The event stream knows a run failed with `reason=task_error`,
 * `error_class=provider_quota`, `killed=budget`, `last_state=CRITIQUING`. Read
 * as a table of six labelled facts that is a puzzle; read as ONE sentence —
 * "The worker stopped because the provider (claude) ran out of quota" — it is
 * an answer. This module picks the most specific cause the facts support and
 * turns it into that sentence, plus at most one quotation (what the provider
 * or the reviewer actually said) and a closing note (what was kept).
 *
 * Pure: facts in, i18n keys + variables out. The component only renders.
 */
import { fill } from "@/i18n";
import type { SubAgentNode } from "@/store/jarvisAgents";
import { type AgentOutcome, splitReason, REASON_LABEL_KEYS } from "./outcome";

type T = (key: string) => string;

export interface Narrative {
  /** `running` | `approved` | `failed` | `cancelled` | `timed_out` */
  state: "running" | "approved" | "failed" | "cancelled" | "timed_out";
  headline: string;
  /** The explanation, one to three plain sentences. */
  paragraph: string;
  /** What the provider or the reviewer literally said, when they said anything. */
  quote: { label: string; text: string; mono: boolean } | null;
  /** A closing fact — "3 partial files were kept" — or null. */
  note: string | null;
}

const K = "subagents_view.narrative";

function roundsOf(outcome: AgentOutcome): number {
  return Math.max(outcome.revisions, outcome.verdicts.length > 0 ? outcome.verdicts.at(-1)!.iteration : 0) + 1;
}

/** Also collapses the story's own repetition: the same upstream message can
 * arrive as a progress note, a kill detail and the terminal detail. */
export function composeNarrative(
  agent: SubAgentNode,
  outcome: AgentOutcome,
  ctx: { agentName: string; runtime: string; artifactCount: number; notes: number },
  t: T,
): Narrative {
  const vars = {
    agent: ctx.agentName,
    runtime: ctx.runtime,
    provider: outcome.failed_provider || outcome.workers.at(0)?.cli || t(`${K}.the_provider`),
    n: 0,
  };
  const say = (key: string, extra: Record<string, string | number> = {}) =>
    fill(t(`${K}.${key}`), { ...vars, ...extra });

  const state: Narrative["state"] =
    outcome.terminal ??
    (agent.status === "running"
      ? "running"
      : agent.status === "failed"
        ? "failed"
        : agent.status === "cancelled"
          ? "cancelled"
          : "approved");

  const providerQuote = outcome.error_detail
    ? { label: t(`${K}.quote_provider`), text: outcome.error_detail, mono: true }
    : null;
  const reviewerQuote = outcome.lastVerdict
    ? { label: t(`${K}.quote_reviewer`), text: outcome.lastVerdict.summary, mono: false }
    : null;
  const kept =
    outcome.partial_artifacts.length > 0
      ? say("kept_partial", { n: outcome.partial_artifacts.length })
      : null;

  if (state === "running") {
    return {
      state,
      headline: t("subagents_view.outcome.running"),
      paragraph: ctx.notes > 0 ? say("running_notes", { n: ctx.notes }) : say("running_fresh"),
      quote: outcome.lastCorrection
        ? { label: t(`${K}.quote_reviewer`), text: outcome.lastCorrection.correction_instruction, mono: false }
        : null,
      note: null,
    };
  }

  if (state === "approved") {
    const rounds = roundsOf(outcome);
    const reviewed = outcome.verdicts.length > 0;
    const base = outcome.summary || say("approved_plain", { n: ctx.artifactCount });
    const tail = reviewed
      ? rounds > 1
        ? say("approved_after_rounds", { n: rounds })
        : say("approved_first_round")
      : "";
    return {
      state,
      headline: t("subagents_view.outcome.approved"),
      paragraph: tail ? `${base} ${tail}` : base,
      quote: null,
      note: ctx.artifactCount > 0 ? say("delivered_files", { n: ctx.artifactCount }) : null,
    };
  }

  if (state === "cancelled") {
    const { head } = splitReason(outcome.reason);
    const paragraph =
      head === "ui_cancel" || head === "user_cancelled" || head === "user"
        ? say("cancelled_by_you")
        : head === "parent_cancelled"
          ? say("cancelled_parent")
          : say("cancelled_other", { reason: reasonText(outcome.reason, t) });
    return { state, headline: t("subagents_view.outcome.cancelled"), paragraph, quote: null, note: kept };
  }

  if (state === "timed_out") {
    return {
      state,
      headline: t("subagents_view.outcome.timed_out"),
      paragraph: say("timed_out"),
      quote: reviewerQuote,
      note: kept,
    };
  }

  // Failed — the most specific cause the facts support, in this order:
  // a classified provider failure, a safety or runtime kill, a review
  // outcome, a mission-level reason, then the generic sentence.
  const kill = outcome.kills.at(-1) ?? null;
  const { head, tail } = splitReason(outcome.reason);
  let paragraph: string;
  let quote: Narrative["quote"] = null;

  switch (outcome.error_class) {
    case "provider_quota":
      paragraph = say("failed_provider_quota");
      quote = providerQuote;
      break;
    case "provider_auth":
      paragraph = say("failed_provider_auth");
      quote = providerQuote;
      break;
    case "provider_unreachable":
      paragraph = say("failed_provider_unreachable");
      quote = providerQuote;
      break;
    case "worker_timeout":
      paragraph = say("failed_worker_timeout");
      quote = providerQuote;
      break;
    default:
      if (kill?.reason === "injection_detected") {
        paragraph = say("failed_injection");
      } else if (kill?.reason === "path_guard") {
        paragraph = say("failed_path_guard");
      } else if (kill?.reason === "timeout" || head === "attempts_timed_out") {
        paragraph = say("failed_timeout");
      } else if (kill?.reason === "budget" || head === "budget_exceeded") {
        paragraph = say("failed_budget");
        quote = providerQuote;
      } else if (head === "review_time_budget_exhausted") {
        paragraph = say("failed_review_time");
        quote = reviewerQuote;
      } else if (head === "critic_loop_exhausted") {
        paragraph = say("failed_review_loop", { n: roundsOf(outcome) });
        quote = reviewerQuote;
      } else if (head === "critic_rejected") {
        paragraph = say("failed_review_rejected");
        quote = reviewerQuote;
      } else if (head === "critic_unavailable") {
        paragraph = say("failed_no_reviewer");
      } else if (head === "worktree_setup_failed") {
        paragraph = say("failed_workspace");
      } else if (head === "decompose_failed") {
        paragraph = say("failed_plan", { detail: tail ?? "" }).trim();
      } else if (head === "interrupted") {
        paragraph = say("failed_interrupted");
      } else if (kill?.reason === "worker_error") {
        paragraph = say("failed_worker_error");
        quote = providerQuote;
      } else if (outcome.lastVerdict && outcome.lastVerdict.verdict !== "approve") {
        paragraph = say("failed_after_review");
        quote = reviewerQuote;
      } else {
        paragraph = say("failed_generic");
        quote = providerQuote;
      }
  }

  return { state, headline: t("subagents_view.outcome.failed"), paragraph, quote, note: kept };
}

/** Human label for a raw reason token, falling back to the token itself. */
export function reasonText(reason: string | null | undefined, t: T): string {
  const { head, tail } = splitReason(reason);
  if (!head) return "";
  const key = REASON_LABEL_KEYS[head];
  const label = key ? t(key) : head;
  return tail ? `${label} (${tail})` : label;
}
