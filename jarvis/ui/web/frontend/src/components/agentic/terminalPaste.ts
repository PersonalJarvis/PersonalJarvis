/**
 * Making Ctrl+V / Cmd+V paste into a terminal pane.
 *
 * ## Why this file has to exist
 *
 * xterm.js emulates a real terminal, and in a real terminal Ctrl+<letter> is a
 * CONTROL CODE, not an editing shortcut: `evaluateKeyboardEvent` turns Ctrl+V
 * into character 22 (`^V`, "quote the next key") and `Terminal._keyDown` then
 * cancels the browser event. Cancelling is what actually breaks pasting — the
 * browser never runs its own paste, so no `paste` event is ever fired, and
 * xterm's own (correct, bracketing) paste handler is never reached. The user
 * presses Ctrl+V and nothing at all happens.
 *
 * That is right for a terminal on a Linux desktop, where paste is bound to
 * Ctrl+Shift+V. It is wrong here: these panes are part of a desktop
 * application, and every terminal people actually use on Windows and macOS
 * (Windows Terminal, iTerm, the VS Code terminal) pastes on Ctrl+V / Cmd+V.
 *
 * It also silently disabled a whole category of assistive software. Dictation
 * tools, text expanders, clipboard managers and password-manager auto-type all
 * insert text the same way: put it on the clipboard and send Ctrl+V. Their own
 * logs then report success — they sent the keystroke, and nobody tells them it
 * was swallowed — so the failure surfaces only as "my dictation does nothing
 * in that window" (confirmed 2026-07-26 against the insertion log of a
 * commercial dictation app, which recorded the paste as delivered).
 *
 * ## How it is fixed, and why not more simply
 *
 * The browser's own paste is still the best path when it works: it needs no
 * clipboard permission, it carries images as well as text, and it ends up in
 * xterm's bracketed-paste handler, which is what a coding agent's TUI expects.
 * So this bridge does not replace it — it stops xterm from swallowing the
 * chord and lets the browser proceed.
 *
 * The embedded desktop WebView, however, does not always deliver that paste
 * (it withholds clipboard access from the page; measured 2026-07-26:
 * `navigator.clipboard.readText()` there never settles at all). A fix that
 * only handed the chord back to the browser would therefore work in a browser
 * and stay broken in the desktop app — which is where it was reported. Hence
 * the second half: if no clipboard payload shows up within a short window, the
 * text is read through the operating system instead and pasted directly.
 *
 * The two paths cannot both run for one keystroke. A browser executes the
 * paste as the key event's default action, in the same task as the keydown, so
 * the `paste` event always arrives long before the fallback timer — and when
 * it arrives it disarms the timer.
 */

/** The slice of an xterm terminal this bridge drives. */
export interface PasteCapableTerminal {
  attachCustomKeyEventHandler(handler: (event: KeyboardEvent) => boolean): void;
  /** Write text into the terminal as a paste (bracketed where supported). */
  paste(text: string): void;
}

export interface PasteBridgeOptions {
  /**
   * Read the clipboard when the embedded browser would not.
   *
   * `null` means no path could read it at all; `""` means the clipboard is
   * genuinely empty, which is not an error and must stay distinguishable.
   */
  readClipboard: () => Promise<string | null>;
  /** True on macOS, where the paste chord is Cmd+V rather than Ctrl+V. */
  isMac: boolean;
  /** How long to wait for the browser's own paste before reading the OS. */
  fallbackDelayMs?: number;
  /** The clipboard could not be read on any path — worth telling the user. */
  onUnavailable?: () => void;
}

/**
 * Long enough that a browser paste always wins the race (it happens in the
 * same task as the keydown), short enough to feel instant.
 */
const DEFAULT_FALLBACK_DELAY_MS = 140;

/**
 * Is this the keystroke the user means as "paste"?
 *
 * Matched on `key` rather than the physical `code` so it follows the keyboard
 * LAYOUT — on AZERTY or Dvorak, paste is the key that prints "v", not the key
 * sitting where a US keyboard has one.
 *
 * Alt is excluded on purpose: on many European layouts AltGr arrives as
 * Ctrl+Alt, and those combinations type real characters.
 *
 * Ctrl+Shift+V (Cmd+Shift+V) counts too — the traditional terminal binding,
 * which people coming from Linux will reach for first.
 */
export function isPasteChord(event: KeyboardEvent, isMac: boolean): boolean {
  if (event.altKey) return false;
  if ((event.key || "").toLowerCase() !== "v") return false;
  return isMac
    ? event.metaKey && !event.ctrlKey
    : event.ctrlKey && !event.metaKey;
}

/**
 * Wire the paste chord into `term`, returning a cleanup function.
 *
 * The clipboard listener is registered in the CAPTURE phase deliberately.
 * xterm's own paste handler calls `stopPropagation()`, so anything listening
 * on an ancestor in the normal (bubbling) phase never runs at all — capture
 * travels downwards and reaches this container before xterm can stop it.
 */
export function installPasteBridge(
  term: PasteCapableTerminal,
  container: HTMLElement,
  options: PasteBridgeOptions,
): () => void {
  const delay = options.fallbackDelayMs ?? DEFAULT_FALLBACK_DELAY_MS;
  // Bumped for every chord and every delivered paste, so a timer that fires
  // after its keystroke was already served can tell that it is stale.
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const disarm = (): void => {
    generation += 1;
    if (timer !== undefined) {
      clearTimeout(timer);
      timer = undefined;
    }
  };

  const onPasteCapture = (event: ClipboardEvent): void => {
    const data = event.clipboardData;
    if (!data) return;
    // Files count as well as text: a pasted screenshot is handled by the pane's
    // own attach handler, and reading the OS clipboard afterwards would append
    // whatever text happened to sit beside the image.
    const carriesText = data.getData("text/plain") !== "";
    const carriesFile = Array.from(data.items ?? []).some(
      (item) => item.kind === "file",
    );
    if (carriesText || carriesFile) disarm();
  };

  term.attachCustomKeyEventHandler((event) => {
    if (event.type !== "keydown") return true;
    if (!isPasteChord(event, options.isMac)) return true;
    // `false` means: xterm, keep your hands off this one. It returns before
    // both the `^V` data event and the `cancel()` that suppresses the browser's
    // own paste — which is the whole point.
    disarm();
    const mine = generation;
    // Read the clipboard NOW rather than when the timer fires, and that timing
    // is load-bearing. Dictation and voice-typing tools insert text by putting
    // it on the clipboard, sending Ctrl+V, and then putting the user's PREVIOUS
    // clipboard back — measured 2026-07-26. Asking for the clipboard
    // after the wait can land on the far side of that restore and paste the
    // wrong text — intermittently, which is the worst way for it to be wrong.
    // The wait below is only there to give the browser its chance; it waits on
    // an answer that is already in flight.
    const pending = options.readClipboard().catch(() => null);
    timer = setTimeout(() => {
      timer = undefined;
      void (async () => {
        const text = await pending;
        // A browser paste landed while the clipboard was being read — it has
        // already inserted the text, so inserting it again would double it.
        if (generation !== mine) return;
        if (text === null) options.onUnavailable?.();
        else if (text !== "") term.paste(text);
      })();
    }, delay);
    return false;
  });

  container.addEventListener("paste", onPasteCapture, true);
  return () => {
    disarm();
    container.removeEventListener("paste", onPasteCapture, true);
  };
}
