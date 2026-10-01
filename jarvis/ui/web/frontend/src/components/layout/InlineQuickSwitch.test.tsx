/**
 * The sidebar search field: typing drops the results down under it, Enter
 * opens the top one, Escape clears then closes.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

class ResizeObserverPolyfill {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof (globalThis as { ResizeObserver?: unknown }).ResizeObserver === "undefined") {
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverPolyfill }).ResizeObserver =
    ResizeObserverPolyfill;
}
if (typeof Element !== "undefined" && !("scrollIntoView" in Element.prototype)) {
  // @ts-expect-error -- jsdom shim
  Element.prototype.scrollIntoView = () => {};
}

import { InlineQuickSwitch } from "./InlineQuickSwitch";
import { useEventStore } from "@/store/events";

function renderField(initialValue = "") {
  render(
    <InlineQuickSwitch
      initialValue={initialValue}
      autoFocus={false}
      accessibleName="Search George"
      placeholder="Search"
      caps={["Ctrl", "Space"]}
    />,
  );
  return screen.getByTestId("sidebar-search") as HTMLInputElement;
}

describe("InlineQuickSwitch", () => {
  beforeEach(() => useEventStore.getState().setActiveSection("chats"));
  afterEach(cleanup);

  it("stays closed until something is typed", () => {
    const input = renderField();
    expect(screen.queryByTestId("sidebar-search-results")).toBeNull();
    fireEvent.focus(input);
    expect(screen.queryByTestId("sidebar-search-results")).toBeNull();
  });

  it("drops the results down under the field and opens the top one on Enter", () => {
    const input = renderField();
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "agentic" } });
    expect(screen.getByTestId("sidebar-search-results")).toBeTruthy();
    expect(screen.getByTestId("quick-switch-agentic-ide")).toBeTruthy();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(useEventStore.getState().activeSection).toBe("agentic-ide");
    expect(input.value).toBe("");
    expect(screen.queryByTestId("sidebar-search-results")).toBeNull();
  });

  it("clears on the first Escape and closes on the second", () => {
    const input = renderField();
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "set" } });
    expect(screen.getByTestId("sidebar-search-results")).toBeTruthy();
    fireEvent.keyDown(input, { key: "Escape" });
    // Cleared: an empty field shows no list, but keeps the focus.
    expect(input.value).toBe("");
    expect(screen.queryByTestId("sidebar-search-results")).toBeNull();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByTestId("sidebar-search-results")).toBeNull();
  });

  it("keeps what was typed before it loaded", () => {
    const input = renderField("blo");
    expect(input.value).toBe("blo");
  });
});
