import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { initialSectionFromSearch, useEventStore } from "@/store/events";
import { useSectionUrlMemory } from "./useSectionUrlMemory";

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  useEventStore.setState({ activeSection: "chats" });
});

describe("useSectionUrlMemory", () => {
  it("puts the section a reload would need into the address", () => {
    renderHook(() => useSectionUrlMemory());

    act(() => useEventStore.getState().setActiveSection("agentic-ide"));

    expect(window.location.search).toBe("?view=agentic-ide");
  });

  it("hands the boot path the section the user was on", () => {
    // The round trip is the point: a reload keeps the address, and the store
    // reads exactly this on the way up. Asserting the query string alone would
    // pass on a parameter nothing consumes.
    renderHook(() => useSectionUrlMemory());

    act(() => useEventStore.getState().setActiveSection("sessions"));

    expect(initialSectionFromSearch(window.location.search)).toBe("sessions");
  });

  it("leaves a detached window's own flags alone", () => {
    window.history.replaceState(null, "", "/?solo=1&view=chats");
    renderHook(() => useSectionUrlMemory());

    act(() => useEventStore.getState().setActiveSection("outputs"));

    expect(window.location.search).toBe("?solo=1&view=outputs");
  });
});
