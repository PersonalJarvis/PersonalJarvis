import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { InputIsolationReport } from "@/hooks/useInputIsolation";

const pushToast = vi.fn();
let mockReport: InputIsolationReport | null = null;

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (state: { pushToast: typeof pushToast }) => unknown) =>
    selector({ pushToast }),
}));

vi.mock("@/hooks/useInputIsolation", () => ({
  useInputIsolation: () => ({ report: mockReport, refetch: vi.fn() }),
}));

import { InputIsolationBanner } from "./InputIsolationBanner";

function blocked(overrides: Partial<InputIsolationReport> = {}): InputIsolationReport {
  return {
    blocked: true,
    reason: "elevated",
    platform: "win32",
    summary: "elevated",
    remedy: "restart",
    can_restart_unelevated: true,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  mockReport = null;
});

describe("InputIsolationBanner", () => {
  it("stays invisible while outside input software can reach the window", () => {
    mockReport = blocked({ blocked: false, reason: "none" });
    render(<InputIsolationBanner />);
    expect(screen.queryByTestId("input-isolation-banner")).toBeNull();
  });

  it("stays invisible while the privilege state is still unknown", () => {
    // Never warn on a guess: a user without the problem must see nothing.
    mockReport = null;
    render(<InputIsolationBanner />);
    expect(screen.queryByTestId("input-isolation-banner")).toBeNull();
  });

  it("names the problem and offers the one-click repair when blocked", () => {
    mockReport = blocked();
    render(<InputIsolationBanner />);

    expect(screen.getByTestId("input-isolation-banner")).toBeDefined();
    expect(screen.getByText("input_isolation.title")).toBeDefined();
    expect(screen.getByText("input_isolation.impact")).toBeDefined();
    expect(screen.getByText("input_isolation.restart_now")).toBeDefined();
  });

  it("posts the unelevated restart when the repair button is clicked", async () => {
    mockReport = blocked();
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    render(<InputIsolationBanner />);
    fireEvent.click(screen.getByText("input_isolation.restart_now"));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/settings/restart-unelevated", {
        method: "POST",
      }),
    );
  });

  it("explains the manual route when the app cannot drop its own privileges", () => {
    // UAC disabled / built-in Administrator: offering a button that cannot work
    // would be worse than saying what the user has to do.
    mockReport = blocked({ can_restart_unelevated: false });
    render(<InputIsolationBanner />);

    expect(screen.getByText("input_isolation.manual_hint")).toBeDefined();
    expect(screen.queryByText("input_isolation.restart_now")).toBeNull();
  });

  it("surfaces the backend reason when de-elevation fails, and stays up", async () => {
    mockReport = blocked();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({
          detail: { error: "deescalation_failed", message: "no linked token" },
        }),
      }),
    );

    render(<InputIsolationBanner />);
    fireEvent.click(screen.getByText("input_isolation.restart_now"));

    await waitFor(() => expect(pushToast).toHaveBeenCalledWith("error", "no linked token"));
    // The banner must survive a failed repair — the problem is still there.
    expect(screen.getByTestId("input-isolation-banner")).toBeDefined();
  });

  it("warns instead of killing running missions", async () => {
    mockReport = blocked();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ detail: { error: "missions_running" } }),
      }),
    );

    render(<InputIsolationBanner />);
    fireEvent.click(screen.getByText("input_isolation.restart_now"));

    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(
        "warning",
        "topbar.restart_missions_running",
      ),
    );
  });

  it("can be hidden by the user without pretending the problem is solved", () => {
    mockReport = blocked();
    render(<InputIsolationBanner />);

    fireEvent.click(screen.getByLabelText("input_isolation.dismiss"));
    expect(screen.queryByTestId("input-isolation-banner")).toBeNull();
  });
});
