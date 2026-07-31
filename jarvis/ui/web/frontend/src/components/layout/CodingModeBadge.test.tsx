import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CodingModeBadge } from "@/components/layout/CodingModeBadge";
import { useEventStore } from "@/store/events";

const OFF = { active: false, hasWorkspace: false, workspace: "" };

afterEach(() => {
  cleanup();
  useEventStore.setState({ codingMode: OFF, activeSection: "chats" });
});

describe("CodingModeBadge", () => {
  it("says nothing when no coding workspace is open", () => {
    useEventStore.setState({ codingMode: OFF });
    render(<CodingModeBadge />);
    // Silence for everyone who never opens the IDE — a permanent "off" chip
    // would be noise on every screen of the app.
    expect(screen.queryByTestId("coding-mode-badge")).toBeNull();
  });

  it("reports the mode on a screen that is not the workspace", () => {
    useEventStore.setState({
      codingMode: { active: true, hasWorkspace: true, workspace: "Personal Jarvis" },
      activeSection: "settings",
    });
    render(<CodingModeBadge />);
    const badge = screen.getByTestId("coding-mode-badge");
    // The whole point: the assistant behaves differently here, and until this
    // badge existed nothing on this screen said so.
    expect(badge.textContent).toMatch(/ON|AN|ACTIVO/);
    expect(badge.getAttribute("title")).toContain("Personal Jarvis");
  });

  it("distinguishes 'agents running' from 'Jarvis is in their context'", () => {
    useEventStore.setState({
      codingMode: { active: false, hasWorkspace: true, workspace: "" },
      activeSection: "settings",
    });
    render(<CodingModeBadge />);
    const badge = screen.getByTestId("coding-mode-badge");
    expect(badge.textContent).toMatch(/OFF|AUS|INACTIVO/);
  });

  it("navigates to the workspace instead of toggling the mode", () => {
    useEventStore.setState({
      codingMode: { active: true, hasWorkspace: true, workspace: "Repo" },
      activeSection: "settings",
    });
    render(<CodingModeBadge />);
    fireEvent.click(screen.getByTestId("coding-mode-badge"));
    expect(useEventStore.getState().activeSection).toBe("agentic-ide");
    // Turning the mode off changes how the assistant answers everywhere; a
    // one-click chip on an unrelated screen must not be able to do that.
    expect(useEventStore.getState().codingMode.active).toBe(true);
  });

  it("never claims a pressed state it cannot deliver", () => {
    useEventStore.setState({
      codingMode: { active: true, hasWorkspace: true, workspace: "Repo" },
      activeSection: "settings",
    });
    render(<CodingModeBadge />);
    // It navigates, it does not toggle — aria-pressed would tell a screen
    // reader user this button controls the mode.
    expect(screen.getByTestId("coding-mode-badge").hasAttribute("aria-pressed")).toBe(
      false,
    );
  });
});
