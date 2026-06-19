import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mutable mock store state — hoisted so the vi.mock factory below can close over
// it. Each test sets `activeSection` before rendering and inspects the
// `setActiveSection` spy.
const { mockState } = vi.hoisted(() => ({
  mockState: {
    activeSection: "clis" as string,
    setActiveSection: vi.fn(),
  },
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
}));

vi.mock("@/i18n", () => ({
  // Identity translator: returns the key so assertions can match exact labels.
  useT: () => (key: string) => key,
}));

// Stub the two embedded views — ClisHubView is a thin tab wrapper; we only
// assert which child it renders, not the children's own behaviour.
vi.mock("@/views/ClisView", () => ({ ClisView: () => <div>CLIS_CONTENT</div> }));
vi.mock("@/views/CliTestHubView", () => ({ CliTestHubView: () => <div>CLI_TEST_CONTENT</div> }));

import { ClisHubView } from "@/views/ClisHubView";

beforeEach(() => {
  mockState.activeSection = "clis";
  mockState.setActiveSection = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ClisHubView tab switching", () => {
  it("shows the CLIs view and an active CLIs tab when activeSection is 'clis'", () => {
    mockState.activeSection = "clis";
    render(<ClisHubView />);

    expect(screen.getByText("CLIS_CONTENT")).toBeDefined();
    expect(screen.queryByText("CLI_TEST_CONTENT")).toBeNull();

    const clisTab = screen.getByRole("button", { name: "nav.clis" });
    expect(clisTab.getAttribute("aria-current")).toBe("page");
    // Both tabs are always present; the CLI Test Hub tab is the second one.
    expect(screen.getByRole("button", { name: "nav.cli_test_hub" })).toBeDefined();
  });

  it("activates the cli-test-hub section when the CLI Test Hub tab is clicked", () => {
    mockState.activeSection = "clis";
    render(<ClisHubView />);

    fireEvent.click(screen.getByRole("button", { name: "nav.cli_test_hub" }));
    expect(mockState.setActiveSection).toHaveBeenCalledWith("cli-test-hub");
  });

  it("shows the CLI Test Hub view when activeSection is 'cli-test-hub'", () => {
    mockState.activeSection = "cli-test-hub";
    render(<ClisHubView />);

    expect(screen.getByText("CLI_TEST_CONTENT")).toBeDefined();
    expect(screen.queryByText("CLIS_CONTENT")).toBeNull();
    expect(
      screen.getByRole("button", { name: "nav.cli_test_hub" }).getAttribute("aria-current"),
    ).toBe("page");
  });
});
