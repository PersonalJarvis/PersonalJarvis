import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SectionNavButtons } from "./SectionNavButtons";
import { useEventStore } from "@/store/events";

beforeEach(() => {
  useEventStore.setState({ activeSection: "chats", solo: false });
});

afterEach(() => cleanup());

describe("SectionNavButtons", () => {
  it("renders nothing unless a sidebar toggle is offered", () => {
    render(<SectionNavButtons />);

    // The back/forward arrows are gone; with no toggle the group is empty.
    expect(screen.queryByTestId("section-nav-buttons")).toBeNull();
    expect(screen.queryByTestId("section-nav-back")).toBeNull();
    expect(screen.queryByTestId("section-nav-forward")).toBeNull();
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
    render(<SectionNavButtons sidebarToggle={{ collapsed: false, onToggle: () => {} }} />);

    // A solo window is pinned to its one view: there is no sidebar to toggle.
    expect(screen.queryByTestId("section-nav-buttons")).toBeNull();
  });
});
