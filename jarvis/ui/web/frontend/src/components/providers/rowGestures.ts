import { useCallback } from "react";
import type React from "react";

/**
 * The one pointer gesture a provider row understands: a click SELECTS it.
 *
 * Selecting means two things at once — the editor body opens, and the
 * provider becomes the active one (maintainer request 2026-08-23: "one
 * click on a row is enough — the row that is open is the active one"; this
 * replaced the earlier click-to-open / double-click-to-activate split, which
 * made every switch a two-gesture affair). The "Use" control still switches
 * without touching the body.
 *
 * The caller decides whether a row can be activated by a click at all: it
 * passes `onActivate` only for a row that would switch SILENTLY (a key is
 * saved, the row is not already active, the tier allows the switch). A row
 * that would only answer with a "save a key first" warning gets no
 * `onActivate`, so clicking it just opens the key field — which is what that
 * click was for.
 *
 * Click semantics, in order:
 *   closed row            → open it, then activate (if it can)
 *   open row, activatable → activate, keep it open (the open row IS the
 *                           active one; e.g. after a failed switch)
 *   open row, otherwise   → close it
 *
 * A double click is delivered as click, click, dblclick; the second click
 * (`detail >= 2`) is ignored so a quick double does not open-then-close the
 * row. No timer, no wait: the first click acts immediately.
 *
 * Clicks on the row's own controls — the Use radio, a button, a link, an
 * input, or anything marked `data-agent-card-control` — belong to those
 * controls and are ignored here.
 */
const CONTROL_SELECTOR =
  "input, label, button, a, select, textarea, summary, [data-agent-card-control]";

export function isRowControlTarget(target: EventTarget | null): boolean {
  return Boolean(target && (target as HTMLElement).closest?.(CONTROL_SELECTOR));
}

export function useRowGestures({
  expanded,
  onToggle,
  onActivate,
}: {
  /** Whether the body is currently open. */
  expanded: boolean;
  /** Open/close the body; absent when the row has nothing to open. */
  onToggle?: () => void;
  /** Make this provider the active one; absent when a click must not
   *  switch (the row is already active, cannot switch, or would only warn). */
  onActivate?: () => void;
}) {
  const select = useCallback(() => {
    if (!expanded) {
      onToggle?.();
      onActivate?.();
      return;
    }
    if (onActivate) {
      onActivate();
      return;
    }
    onToggle?.();
  }, [expanded, onToggle, onActivate]);

  const onClick = useCallback(
    (e: React.MouseEvent) => {
      if (isRowControlTarget(e.target)) return;
      // The second click of a double: the first already selected the row.
      if (e.detail >= 2) return;
      select();
    },
    [select],
  );

  return { onClick, select };
}
