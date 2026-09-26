// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { bindPlayerInput, NO_INPUT, ownsTextOrUi } from "./input";

const disposers: (() => void)[] = [];
function fixture() {
  const host = document.createElement("div"); host.tabIndex = 0; document.body.append(host); host.focus();
  const invalidate = vi.fn();
  const input = bindPlayerInput(host, invalidate);
  disposers.push(() => input.dispose());
  return { host, input, invalidate };
}
function key(target: Element | Window, type: string, code: string) { target.dispatchEvent(new KeyboardEvent(type, { code, bubbles: true, cancelable: true })); }
afterEach(() => { disposers.splice(0).forEach((dispose) => dispose()); document.body.replaceChildren(); });

describe("Mars input ownership", () => {
  it("retains short taps until physics consumes them and then releases movement", () => {
    const { host, input } = fixture();
    key(host, "keydown", "KeyW"); key(host, "keyup", "KeyW");
    expect(input.read().forward).toBe(1);
    expect(input.read().forward).toBe(1);
    input.consume();
    expect(input.read()).toEqual(NO_INPUT);
    key(host, "keydown", "Space"); key(host, "keyup", "Space");
    window.dispatchEvent(new Event("blur"));
    expect(input.read()).toEqual(NO_INPUT);
  });
  it("keeps held movement when a mouse-look drag ends normally", () => {
    const { host, input } = fixture();
    key(host, "keydown", "KeyW"); input.consume();
    host.dispatchEvent(new Event("lostpointercapture"));
    expect(input.read().forward).toBe(1);
    key(host, "keyup", "KeyW");
    expect(input.read()).toEqual(NO_INPUT);
  });
  it("moves only the focused world and clears keys on blur", () => {
    const { host, input } = fixture();
    key(host, "keydown", "KeyW"); expect(input.read().forward).toBe(1);
    window.dispatchEvent(new Event("blur")); expect(input.read()).toEqual(NO_INPUT);
    key(host, "keyup", "KeyW"); expect(input.read()).toEqual(NO_INPUT);
  });
  it.each(["input", "textarea", "button", "select"])("typing or focusing %s releases held movement", (tag) => {
    const { host, input } = fixture(); key(host, "keydown", "KeyW");
    const field = document.createElement(tag); host.append(field); field.focus();
    key(field, "keydown", "KeyW"); key(field, "keydown", "Space");
    expect(input.read()).toEqual(NO_INPUT);
    host.focus(); expect(input.read()).toEqual(NO_INPUT);
  });
  it("protects terminal, contenteditable and modal descendants", () => {
    for (const markup of ['<div class="xterm"><span></span></div>', '<div contenteditable="true"><span></span></div>', '<div role="dialog"><span></span></div>']) {
      const root = document.createElement("div"); root.innerHTML = markup; document.body.append(root);
      expect(ownsTextOrUi(root.querySelector("span"))).toBe(true);
    }
  });
  it("releases movement on Escape and ignores all input after disposal", () => {
    const { host, input } = fixture();
    key(host, "keydown", "KeyW"); key(host, "keydown", "Escape"); expect(input.read()).toEqual(NO_INPUT);
    input.dispose(); key(host, "keydown", "KeyW"); expect(input.read()).toEqual(NO_INPUT);
  });
});
