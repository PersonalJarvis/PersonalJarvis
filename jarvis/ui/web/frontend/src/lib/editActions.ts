/**
 * Cut / copy / paste / select-all against whatever the user right-clicked.
 *
 * Background: the desktop shell runs the UI inside an embedded WebView whose
 * built-in context menu is switched off, so the browser's own Cut/Copy/Paste
 * entries do not exist anywhere in the app. This module is the replacement's
 * engine — deliberately free of React so it can be tested directly.
 *
 * Two details drive the design:
 *
 *  - **The click steals the focus.** Activating a menu item moves focus off the
 *    field, taking the caret with it. Every action therefore takes a snapshot
 *    captured at right-click time and restores focus + selection first.
 *  - **Terminals are not text.** The IDE terminals paint onto a canvas, so the
 *    browser selection is empty there and inserting characters does nothing.
 *    A terminal registers a small bridge on its container instead.
 */

export interface TerminalBridge {
  /** Text the user has selected inside the terminal ("" when none). */
  getSelection(): string;
  /** Write text into the terminal as a paste (bracketed where supported). */
  paste(text: string): void;
  focus(): void;
}

const BRIDGE_KEY = "__jarvisTerminalBridge";
const TERMINAL_SELECTOR = "[data-jarvis-terminal]";

/** Register a terminal so the context menu can copy from / paste into it. */
export function attachTerminalBridge(
  element: HTMLElement,
  bridge: TerminalBridge,
): void {
  element.setAttribute("data-jarvis-terminal", "");
  (element as unknown as Record<string, unknown>)[BRIDGE_KEY] = bridge;
}

/** Find the bridge of the terminal containing `element`, if any. */
export function findTerminalBridge(
  element: Element | null,
): TerminalBridge | null {
  const host = element?.closest?.(TERMINAL_SELECTOR) ?? null;
  if (!host) return null;
  const bridge = (host as unknown as Record<string, unknown>)[BRIDGE_KEY];
  return (bridge as TerminalBridge) ?? null;
}

export type EditTargetKind = "field" | "terminal" | "plain";

/**
 * Everything an action needs, frozen at right-click time.
 *
 * `selectionStart`/`selectionEnd` are captured here because activating a menu
 * item destroys them.
 */
export interface EditSnapshot {
  kind: EditTargetKind;
  element: HTMLElement | null;
  bridge: TerminalBridge | null;
  /** Text currently selected, wherever the selection lives. */
  selectedText: string;
  /** True when text can be written at the target. */
  editable: boolean;
  selectionStart: number | null;
  selectionEnd: number | null;
  /** Saved DOM range for contenteditable targets. */
  range: Range | null;
}

function isTextInput(
  element: Element | null,
): element is HTMLInputElement | HTMLTextAreaElement {
  if (!element) return false;
  const tag = element.tagName;
  if (tag === "TEXTAREA") return true;
  if (tag !== "INPUT") return false;
  // Only inputs that hold editable text — a checkbox or a colour picker has
  // nothing to cut, and a file input must never be writable from a menu.
  const type = (element as HTMLInputElement).type.toLowerCase();
  return [
    "text",
    "search",
    "url",
    "tel",
    "email",
    "password",
    "number",
    "",
  ].includes(type);
}

function isWritable(element: HTMLInputElement | HTMLTextAreaElement): boolean {
  return !element.disabled && !element.readOnly;
}

/** Capture what was right-clicked, before any focus moves. */
export function captureEditSnapshot(target: EventTarget | null): EditSnapshot {
  const element = target instanceof HTMLElement ? target : null;
  const docSelection =
    typeof window !== "undefined" ? window.getSelection() : null;

  const bridge = findTerminalBridge(element);
  if (bridge) {
    return {
      kind: "terminal",
      element,
      bridge,
      selectedText: bridge.getSelection() || "",
      editable: true,
      selectionStart: null,
      selectionEnd: null,
      range: null,
    };
  }

  if (isTextInput(element)) {
    const start = element.selectionStart;
    const end = element.selectionEnd;
    const selected =
      start !== null && end !== null && end > start
        ? element.value.slice(start, end)
        : "";
    return {
      kind: "field",
      element,
      bridge: null,
      selectedText: selected,
      editable: isWritable(element),
      selectionStart: start,
      selectionEnd: end,
      range: null,
    };
  }

  const editableHost = element?.closest?.<HTMLElement>(
    '[contenteditable="true"]',
  );
  if (editableHost) {
    const range =
      docSelection && docSelection.rangeCount > 0
        ? docSelection.getRangeAt(0).cloneRange()
        : null;
    return {
      kind: "field",
      element: editableHost,
      bridge: null,
      selectedText: docSelection?.toString() ?? "",
      editable: true,
      selectionStart: null,
      selectionEnd: null,
      range,
    };
  }

  return {
    kind: "plain",
    element,
    bridge: null,
    selectedText: docSelection?.toString() ?? "",
    editable: false,
    selectionStart: null,
    selectionEnd: null,
    range: null,
  };
}

