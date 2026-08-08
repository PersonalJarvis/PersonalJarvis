import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TopBar, TopBarActions } from "./TopBar";
import { useEventStore } from "@/store/events";

vi.mock("@/hooks/useUpdate", () => ({
  useUpdate: () => ({ status: { managed: false, update_available: false } }),
}));

// The bar is section-aware now, so every test below states which screen it is
// on rather than inheriting whatever a previous one left behind.
beforeEach(() =>
  useEventStore.setState({
    activeSection: "chats",
    solo: false,
    detachedViews: [],
  }),
);
afterEach(() => vi.restoreAllMocks());

describe("TopBar detach button", () => {
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

/*
 * The one screen that renders these actions itself.
 *
 * The Agentic IDE is a wall of terminal output with a single header row, and a
 * second full-width strip above it holding two buttons was the third horizontal
 * band in a row. So the bar steps aside there — but the ACTIONS must not, and
 * that is the half worth pinning: a frontend change only reaches the user
 * through that Restart button, so a refactor that quietly drops it from the IDE
 * would leave the section with no way to pick up its own rebuild.
 */
describe("TopBar in the classic terminal grid", () => {
  it("renders no bar of its own there", () => {
    useEventStore.setState({ activeSection: "agentic-ide-classic" });

    const { container } = render(<TopBar />);

    expect(container.firstChild).toBeNull();
  });

  it("still offers the restart through the actions the IDE carries", () => {
    useEventStore.setState({ activeSection: "agentic-ide-classic" });

    render(<TopBarActions />);

    expect(screen.getByRole("button", { name: /^restart$/i })).toBeTruthy();
  });

  it("restores the global bar in the main window while the IDE is detached", () => {
    useEventStore.setState({
      activeSection: "agentic-ide-classic",
      detachedViews: ["agentic-ide"],
    });

    const { container } = render(<TopBar />);

    expect(container.firstChild).not.toBeNull();
    expect(screen.getByRole("button", { name: /^restart$/i })).toBeTruthy();
  });

  it("keeps its bar on every other screen", () => {
    const { container } = render(<TopBar />);

    expect(container.firstChild).not.toBeNull();
    expect(screen.getByRole("button", { name: /^restart$/i })).toBeTruthy();
  });
});
