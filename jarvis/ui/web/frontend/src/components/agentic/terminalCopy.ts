/**
 * Desktop-style copy shortcuts for an xterm pane.
 *
 * xterm normally turns Ctrl+C into the terminal control code `^C`. That is a
 * useful shell convention, but it is a dangerous surprise inside Jarvis's
 * desktop IDE: every other surface treats Ctrl+C as Copy, while a Codex pane
 * interprets it as "cancel the current turn" (and a second press may exit).
 *
 * The pane therefore owns the platform copy chord. With a selection it copies
 * xterm's canvas-backed text; without one it deliberately does nothing. In
 * both cases the chord stays out of the PTY, so an attempted copy can never
 * stop the agent.
 */

export interface CopyCapableTerminal {
  attachCustomKeyEventHandler(
    handler: (event: KeyboardEvent) => boolean,
  ): void | (() => void);
  getSelection(): string;
  focus(): void;
}

export interface CopyBridgeOptions {
  copy: (text: string) => Promise<boolean>;
  /** True on macOS, where the desktop copy chord is Cmd+C. */
  isMac: boolean;
  onUnavailable?: () => void;
}

/** Match the platform copy chord without stealing AltGr input. */
export function isCopyChord(event: KeyboardEvent, isMac: boolean): boolean {
  if (event.altKey) return false;
  if ((event.key || "").toLowerCase() !== "c") return false;
  return isMac
    ? event.metaKey && !event.ctrlKey
    : event.ctrlKey && !event.metaKey;
}

/** Install the copy shortcut and return a cleanup function. */
export function installCopyBridge(
  term: CopyCapableTerminal,
  options: CopyBridgeOptions,
): () => void {
  let disposed = false;
  const disposeKeyHandler = term.attachCustomKeyEventHandler((event) => {
    if (event.type !== "keydown") return true;
    if (!isCopyChord(event, options.isMac)) return true;

    // Claim the chord even when there is no selection. Letting that one case
    // fall through is precisely how an innocent Copy attempt cancelled Codex.
    event.preventDefault();
    const selected = term.getSelection();
    if (selected) {
      void options
        .copy(selected)
        .then((copied) => {
          if (!copied && !disposed) options.onUnavailable?.();
        })
        .catch(() => {
          if (!disposed) options.onUnavailable?.();
        })
        .finally(() => {
          if (!disposed) term.focus();
        });
    }
    return false;
  });

  return () => {
    disposed = true;
    if (typeof disposeKeyHandler === "function") disposeKeyHandler();
  };
}
