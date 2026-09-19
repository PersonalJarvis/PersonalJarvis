import { afterEach, describe, expect, it, vi } from "vitest";
import { bindFollowEscape } from "./followInput";

const disposers: (() => void)[] = [];
function fixture() {
  const host = document.createElement("div"); host.tabIndex = 0; document.body.append(host); host.focus();
  const stop = vi.fn(); disposers.push(bindFollowEscape(host, stop));
  return { host, stop };
}
function escape(target: Element, prevented = false) {
  const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  if (prevented) event.preventDefault();
  target.dispatchEvent(event); return event;
}
afterEach(() => { disposers.splice(0).forEach((dispose) => dispose()); document.body.replaceChildren(); });

describe("follow Escape ownership", () => {
  it("consumes focused viewport Escape before the Society document handler and disposes", () => {
    const { host, stop } = fixture();
    let seen = false;
    document.addEventListener("keydown", (event) => { seen = event.defaultPrevented; }, { once: true });
    expect(escape(host).defaultPrevented).toBe(true);
    expect(seen).toBe(true); expect(stop).toHaveBeenCalledTimes(1);
    disposers.splice(0).forEach((dispose) => dispose());
    expect(escape(host).defaultPrevented).toBe(false); expect(stop).toHaveBeenCalledTimes(1);
  });
  it.each(["input", "textarea", "select", "button", "[contenteditable]", ".xterm", "[role=dialog]", "[role=menu]", "[data-mars-ui]"])("protects %s descendants and active fields", (selector) => {
    const { host, stop } = fixture();
    const field = document.createElement(selector.startsWith("[") || selector.startsWith(".") ? "div" : selector);
    if (selector === "[contenteditable]") field.setAttribute("contenteditable", "true");
    if (selector === ".xterm") field.className = "xterm";
    if (selector.startsWith("[role=")) field.setAttribute("role", selector.slice(6, -1));
    if (selector === "[data-mars-ui]") field.dataset.marsUi = "";
    field.tabIndex = 0; host.append(field); field.focus();
    expect(escape(field).defaultPrevented).toBe(false);
    expect(escape(host).defaultPrevented).toBe(false);
    expect(stop).not.toHaveBeenCalled();
  });
  it("ignores unfocused worlds and already handled Escape", () => {
    const { host, stop } = fixture();
    escape(host, true); expect(stop).not.toHaveBeenCalled();
    host.blur(); escape(host); expect(stop).not.toHaveBeenCalled();
  });
  it("stops focused follow on browser fullscreen exit without a keydown and releases its listener", () => {
    const { stop } = fixture();
    document.dispatchEvent(new Event("fullscreenchange"));
    expect(stop).toHaveBeenCalledTimes(1);
    disposers.splice(0).forEach((dispose) => dispose());
    document.dispatchEvent(new Event("fullscreenchange"));
    expect(stop).toHaveBeenCalledTimes(1);
  });
  it("ignores fullscreen exit while a form or another view owns focus", () => {
    const { host, stop } = fixture();
    const field = document.createElement("input"); host.append(field); field.focus();
    document.dispatchEvent(new Event("fullscreenchange"));
    field.blur();
    document.dispatchEvent(new Event("fullscreenchange"));
    expect(stop).not.toHaveBeenCalled();
  });
});
