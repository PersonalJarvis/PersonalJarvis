import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TopBar } from "./TopBar";
import { useEventStore } from "@/store/events";
import { resetSectionHistory } from "@/hooks/useSectionHistory";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { useIdeThreadsStore } from "@/store/ideThreads";
import { useThreadTerminalsStore } from "@/store/threadTerminals";
import { useWorkspacePanesStore } from "@/store/workspacePanes";

vi.mock("@/hooks/useUpdate", () => ({
  useUpdate: () => ({ status: { managed: false, update_available: false } }),
}));
vi.mock("@/components/MascotGigi", () => ({
  MascotGigi: () => <div data-testid="mascot-gigi" />,
}));

// The caption is on every screen. Tests still name the section so a later
// change cannot quietly drop back/forward on one of them.
beforeEach(() => {
  useEventStore.setState({
    activeSection: "dictation",
    solo: false,
    detachedViews: [],
  });
  // The shut side panel's toggle counts waiting agents; no poll leaves the test.
  useWorkspacePanesStore.setState({ panes: [], load: async () => {} });
});
afterEach(() => vi.restoreAllMocks());

describe("TopBar caption buttons", () => {
  it("offers a section window without theme or restart controls", () => {
    useEventStore.setState({ activeSection: "agents" });
    render(<TopBar />);
    expect(screen.queryByTestId("theme-toggle")).toBeNull();
    expect(screen.getByTestId("detach-view-button")).toBeTruthy();
    expect(screen.queryByTestId("section-nav-sidebar")).toBeNull();
    expect(screen.queryByRole("button", { name: /restart/i })).toBeNull();
  });
});

describe("TopBar side panel toggle", () => {
  it("opens and closes the IDE side panel from the caption", () => {
    useEventStore.setState({ activeSection: "agentic-ide" });
    useIdeSidePanelStore.setState({ open: false, maximized: false });
    render(<TopBar />);
    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    expect(useIdeSidePanelStore.getState().open).toBe(true);
    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    expect(useIdeSidePanelStore.getState().open).toBe(false);
  });

  it("stays out of every other section", () => {
    render(<TopBar />);
    expect(screen.queryByTestId("ide-side-panel-toggle")).toBeNull();
    expect(screen.queryByTestId("thread-terminal-toggle")).toBeNull();
  });
});

describe("TopBar terminal drawer toggle", () => {
  beforeEach(() => {
    useEventStore.setState({ activeSection: "agentic-ide" });
    useIdeSidePanelStore.setState({ open: false, maximized: false });
    useThreadTerminalsStore.setState({ folder: "/code/app", open: false, shells: [], active: {} });
  });
  afterEach(() => useIdeThreadsStore.setState({ layout: "grid" }));

  it("sits before the side panel toggle in the thread layout and starts a shell", () => {
    useIdeThreadsStore.setState({ layout: "threads" });
    render(<TopBar />);
    const terminal = screen.getByTestId("thread-terminal-toggle");
    const panel = screen.getByTestId("ide-side-panel-toggle");
    expect(terminal.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(terminal);
    expect(useThreadTerminalsStore.getState()).toMatchObject({ open: true });
    expect(useThreadTerminalsStore.getState().shells).toHaveLength(1);
  });

  it("is not offered in the terminal grid", () => {
    useIdeThreadsStore.setState({ layout: "grid" });
    render(<TopBar />);
    expect(screen.queryByTestId("thread-terminal-toggle")).toBeNull();
    expect(screen.getByTestId("ide-side-panel-toggle")).toBeTruthy();
  });

  it("steps aside while a maximized side panel covers the thread", () => {
    useIdeThreadsStore.setState({ layout: "threads" });
    useIdeSidePanelStore.setState({ open: true, maximized: true });
    render(<TopBar />);
    expect(screen.queryByTestId("thread-terminal-toggle")).toBeNull();
  });
});

describe("TopBar section navigation", () => {
  beforeEach(() => {
    resetSectionHistory();
    useEventStore.setState({ activeSection: "chats", solo: false });
  });

  it.each(["chats", "agents", "dictation", "visualization", "profile"] as const)(
    "shows back/forward in the caption on %s",
    (section) => {
      useEventStore.setState({ activeSection: section });
      render(<TopBar />);
      expect(screen.getByTestId("section-nav-buttons")).toBeTruthy();
      expect(screen.getByTestId("section-nav-back")).toBeTruthy();
      expect(screen.getByTestId("section-nav-forward")).toBeTruthy();
      cleanup();
    },
  );

  it("stays out of a detached solo window", () => {
    useEventStore.setState({ solo: true });
    render(<TopBar />);
    expect(screen.queryByTestId("section-nav-buttons")).toBeNull();
  });
});

describe("TopBar caption on every section", () => {
  it.each([false, true])("leaves native macOS controls to Cocoa (solo=%s)", async (solo) => {
    useEventStore.setState({ solo });
    (window as unknown as { pywebview?: { api: object } }).pywebview = { api: {} };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, frameless: false, controls: "none", platform: "darwin" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    try {
      await act(async () => { render(<TopBar />); });
      expect(fetchMock).toHaveBeenCalledWith("/api/window/chrome");
      expect(screen.queryByTestId("window-controls")).toBeNull();
      fireEvent.doubleClick(screen.getByTestId("window-caption").querySelector(".pywebview-drag-region")!);
      expect(fetchMock.mock.calls.some(([url]) => url === "/api/window/command")).toBe(false);
    } finally {
      delete (window as unknown as { pywebview?: unknown }).pywebview;
    }
  });

  it.each(["chats", "agents", "agentic-ide-classic", "dictation"] as const)(
    "renders the caption on %s",
    (section) => {
      useEventStore.setState({ activeSection: section });
      render(<TopBar />);
      const caption = screen.getByTestId("window-caption");
      expect(caption.className).not.toContain("jarvis-shell-surface");
      expect(caption).toBeTruthy();
      expect(screen.queryByTestId("window-close")).toBeNull();
    },
  );

  it("keeps the window buttons at the right end of the caption", async () => {
    (window as unknown as { pywebview?: { api: object } }).pywebview = { api: {} };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ ok: true, frameless: true, controls: "trailing", platform: "windows" }),
      }),
    );

    render(<TopBar />);

    const minimize = await screen.findByTestId("window-minimize");
    expect(minimize.compareDocumentPosition(screen.getByTestId("window-close")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    delete (window as unknown as { pywebview?: unknown }).pywebview;
  });
});
