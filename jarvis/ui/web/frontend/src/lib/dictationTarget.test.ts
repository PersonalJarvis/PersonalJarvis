import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { attachTerminalBridge } from "./editActions";
import {
  deliverDictationText,
  installDictationFocusTracker,
  isForThisWindow,
  resetDictationTarget,
} from "./dictationTarget";

/*
 * The regression under test is a dictation that vanished: inside Jarvis's own
 * window the text was never typed in, it was handed to the chat composer — a
 * component that only exists on one section. Every case below is a place where
 * a user really dictates and the words used to go nowhere.
 *
 * jsdom implements no execCommand, so every insertion here takes the manual
 * fallback path in editActions — which is also the path the desktop WebView
 * takes when it refuses insertText, so it is worth pinning.
 */

let removeTracker: (() => void) | null = null;

beforeEach(() => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  resetDictationTarget();
  removeTracker = installDictationFocusTracker();
});

afterEach(() => {
  removeTracker?.();
  removeTracker = null;
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

function mountField(value = ""): HTMLTextAreaElement {
  const field = document.createElement("textarea");
  field.value = value;
  document.body.appendChild(field);
  return field;
}

function mountTerminal(): { host: HTMLElement; pasted: string[] } {
  const host = document.createElement("div");
  // xterm reads the keyboard through a hidden textarea inside its container;
  // that textarea — not the container — is what holds focus.
  const helper = document.createElement("textarea");
  host.appendChild(helper);
  document.body.appendChild(host);
  const pasted: string[] = [];
  attachTerminalBridge(host, {
    getSelection: () => "",
    paste: (text) => pasted.push(text),
    focus: () => helper.focus(),
  });
  return { host, pasted };
}

describe("deliverDictationText", () => {
  it("writes into the field that has focus, at the caret", () => {
    const field = mountField("before after");
    field.focus();
    field.setSelectionRange(7, 7);

    expect(deliverDictationText("dictated ")).toBe("field");
    expect(field.value).toBe("before dictated after");
  });

  it("pastes through the terminal bridge, not into its hidden textarea", () => {
    const { host, pasted } = mountTerminal();
    (host.firstElementChild as HTMLTextAreaElement).focus();

    expect(deliverDictationText("run the tests")).toBe("terminal");
    expect(pasted).toEqual(["run the tests"]);
    // Writing into the helper textarea would look like success and do nothing.
    expect((host.firstElementChild as HTMLTextAreaElement).value).toBe("");
  });

  it("falls back to the last field when a button took the focus", () => {
    const field = mountField("");
    field.focus();
    const button = document.createElement("button");
    button.dataset.jarvisDictationTrigger = "true";
    document.body.appendChild(button);
    button.focus();

    expect(deliverDictationText("still mine")).toBe("field");
    expect(field.value).toBe("still mine");
  });

  it("does not fall back to a stale field after an unrelated control took focus", () => {
    const field = mountField("");
    field.focus();
    const button = document.createElement("button");
    document.body.appendChild(button);
    button.focus();

    expect(deliverDictationText("not the old field")).toBe("none");
    expect(field.value).toBe("");
  });

  it("does not deliver into a field whose view was unmounted", () => {
    const field = mountField("");
    field.focus();
    field.remove();

    expect(deliverDictationText("nowhere")).toBe("none");
  });

  it("refuses a read-only field rather than silently doing nothing", () => {
    const field = mountField("locked");
    field.readOnly = true;
    field.focus();

    expect(deliverDictationText("nope")).toBe("none");
    expect(field.value).toBe("locked");
  });

  it("reports none when nothing on screen can take text", () => {
    expect(deliverDictationText("into the void")).toBe("none");
  });

  it("treats a blank transcript as nothing to deliver", () => {
    const field = mountField("kept");
    field.focus();

    expect(deliverDictationText("   ")).toBe("none");
    expect(field.value).toBe("kept");
  });

  it("prefers what has focus NOW over what had it before", () => {
    const first = mountField("");
    first.focus();
    const second = mountField("");
    second.focus();

    expect(deliverDictationText("second")).toBe("field");
    expect(first.value).toBe("");
    expect(second.value).toBe("second");
  });

  it("does not paste a broadcast from a background client", () => {
    const field = mountField("");
    field.focus();
    vi.mocked(document.hasFocus).mockReturnValue(false);

    expect(deliverDictationText("one client only")).toBe("inactive");
    expect(field.value).toBe("");
  });

  it("does not paste a broadcast from a hidden client", () => {
    const field = mountField("");
    field.focus();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");

    expect(deliverDictationText("one tab only")).toBe("inactive");
    expect(field.value).toBe("");
  });
});

describe("isForThisWindow", () => {
  /* The dangerous direction: the same event fires for a dictation the backend
   * is pasting into ANOTHER program. Treating that one as ours would duplicate
   * it into a Jarvis field nobody is looking at. */
  it("refuses a transcript the backend is pasting elsewhere", () => {
    expect(isForThisWindow({ text: "for notepad", target: "insert" }, false)).toBe(
      false,
    );
  });

  it("refuses a transcript from a backend too old to say", () => {
    expect(isForThisWindow({ text: "unknown route" }, false)).toBe(false);
  });

  it("accepts a transcript meant for this window", () => {
    expect(isForThisWindow({ text: "for us", target: "chat" }, false)).toBe(true);
  });

  it("refuses the empty final an aborted dictation publishes", () => {
    expect(isForThisWindow({ text: "", target: "chat" }, false)).toBe(false);
    expect(isForThisWindow({ text: "  ", target: "chat" }, false)).toBe(false);
  });

  it("leaves the composer's own microphone button to the composer", () => {
    expect(isForThisWindow({ text: "typed live", target: "chat" }, true)).toBe(
      false,
    );
  });
});

describe("installDictationFocusTracker", () => {
  it("stops remembering once removed", () => {
    removeTracker?.();
    removeTracker = null;
    const field = mountField("");
    field.focus();
    const button = document.createElement("button");
    document.body.appendChild(button);
    button.focus();

    // Focus moved to the button before the field was ever recorded, so there is
    // no remembered target left to fall back to.
    expect(deliverDictationText("lost")).toBe("none");
  });
});
