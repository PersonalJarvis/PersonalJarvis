import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useEventStore } from "@/store/events";
import {
  resetSectionHistory,
  useSectionHistory,
  useSectionNavHistory,
} from "./useSectionHistory";

function visit(section: "chats" | "agents" | "dictation" | "tasks"): void {
  act(() => useEventStore.getState().setActiveSection(section));
}

beforeEach(() => {
  resetSectionHistory();
  useEventStore.setState({ activeSection: "chats" });
});

describe("useSectionHistory", () => {
  it("starts with both directions disabled", () => {
    const { result } = renderHook(() => useSectionHistory());
    expect(result.current.canGoBack).toBe(false);
    expect(result.current.canGoForward).toBe(false);
  });

  it("goes back to the previously visited section", () => {
    const { result } = renderHook(() => useSectionHistory());
    visit("agents");
    visit("dictation");

    expect(result.current.canGoBack).toBe(true);

    let target: string | null = null;
    act(() => {
      target = result.current.goBack();
    });

    expect(target).toBe("agents");
    expect(useEventStore.getState().activeSection).toBe("agents");
  });

  it("keeps forward disabled until a step was undone", () => {
    const { result } = renderHook(() => useSectionHistory());
    visit("agents");
    visit("dictation");

    // Forward leads nowhere before any back step — a section never visited
    // cannot be gone to.
    expect(result.current.canGoForward).toBe(false);
    expect(useSectionNavHistory.getState().goForward()).toBeNull();

    act(() => {
      result.current.goBack();
    });
    expect(result.current.canGoForward).toBe(true);

    let target: string | null = null;
    act(() => {
      target = result.current.goForward();
    });
    expect(target).toBe("dictation");
    expect(useEventStore.getState().activeSection).toBe("dictation");
    // Redone: nothing left to redo.
    expect(result.current.canGoForward).toBe(false);
  });

  it("clears the redo stack on a new visit", () => {
    const { result } = renderHook(() => useSectionHistory());
    visit("agents");
    visit("dictation");

    act(() => {
      result.current.goBack();
    });
    expect(result.current.canGoForward).toBe(true);

    visit("tasks");
    expect(result.current.canGoForward).toBe(false);
    expect(result.current.canGoBack).toBe(true);
  });

  it("walks several steps back and forward in order", () => {
    const { result } = renderHook(() => useSectionHistory());
    visit("agents");
    visit("dictation");
    visit("tasks");

    act(() => {
      result.current.goBack();
    });
    expect(useEventStore.getState().activeSection).toBe("dictation");

    act(() => {
      result.current.goBack();
    });
    expect(useEventStore.getState().activeSection).toBe("agents");
    expect(result.current.canGoBack).toBe(true);

    act(() => {
      result.current.goBack();
    });
    expect(useEventStore.getState().activeSection).toBe("chats");
    expect(result.current.canGoBack).toBe(false);

    act(() => {
      result.current.goForward();
    });
    expect(useEventStore.getState().activeSection).toBe("agents");
  });

  it("ignores a back step with no history", () => {
    const { result } = renderHook(() => useSectionHistory());
    expect(result.current.canGoBack).toBe(false);

    let target: string | null = null;
    act(() => {
      target = result.current.goBack();
    });

    expect(target).toBeNull();
    expect(useEventStore.getState().activeSection).toBe("chats");
  });

  it("does not record re-selecting the same section", () => {
    const { result } = renderHook(() => useSectionHistory());
    visit("agents");
    visit("agents");

    act(() => {
      result.current.goBack();
    });
    // One genuine visit back — not stuck on a duplicate entry.
    expect(useEventStore.getState().activeSection).toBe("chats");
    expect(result.current.canGoBack).toBe(false);
  });
});
