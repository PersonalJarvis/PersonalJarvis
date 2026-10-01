/**
 * The quick switcher end to end: type, Enter, you are there.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

// Radix Dialog and cmdk touch browser APIs jsdom does not ship.
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

import { QuickSwitcher } from "./QuickSwitcher";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useSettingsJump } from "@/store/settingsJump";

function renderOpen() {
  const state = { open: true };
  const onOpenChange = (next: boolean) => {
    state.open = next;
  };
  render(<QuickSwitcher open onOpenChange={onOpenChange} />);
  return state;
}

describe("QuickSwitcher", () => {
  beforeEach(() => {
    useEventStore.getState().setActiveSection("chats");
    useSettingsJump.setState({ target: null });
  });
  afterEach(cleanup);

  it("lists every destination before anything is typed", () => {
    renderOpen();
    expect(screen.getByTestId("quick-switch-agentic-ide")).toBeTruthy();
    expect(screen.getByTestId("quick-switch-settings")).toBeTruthy();
  });

  it("goes to the top match on Enter and closes", () => {
    const state = renderOpen();
    const input = screen.getByTestId("quick-switcher-input");
    fireEvent.change(input, { target: { value: "agentic" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(useEventStore.getState().activeSection).toBe("agentic-ide");
    expect(state.open).toBe(false);
  });

  it("flips the front page to the face that was picked", () => {
    useHomeStore.getState().setSurface("voice");
    renderOpen();
    fireEvent.click(screen.getByTestId("quick-switch-chat"));
    expect(useEventStore.getState().activeSection).toBe("chats");
    expect(useHomeStore.getState().surface).toBe("chat");
  });

  it("hands a Settings group to the hub", () => {
    renderOpen();
    fireEvent.change(screen.getByTestId("quick-switcher-input"), { target: { value: "wake word" } });
    fireEvent.click(screen.getByTestId("quick-switch-setting-wake-word"));
    expect(useEventStore.getState().activeSection).toBe("settings");
    expect(useSettingsJump.getState().target).toBe("wake-word");
  });

  it("says so when nothing matches", () => {
    renderOpen();
    fireEvent.change(screen.getByTestId("quick-switcher-input"), { target: { value: "zzqxv" } });
    expect(screen.queryByTestId("quick-switch-settings")).toBeNull();
  });
});