/** Put focus and caret back where they were when the menu opened. */
function restore(snapshot: EditSnapshot): void {
  const { element } = snapshot;
  if (!element) return;
  if (snapshot.bridge) {
    snapshot.bridge.focus();
    return;
  }
  element.focus({ preventScroll: true });
  if (
    isTextInput(element) &&
    snapshot.selectionStart !== null &&
    snapshot.selectionEnd !== null
  ) {
    element.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd);
    return;
  }
  if (snapshot.range && typeof window !== "undefined") {
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(snapshot.range);
  }
}

/**
 * Insert `text` at the caret.
 *
 * `insertText` is used rather than assigning `.value` because it keeps the
 * caret position, keeps undo history intact, and emits the native `input`
 * event that React's controlled inputs listen for — a direct `.value` write is
 * invisible to React and gets overwritten on the next render.
 */
export function pasteInto(snapshot: EditSnapshot, text: string): boolean {
  if (!text) return false;
  restore(snapshot);

  if (snapshot.bridge) {
    snapshot.bridge.paste(text);
    return true;
  }
  if (!snapshot.editable) return false;

  try {
    if (document.execCommand("insertText", false, text)) return true;
  } catch {
    // Fall through to the manual path below.
  }
  return insertTextManually(snapshot, text);
}

/** Fallback for engines where `insertText` is unavailable or refused. */
function insertTextManually(snapshot: EditSnapshot, text: string): boolean {
  const element = snapshot.element;
  if (!isTextInput(element)) return false;

  const start = snapshot.selectionStart ?? element.value.length;
  const end = snapshot.selectionEnd ?? start;
  const next = element.value.slice(0, start) + text + element.value.slice(end);

  // React tracks the previous value on the DOM node and skips the change event
  // when it looks unchanged; going through the prototype setter defeats that.
  const prototype =
    element.tagName === "TEXTAREA"
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  if (setter) setter.call(element, next);
  else element.value = next;

  const caret = start + text.length;
  element.setSelectionRange(caret, caret);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  return true;
}

/** Remove the selected text from an editable target. */
export function deleteSelection(snapshot: EditSnapshot): boolean {
  if (!snapshot.editable || !snapshot.selectedText) return false;
  if (snapshot.bridge) return false; // a terminal's scrollback is not editable
  restore(snapshot);
  try {
    if (document.execCommand("delete")) return true;
  } catch {
    // Fall through.
  }
  return insertTextManually(snapshot, "");
}

/** Select everything at the target. */
export function selectAll(snapshot: EditSnapshot): boolean {
  const element = snapshot.element;
  if (!element) return false;
  if (isTextInput(element)) {
    element.focus({ preventScroll: true });
    element.select();
    return true;
  }
  element.focus?.({ preventScroll: true });
  try {
    return document.execCommand("selectAll");
  } catch {
    return false;
  }
}

/** Which menu entries make sense for this target. */
export interface EditCapabilities {
  canCut: boolean;
  canCopy: boolean;
  canPaste: boolean;
  canSelectAll: boolean;
}

export function capabilitiesFor(snapshot: EditSnapshot): EditCapabilities {
  const hasSelection = snapshot.selectedText.length > 0;
  return {
    canCut: snapshot.editable && hasSelection && snapshot.kind === "field",
    canCopy: hasSelection,
    canPaste: snapshot.editable,
    canSelectAll: snapshot.kind !== "terminal",
  };
}
