import type { TerminalState } from "@/lib/agenticIdeApi";

/** One pane lifetime, independent of its reusable grid call-sign. */
export function chatTerminalIdentity(terminal: TerminalState): string {
  return terminal.history_id ?? `${terminal.key}@${terminal.started_at ?? "pending"}`;
}

/** Best available arrival order when this browser has not seen the workspace. */
export function initialChatOrder(terminals: readonly TerminalState[]): readonly string[] {
  return [...terminals]
    .sort((left, right) => {
      const leftStarted = left.started_at ?? Number.POSITIVE_INFINITY;
      const rightStarted = right.started_at ?? Number.POSITIVE_INFINITY;
      return leftStarted - rightStarted || left.index - right.index;
    })
    .map(chatTerminalIdentity);
}

/** Keep chat sessions in arrival order even when the grid is rearranged. */
export function reconcileChatOrder(
  previous: readonly string[],
  terminals: readonly TerminalState[],
): readonly string[] {
  const current = terminals.map(chatTerminalIdentity);
  const live = new Set(current);
  const next = previous.filter((key) => live.has(key));
  const known = new Set(next);
  for (const key of current) {
    if (!known.has(key)) {
      next.push(key);
      known.add(key);
    }
  }
  return sameStrings(previous, next) ? previous : next;
}

/** Exchange two session positions without disturbing any rows between them. */
export function swapChatOrder(
  previous: readonly string[],
  first: string,
  second: string,
): readonly string[] {
  if (first === second) return previous;
  const firstIndex = previous.indexOf(first);
  const secondIndex = previous.indexOf(second);
  if (firstIndex < 0 || secondIndex < 0) return previous;
  const next = [...previous];
  next[firstIndex] = second;
  next[secondIndex] = first;
  return next;
}

/** Put terminal objects into the stable order used by the chat rail. */
export function orderChatTerminals(
  terminals: readonly TerminalState[],
  identities: readonly string[],
): TerminalState[] {
  const byIdentity = new Map(
    terminals.map((terminal) => [chatTerminalIdentity(terminal), terminal]),
  );
  return identities.flatMap((identity) => {
    const terminal = byIdentity.get(identity);
    return terminal ? [terminal] : [];
  });
}

/**
 * Avoid repainting every terminal when a poll returns unchanged rows.
 *
 * Generic over the row type because two polls need it for the same reason:
 * the recap poll (sentences, every 5s) and the activity poll (status words,
 * every 1.5s) both replace a per-pane map on a timer inside a very large
 * component, and a fresh-but-equal object every tick is a full re-render
 * bought for nothing.
 */
export function sameRows<T extends object>(
  current: Readonly<Record<string, T>>,
  next: Readonly<Record<string, T>>,
): boolean {
  const currentNames = Object.keys(current);
  const nextNames = Object.keys(next);
  if (currentNames.length !== nextNames.length) return false;
  return nextNames.every((name) => {
    const before = current[name];
    const after = next[name];
    if (!before || !after) return false;
    const beforeFields = Object.keys(before) as (keyof T)[];
    const afterFields = Object.keys(after) as (keyof T)[];
    if (
      beforeFields.length !== afterFields.length ||
      afterFields.some((field) => !Object.hasOwn(before, field))
    ) {
      return false;
    }
    return afterFields.every((field) => Object.is(before[field], after[field]));
  });
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}
