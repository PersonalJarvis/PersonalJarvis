/**
 * `?` is a character people type. These tests are the guard that it keeps
 * being one everywhere text is entered.
 */
import { describe, expect, it } from "vitest";
import {
  isTextEntryTarget,
  shouldOpenShortcutOverlay,
} from "./shortcutOverlayTrigger";

function ev(over: Partial<Parameters<typeof shouldOpenShortcutOverlay>[0]> = {}) {
  return {
    key: "?",
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    defaultPrevented: false,
    target: null,
    ...over,
  };
}

function el(html: string): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = html;
  return host.firstElementChild as HTMLElement;
}

describe("shouldOpenShortcutOverlay", () => {
  it("opens on a bare question mark", () => {
    expect(shouldOpenShortcutOverlay(ev())).toBe(true);
  });

  it("ignores any other key", () => {
    expect(shouldOpenShortcutOverlay(ev({ key: "/" }))).toBe(false);
    expect(shouldOpenShortcutOverlay(ev({ key: "Escape" }))).toBe(false);
  });

  it("leaves Ctrl / Cmd / Alt chords to whoever claims them", () => {
    expect(shouldOpenShortcutOverlay(ev({ ctrlKey: true }))).toBe(false);
    expect(shouldOpenShortcutOverlay(ev({ metaKey: true }))).toBe(false);
    expect(shouldOpenShortcutOverlay(ev({ altKey: true }))).toBe(false);
  });

  it("stays out of the way once something else handled the key", () => {
    expect(shouldOpenShortcutOverlay(ev({ defaultPrevented: true }))).toBe(false);
  });

  it("does not steal the character out of a text field", () => {
    for (const html of [
      "<input>",
      '<input type="text">',
      '<input type="search">',
      "<textarea></textarea>",
      '<div contenteditable="true"></div>',
    ]) {
      expect(shouldOpenShortcutOverlay(ev({ target: el(html) }))).toBe(false);
    }
  });

  it("still opens from a control that is not text entry", () => {
    for (const html of ['<input type="checkbox">', "<button></button>", "<div></div>"]) {
      expect(shouldOpenShortcutOverlay(ev({ target: el(html) }))).toBe(true);
    }
  });

  it("respects a region that claims the keyboard, such as a terminal pane", () => {
    const pane = el('<div data-keyboard-capture="true"><span></span></div>');
    const inner = pane.querySelector("span") as HTMLElement;
    expect(shouldOpenShortcutOverlay(ev({ target: inner }))).toBe(false);
  });
});

describe("isTextEntryTarget", () => {
  it("treats a non-element target as safe", () => {
    expect(isTextEntryTarget(null)).toBe(false);
  });

  it("treats a bare input with no type as text, the safe default", () => {
    expect(isTextEntryTarget(el("<input>"))).toBe(true);
  });
});
