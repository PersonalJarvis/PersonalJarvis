/**
 * "Are these the same person?" — the one question the identity layer refuses
 * to answer on its own.
 *
 * A shared e-mail, phone number or address-book entry is proof, and the layer
 * merges on it silently. A similar NAME is not: "Alex Rivera" and "A. Rivera"
 * are the same person often enough to be worth asking about and different
 * often enough that guessing corrupts the knowledge base. Everything in this
 * queue is that second case, and nothing in it has been merged.
 *
 * The two answers are deliberately NOT symmetrical, because the actions are
 * not:
 *
 * - "one person" merges, and the merge is reversible — one click, and the
 *   answer even hands back the undo handle.
 * - "two people" is PERMANENT. There is no un-reject route by design: a
 *   rejected pair is never proposed again, which is exactly what keeps the
 *   queue from asking the same settled question forever. So it arms first and
 *   says what it costs, rather than turning one stray click into a decision
 *   that outranks every later piece of evidence.
 *
 * A refusal from the store (409) is printed verbatim on the card it came
 * from: "undo merge 12 first" is the entire value of that answer, and a
 * re-worded version of it would be a second opinion on a question the store
 * already answered.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, Users, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import {
  confirmIdentityMerge,
  evidenceParts,
  identityErrorMessage,
  rejectIdentityMerge,
  scorePercent,
  tierKey,
  type UltraWikiMatchEvidence,
  type UltraWikiMergeProposal,
} from "@/lib/ultrawikiIdentityApi";

/**
 * Every identity query hangs under this prefix, so one decision can
 * invalidate the People list, the open profile and the queue in a single
 * call — a confirmed merge changes all three at once, and a screen where two
 * of them agree and the third does not is worse than a slow refresh.
 */
export const IDENTITY_QUERY_KEY = ["ultrawiki", "identity"] as const;

export interface MergeQueuePanelProps {
  proposals: UltraWikiMergeProposal[];
  /** Jump to one side of a pair — deciding often needs the full profile. */
  onOpenPerson?: (entityId: number) => void;
  /** Rendered when the queue is empty; the caller words its own emptiness. */
  emptyMessage?: string;
  className?: string;
}

export function MergeQueuePanel({
  proposals,
  onOpenPerson,
  emptyMessage,
  className,
}: MergeQueuePanelProps): JSX.Element {
  const t = useT();

  if (proposals.length === 0) {
    return (
      <div
        data-testid="identity-queue-empty"
        className={cn(
          "rounded-xl border border-dashed border-border/70 bg-card/30 p-4 text-xs text-muted-foreground",
          className,
        )}
      >
        {emptyMessage ?? t("ultrawiki.people.queue_empty")}
      </div>
    );
  }

  return (
    <ul
      data-testid="identity-queue"
      className={cn("space-y-2.5", className)}
    >
      {proposals.map((proposal) => (
        <MergeProposalCard
          key={proposal.id}
          proposal={proposal}
          onOpenPerson={onOpenPerson}
        />
      ))}
    </ul>
  );
}

export interface MergeProposalCardProps {
  proposal: UltraWikiMergeProposal;
  onOpenPerson?: (entityId: number) => void;
}

