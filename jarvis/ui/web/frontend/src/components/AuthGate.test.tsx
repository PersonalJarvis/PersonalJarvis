import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "./AuthGate";
import { useI18nStore } from "@/i18n";

function response(status: number): Response {
  return { ok: status >= 200 && status < 300, status } as Response;
}

describe("AuthGate", () => {
  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
    window.__JARVIS_TOKEN = undefined;
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.__JARVIS_TOKEN = undefined;
  });

  it("renders the application when the session cookie is authorized", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(200));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><div>Application</div></AuthGate>);

    expect(await screen.findByText("Application")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith("/api/config", {
      cache: "no-store",
      credentials: "same-origin",
    });
  });

  it("exchanges an injected desktop token before showing the login form", async () => {
    window.__JARVIS_TOKEN = "desktop-session";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(204));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><div>Application</div></AuthGate>);

    expect(await screen.findByText("Application")).toBeTruthy();
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/ui/session", {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_token: "desktop-session" }),
    });
    expect(window.__JARVIS_TOKEN).toBeUndefined();
  });

  it("waits for the desktop token-ready event before opening the login form", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(204));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><div>Application</div></AuthGate>);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await act(async () => Promise.resolve());
    act(() => {
      window.__JARVIS_TOKEN = "late-desktop-session";
      window.dispatchEvent(new Event("jarvis-token-ready"));
    });

    expect(await screen.findByText("Application")).toBeTruthy();
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/ui/session", {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_token: "late-desktop-session" }),
    });
  });

  it("accepts a control key without persisting it in the browser", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(204));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><div>Application</div></AuthGate>);
    const input = await screen.findByLabelText("Control key");
    fireEvent.change(input, { target: { value: "control-secret" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => expect(screen.getByText("Application")).toBeTruthy());
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/ui/session", {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ control_key: "control-secret" }),
    });
  });

  it("falls back to the login form when desktop token exchange is unavailable", async () => {
    window.__JARVIS_TOKEN = "stale-session";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(401))
      .mockRejectedValueOnce(new Error("backend restarted"));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><div>Application</div></AuthGate>);

    expect(await screen.findByLabelText("Control key")).toBeTruthy();
    expect(window.__JARVIS_TOKEN).toBeUndefined();
    expect(screen.queryByText("Application")).toBeNull();
  });

  it("keeps the gate closed when the control key is rejected", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(401));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthGate><div>Application</div></AuthGate>);
    const input = await screen.findByLabelText("Control key");
    fireEvent.change(input, { target: { value: "wrong" } });
    fireEvent.submit(input.closest("form")!);

    expect((await screen.findByRole("alert")).textContent).toBe(
      "That control key was not accepted.",
    );
    expect(screen.queryByText("Application")).toBeNull();
  });
});
