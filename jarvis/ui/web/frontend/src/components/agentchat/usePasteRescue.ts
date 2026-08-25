import { useCallback, useEffect, useRef } from "react";

import { robustPaste } from "@/lib/clipboard";
import { captureEditSnapshot, pasteInto } from "@/lib/editActions";

/**
 * Make Ctrl+V put text in the box even where the embedded browser never sends
 * a `paste` event.
 *
 * In a real browser this hook does nothing at all, and that is the design. A
 * `<textarea>` pastes on its own; nothing here competes with it.
 *
 * The desktop shell is the reason it exists. Its WebView already withholds
 * `clipboard-read` — measured in 2026-07, `navigator.clipboard.readText()`
 * inside it never settles, resolved or rejected, which is why the right-click
 * menu reads the clipboard through the host process instead (lib/clipboard).
 * A build that also declines to deliver the `paste` event leaves Ctrl+V doing
 * nothing whatsoever in a text box, with no error anywhere to explain it.
 *
 * ## How it can tell the difference
 *
 * A real paste delivers its event in the SAME task as the keystroke. So the
 * keystroke arms a one-shot timer and the `paste` event disarms it; if the
 * timer still fires, no paste was delivered and the text is fetched through
 * the host route and inserted at the caret that was saved when the key went
 * down. The window is deliberately short — long enough to outlast a busy main
 * thread, far shorter than a person's next keystroke — because the failure
 * mode to avoid is pasting twice.
 *
 * `pasteInto` goes through `insertText`, so the caret, the undo history and
 * React's controlled value all survive (lib/editActions).
 */

/**
 * How long a real `paste` has to arrive before the keystroke counts as
 * unanswered. Browsers deliver it synchronously; this allows for a main thread
 * busy with a streaming turn.
 */
const RESCUE_AFTER_MS = 150;

export interface PasteRescue {
  /** Call from the field's `onKeyDown`, before anything else. */
  onKeyDown: (event: React.KeyboardEvent<HTMLElement>) => void;
  /** Call from the field's `onPaste` — this is what proves the event arrives. */
  onPaste: () => void;
}

export function usePasteRescue(): PasteRescue {
  const timerRef = useRef<number | undefined>(undefined);

  const disarm = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
  }, []);

  // A component unmounting mid-window must not paste into a field that is gone.
  useEffect(() => disarm, [disarm]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      const key = event.key.toLowerCase();
      if (key !== "v" || !(event.ctrlKey || event.metaKey) || event.altKey) return;
      // Captured now: by the time the timer fires the caret may have moved,
      // and the position the text belongs at is this one.
      const snapshot = captureEditSnapshot(event.currentTarget);
      if (!snapshot.editable) return;
      disarm();
      timerRef.current = window.setTimeout(() => {
        timerRef.current = undefined;
        void (async () => {
          const text = await robustPaste();
          // Null = no route could read the clipboard; empty = nothing on it.
          // Neither is an error worth a message: the person pressed a key and
          // the box stayed as it was, which is what an empty clipboard means.
          if (text) pasteInto(snapshot, text);
        })();
      }, RESCUE_AFTER_MS);
    },
    [disarm],
  );

  return { onKeyDown, onPaste: disarm };
}
