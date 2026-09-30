import { afterEach, describe, expect, it } from "vitest";
import { ownsKeyboard } from "./OfficePlayer";

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
