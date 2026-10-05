import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SectionNavButtons } from "./SectionNavButtons";
import { resetSectionHistory } from "@/hooks/useSectionHistory";
import { useEventStore } from "@/store/events";

function backButton(): HTMLButtonElement {
  return screen.getByTestId("section-nav-back") as HTMLButtonElement;
}

function forwardButton(): HTMLButtonElement {
  return screen.getByTestId("section-nav-forward") as HTMLButtonElement;
}

beforeEach(() => {
  resetSectionHistory();
  useEventStore.setState({ activeSection: "chats", solo: false });
});

afterEach(() => cleanup());

describe("SectionNavButtons", () => {
  it("renders back and forward with no sidebar toggle", () => {
    render(<SectionNavButtons />);

    expect(screen.getByTestId("section-nav-buttons")).toBeTruthy();
    expect(backButton()).toBeTruthy();
    expect(forwardButton()).toBeTruthy();
    expect(screen.queryByTestId("section-nav-sidebar")).toBeNull();
  });

  it("starts with both directions disabled and honest tooltips", () => {
    render(<SectionNavButtons />);

    expect(backButton().disabled).toBe(true);
    expect(forwardButton().disabled).toBe(true);
    // WHAT the tooltip says is the locale's business; that each button names
    // itself is not — a forward button that cannot explain itself (including
    // while disabled) teaches nothing.
    for (const button of [backButton(), forwardButton()]) {
      expect(button.getAttribute("aria-label")).toBeTruthy();
      expect(button.getAttribute("title")).toBe(button.getAttribute("aria-label"));
    }
  });

  it.each(["agents", "agentic-ide", "profile"] as const)("leaves a freshly opened %s section for the front page", (section) => {
    useEventStore.setState({ activeSection: section });
    render(<SectionNavButtons />);

    expect(backButton().disabled).toBe(false);
    fireEvent.click(backButton());
    expect(useEventStore.getState().activeSection).toBe("chats");
  });

  it("goes home to a fresh front-page chat from any section", () => {
    useEventStore.setState({ activeSection: "agentic-ide" });
    render(<SectionNavButtons />);

    const home = screen.getByTestId("section-nav-home") as HTMLButtonElement;
    expect(home.disabled).toBe(false);
    expect(home.getAttribute("title")).toBe(home.getAttribute("aria-label"));
    fireEvent.click(home);
    expect(useEventStore.getState().activeSection).toBe("chats");
  });

  it("walks back to the last visited section on every section", () => {
    render(<SectionNavButtons />);

    // The history is section-driven, not view-driven: whatever put the voice
    // section on screen (sidebar, voice command, deck jump) lands here too.
    act(() => useEventStore.getState().setActiveSection("agents"));
    act(() => useEventStore.getState().setActiveSection("dictation"));

    expect(backButton().disabled).toBe(false);
    fireEvent.click(backButton());
    expect(useEventStore.getState().activeSection).toBe("agents");
  });

  it("enables forward only after going back, and only for visited sections", () => {
    render(<SectionNavButtons />);

    act(() => useEventStore.getState().setActiveSection("agents"));
    act(() => useEventStore.getState().setActiveSection("dictation"));

    expect(forwardButton().disabled).toBe(true);

    fireEvent.click(backButton());
    expect(forwardButton().disabled).toBe(false);

    fireEvent.click(forwardButton());
    expect(useEventStore.getState().activeSection).toBe("dictation");
    expect(forwardButton().disabled).toBe(true);
  });

  it("stays out of a detached solo window", () => {
    useEventStore.setState({ solo: true });
    render(<SectionNavButtons />);

    // A solo window is pinned to its one view: back/forward would walk an
    // audience of one, and there is no sidebar to toggle.
    expect(screen.queryByTestId("section-nav-buttons")).toBeNull();
  });
});
