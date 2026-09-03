/**
 * The review queue — observations the curator is not confident enough to write
 * on its own.
 *
 * The one contract that must never regress: `GET /api/profile/reviews` answers
 * 503 when the legacy curator is soft-disabled, which is a DESIGNED state, not
 * a fault. It renders as a calm explanation; only a real failure gets the
 * destructive treatment. Both branches are pinned by ProfileView.test.tsx.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronRight, RefreshCw, ShieldQuestion, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { IdentityAvatar } from "@/components/identity/IdentityAvatar";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import {
  fetchJson,
  renderValue,
  statusOf,
  type ReviewCandidate,
  type ReviewsResponse,
} from "@/views/profile/api";

export function ReviewsCard({ reviewsCount }: { reviewsCount: number }) {
  const t = useT();
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);

  const { data, isLoading, error, refetch, isRefetching } = useQuery<ReviewsResponse, Error>({
    queryKey: ["profile", "reviews"],
    queryFn: () => fetchJson<ReviewsResponse>("/api/profile/reviews"),
    retry: false,
  });

  const accept = useMutation({
    mutationFn: (idx: number) =>
      fetchJson<{ ok: boolean; applied: number }>(`/api/profile/reviews/${idx}/accept`, {
        method: "POST",
      }),
    onSuccess: (res) => {
      pushToast(
        "success",
        res.applied > 0
          ? t("profile_toast.fact_applied").replace("{0}", String(res.applied))
          : t("profile_view.accepted"),
      );
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      queryClient.invalidateQueries({ queryKey: ["profile", "reviews"] });
    },
    onError: (err: Error) => pushToast("error", err.message),
  });

  const reject = useMutation({
    mutationFn: (idx: number) =>
      fetchJson<{ ok: boolean }>(`/api/profile/reviews/${idx}/reject`, { method: "POST" }),
    onSuccess: () => {
      pushToast("info", t("profile_view.reject_tooltip"));
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      queryClient.invalidateQueries({ queryKey: ["profile", "reviews"] });
    },
    onError: (err: Error) => pushToast("error", err.message),
  });

  const pendingIdx: number | null = accept.isPending
    ? (accept.variables ?? null)
    : reject.isPending
      ? (reject.variables ?? null)
      : null;

  const items = data?.reviews ?? [];

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex-row items-center justify-between gap-3 pb-2">
        <span className="flex min-w-0 items-center gap-2">
          <CardTitle className="truncate">{t("profile_view.section_reviews")}</CardTitle>
          {reviewsCount > 0 && (
            <Badge variant="accent" className="shrink-0 tabular-nums">
              {reviewsCount}
            </Badge>
          )}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0 text-muted-foreground"
          onClick={() => refetch()}
          disabled={isRefetching}
          title={t("profile_view.reload_tooltip")}
          aria-label={t("profile_view.reload_tooltip")}
        >
          <RefreshCw className={cn(isRefetching && "animate-spin")} />
        </Button>
      </CardHeader>

      <CardContent>
        {isLoading && (
          <div className="flex flex-col gap-3" aria-hidden>
            {[0, 1].map((i) => (
              <div key={i} className="flex flex-col gap-2">
                <div className="h-3 w-4/5 animate-pulse rounded-full bg-secondary" />
                <div className="h-3 w-2/5 animate-pulse rounded-full bg-secondary" />
                <div className="h-8 animate-pulse rounded-md bg-secondary" />
              </div>
            ))}
          </div>
        )}

        {error &&
          (statusOf(error) === 503 ? (
            // The curator is intentionally not running in this session. An
            // expected state, so it reads as an explanation, not an alarm.
            <div data-testid="reviews-disabled">
              <EmptyState
                className="max-w-none py-6"
                icon={<ShieldQuestion />}
                title={t("profile_view.reviews_disabled_title")}
                description={t("profile_view.reviews_disabled_body")}
              />
            </div>
          ) : (
            <p
              data-testid="reviews-error"
              className="flex items-start gap-2 rounded-md border border-destructive/20 bg-destructive/[0.12] p-3 text-base text-destructive"
            >
              <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="min-w-0 [overflow-wrap:anywhere]">{error.message}</span>
            </p>
          ))}

        {data && items.length === 0 && (
          <EmptyState
            className="max-w-none py-6"
            icon={<ShieldQuestion />}
            title={t("profile_view.reviews_empty_title")}
            description={t("profile_view.reviews_empty_body")}
          />
        )}

        {items.length > 0 && (
          <ul className="flex flex-col">
            {items.map((c, i) => (
              <ReviewRow
                key={c.idx}
                candidate={c}
                first={i === 0}
                pending={pendingIdx === c.idx}
                onAccept={() => accept.mutate(c.idx)}
                onReject={() => reject.mutate(c.idx)}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * One candidate. The evidence quote leads, because that is the sentence the
 * user actually said and the only thing they can judge the claim by; the
 * cluster → field path and the two verdicts sit under it.
 */
function ReviewRow({
  candidate,
  first,
  pending,
  onAccept,
  onReject,
}: {
  candidate: ReviewCandidate;
  first: boolean;
  pending: boolean;
  onAccept: () => void;
  onReject: () => void;
}) {
  const t = useT();
  // Who the observation is about — the coloured mark tells "about you" from
  // "about Anna" without reading the line.
  const subject = candidate.is_person
    ? (candidate.person_name ?? t("profile_view.review_subject_user"))
    : t("profile_view.review_subject_user");

  return (
    <li className={cn("py-4", !first && "border-t border-border")}>
      <div className="flex items-center gap-2">
        <IdentityAvatar name={subject} size="sm" />
        <span className="min-w-0 flex-1 truncate text-base text-foreground">{subject}</span>
        <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
          {(candidate.confidence * 100).toFixed(0)}%
        </span>
      </div>

      {candidate.evidence && (
        <blockquote className="mt-3 border-l-2 border-border pl-3 text-base text-muted-foreground">
          “{candidate.evidence}”
        </blockquote>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
        <span>{candidate.cluster}</span>
        <ChevronRight aria-hidden className="h-3 w-3" />
        <span className="text-foreground">{candidate.field}</span>
        <Badge variant="outline">{candidate.operation}</Badge>
      </div>

      <p className="mt-2 text-base">
        <span className="text-muted-foreground">{t("profile_view.review_value")}: </span>
        <span className="text-foreground [overflow-wrap:anywhere]">
          {renderValue(t, candidate.value) || t("profile_view.field_unknown")}
        </span>
      </p>

      {candidate.reason && (
        <p className="mt-1 text-sm text-muted-foreground">
          {t("profile_view.review_reason")}: {candidate.reason}
        </p>
      )}

      <div className="mt-3 flex gap-2">
        <Button
          size="sm"
          variant="default"
          className="flex-1"
          disabled={pending}
          onClick={onAccept}
          title={t("profile_view.accept_tooltip")}
        >
          <Check />
          {t("profile_view.review_confirm")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="flex-1"
          disabled={pending}
          onClick={onReject}
          title={t("profile_view.reject_tooltip")}
        >
          <X />
          {t("profile_view.review_strike")}
        </Button>
      </div>
    </li>
  );
}
