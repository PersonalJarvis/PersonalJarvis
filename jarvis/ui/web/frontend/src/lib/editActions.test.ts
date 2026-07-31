import { afterEach, describe, expect, it, vi } from "vitest";

import {
  attachTerminalBridge,
  capabilitiesFor,
  captureEditSnapshot,
  deleteSelection,
  findTerminalBridge,
  pasteInto,
  selectAll,
} from "./editActions";

/* jsdom implements neither execCommand nor a canvas terminal, which is exactly
 * the situation these helpers are built to survive: every action falls back to
 * a manual path, and terminals arrive through the registered bridge. */

afterEach(() => {
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

function makeTextarea(value: string, start?: number, end?: number) {
  const el = document.createElement("textarea");
  el.value = value;
  document.body.appendChild(el);
  el.focus();
  el.setSelectionRange(start ?? value.length, end ?? start ?? value.length);
  return el;
}

describe("captureEditSnapshot", () => {
  it("treats a writable textarea as an editable field", () => {
    const el = makeTextarea("hello world", 0, 5);
    const snap = captureEditSnapshot(el);

    expect(snap.kind).toBe("field");
    expect(snap.editable).toBe(true);
    expect(snap.selectedText).toBe("hello");
    expect(snap.selectionStart).toBe(0);
    expect(snap.selectionEnd).toBe(5);
  });

  it("marks a read-only field as not editable", () => {
    const el = makeTextarea("locked");
    el.readOnly = true;
    expect(captureEditSnapshot(el).editable).toBe(false);
  });

  it("ignores non-text inputs such as checkboxes", () => {
    const el = document.createElement("input");
    el.type = "checkbox";
    document.body.appendChild(el);
    expect(captureEditSnapshot(el).kind).toBe("plain");
  });

  it("never offers to write into a file input", () => {
    const el = document.createElement("input");
    el.type = "file";
    document.body.appendChild(el);
    expect(capabilitiesFor(captureEditSnapshot(el)).canPaste).toBe(false);
  });

  it("resolves a click inside a terminal to that terminal's bridge", () => {
    const host = document.createElement("div");
    const inner = document.createElement("span");
    host.appendChild(inner);
    document.body.appendChild(host);
    attachTerminalBridge(host, {
      getSelection: () => "selected in terminal",
      paste: () => {},
      focus: () => {},
    });

    const snap = captureEditSnapshot(inner);
    expect(snap.kind).toBe("terminal");
    expect(snap.selectedText).toBe("selected in terminal");
    expect(snap.editable).toBe(true);
  });

  it("returns no bridge for an element outside any terminal", () => {
    const el = document.createElement("div");
    document.body.appendChild(el);
    expect(findTerminalBridge(el)).toBeNull();
  });
});

describe("pasteInto", () => {
  it("inserts at the caret rather than replacing the whole field", () => {
    const el = makeTextarea("ab", 1, 1);
    const snap = captureEditSnapshot(el);

    expect(pasteInto(snap, "XY")).toBe(true);
    expect(el.value).toBe("aXYb");
  });

  it("replaces the selected text", () => {
    const el = makeTextarea("keep DROP keep", 5, 9);
    const snap = captureEditSnapshot(el);

    pasteInto(snap, "NEW");
    expect(el.value).toBe("keep NEW keep");
  });

  it("emits an input event so a React-controlled field updates", () => {
    const el = makeTextarea("", 0, 0);
    const onInput = vi.fn();
    el.addEventListener("input", onInput);

    pasteInto(captureEditSnapshot(el), "typed by menu");
    expect(onInput).toHaveBeenCalledTimes(1);
  });

  it("routes a terminal paste through the bridge", () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const paste = vi.fn();
    attachTerminalBridge(host, {
      getSelection: () => "",
      paste,
      focus: () => {},
    });

    expect(pasteInto(captureEditSnapshot(host), "ls -la")).toBe(true);
    expect(paste).toHaveBeenCalledWith("ls -la");
  });

  it("refuses to write into a read-only field", () => {
    const el = makeTextarea("locked");
    el.readOnly = true;
    expect(pasteInto(captureEditSnapshot(el), "nope")).toBe(false);
    expect(el.value).toBe("locked");
  });

  it("does nothing when the clipboard was empty", () => {
    const el = makeTextarea("unchanged");
    expect(pasteInto(captureEditSnapshot(el), "")).toBe(false);
    expect(el.value).toBe("unchanged");
  });
});

describe("deleteSelection", () => {
  it("removes exactly the selected range", () => {
    const el = makeTextarea("keep DROP keep", 5, 10);
    expect(deleteSelection(captureEditSnapshot(el))).toBe(true);
    expect(el.value).toBe("keep keep");
  });

  it("never edits a terminal's scrollback", () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    attachTerminalBridge(host, {
      getSelection: () => "some output",
      paste: () => {},
      focus: () => {},
    });
    expect(deleteSelection(captureEditSnapshot(host))).toBe(false);
  });
});

describe("selectAll", () => {
  it("selects the whole field", () => {
    const el = makeTextarea("select all of this", 0, 0);
    expect(selectAll(captureEditSnapshot(el))).toBe(true);
    expect(el.selectionStart).toBe(0);
    expect(el.selectionEnd).toBe("select all of this".length);
  });
});

describe("capabilitiesFor", () => {
  it("offers cut and copy only when something is selected", () => {
    const el = makeTextarea("nothing selected", 3, 3);
    const caps = capabilitiesFor(captureEditSnapshot(el));
    expect(caps.canCut).toBe(false);
    expect(caps.canCopy).toBe(false);
    expect(caps.canPaste).toBe(true);
    expect(caps.canSelectAll).toBe(true);
  });

  it("offers copy but not cut for a terminal selection", () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    attachTerminalBridge(host, {
      getSelection: () => "output text",
      paste: () => {},
      focus: () => {},
    });

    const caps = capabilitiesFor(captureEditSnapshot(host));
    expect(caps.canCopy).toBe(true);
    expect(caps.canCut).toBe(false);
    expect(caps.canPaste).toBe(true);
    expect(caps.canSelectAll).toBe(false);
  });

  it("offers copy on plain page text with a selection", () => {
    const p = document.createElement("p");
    p.textContent = "some prose";
    document.body.appendChild(p);
    const range = document.createRange();
    range.selectNodeContents(p);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);

    const caps = capabilitiesFor(captureEditSnapshot(p));
    expect(caps.canCopy).toBe(true);
    expect(caps.canPaste).toBe(false);
  });
});
