import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TopBar } from "./TopBar";

vi.mock("@/hooks/useUpdate", () => ({
  useUpdate: () => ({ status: { managed: false, update_available: false } }),
}));

afterEach(() => vi.restoreAllMocks());

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
