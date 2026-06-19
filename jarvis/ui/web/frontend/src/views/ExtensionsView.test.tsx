import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mutable mock store state — hoisted so the vi.mock factory below can close over
// it. Each test sets `activeSection` before rendering and inspects the
// `setActiveSection` spy.
const { mockState } = vi.hoisted(() => ({
  mockState: {
    activeSection: "skills" as string,
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

// Stub the three embedded views — ExtensionsView ("Skills & Tools") is a thin
// tab wrapper; we only assert which child it renders, not their behaviour.
vi.mock("@/views/SkillsView", () => ({ SkillsView: () => <div>SKILLS_CONTENT</div> }));
vi.mock("@/views/PluginsView", () => ({ PluginsView: () => <div>PLUGINS_CONTENT</div> }));
vi.mock("@/views/McpsView", () => ({ McpsView: () => <div>MCPS_CONTENT</div> }));

import { ExtensionsView } from "@/views/ExtensionsView";

beforeEach(() => {
  mockState.activeSection = "skills";
  mockState.setActiveSection = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ExtensionsView (Skills & Tools) tab switching", () => {
  it("shows the Skills view and an active Skills tab when activeSection is 'skills'", () => {
    mockState.activeSection = "skills";
    render(<ExtensionsView />);

    expect(screen.getByText("SKILLS_CONTENT")).toBeDefined();
    expect(screen.queryByText("PLUGINS_CONTENT")).toBeNull();

    const skillsTab = screen.getByRole("button", { name: "nav.skills" });
    expect(skillsTab.getAttribute("aria-current")).toBe("page");
    // The flat tab bar always shows all three tabs, even while on Skills.
    expect(screen.getByRole("button", { name: "nav.plugins" })).toBeDefined();
    expect(screen.getByRole("button", { name: "nav.mcps" })).toBeDefined();
    // CLIs were split into their own section — no CLIs tab here anymore.
    expect(screen.queryByRole("button", { name: "nav.clis" })).toBeNull();
  });

  it("activates the plugins section when the Plugins tab is clicked", () => {
    mockState.activeSection = "skills";
    render(<ExtensionsView />);

    fireEvent.click(screen.getByRole("button", { name: "nav.plugins" }));
    expect(mockState.setActiveSection).toHaveBeenCalledWith("plugins");
  });

  it("shows the Plugins view and an active Plugins tab when activeSection is 'plugins'", () => {
    mockState.activeSection = "plugins";
    render(<ExtensionsView />);

    expect(screen.getByText("PLUGINS_CONTENT")).toBeDefined();
    expect(screen.queryByText("SKILLS_CONTENT")).toBeNull();

    const pluginsTab = screen.getByRole("button", { name: "nav.plugins" });
    expect(pluginsTab.getAttribute("aria-current")).toBe("page");

    // All three flat tabs are present regardless of which one is active.
    expect(screen.getByRole("button", { name: "nav.skills" })).toBeDefined();
    expect(screen.getByRole("button", { name: "nav.mcps" })).toBeDefined();
  });

  it("renders the MCPs view when activeSection is 'mcps'", () => {
    mockState.activeSection = "mcps";
    render(<ExtensionsView />);
    expect(screen.getByText("MCPS_CONTENT")).toBeDefined();
    expect(screen.getByRole("button", { name: "nav.mcps" }).getAttribute("aria-current")).toBe("page");
  });
});
