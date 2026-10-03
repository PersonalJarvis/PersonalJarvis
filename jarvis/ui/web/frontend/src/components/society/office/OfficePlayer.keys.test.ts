import { afterEach, describe, expect, it } from "vitest";
import { forgetHeldKeysUnderDialog, ownsKeyboard } from "./OfficePlayer";

afterEach(() => { document.body.innerHTML = ""; });

describe("ownsKeyboard", () => {
  it("leaves plain page focus to the character", () => {
    expect(ownsKeyboard(document.body)).toBe(false);
  });
  it("gives text fields their keys", () => {
    const input = document.createElement("input");
    document.body.append(input);
    expect(ownsKeyboard(input)).toBe(true);
  });
  it("gives an open dialog every key, even with focus outside it", () => {
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("data-state", "open");
    document.body.append(dialog);
    expect(ownsKeyboard(document.body)).toBe(true);
  });
});

describe("forgetHeldKeysUnderDialog", () => {
  it("keeps held walking keys while nothing else owns the keyboard", () => {
    const pressed = new Set(["KeyD"]);
    expect(forgetHeldKeysUnderDialog(pressed)).toBe(false);
    expect([...pressed]).toEqual(["KeyD"]);
  });
  it("drops a key still held when an arcade game opened, so the character stops behind it", () => {
    const pressed = new Set(["ArrowUp", "KeyD"]);
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("data-state", "open");
    document.body.append(dialog);
    expect(forgetHeldKeysUnderDialog(pressed)).toBe(true);
    expect(pressed.size).toBe(0);
  });
});
