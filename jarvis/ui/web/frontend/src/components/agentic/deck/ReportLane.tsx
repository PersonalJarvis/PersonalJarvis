/**
 * The reports waiting to be heard, as a visible stack rather than as magic.
 *
 * The queue behind the Command Deck is a real mechanism with real rules — one
 * speaker, blockers first, a headline when several land together (see
 * `jarvis/agentic_ide/standup.py`). Left invisible it would read as Jarvis
 * being arbitrary about what it mentions and what it does not, which is the
 * fastest way to lose a user's trust in a surface that talks.
 *
 * So the lane is on screen: what is waiting, in the order it will be heard,
 * with the one on air lifted out of the stack. That way "three are done, which
 * one first?" has something to point at, and pressing a row is the same
 * instruction as saying its name.
 *
 * It also says when the deck has gone QUIET. An unanswered line settles the
 * queue on purpose — repeating yourself at somebody reading code is nagging,
 * not helpfulness — but a surface that silently stops talking is
 * indistinguishable from a broken one. The strip says which it is, and offers
 * the way back.
 */
import { BellOff, MessageCircleQuestion, Volume2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DeckReport } from "@/lib/agenticIdeApi";

export interface ReportLaneProps {
  pending: DeckReport[];
  onAir: DeckReport | null;
  /** True once a line went unanswered and the deck stopped offering. */
  sleeping: boolean;
  onHear: (id: string) => void;
  onDrop: (id: string) => void;
  onWake: () => void;
}

export function ReportLane({
  pending,
  onAir,
  sleeping,
  onHear,
  onDrop,
  onWake,
}: ReportLaneProps) {
  const empty = pending.length === 0 && onAir === null;
  return (
    <aside
      data-testid="deck-report-lane"
      aria-label="Reports waiting"
      className="flex w-64 shrink-0 flex-col gap-2 border-l border-border pl-4"
    >
      <div className="flex items-center gap-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
        <span>Reports</span>
        {pending.length > 0 && (
          <span
            data-testid="deck-lane-count"
            className="rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-semibold text-primary"
          >
            {pending.length}
          </span>
        )}
      </div>

      {empty && (
        <p
          data-testid="deck-lane-empty"
          className="text-xs leading-relaxed text-muted-foreground"
        >
          Nothing waiting. You will hear about an agent when it stops.
        </p>
      )}

      {onAir && (
        <div
          data-testid="deck-lane-on-air"
          className="rounded-lg border border-primary/60 bg-primary/[0.06] p-3"
        >
          <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-primary">
            <Volume2 className="h-3 w-3 shrink-0" />
            Reporting
          </span>
          <p className="mt-1 truncate text-sm font-medium text-foreground">{onAir.pane}</p>
          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
            {onAir.headline}
          </p>
        </div>
      )}

      <ul className="flex min-h-0 flex-col gap-1.5 overflow-y-auto">
        {pending.map((report) => (
          <li key={report.id}>
            <div
              className={cn(
                "group flex items-start gap-2 rounded-lg border border-border/70 p-2",
                "transition-colors hover:border-border",
              )}
            >
              <button
                type="button"
                data-testid={`deck-lane-hear-${report.pane}`}
                onClick={() => onHear(report.id)}
                title={`Hear ${report.pane}'s report`}
                className="flex min-w-0 flex-1 flex-col items-start text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
              >
                <span className="flex w-full min-w-0 items-center gap-1.5">
                  {report.kind === "needs_input" && (
                    <MessageCircleQuestion className="h-3 w-3 shrink-0 text-amber-400" />
                  )}
                  <span className="truncate text-xs font-medium text-foreground">
                    {report.pane}
                  </span>
                </span>
                <span className="line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
                  {report.headline}
                </span>
              </button>
              <button
                type="button"
                data-testid={`deck-lane-drop-${report.pane}`}
                onClick={() => onDrop(report.id)}
                title="I have seen this one"
                className={cn(
                  "shrink-0 rounded-control px-1.5 py-0.5 text-[10px] text-muted-foreground",
                  "opacity-0 transition-opacity hover:bg-secondary hover:text-foreground",
                  "group-hover:opacity-100 focus-visible:opacity-100",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                )}
              >
                Seen
              </button>
            </div>
          </li>
        ))}
      </ul>

      {sleeping && pending.length > 0 && (
        <button
          type="button"
          data-testid="deck-lane-wake"
          onClick={onWake}
          className={cn(
            "mt-auto flex items-start gap-2 rounded-lg border border-border/70 p-2 text-left",
            "text-[11px] leading-relaxed text-muted-foreground",
            "transition-colors hover:border-border hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
          )}
        >
          <BellOff className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            Gone quiet so it does not nag. Press to hear the next one.
          </span>
        </button>
      )}
    </aside>
  );
}
