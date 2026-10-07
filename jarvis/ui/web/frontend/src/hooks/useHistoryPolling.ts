import { useEffect } from "react";

import { jitteredDelay, requestConnect, spreadDelay } from "@/lib/connectBudget";

export const HISTORY_REFRESH_MS = 5000;
type ReadHistory = () => Promise<void>;
const owners = new Map<ReadHistory, { readers: number; stop: () => void }>();

export function historyDocumentVisible(): boolean {
  if (typeof document === "undefined") return true;
  // Some WebViews report hidden even while their composer has focus. Missing
  // visibility APIs also mean unknown, not permission to freeze the history.
  return document.hidden !== true || document.hasFocus?.() === true;
}

function subscribe(read: ReadHistory): () => void {
  let owner = owners.get(read);
  if (!owner) {
    let stopped = false;
    let running = false;
    let visible = historyDocumentVisible();
    let cancelScheduled: (() => void) | undefined;

    const schedule = (delay = spreadDelay(HISTORY_REFRESH_MS)) => {
      cancelScheduled?.();
      if (stopped) return;
      if (!historyDocumentVisible()) {
        // Recheck cheaply when an embedder misses visibilitychange. No HTTP
        // while hidden; restoration still catches up without another click.
        const timer = window.setTimeout(onVisibility, HISTORY_REFRESH_MS);
        cancelScheduled = () => window.clearTimeout(timer);
      } else {
        cancelScheduled = requestConnect(() => void run(), delay);
      }
    };
    const run = async () => {
      cancelScheduled = undefined;
      if (stopped || running) return;
      visible = historyDocumentVisible();
      if (!visible) return schedule();
      running = true;
      try {
        await read();
      } catch {
        // Readers retain their last list while offline. The next budgeted,
        // jittered poll is the retry; there is no immediate retry storm.
      } finally {
        running = false;
        if (!stopped) schedule();
      }
    };
    function onVisibility() {
      const next = historyDocumentVisible();
      const resumed = next && !visible;
      visible = next;
      if (!next) schedule();
      else if (resumed && !running) schedule(jitteredDelay(0, 250, 250));
    }
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onVisibility);
    owner = { readers: 0, stop: () => {
      stopped = true;
      cancelScheduled?.();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onVisibility);
    } };
    owners.set(read, owner);
    // Only the FIRST consumer reads on mount. A sidebar, stage and open
    // history rail share the same owner even if their mounts are staggered.
    void run();
  }
  owner.readers += 1;
  return () => {
    owner.readers -= 1;
    if (owner.readers === 0) {
      owner.stop();
      if (owners.get(read) === owner) owners.delete(read);
    }
  };
}

/** One visible, non-overlapping polling owner per stable store read action. */
export function useHistoryPolling(read: ReadHistory, enabled = true): void {
  useEffect(() => enabled ? subscribe(read) : undefined, [read, enabled]);
}
