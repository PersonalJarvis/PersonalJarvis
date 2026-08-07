/**
 * The update card under the voice bubble — what happened while you were talking.
 *
 * ## Why it exists next to the bell
 *
 * The bell in the header counts finished panes, but a counter is something you
 * have to go and look at. While the bubble is open the user is already looking
 * at the orb, and that is exactly the moment a terminal quietly finishes. So
 * the newest unread entry gets to say so where the eyes already are — one card,
 * newest only, never a list. The bell keeps the full history; this is a peek.
 *
 * ## What dismissing means here
 *
 * The X hides the card and nothing else: the entry stays in the bell, still
 * unread, still counted. A peek that could silently delete the record would
 * make the bell lie about work nobody has seen. The hidden ids live in a module
 * set so closing and reopening the bubble does not bring a waved-away card
 * back; a reload starts over, which is the honest reading of "still unread".
 *
 * Opening the bell marks everything read, and this card follows — the user has
 * seen the list, so the peek has nothing left to announce.
 */
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleHelp, PowerOff, X } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import {
  fetchPaneNotifications,
  type PaneNotification,
  type PaneNotificationKind,
} from "@/lib/agenticIdeApi";

/**
 * How often the card asks.
 *
 * The same beat as the bell: both read the one in-memory store the backend
 * sweep fills every 2 s, and a card that lags the bell it sits under would read
 * as a bug long before anyone worked out it was only slower.
 */
export const NOTICE_POLL_MS = 5_000;

/** Waved away by hand — kept out of the DOM, kept in the bell. */
const dismissedIds = new Set<string>();

/** For tests: forget what was waved away. */
export function resetDismissedNotices(): void {
  dismissedIds.clear();
}

const KINDS: Record<
  PaneNotificationKind,
  { key: string; icon: typeof CheckCircle2; tone: string }
> = {
  completed: {
    key: "agentic_grid.notifications.kind_completed",
    icon: CheckCircle2,
    tone: "text-emerald-400",
  },
  needs_input: {
    key: "agentic_grid.notifications.kind_needs_input",
    icon: CircleHelp,
    tone: "text-amber-400",
  },
  exited: {
    key: "agentic_grid.notifications.kind_exited",
    icon: PowerOff,
    tone: "text-muted-foreground",
  },
  failed: {
    key: "agentic_grid.notifications.kind_failed",
    icon: AlertTriangle,
    tone: "text-destructive",
  },
};

export function VoiceBubbleNotice({
  onJump,
}: {
  /**
   * Take the user to the pane the entry came from. Optional: without it the
   * card still reports, it just does not pretend to be a link.
   */
  onJump?: (entry: PaneNotification) => void;
}) {
  const t = useT();
  const [entry, setEntry] = useState<PaneNotification | null>(null);
  // Bumped by a dismissal so the next entry, if any, takes the slot at once
  // instead of after the rest of the current interval.
  const [dismissals, setDismissals] = useState(0);
  const documentVisible = useDocumentVisible();

  useEffect(() => {
    if (!documentVisible) return;
    let cancelled = false;
    const read = async () => {
      try {
        const state = await fetchPaneNotifications();
        if (cancelled) return;
        // Newest first, so the first survivor of the two filters IS the newest
        // thing the user has not seen.
        setEntry(
          state.notifications.find((n) => !n.read && !dismissedIds.has(n.id)) ?? null,
        );
      } catch {
        // A warming backend or a dropped request keeps whatever is on screen.
        // Blinking an error at somebody who is mid-sentence would be worse
        // than being five seconds behind, and the next tick corrects it.
      }
    };
    void read();
    const timer = window.setInterval(() => void read(), NOTICE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [documentVisible, dismissals]);

  const dismiss = useCallback(() => {
    setEntry((current) => {
      if (current) dismissedIds.add(current.id);
      return null;
    });
    setDismissals((n) => n + 1);
  }, []);

  if (!entry) return null;

  const kind = KINDS[entry.kind] ?? KINDS.completed;
  const Icon = kind.icon;
  // What the pane was last asked to do is the line a person recognises; the
  // backend's own sentence is the fallback for a pane nobody prompted.
  const headline = entry.detail.trim() || entry.title;
  const subline = `${entry.pane} · ${t(kind.key)}`;

  const body = (
    <>
      {/* Two lines rather than one truncated one: this is the sentence that
          tells the user WHICH of their agents this is about, and half of it is
          often the half that names the thing. */}
      <span className="line-clamp-2 text-[12px] font-semibold leading-snug text-foreground">
        {headline}
      </span>
      <span className="mt-0.5 block truncate text-[11px] leading-snug text-muted-foreground">
        {subline}
      </span>
    </>
  );

  return (
    <div
      data-testid="voice-bubble-notice"
      data-kind={entry.kind}
      data-no-drag
      role="status"
      aria-live="polite"
      className={cn(
        "pointer-events-auto flex w-full shrink-0 items-center gap-2.5",
        "rounded-2xl border border-border/50 bg-background/90 px-3 py-2.5",
        "text-left shadow-xl backdrop-blur",
      )}
    >
      {onJump ? (
        <button
          type="button"
          data-testid="voice-bubble-notice-jump"
          onClick={() => onJump(entry)}
          title={t("agentic_grid.voice_bubble.notice_jump").replace("{0}", entry.pane)}
          className="min-w-0 flex-1 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
        >
          {body}
        </button>
      ) : (
        <span className="min-w-0 flex-1">{body}</span>
      )}

      <Icon className={cn("h-4 w-4 shrink-0", kind.tone)} aria-hidden="true" />

      <button
        type="button"
        data-testid="voice-bubble-notice-dismiss"
        onClick={dismiss}
        title={t("agentic_grid.voice_bubble.notice_dismiss")}
        aria-label={t("agentic_grid.voice_bubble.notice_dismiss")}
        className={cn(
          // Dim rather than hidden-until-hover: a touch screen has no hover,
          // and a control nobody can reach is not a control.
          "grid h-5 w-5 shrink-0 place-items-center rounded text-muted-foreground",
          "opacity-40 transition-opacity hover:opacity-100 focus-visible:opacity-100",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
        )}
      >
        <X className="h-3 w-3" aria-hidden="true" />
      </button>
    </div>
  );
}

export default VoiceBubbleNotice;
