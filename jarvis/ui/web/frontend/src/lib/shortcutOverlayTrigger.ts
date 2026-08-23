/**
 * When `?` should open the shortcut overlay — and, more importantly, when it
 * must not.
 *
 * `?` is a character people type. A global listener that opened a dialog every
 * time it appeared would eat the question mark out of a chat message, a wiki
 * note, a search box and every command typed into a terminal pane, which is a
 * far worse bug than the missing overlay it was meant to fix. So the guard
 * below is the substance of this feature, not a detail of it.
 *
 * Kept as a plain function on the event rather than inside the hook so the
 * rules are testable without mounting anything.
 */

/** Elements that are text entry by their tag alone. */
const TEXT_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/**
 * `<input type="checkbox">` and friends are not text entry, so `?` is free
 * there. Everything not on this list — including a bare `<input>` with no type
 * — is treated as text, because the safe default is to let the character
 * through.
 */
const NON_TEXT_INPUT_TYPES = new Set([
  "button",
  "checkbox",
  "color",
  "file",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
]);

export function isTextEntryTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  // Read the attribute as well as the property: `isContentEditable` is
  // computed from the rendered tree, so it is false for an element that is
  // editable but not attached to the document yet — and an element that is
  // about to receive typed text is exactly the case this must catch.
  const editable = target.getAttribute("contenteditable");
  if (target.isContentEditable || editable === "" || editable === "true") {
    return true;
  }
  if (target.closest?.('[contenteditable=""], [contenteditable="true"]')) {
    return true;
  }
  const tag = target.tagName;
  if (tag === "INPUT") {
    const type = (target as HTMLInputElement).type?.toLowerCase() ?? "text";
    return !NON_TEXT_INPUT_TYPES.has(type);
  }
  if (TEXT_TAGS.has(tag)) return true;
  // xterm renders an off-screen textarea that is caught above, but a pane can
  // also mark a whole region as keyboard-owning; honour that claim.
  return Boolean(target.closest?.('[data-keyboard-capture="true"]'));
}

export interface OverlayTriggerEvent {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  defaultPrevented: boolean;
  target: EventTarget | null;
}

/**
 * True when this keystroke is a bare `?` typed outside any text entry.
 *
 * Shift is allowed and expected: `?` is Shift plus `/` on a US layout, and Shift
 * plus the key right of `0` on a German one. Reading `event.key` rather than a
 * physical code is what makes both work without a per-layout table.
 * Ctrl/Cmd/Alt disqualify: those chords belong to whatever else claims them.
 */
export function shouldOpenShortcutOverlay(event: OverlayTriggerEvent): boolean {
  if (event.defaultPrevented) return false;
  if (event.key !== "?") return false;
  if (event.ctrlKey || event.metaKey || event.altKey) return false;
  return !isTextEntryTarget(event.target);
}
