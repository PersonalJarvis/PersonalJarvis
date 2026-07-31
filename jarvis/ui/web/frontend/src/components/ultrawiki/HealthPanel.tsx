/**
 * "Is my knowledge base working?" — the checklist tab.
 *
 * Written after a forensic where every surface told the truth and the whole
 * was still unreadable: sources showed "approved" (permission, not import),
 * the pipeline said "everything is processed" (of nothing — nothing had ever
 * been fetched), every capability slot was green, and seven apps showed as
 * connected in the Plugins store that UltraWiki has no reader for. The user
 * concluded, reasonably, that it was broken. Finding the one missing step took
 * a database query.
 *
 * So this is one screen that answers the question in the order a person asks
 * it, with a plain sentence per row and — where something IS fixable — the
 * button that fixes it. The states are deliberately four, not two:
 *
 * - **ok** — nothing to do.
 * - **working** — busy; it will resolve itself, so it must not look like a
 *   fault (a first import or a draining backlog is progress).
 * - **attention** — the user can fix this, and the action says how.
 * - **blocked** — genuinely not possible yet. No button, because clicking
 *   harder cannot help; the sentence explains what is missing instead.
 *
 * The verdict is computed in the backend (`jarvis/ultrawiki/health.py`) from
 * the same status payload the other tabs read, so this screen can never claim
 * something the rest of the UI contradicts.
 */
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  MinusCircle,
  RefreshCw,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { ReconcileStrip } from "@/components/ultrawiki/ReconcileStrip";
import { useCheckActions } from "@/components/ultrawiki/overview/useCheckActions";
import {
  fetchUltraWikiHealth,
  type UltraWikiHealth,
  type UltraWikiHealthCheck,
  type UltraWikiCheckState,
} from "@/lib/ultrawikiApi";

const STATE_STYLE: Record<
  UltraWikiCheckState,
  { icon: typeof CheckCircle2; tone: string; ring: string }
> = {
  ok: {
    icon: CheckCircle2,
    tone: "text-emerald-500",
    ring: "border-emerald-500/25 bg-emerald-500/[0.04]",
  },
  working: {
    icon: Loader2,
    tone: "text-primary",
    ring: "border-primary/25 bg-primary/[0.04]",
  },
  attention: {
    icon: AlertTriangle,
    tone: "text-[#ffb84d]",
    ring: "border-[#ffb84d]/30 bg-[#ffb84d]/[0.05]",
  },
  blocked: {
    icon: MinusCircle,
    tone: "text-muted-foreground",
    ring: "border-border bg-muted/20",
  },
};

