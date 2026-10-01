import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TopBar, TopBarActions } from "./TopBar";
import { ThemeProvider } from "@/hooks/useTheme";
import { useEventStore } from "@/store/events";

vi.mock("@/hooks/useUpdate", () => ({
  useUpdate: () => ({ status: { managed: false, update_available: false } }),
}));
vi.mock("@/components/MascotGigi", () => ({
  MascotGigi: () => <div data-testid="mascot-gigi" />,
}));

// The caption is on every screen. Tests still name the section so a later
// change cannot quietly hide the caption on one of them.
beforeEach(() => {
  useEventStore.setState({
    activeSection: "dictation",
    solo: false,
    detachedViews: [],
  });
});
afterEach(() => vi.restoreAllMocks());

describe("TopBar detach button", () => {
  it("offers the sidebar toggle with its current state", () => {
    const onToggle = vi.fn();
    const { rerender } = render(<TopBar navToggle={{ collapsed: true, onToggle }} />);
    const toggle = screen.getByTestId("section-nav-sidebar");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledOnce();
    rerender(<TopBar navToggle={{ collapsed: false, onToggle }} />);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("offers 'own window' on the detachable sections only", () => {
    render(<TopBar />);
    expect(screen.getByTestId("detach-view-button")).toBeTruthy();

    cleanup();
    useEventStore.setState({ activeSection: "settings" });
    render(<TopBar />);
    expect(screen.queryByTestId("detach-view-button")).toBeNull();
  });

  it("never renders inside a solo window (no detaching a detached view)", () => {
    useEventStore.setState({ solo: true });
    render(<TopBarActions />);
    expect(screen.queryByTestId("detach-view-button")).toBeNull();
  });
});

describe("TopBar section navigation", () => {
  beforeEach(() => {
    useEventStore.setState({ activeSection: "chats", solo: false });
  });

  it("carries no back/forward or restart buttons", () => {
    render(<TopBar navToggle={{ collapsed: false, onToggle: () => {} }} />);
    expect(screen.queryByTestId("section-nav-back")).toBeNull();
    expect(screen.queryByTestId("section-nav-forward")).toBeNull();
    expect(screen.queryByRole("button", { name: /restart/i })).toBeNull();
  });

  it("offers the sidebar toggle from the caption and hands the click to the shell", () => {
    const onToggle = vi.fn();
    render(<TopBar navToggle={{ collapsed: false, onToggle }} />);

    const toggle = screen.getByTestId("section-nav-sidebar");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it("stays out of a detached solo window", () => {
    useEventStore.setState({ solo: true });
    render(<TopBar navToggle={{ collapsed: false, onToggle: () => {} }} />);
    expect(screen.queryByTestId("section-nav-buttons")).toBeNull();
  });
});

describe("TopBar caption on every section", () => {
  it.each(["chats", "agents", "agentic-ide-classic", "dictation"] as const)(
    "keeps the theme toggle on %s",
    (section) => {
      useEventStore.setState({ activeSection: section });
      render(
        <ThemeProvider>
          <TopBar />
        </ThemeProvider>,
      );
      const caption = screen.getByTestId("window-caption");
      expect(caption.className).not.toContain("jarvis-shell-surface");
      expect(caption).toBeTruthy();
      expect(screen.getByTestId("theme-toggle")).toBeTruthy();
      expect(screen.queryByTestId("window-close")).toBeNull();
    },
  );

  it("places the theme toggle immediately before the window buttons", async () => {
    (window as unknown as { pywebview?: { api: object } }).pywebview = { api: {} };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ ok: true, frameless: true, controls: "trailing", platform: "windows" }),
      }),
    );

    render(
      <ThemeProvider>
        <TopBar />
      </ThemeProvider>,
    );

    const minimize = await screen.findByTestId("window-minimize");
    const theme = screen.getByTestId("theme-toggle");
    expect(theme.compareDocumentPosition(minimize) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(minimize.compareDocumentPosition(screen.getByTestId("window-close")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    delete (window as unknown as { pywebview?: unknown }).pywebview;
  });
});
