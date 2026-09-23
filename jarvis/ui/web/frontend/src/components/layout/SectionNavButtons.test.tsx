import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  it("renders back and forward with no sidebar toggle unless one is offered", () => {
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

  it("offers the sidebar toggle with its state and hands the click to the shell", () => {
    const onToggle = vi.fn();
    const { rerender } = render(
      <SectionNavButtons sidebarToggle={{ collapsed: false, onToggle }} />,
    );

    const toggle = screen.getByTestId("section-nav-sidebar");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.getAttribute("aria-label")).toBeTruthy();
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledTimes(1);

    rerender(
      <SectionNavButtons sidebarToggle={{ collapsed: true, onToggle }} />,
    );
    expect(
      screen.getByTestId("section-nav-sidebar").getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("keeps a caller-provided test id for the sidebar toggle", () => {
    render(
      <SectionNavButtons
        sidebarToggle={{ collapsed: true, onToggle: () => {}, testId: "settings-sidebar-toggle" }}
      />,
    );

    expect(screen.getByTestId("settings-sidebar-toggle")).toBeTruthy();
  });

  it("stays out of a detached solo window", () => {
    useEventStore.setState({ solo: true });
    render(<SectionNavButtons />);

    // A solo window is pinned to its one view: back/forward would walk an
    // audience of one, and there is no sidebar to toggle.
    expect(screen.queryByTestId("section-nav-buttons")).toBeNull();
  });
});
