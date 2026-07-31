/**
 * "Needs you" — the checks that are NOT fine, and nothing else.
 *
 * The screen this replaces printed all seven checks at equal weight, six of
 * them green. That is a reading cost with no payoff: a person scanning for a
 * problem has to read six reassurances to find out there is none, and on the
 * day something IS wrong it looks exactly like the other six.
 *
 * So the list shows only what is not ok, and an all-clear collapses to a
 * single line. The full checklist is still one click away behind "all checks"
 * — nothing is hidden, it is just no longer the first thing in the way.
 *
 * `working` is deliberately excluded from "problems": a draining backlog is
 * progress, and the verdict card above already reports it. Dressing it up as
 * something to fix is how people learn to ignore this list.
 */
import { AlertTriangle, CheckCircle2, MinusCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { Eyebrow } from "@/components/ultrawiki/overview/primitives";
import {
  useCheckActions,
  type CheckActionHandlers,
} from "@/components/ultrawiki/overview/useCheckActions";
import type { UltraWikiHealthCheck } from "@/lib/ultrawikiApi";

/** Only states a person can do something about, or must know are impossible. */
export function problemsOf(
  checks: UltraWikiHealthCheck[],
): UltraWikiHealthCheck[] {
  return checks.filter(
    (check) => check.state === "attention" || check.state === "blocked",
  );
}

export function ProblemList({
  checks,
  handlers,
}: {
  checks: UltraWikiHealthCheck[];
  handlers: CheckActionHandlers;
}): JSX.Element {
  const t = useT();
  const { busy, runAction } = useCheckActions(handlers);
  const problems = problemsOf(checks);

  if (problems.length === 0) {
    return (
      <section data-testid="ultrawiki-problems" data-count="0">
        <Eyebrow>{t("ultrawiki.overview.eyebrow_problems")}</Eyebrow>
        <p className="flex items-center gap-2 rounded-xl border border-border bg-card/40 px-3 py-2.5 text-[13px] text-muted-foreground">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-[#5bd4a4]" aria-hidden />
          {t("ultrawiki.overview.no_problems")}
        </p>
      </section>
    );
  }

  return (
    <section data-testid="ultrawiki-problems" data-count={problems.length}>
      <Eyebrow>{t("ultrawiki.overview.eyebrow_problems")}</Eyebrow>
      <ul className="space-y-2">
        {problems.map((check) => {
          const blocked = check.state === "blocked";
          const Icon = blocked ? MinusCircle : AlertTriangle;
          return (
            <li
              key={check.id}
              className={cn(
                "flex items-start gap-3 rounded-xl border p-3",
                blocked
                  ? "border-border bg-muted/20"
                  : "border-[#ffb84d]/30 bg-[#ffb84d]/[0.05]",
              )}
              data-testid={`ultrawiki-problem-${check.id}`}
              data-state={check.state}
            >
              <Icon
                className={cn(
                  "mt-0.5 h-4 w-4 shrink-0",
                  blocked ? "text-muted-foreground" : "text-[#ffb84d]",
                )}
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium text-foreground">
                  {check.title}
                </p>
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                  {check.detail}
                </p>
              </div>
              {check.action && (
                <Button
                  size="sm"
                  variant={blocked ? "outline" : "default"}
                  onClick={() => runAction(check)}
                  disabled={busy}
                  data-testid={`ultrawiki-problem-action-${check.id}`}
                >
                  {t(`ultrawiki.health.action_${check.action.kind}`)}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
