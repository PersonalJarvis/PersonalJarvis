/**
 * Sharing xterm's ONE custom key handler between several bridges.
 *
 * `attachCustomKeyEventHandler` reads like a subscription but is a setter:
 * xterm keeps a single handler, and a second call silently replaces the first.
 * Two features in a terminal pane need to claim keystrokes — pasting
 * (./terminalPaste) and the newline chord (./terminalNewline) — so whichever
 * was wired last would have switched the other one off, with no error and
 * nothing in the console to notice it by.
 *
 * The chain registers one real handler and offers the event to its members in
 * the order they were added, stopping at the first that claims it. "Claims" is
 * xterm's own convention: returning `false` means "xterm, keep your hands off
 * this one", and a handler that says so must also be the last to see the key —
 * otherwise two bridges would act on the same keystroke.
 */

/** The slice of an xterm terminal this chain drives. */
export interface KeyHandlerHost {
  attachCustomKeyEventHandler(handler: (event: KeyboardEvent) => boolean): void;
}

/** `true` lets the key through to xterm, `false` claims it. */
export type KeyEventHandler = (event: KeyboardEvent) => boolean;

export interface KeyEventChain {
  /** Add a handler, returning a function that removes it again. */
  add(handler: KeyEventHandler): () => void;
}

export function createKeyEventChain(host: KeyHandlerHost): KeyEventChain {
  const handlers: KeyEventHandler[] = [];

  host.attachCustomKeyEventHandler((event) =>
    // A copy, because a handler is allowed to remove itself while running.
    // `every` short-circuits, which is exactly the contract above: once one has
    // claimed the keystroke, the ones behind it must not see it as well.
    handlers.slice().every((handler) => handler(event)),
  );

  return {
    add(handler) {
      handlers.push(handler);
      return () => {
        const at = handlers.indexOf(handler);
        if (at >= 0) handlers.splice(at, 1);
      };
    },
  };
}
