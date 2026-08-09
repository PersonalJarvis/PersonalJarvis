/**
 * The report lane's feed.
 *
 * A poll rather than a subscription for the same reason the pane recaps use
 * one: the lane is a glanceable list, the backend already recomputes it on its
 * own sweep, and a websocket for a handful of rows would be a second lifecycle
 * to get wrong for no gain the user could see.
 *
 * Two things it is careful about, both learned from the recap poll next door:
 * it does not run while nobody is looking, and it keeps object identity when a
 * tick changes nothing — a fresh-but-equal array every two seconds would
 * re-render the deck (and every card in it) thirty times a minute for nothing.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import { ackDeckReport, fetchDeckQueue, type DeckQueue, type DeckReport } from "@/lib/agenticIdeApi";

/**
 * How often the lane re-reads what is waiting.
 *
 * Matched to the backend's own sweep (2 s in `notifications.SWEEP_INTERVAL_S`):
 * polling faster cannot produce fresher answers, and polling slower would let
 * a report be SPOKEN before it appears in the lane it is supposed to be in.
 */
export const DECK_POLL_MS = 2000;

const EMPTY: DeckQueue = {
  sleeping: false,
  in_conversation: false,
  on_air: null,
  pending: [],
  reports: [],
};

/** Same-rows check, on the fields the lane actually draws. */
function sameQueue(a: DeckQueue, b: DeckQueue): boolean {
  if (a.sleeping !== b.sleeping || a.in_conversation !== b.in_conversation) return false;
  if ((a.on_air?.id ?? "") !== (b.on_air?.id ?? "")) return false;
  if (a.pending.length !== b.pending.length) return false;
  return a.pending.every((row, index) => {
    const other = b.pending[index];
    return (
      other !== undefined &&
      row.id === other.id &&
      row.kind === other.kind &&
      row.headline === other.headline
    );
  });
}

export interface DeckQueueHandle {
  queue: DeckQueue;
  /** Pane call-signs with news waiting, for the cards' dots. */
  reporting: ReadonlySet<string>;
  hear: (id: string) => void;
  drop: (id: string) => void;
  wake: () => void;
}

export function useDeckQueue(active: boolean): DeckQueueHandle {
  const [queue, setQueue] = useState<DeckQueue>(EMPTY);
  const visible = useDocumentVisible();
  const polling = active && visible;
  // Read by the actions so they can apply an answer straight away without
  // re-subscribing the poll to their own dependencies.
  const queueRef = useRef(queue);
  queueRef.current = queue;

  const apply = useCallback((next: DeckQueue) => {
    setQueue((current) => (sameQueue(current, next) ? current : next));
  }, []);

  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let pulling = false;
    let warned = false;
    const pull = async () => {
      if (pulling) return;
      pulling = true;
      try {
        const answer = await fetchDeckQueue();
        if (!cancelled) apply(answer);
        warned = false;
      } catch (error) {
        // Keep the last lane rather than blanking it: the backend warming up,
        // or one failed request, is not a reason to tell the user their
        // reports are gone. Logged once until a read succeeds again.
        if (!warned) {
          console.warn("Command Deck: could not read the report lane:", error);
          warned = true;
        }
      } finally {
        pulling = false;
      }
    };
    void pull();
    const timer = window.setInterval(() => void pull(), DECK_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [apply, polling]);

  // The lane empties itself when the deck is left, so coming back does not
  // show a stack that was answered in another view.
  useEffect(() => {
    if (!active) setQueue(EMPTY);
  }, [active]);

  const answer = useCallback(
    (id: string, action: "next" | "later" | "drop") => {
      void ackDeckReport(id, action)
        .then(apply)
        .catch((error) => {
          console.warn("Command Deck: could not answer a report:", error);
        });
    },
    [apply],
  );

  const hear = useCallback((id: string) => answer(id, "next"), [answer]);
  const drop = useCallback((id: string) => answer(id, "drop"), [answer]);
  /**
   * "Go on" with nothing named — the next report in line.
   *
   * The lane's own way out of the quiet state. Nothing happens when the queue
   * is empty, which is why the button that calls this is only drawn beside a
   * non-empty one.
   */
  const wake = useCallback(() => {
    const next: DeckReport | undefined = queueRef.current.pending[0];
    if (next) answer(next.id, "next");
  }, [answer]);

  const reporting = new Set(queue.pending.map((row) => row.pane));
  return { queue, reporting, hear, drop, wake };
}
