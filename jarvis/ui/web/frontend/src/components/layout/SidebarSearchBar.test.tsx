/**
 * The sidebar search field swaps its plain stand-in for the live field once
 * the app has settled — without a render loop, and keeping what was typed.
 */
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

class ResizeObserverPolyfill {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof (globalThis as { ResizeObserver?: unknown }).ResizeObserver === "undefined") {
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverPolyfill }).ResizeObserver =
    ResizeObserverPolyfill;
}

import { SidebarSearchBar } from "./SidebarSearchBar";
import { useEventStore } from "@/store/events";

describe("SidebarSearchBar", () => {
  afterEach(cleanup);

  it("loads the live field on focus and keeps the typed text", async () => {
    let renders = 0;
    function Probe() {
      renders += 1;
      return <SidebarSearchBar assistantName="George" status={<span />} />;
    }
    render(<Probe />);
    const plain = screen.getByTestId("sidebar-search") as HTMLInputElement;
    expect(plain.closest("[cmdk-root]")).toBeNull();
    fireEvent.change(plain, { target: { value: "blo" } });
    fireEvent.focus(plain);
    // The live field is a lazy chunk; its first import is slow under test.
    await waitFor(() => expect(document.querySelector("[cmdk-root]")).not.toBeNull(), {
      timeout: 8000,
    });
    const live = screen.getByTestId("sidebar-search") as HTMLInputElement;
    expect(live.value).toBe("blo");
    // Let any effect chain settle; a render loop would keep counting.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
    });
    const settled = renders;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
    });
    expect(renders).toBe(settled);
  }, 15000);

  it("names the assistant in its accessible name", () => {
    useEventStore.setState({ assistantName: "George" });
    render(<SidebarSearchBar assistantName="George" />);
    expect(screen.getByTestId("sidebar-search").getAttribute("aria-label")).toContain("George");
  });
});
