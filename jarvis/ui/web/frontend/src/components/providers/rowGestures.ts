import { useCallback, useEffect, useRef } from "react";
import type React from "react";

/**
 * The two pointer gestures a provider row understands.
 *
 * One click opens or closes the editor body; a double click activates the
 * provider (maintainer request 2026-08-23: "double-click a block anywhere —
 * Realtime, Tool Model, Voice, the agents — and it is selected"). The two
 * have to be told apart, because a double click is delivered as click, click,
 * dblclick: toggling on each click would flash the body open and shut (and
 * mount its panels, which poll) before the activation even fires.
 *
 * So a single click waits out the double-click window before it toggles, and
 * the second click of a double cancels it. The window is short enough to
 * read as immediate and long enough for a normal double click; keyboard
 * activation (Enter/Space) never waits — it is unambiguous.
 *
 * Clicks on the row's own controls — the Use radio, a button, a link, an
 * input, or anything marked `data-agent-card-control` — belong to those
 * controls and are ignored here.
 */
const DOUBLE_CLICK_WINDOW_MS = 220;

const CONTROL_SELECTOR =
  "input, label, button, a, select, textarea, summary, [data-agent-card-control]";

export function isRowControlTarget(target: EventTarget | null): boolean {
  return Boolean(target && (target as HTMLElement).closest?.(CONTROL_SELECTOR));
}

export function useRowGestures({
  onToggle,
  onActivate,
}: {
  /** Open/close the body; absent when the row has nothing to open. */
  onToggle?: () => void;
  /** Make this provider the active one; absent when the row cannot switch. */
  onActivate?: () => void;
}) {
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelPending = useCallback(() => {
    if (pending.current !== null) {
      clearTimeout(pending.current);
      pending.current = null;
    }
  }, []);

  // A row that unmounts mid-window (a refetch re-keyed the list) must not
  // toggle a component that no longer exists.
  useEffect(() => cancelPending, [cancelPending]);

  const onClick = useCallback(
    (e: React.MouseEvent) => {
      if (isRowControlTarget(e.target)) return;
      // The second click of a double: the first one is still waiting — drop
      // it, the dblclick handler takes over.
      if (e.detail >= 2) {
        cancelPending();
        return;
      }
      if (!onToggle) return;
      cancelPending();
      pending.current = setTimeout(() => {
        pending.current = null;
        onToggle();
      }, DOUBLE_CLICK_WINDOW_MS);
    },
    [onToggle, cancelPending],
  );

  const onDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      if (isRowControlTarget(e.target)) return;
      cancelPending();
      onActivate?.();
    },
    [onActivate, cancelPending],
  );

  return { onClick, onDoubleClick };
}
