import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TopBar, TopBarActions } from "./TopBar";
import { ThemeProvider } from "@/hooks/useTheme";
import { useEventStore } from "@/store/events";
import { resetSectionHistory } from "@/hooks/useSectionHistory";

vi.mock("@/hooks/useUpdate", () => ({
  useUpdate: () => ({ status: { managed: false, update_available: false } }),
}));
vi.mock("@/components/MascotGigi", () => ({
  MascotGigi: () => <div data-testid="mascot-gigi" />,
}));

// The caption is on every screen. Tests still name the section so a later
// change cannot quietly hide Restart on one of them.
beforeEach(() => {
  useEventStore.setState({
    activeSection: "dictation",
    solo: false,
    detachedViews: [],
  });
});
afterEach(() => vi.restoreAllMocks());

describe("TopBar detach button", () => {
  it("offers the Settings navigation toggle with its current state", () => {
    useEventStore.setState({ activeSection: "profile" });
    const onToggle = vi.fn();
    const { rerender } = render(<TopBar settingsNavigation={{ open: false, onToggle }} />);
    const toggle = screen.getByTestId("settings-sidebar-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledOnce();
    rerender(<TopBar settingsNavigation={{ open: true, onToggle }} />);
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

describe("TopBar restart button", () => {
  it("renders a restart button labelled in the active locale", () => {
    render(<TopBar />);
    expect(
      screen.getByRole("button", { name: /restart/i }),
    ).toBeTruthy();
  });

  it("requires a confirming second click before it calls the backend", () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);

    render(<TopBar />);
    // First click only arms the confirmation — no network call yet.
    fireEvent.click(screen.getByRole("button", { name: /^restart$/i }));
    expect(fetchMock).not.toHaveBeenCalled();
    // The button now asks for confirmation.
    expect(
      screen.getByRole("button", { name: /confirm restart/i }),
    ).toBeTruthy();
  });

  it("POSTs to /api/settings/restart-app on the confirming click", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);

    render(<TopBar />);
    fireEvent.click(screen.getByRole("button", { name: /^restart$/i }));
    fireEvent.click(screen.getByRole("button", { name: /confirm restart/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/settings/restart-app");
    expect(opts?.method).toBe("POST");
  });

  it("on 409 surfaces running missions and the next click forces the restart", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: async () => ({
          detail: {
            error: "missions_running",
            missions: [{ id: "a", title: "research" }],
          },
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);

    render(<TopBar />);
    fireEvent.click(screen.getByRole("button", { name: /^restart$/i }));
    fireEvent.click(screen.getByRole("button", { name: /confirm restart/i }));

    // The guard refused: the button now offers a force restart instead.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /restart anyway/i }),
      ).toBeTruthy();
    });

    // The first POST carried NO force flag (the mission was not killed).
    expect(fetchMock.mock.calls[0][0]).not.toContain("force");

    // Forcing it sends force=true.
    fireEvent.click(screen.getByRole("button", { name: /restart anyway/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    expect(fetchMock.mock.calls[1][0]).toContain("force=true");
  });

  it("surfaces a failed restart instead of leaving the button stuck", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 503 });
    vi.stubGlobal("fetch", fetchMock);

    render(<TopBar />);
    fireEvent.click(screen.getByRole("button", { name: /^restart$/i }));
    fireEvent.click(screen.getByRole("button", { name: /confirm restart/i }));

    // After the failure the button returns to its idle, re-clickable state.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /^restart$/i }),
      ).toBeTruthy();
    });
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
    render(<TopBar />);
    expect(screen.queryByTestId("section-nav-buttons")).toBeNull();
  });
});

describe("TopBar caption on every section", () => {
  it.each(["chats", "agents", "agentic-ide-classic", "dictation"] as const)(
    "keeps restart on %s",
    (section) => {
      useEventStore.setState({ activeSection: section });
      render(<TopBar />);
      const caption = screen.getByTestId("window-caption");
      expect(caption.className).not.toContain("jarvis-shell-surface");
      expect(caption).toBeTruthy();
      expect(screen.getByRole("button", { name: /^restart$/i })).toBeTruthy();
      expect(screen.queryByTestId("window-close")).toBeNull();
    },
  );

  it("places theme and restart immediately before the window buttons", async () => {
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
    const restart = screen.getByRole("button", { name: /^restart$/i });
    const theme = screen.getByTestId("theme-toggle");
    expect(theme.compareDocumentPosition(restart) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(restart.compareDocumentPosition(minimize) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(minimize.compareDocumentPosition(screen.getByTestId("window-close")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    delete (window as unknown as { pywebview?: unknown }).pywebview;
  });
});
