/**
 * Decision for a mission parked in WAITING_CAPACITY: wait for its own
 * subscription, approve paid API use for THIS mission only, or cancel.
 *
 * Nothing here is automatic. The offer (provider, model, estimated cost, hard
 * cap, reason) is shown before any paid use; an approval needs a second,
 * explicit confirmation and echoes exactly the offer shown, so a changed
 * offer is rejected by the server instead of silently billed.
 */
import { useEffect, useState } from "react";
import { AlertTriangle, Clock3, CreditCard, Loader2, X } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { fill, useT } from "@/i18n";
import type { CapacityDecision, MissionState, PaidOffer } from "@/types/missions";
import { decideCapacity, fetchPaidOffer, paidOfferQueryKey } from "./api";

const OFFER_REFRESH_MS = 15_000;

function usd(value: number): string {
  return `$${value.toFixed(2)}`;
}

export function CapacityDecisionPanel({
  missionId,
  state,
}: {
  missionId: string | null;
  state: MissionState | null;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const waiting = missionId !== null && state === "WAITING_CAPACITY";
  const [confirming, setConfirming] = useState<CapacityDecision | null>(null);
  useEffect(() => setConfirming(null), [missionId, state]);

  const offerQuery = useQuery({
    queryKey: paidOfferQueryKey(missionId),
    queryFn: () => fetchPaidOffer(missionId as string),
    enabled: waiting,
    refetchInterval: waiting ? OFFER_REFRESH_MS : false,
  });
  const offer: PaidOffer | null = offerQuery.data?.offer ?? null;

  const decision = useMutation({
    mutationFn: (choice: CapacityDecision) =>
      decideCapacity(
        missionId as string,
        choice,
        choice === "approve_paid" && offer
          ? { provider: offer.provider, model: offer.model }
          : undefined,
      ),
    onSuccess: () => setConfirming(null),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["missions"] });
      void queryClient.invalidateQueries({ queryKey: ["outputs"] });
    },
  });

  if (!waiting) return null;

  const busy = decision.isPending;
  const overCap = offer !== null && offer.estimated_cost_usd > offer.cost_cap_usd;

  return (
    <section
      aria-label={t("capacity_decision.title")}
      className="space-y-2.5 border-b border-border p-3 text-xs"
    >
      <div className="flex items-center gap-1.5 font-medium text-warning">
        <Clock3 className="h-3.5 w-3.5" />
        {t("capacity_decision.title")}
      </div>

      {offerQuery.isLoading ? (
        <p className="flex items-center gap-1.5 text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {t("capacity_decision.loading")}
        </p>
      ) : offer ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-md border border-border bg-card/40 p-2.5">
          <dt className="text-muted-foreground">{t("capacity_decision.provider")}</dt>
          <dd className="font-mono text-foreground">{offer.provider}</dd>
          <dt className="text-muted-foreground">{t("capacity_decision.model")}</dt>
          <dd className="break-all font-mono text-foreground">{offer.model}</dd>
          <dt className="text-muted-foreground">{t("capacity_decision.estimate")}</dt>
          <dd className="font-mono text-foreground">
            {fill(t("capacity_decision.estimate_value"), {
              cost: usd(offer.estimated_cost_usd),
              steps: offer.open_steps,
            })}
          </dd>
          <dt className="text-muted-foreground">{t("capacity_decision.cap")}</dt>
          <dd className="font-mono text-foreground">{usd(offer.cost_cap_usd)}</dd>
          <dt className="text-muted-foreground">{t("capacity_decision.reason")}</dt>
          <dd className="text-foreground">
            {t(`capacity_decision.reasons.${offer.reason}`)}
          </dd>
        </dl>
      ) : (
        <p className="rounded-md border border-border bg-card/40 p-2.5 text-muted-foreground">
          {t("capacity_decision.no_offer")}
        </p>
      )}

      {overCap && (
        <p className="flex items-start gap-1.5 text-warning">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("capacity_decision.over_cap")}
        </p>
      )}

      <p className="text-muted-foreground">{t("capacity_decision.scope")}</p>

      {confirming === null ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => decision.mutate("wait")}>
            <Clock3 className="mr-1.5 h-3.5 w-3.5" />
            {t("capacity_decision.wait")}
          </Button>
          <Button
            size="sm"
            disabled={busy || offer === null}
            onClick={() => setConfirming("approve_paid")}
          >
            <CreditCard className="mr-1.5 h-3.5 w-3.5" />
            {t("capacity_decision.approve")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => setConfirming("cancel")}
          >
            <X className="mr-1.5 h-3.5 w-3.5" />
            {t("capacity_decision.cancel")}
          </Button>
        </div>
      ) : (
        <div className="space-y-2 rounded-md border border-warning/40 bg-warning/10 p-2.5">
          <p className="text-foreground">
            {confirming === "approve_paid" && offer
              ? fill(t("capacity_decision.confirm_approve"), {
                  provider: offer.provider,
                  model: offer.model,
                  cap: usd(offer.cost_cap_usd),
                })
              : t("capacity_decision.confirm_cancel")}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant={confirming === "cancel" ? "destructive" : "default"}
              disabled={busy}
              onClick={() => decision.mutate(confirming)}
            >
              {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              {t("capacity_decision.confirm")}
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirming(null)}>
              {t("capacity_decision.back")}
            </Button>
          </div>
        </div>
      )}

      {decision.isError && (
        <p className="text-destructive">
          {fill(t("capacity_decision.error"), { detail: decision.error.message })}
        </p>
      )}
      {decision.isSuccess && decision.data.decision === "wait" && (
        <p className="text-muted-foreground">{t("capacity_decision.waiting_ack")}</p>
      )}
    </section>
  );
}
