import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  CircleSlash,
  Clock3,
  Loader2,
  RefreshCw,
  ShieldAlert,
  X,
} from "lucide-react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT, useUiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";
import type {
  MissionToolApprovalDecision,
  MissionToolApprovalsResponse,
  PendingMissionToolApproval,
} from "@/types/missions";
import {
  approveMissionToolCall,
  denyMissionToolCall,
  fetchMissionToolApprovals,
  missionToolApprovalsQueryKey,
} from "./api";

const APPROVAL_REFRESH_MS = 1_500;

type ApprovalQuery = UseQueryResult<MissionToolApprovalsResponse, Error>;

interface DecisionRequest {
  approval: PendingMissionToolApproval;
  decision: "approve" | "deny";
}

export function useMissionToolApprovals(
  missionId: string | null,
): ApprovalQuery {
  return useQuery({
    queryKey: missionToolApprovalsQueryKey(missionId),
    queryFn: () => fetchMissionToolApprovals(missionId as string),
    enabled: missionId !== null,
    refetchInterval: missionId === null ? false : APPROVAL_REFRESH_MS,
  });
}

export function ToolApprovalPanel({
  missionId,
  query,
}: {
  missionId: string | null;
  query: ApprovalQuery;
}) {
  const t = useT();
  const language = useUiLanguage();
  const queryClient = useQueryClient();
  const approvals = query.data?.approvals ?? [];
  const [confirmingTraceId, setConfirmingTraceId] = useState<string | null>(
    null,
  );
  const [nowMs, setNowMs] = useState(() => Date.now());
  const relativeTime = useMemo(
    () => new Intl.RelativeTimeFormat(language, { numeric: "always" }),
    [language],
  );

  useEffect(() => {
    if (approvals.length === 0) return;
    setNowMs(Date.now());
    const intervalId = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(intervalId);
  }, [approvals.length]);

  useEffect(() => {
    if (
      confirmingTraceId !== null &&
      !approvals.some((approval) => approval.trace_id === confirmingTraceId)
    ) {
      setConfirmingTraceId(null);
    }
  }, [approvals, confirmingTraceId]);

  useEffect(() => setConfirmingTraceId(null), [missionId]);

  const decision = useMutation<
    MissionToolApprovalDecision,
    Error,
    DecisionRequest
  >({
    mutationFn: ({ approval, decision: requestedDecision }) =>
      requestedDecision === "approve"
        ? approveMissionToolCall(approval.mission_id, approval.trace_id)
        : denyMissionToolCall(approval.mission_id, approval.trace_id),
    onSuccess: (_result, request) => {
      const key = missionToolApprovalsQueryKey(request.approval.mission_id);
      queryClient.setQueryData<MissionToolApprovalsResponse>(key, (current) =>
        current
          ? {
              ...current,
              approvals: current.approvals.filter(
                (item) => item.trace_id !== request.approval.trace_id,
              ),
            }
          : current,
      );
      setConfirmingTraceId(null);
    },
    onSettled: (_result, _error, request) => {
      void queryClient.invalidateQueries({
        queryKey: missionToolApprovalsQueryKey(
          request?.approval.mission_id ?? missionId,
        ),
      });
    },
  });

  if (missionId === null) {
    return (
      <PanelMessage
        icon={ShieldAlert}
        text={t("mission_tool_approvals.select_mission")}
      />
    );
  }

  if (query.isLoading) {
    return (
      <PanelMessage
        icon={Loader2}
        iconClassName="animate-spin"
        text={t("mission_tool_approvals.loading")}
      />
    );
  }

  if (query.isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
        <AlertTriangle className="h-7 w-7 text-destructive" />
        <div>
          <p className="text-xs font-medium text-foreground">
            {t("mission_tool_approvals.load_error")}
          </p>
          <p className="mt-1 break-words text-[11px] text-muted-foreground">
            {query.error.message}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          {t("mission_tool_approvals.retry")}
        </Button>
      </div>
    );
  }

  if (approvals.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
        <CircleSlash className="h-7 w-7 text-muted-foreground/40" />
        <p className="text-xs font-medium text-foreground/80">
          {t("mission_tool_approvals.empty_title")}
        </p>
        <p className="max-w-56 text-[11px] text-muted-foreground">
          {t("mission_tool_approvals.empty_body")}
        </p>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-3 p-3">
        <div className="rounded-md border border-amber-400/30 bg-amber-400/10 p-2.5 text-[11px] text-amber-100/90">
          {t("mission_tool_approvals.scope_notice")}
        </div>

        {approvals.map((approval) => {
          const expiresAtMs = approval.expires_at_ns / 1_000_000;
          const remainingSeconds = Math.ceil((expiresAtMs - nowMs) / 1_000);
          const expired = remainingSeconds <= 0;
          const isConfirming = confirmingTraceId === approval.trace_id;
          const isThisDecision =
            decision.isPending &&
            decision.variables?.approval.trace_id === approval.trace_id;
          const decisionError =
            decision.isError &&
            decision.variables?.approval.trace_id === approval.trace_id
              ? decision.error.message
              : null;

          return (
            <article
              key={approval.trace_id}
              className={cn(
                "rounded-lg border bg-card/50 p-3 shadow-sm",
                expired
                  ? "border-border/60 opacity-70"
                  : "border-amber-400/40",
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-amber-300">
                    <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
                    {t("mission_tool_approvals.pending_title")}
                  </div>
                  <h3 className="mt-1 break-all font-mono text-xs font-semibold text-foreground">
                    {approval.tool_name}
                  </h3>
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    "shrink-0 font-mono text-[9px] uppercase",
                    riskClassName(approval.risk_tier),
                  )}
                >
                  {t("mission_tool_approvals.risk")}: {approval.risk_tier}
                </Badge>
              </div>

              <dl className="mt-3 space-y-2 text-[11px]">
                <DetailRow
                  label={t("mission_tool_approvals.reason")}
                  value={formatReason(approval.reason, t)}
                />
                {approval.worker_id ? (
                  <DetailRow
                    label={t("mission_tool_approvals.worker")}
                    value={approval.worker_id}
                    mono
                  />
                ) : null}
                <div>
                  <dt className="text-[9px] font-medium uppercase tracking-wider text-muted-foreground">
                    {t("mission_tool_approvals.arguments")}
                  </dt>
                  <dd className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-all rounded border border-border/60 bg-background/60 p-2 font-mono text-[10px] text-foreground/80">
                    {approval.args_preview || t("mission_tool_approvals.no_arguments")}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className="flex items-center gap-1 text-[9px] font-medium uppercase tracking-wider text-muted-foreground">
                    <Clock3 className="h-3 w-3" />
                    {t("mission_tool_approvals.expires")}
                  </dt>
                  <dd
                    className={cn(
                      "text-right font-mono text-[10px]",
                      expired ? "text-destructive" : "text-amber-300",
                    )}
                    title={new Date(expiresAtMs).toLocaleString(language)}
                  >
                    {expired
                      ? t("mission_tool_approvals.expired")
                      : relativeTime.format(remainingSeconds, "second")}
                  </dd>
                </div>
              </dl>

              {decisionError ? (
                <p
                  role="alert"
                  className="mt-3 rounded border border-destructive/40 bg-destructive/10 p-2 text-[10px] text-destructive"
                >
                  {t("mission_tool_approvals.decision_failed")}: {decisionError}
                </p>
              ) : null}

              {expired ? (
                <p className="mt-3 text-[10px] text-muted-foreground">
                  {t("mission_tool_approvals.expired_body")}
                </p>
              ) : isConfirming ? (
                <div
                  role="alert"
                  className="mt-3 rounded-md border border-destructive/50 bg-destructive/10 p-2.5"
                >
                  <p className="text-xs font-semibold text-destructive">
                    {t("mission_tool_approvals.confirm_title")}
                  </p>
                  <p className="mt-1 text-[10px] text-foreground/80">
                    {t("mission_tool_approvals.confirm_body")}
                  </p>
                  <div className="mt-2 flex justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setConfirmingTraceId(null)}
                      disabled={decision.isPending}
                    >
                      {t("common.cancel")}
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() =>
                        decision.mutate({ approval, decision: "approve" })
                      }
                      disabled={decision.isPending}
                    >
                      {isThisDecision ? (
                        <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Check className="mr-1.5 h-3.5 w-3.5" />
                      )}
                      {isThisDecision
                        ? t("mission_tool_approvals.approving")
                        : t("mission_tool_approvals.confirm")}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      decision.mutate({ approval, decision: "deny" })
                    }
                    disabled={decision.isPending}
                  >
                    {isThisDecision ? (
                      <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <X className="mr-1.5 h-3.5 w-3.5" />
                    )}
                    {isThisDecision
                      ? t("mission_tool_approvals.denying")
                      : t("mission_tool_approvals.deny")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-amber-400/50 bg-amber-400/10 text-amber-200 hover:bg-amber-400/20 hover:text-amber-100"
                    onClick={() => setConfirmingTraceId(approval.trace_id)}
                    disabled={decision.isPending}
                  >
                    <ShieldAlert className="mr-1.5 h-3.5 w-3.5" />
                    {t("mission_tool_approvals.approve")}
                  </Button>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </ScrollArea>
  );
}

function PanelMessage({
  icon: Icon,
  iconClassName,
  text,
}: {
  icon: typeof ShieldAlert;
  iconClassName?: string;
  text: string;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted-foreground">
      <Icon className={cn("h-7 w-7 text-muted-foreground/40", iconClassName)} />
      <p>{text}</p>
    </div>
  );
}

function DetailRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="shrink-0 text-[9px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd
        className={cn(
          "min-w-0 break-words text-right text-foreground/80",
          mono && "break-all font-mono text-[10px]",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function riskClassName(riskTier: string): string {
  switch (riskTier) {
    case "block":
      return "border-destructive/60 bg-destructive/10 text-destructive";
    case "ask":
      return "border-amber-400/50 bg-amber-400/10 text-amber-300";
    case "monitor":
      return "border-sky-400/50 bg-sky-400/10 text-sky-300";
    default:
      return "border-emerald-400/50 bg-emerald-400/10 text-emerald-300";
  }
}

function formatReason(reason: string, t: (key: string) => string): string {
  if (reason === "risk_tier") {
    return t("mission_tool_approvals.reason_risk_tier");
  }
  if (reason === "plausibility") {
    return t("mission_tool_approvals.reason_plausibility");
  }
  return reason || t("mission_tool_approvals.reason_unspecified");
}