export function MergeProposalCard({
  proposal,
  onOpenPerson,
}: MergeProposalCardProps): JSX.Element {
  const t = useT();
  const queryClient = useQueryClient();
  // The reject button arms before it fires. Not confirmation theatre: this is
  // the only identity action with no way back.
  const [arming, setArming] = useState(false);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: IDENTITY_QUERY_KEY });

  const confirmMutation = useMutation({
    mutationFn: () => confirmIdentityMerge(proposal.id),
    onSuccess: () => void invalidate(),
  });

  const rejectMutation = useMutation({
    mutationFn: () => rejectIdentityMerge(proposal.id),
    onSuccess: () => {
      setArming(false);
      void invalidate();
    },
  });

  const busy = confirmMutation.isPending || rejectMutation.isPending;
  const failure = confirmMutation.error ?? rejectMutation.error;
  const percent = scorePercent(proposal.score);

  return (
    <li
      data-testid={`merge-proposal-${proposal.id}`}
      data-score={String(percent)}
      className="rounded-xl border border-border/70 bg-card/40 px-3.5 py-3"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Users className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          {t("ultrawiki.people.queue_question")}
        </span>
        <span className="flex-1" />
        <span
          data-testid={`merge-score-${proposal.id}`}
          className="shrink-0 rounded-full border border-border px-2 py-0.5 text-[10px] tabular-nums text-muted-foreground"
        >
          {t("ultrawiki.people.match_strength").replace("{0}", String(percent))}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <ProposalSide side={proposal.left} onOpenPerson={onOpenPerson} />
        <span className="text-xs text-muted-foreground" aria-hidden>
          ↔
        </span>
        <ProposalSide side={proposal.right} onOpenPerson={onOpenPerson} />
      </div>

      <EvidenceList evidence={proposal.evidence} proposalId={proposal.id} />

      {arming ? (
        <div
          data-testid={`merge-reject-armed-${proposal.id}`}
          className="mt-2.5 rounded-lg border border-destructive/40 bg-destructive/5 px-2.5 py-2"
        >
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("ultrawiki.people.reject_warning")}
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <button
              type="button"
              data-testid={`merge-reject-confirm-${proposal.id}`}
              onClick={() => rejectMutation.mutate()}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded-md border border-destructive/50 px-2.5 py-1 text-[11px] text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-60"
            >
              {rejectMutation.isPending && (
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              )}
              {t("ultrawiki.people.reject_final")}
            </button>
            <button
              type="button"
              data-testid={`merge-reject-cancel-${proposal.id}`}
              onClick={() => setArming(false)}
              disabled={busy}
              className="rounded-md border border-border px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-60"
            >
              {t("ultrawiki.people.cancel")}
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <button
            type="button"
            data-testid={`merge-confirm-${proposal.id}`}
            onClick={() => confirmMutation.mutate()}
            disabled={busy}
            className="inline-flex items-center gap-1 rounded-md border border-primary/50 bg-primary/10 px-2.5 py-1 text-[11px] text-foreground transition-colors hover:bg-primary/20 disabled:opacity-60"
          >
            {confirmMutation.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <Check className="h-3 w-3" aria-hidden />
            )}
            {t("ultrawiki.people.same_person")}
          </button>
          <button
            type="button"
            data-testid={`merge-reject-${proposal.id}`}
            onClick={() => setArming(true)}
            disabled={busy}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-60"
          >
            <X className="h-3 w-3" aria-hidden />
            {t("ultrawiki.people.different_people")}
          </button>
        </div>
      )}

      {confirmMutation.data && (
        <p
          data-testid={`merge-confirmed-${proposal.id}`}
          className="mt-1.5 text-[11px] text-muted-foreground"
        >
          {/* merge_id 0 = the pair had already become one entity by other
              evidence, so there is nothing to reverse and saying "undo it
              under the profile" would be a lie. */}
          {confirmMutation.data.merge_id > 0
            ? t("ultrawiki.people.merged_undoable")
            : t("ultrawiki.people.merged_already")}
        </p>
      )}

      {failure && (
        <p
          role="alert"
          data-testid={`merge-error-${proposal.id}`}
          className="mt-1.5 text-[11px] text-destructive"
        >
          {t("ultrawiki.people.action_failed").replace(
            "{0}",
            identityErrorMessage(failure),
          )}
        </p>
      )}
    </li>
  );
}

function ProposalSide({
  side,
  onOpenPerson,
}: {
  side: UltraWikiMergeProposal["left"];
  onOpenPerson?: (entityId: number) => void;
}): JSX.Element {
  const label = side.display_name || String(side.id);
  const className =
    "min-w-0 max-w-full truncate rounded-lg border border-border bg-background/60 px-2.5 py-1 text-xs text-foreground";
  if (!onOpenPerson) {
    return (
      <span data-testid={`merge-side-${side.id}`} className={className}>
        {label}
      </span>
    );
  }
  return (
    <button
      type="button"
      data-testid={`merge-side-${side.id}`}
      onClick={() => onOpenPerson(side.id)}
      className={cn(className, "transition-colors hover:border-primary/50")}
    >
      {label}
    </button>
  );
}

/**
 * Why the layer asked. Rendered as a sentence, never as the raw token: the
 * reader is not the person who wrote the matcher, and "name_similar" tells
 * them nothing about whether to say yes.
 */
function EvidenceList({
  evidence,
  proposalId,
}: {
  evidence: UltraWikiMatchEvidence[];
  proposalId: number;
}): JSX.Element | null {
  const t = useT();
  if (!evidence || evidence.length === 0) return null;
  return (
    <ul
      data-testid={`merge-evidence-${proposalId}`}
      className="mt-2 space-y-0.5"
    >
      {evidence.map((item, index) => {
        const parts = evidenceParts(item.kind);
        const noun = parts.nounKey ? t(parts.nounKey) : parts.raw;
        const tier = tierKey(item.tier);
        return (
          <li
            key={`${item.kind}-${item.value}-${index}`}
            className="text-[11px] leading-relaxed text-muted-foreground"
          >
            <span className="text-foreground/80">
              {t(parts.phraseKey).replace("{0}", noun)}
            </span>
            {item.value && <span>: {item.value}</span>}
            {tier && (
              <span className="ml-1.5 opacity-60">({t(tier)})</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export default MergeQueuePanel;
