/**
 * Avoid repainting every terminal when a poll returns unchanged rows.
 *
 * Generic over the row type because two polls need it for the same reason:
 * the recap poll (sentences, every 5s) and the activity poll (status words,
 * every 1.5s) both replace a per-pane map on a timer inside a very large
 * component, and a fresh-but-equal object every tick is a full re-render
 * bought for nothing.
 *
 * This module was `chatState.ts` while the Agentic IDE read its terminals as a
 * chat: it also held the rail's arrival order. That reading mode is gone — the
 * IDE's chat is the agent chat now — and what is left is this one helper, so
 * the file is named after what it actually does.
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
