/**
 * The update button as a progress bar.
 *
 * While an update runs, the button IS the progress indicator: it shows the
 * percentage the backend reports, fills proportionally, and finishes on the one
 * phase nothing can measure — the restart, announced by the UI itself because
 * the server reporting it is the process shutting down.
 *
 * What these tests defend is honesty. A bar that animates on a timer, rewinds,
 * or sticks at a number after the update failed is worse than no bar at all:
 * it is what the user decides "is this stuck?" from.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopBar } from "@/components/layout/TopBar";
import { useEventStore } from "@/store/events";

const OFFER = {
  managed: true,
  kind: "managed",
  current: "1.0.1",
  latest: "1.0.2",
  update_available: true,
  notes: null,
  published_at: null,
};

function progressBody(percent: number, over: Record<string, unknown> = {}) {
  return {
    active: true,
    phase: "downloading",
    percent,
    detail: null,
    version: "1.0.2",
    kind: "managed",
    error: null,
    restart_required: true,
    ...over,
  };
}

/**
 * Wire a fake backend. ``progress`` is read fresh on every poll, so a test can
 * move the percentage between renders the way a real download does.
 */
function mockBackend(opts: {
  progress: () => Record<string, unknown>;
  applyOk?: boolean;
  restartOk?: boolean;
}) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      if (url.startsWith("/api/update/status")) {
        return { ok: true, status: 200, json: async () => OFFER };
      }
      if (url.startsWith("/api/update/progress")) {
        return { ok: true, status: 200, json: async () => opts.progress() };
      }
      if (url.startsWith("/api/update/apply")) {
        const ok = opts.applyOk !== false;
        return {
          ok,
          status: ok ? 200 : 502,
          json: async () => (ok ? { ok: true } : { detail: "git fetch failed" }),
        };
      }
      if (url.startsWith("/api/settings/restart-app")) {
        const ok = opts.restartOk !== false;
        return { ok, status: ok ? 200 : 500, json: async () => ({}) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    }),
  );
  return calls;
}

async function clickUpdate(): Promise<void> {
  const button = await screen.findByText("Update available");
  button.click();
}

function fillWidth(): string | undefined {
  const fill = document.querySelector<HTMLElement>(
    '[data-testid="update-progress-fill"]',
  );
  return fill?.style.width;
}

describe("TopBar update progress", () => {
  beforeEach(() => {
    useEventStore.setState({
      assistantName: "Assistant",
      toasts: [],
      activeSection: "settings",
    });
    window.localStorage.clear();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the percentage the backend reports", async () => {
    mockBackend({ progress: () => progressBody(70), restartOk: false });
    render(<TopBar />);
    await clickUpdate();

    await waitFor(() => expect(screen.getByText("Updating 70%")).toBeTruthy());
  });

  it("fills the button proportionally to that percentage", async () => {
    mockBackend({ progress: () => progressBody(70), restartOk: false });
    render(<TopBar />);
    await clickUpdate();

    await waitFor(() => expect(fillWidth()).toBe("70%"));
  });

  it("follows the percentage upward as the download runs", async () => {
    let percent = 12;
    mockBackend({ progress: () => progressBody(percent), restartOk: false });
    render(<TopBar />);
    await clickUpdate();

    await waitFor(() => expect(screen.getByText("Updating 12%")).toBeTruthy());
    percent = 84;
    await waitFor(() => expect(screen.getByText("Updating 84%")).toBeTruthy());
  });

  it("exposes the same percentage to assistive technology", async () => {
    mockBackend({ progress: () => progressBody(70), restartOk: false });
    render(<TopBar />);
    await clickUpdate();

    await waitFor(() => {
      const bar = screen.getByRole("progressbar");
      expect(bar.getAttribute("aria-valuenow")).toBe("70");
      expect(bar.getAttribute("aria-valuemax")).toBe("100");
    });
  });

  it("is not a progress bar before an update starts", async () => {
    mockBackend({ progress: () => progressBody(0) });
    render(<TopBar />);
    await screen.findByText("Update available");

    // A stray role here would announce a bar frozen at 0 % on every launch.
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(fillWidth()).toBeUndefined();
  });

  it("ends on the restart, which no server can report", async () => {
    mockBackend({ progress: () => progressBody(100, { phase: "ready" }) });
    render(<TopBar />);
    await clickUpdate();

    await waitFor(() => expect(screen.getByText("Restarting…")).toBeTruthy());
    await waitFor(() => expect(fillWidth()).toBe("100%"));
  });

  it("drops the bar when the update fails instead of freezing it", async () => {
    mockBackend({ progress: () => progressBody(42), applyOk: false });
    render(<TopBar />);
    await clickUpdate();

    await waitFor(() => expect(screen.queryByRole("progressbar")).toBeNull());
    expect(screen.getByText("Update available")).toBeTruthy();
  });

  it("announces a finished update exactly once per install", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/update/status")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              ...OFFER,
              update_available: false,
              last_result: { ok: true, rolled_back: false, completed_at: 1234 },
            }),
          };
        }
        return { ok: true, status: 200, json: async () => ({}) };
      }),
    );

    const { unmount } = render(<TopBar />);
    await waitFor(() =>
      expect(
        useEventStore.getState().toasts.some((t) => t.message.includes("1.0.1")),
      ).toBe(true),
    );

    // A second launch reads the same verdict file off disk. Without the
    // localStorage guard it would re-announce the same install forever.
    useEventStore.setState({ toasts: [] });
    unmount();
    render(<TopBar />);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(useEventStore.getState().toasts).toHaveLength(0);
  });
});