export function HealthPanel({
  onOpenSources,
  onOpenSettings,
  onEnableMode,
  showVerdict = true,
}: {
  onOpenSources: () => void;
  onOpenSettings: () => void;
  onEnableMode?: () => void;
  /** Off when the overview already carries the verdict above this list —
   *  two headlines saying the same thing is how a screen stops being read. */
  showVerdict?: boolean;
}): JSX.Element {
  const t = useT();

  const healthQuery = useQuery({
    queryKey: ["ultrawiki", "health"],
    queryFn: fetchUltraWikiHealth,
    staleTime: 2_000,
    // Follow a running import closely, idle calmly — the same cadence rule the
    // status poll uses, so the two never disagree for long.
    refetchInterval: (query) =>
      (query.state.data as UltraWikiHealth | undefined)?.checks?.some(
        (c) => c.state === "working",
      )
        ? 4_000
        : 20_000,
  });

  const health = healthQuery.data ?? null;

  // Shared with the overview's problem list, so a check offers the same button
  // wherever it is rendered (see overview/useCheckActions.ts).
  const { busy, runAction } = useCheckActions({
    onOpenSources,
    onOpenSettings,
    onEnableMode,
    onChanged: () => void healthQuery.refetch(),
  });

  if (healthQuery.isLoading) {
    return (
      <div
        className="p-6 text-sm text-muted-foreground"
        data-testid="ultrawiki-health-loading"
      >
        {t("ultrawiki.health.loading")}
      </div>
    );
  }

  if (!health) {
    return (
      <div className="p-4">
        <div
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
          data-testid="ultrawiki-health-unavailable"
        >
          {t("ultrawiki.health.unavailable")}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4" data-testid="ultrawiki-health-panel">
      {showVerdict && <Verdict health={health} />}

      <ol className="space-y-2">
        {health.checks.map((check, index) => (
          <CheckRow
            key={check.id}
            index={index + 1}
            check={check}
            busy={busy}
            onAction={() => runAction(check)}
          />
        ))}
      </ol>

      {/* The completeness proof. It belongs under the checklist rather than in
          the inventory: "how many items" is a fact the Contents tab already
          shows, while "is that all of them" is a VERDICT, and verdicts live
          here. Re-runs whenever the checks do. */}
      <ReconcileStrip refreshKey={health.checks.length + (busy ? 1 : 0)} />


      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => void healthQuery.refetch()}
          disabled={healthQuery.isFetching}
          data-testid="ultrawiki-health-refresh"
        >
          {healthQuery.isFetching ? (
            <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden />
          )}
          {t("ultrawiki.health.recheck")}
        </Button>
      </div>
    </div>
  );
}

/** The one-line answer to "can I use this right now?". */
function Verdict({ health }: { health: UltraWikiHealth }): JSX.Element {
  const t = useT();
  const working = health.checks.some((c) => c.state === "working");
  const tone = health.usable
    ? working
      ? "border-primary/30 bg-primary/[0.06] text-primary"
      : "border-emerald-500/30 bg-emerald-500/[0.06] text-emerald-500"
    : "border-[#ffb84d]/30 bg-[#ffb84d]/[0.07] text-[#ffb84d]";
  const key = health.usable
    ? working
      ? "ultrawiki.health.verdict_usable_working"
      : "ultrawiki.health.verdict_usable"
    : "ultrawiki.health.verdict_not_ready";
  return (
    <div
      className={cn("rounded-xl border px-4 py-3", tone)}
      data-testid="ultrawiki-health-verdict"
      data-overall={health.overall}
      data-usable={health.usable ? "true" : "false"}
    >
      <p className="text-sm font-medium">{t(key)}</p>
      <p className="mt-0.5 text-[11px] leading-relaxed opacity-80">
        {t("ultrawiki.health.verdict_hint")}
      </p>
    </div>
  );
}

function CheckRow({
  index,
  check,
  busy,
  onAction,
}: {
  index: number;
  check: UltraWikiHealthCheck;
  busy: boolean;
  onAction: () => void;
}): JSX.Element {
  const t = useT();
  const style = STATE_STYLE[check.state] ?? STATE_STYLE.blocked;
  const Icon = style.icon;
  return (
    <li
      className={cn("flex items-start gap-3 rounded-xl border p-3", style.ring)}
      data-testid={`ultrawiki-check-${check.id}`}
      data-state={check.state}
    >
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center">
        <Icon
          className={cn(
            "h-4 w-4",
            style.tone,
            check.state === "working" && "animate-spin",
          )}
          aria-hidden
        />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">
          <span className="mr-1.5 tabular-nums text-muted-foreground">
            {index}.
          </span>
          {check.title}
        </p>
        <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
          {check.detail}
        </p>
      </div>
      {check.action && (
        <Button
          size="sm"
          variant={check.state === "attention" ? "default" : "outline"}
          onClick={onAction}
          disabled={busy}
          data-testid={`ultrawiki-check-action-${check.id}`}
        >
          {busy && check.action.kind === "sync_all" && (
            <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
          )}
          {t(`ultrawiki.health.action_${check.action.kind}`)}
        </Button>
      )}
    </li>
  );
}
