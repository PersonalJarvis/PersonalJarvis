/**
 * The TopBar update button: it renders ONLY when the backend reports a managed
 * install with an available update, shows the new version, and stays hidden on
 * an unmanaged checkout (the dev-tree safety guard surfaced in the UI).
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopBar } from "@/components/layout/TopBar";
import { useEventStore } from "@/store/events";

function mockUpdateStatus(body: Record<string, unknown>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (typeof url === "string" && url.startsWith("/api/update/status")) {
        return { ok: true, status: 200, json: async () => body };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    }),
  );
}

describe("TopBar update button", () => {
  beforeEach(() => {
    useEventStore.setState({ assistantName: "Assistant", toasts: [] });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the Update button with the new version when an update is available", async () => {
    mockUpdateStatus({
      managed: true,
      current: "1.0.1",
      latest: "1.0.2",
      update_available: true,
      notes: "Fixes and improvements",
      published_at: null,
    });
    render(<TopBar />);
    await waitFor(() => expect(screen.getByText("Update available")).toBeTruthy());
    expect(screen.getByText("v1.0.2")).toBeTruthy();
  });

  it("hides the Update button on an unmanaged checkout (dev-tree guard)", async () => {
    mockUpdateStatus({
      managed: false,
      current: "1.0.1",
      latest: null,
      update_available: false,
      notes: null,
      published_at: null,
    });
    render(<TopBar />);
    // The restart button always renders; the update button must not.
    await waitFor(() => expect(screen.getByText("Restart")).toBeTruthy());
    expect(screen.queryByText("Update available")).toBeNull();
  });

  it("hides the Update button when the managed install is already up to date", async () => {
    mockUpdateStatus({
      managed: true,
      current: "1.0.2",
      latest: "1.0.2",
      update_available: false,
      notes: null,
      published_at: null,
    });
    render(<TopBar />);
    await waitFor(() => expect(screen.getByText("Restart")).toBeTruthy());
    expect(screen.queryByText("Update available")).toBeNull();
  });

  it("surfaces the backend's failure detail instead of a generic error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/update/status")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              managed: true,
              current: "1.0.11",
              latest: "1.0.12",
              update_available: true,
              notes: null,
            }),
          };
        }
        if (url === "/api/update/apply") {
          return {
            ok: false,
            status: 502,
            json: async () => ({
              detail: "git fetch failed: could not resolve host github.com",
            }),
          };
        }
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }),
    );

    render(<TopBar />);
    fireEvent.click(
      await screen.findByRole("button", { name: /update available/i }),
    );

    await waitFor(() => {
      expect(
        useEventStore
          .getState()
          .toasts.some(
            (toast) =>
              toast.kind === "error" &&
              toast.message.includes("Update failed") &&
              toast.message.includes("could not resolve host github.com"),
          ),
      ).toBe(true);
    });
  });

  it("reports a staged update honestly when every restart attempt fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/update/status")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              managed: true,
              current: "1.0.11",
              latest: "1.0.12",
              update_available: true,
              notes: null,
            }),
          };
        }
        if (url === "/api/update/apply") {
          return { ok: true, status: 200, json: async () => ({ ok: true }) };
        }
        if (url.startsWith("/api/settings/restart-app")) {
          return {
            ok: false,
            status: 503,
            json: async () => ({ detail: "self-restart unavailable on this host" }),
          };
        }
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }),
    );

    render(<TopBar />);
    fireEvent.click(
      await screen.findByRole("button", { name: /update available/i }),
    );

    // Three restart attempts with a retry pause happen before the verdict.
    await waitFor(
      () => {
        expect(
          useEventStore
            .getState()
            .toasts.some(
              (toast) =>
                toast.kind === "warning" &&
                toast.message.includes("restart") &&
                toast.message.includes("self-restart unavailable"),
            ),
        ).toBe(true);
      },
      { timeout: 8000 },
    );
  }, 10000);

  it("offers to finish a staged update even without a fresh release offer", async () => {
    mockUpdateStatus({
      managed: true,
      current: "1.0.11",
      latest: null,
      update_available: false,
      notes: null,
      published_at: null,
      pending_update: { version: "1.0.12", target_revision: "b".repeat(40) },
    });
    render(<TopBar />);
    await waitFor(() => expect(screen.getByText("Finish update")).toBeTruthy());
    expect(screen.getByText("v1.0.12")).toBeTruthy();
  });

  it("announces a rolled-back update instead of failing silently", async () => {
    mockUpdateStatus({
      managed: true,
      current: "1.0.11",
      latest: "1.0.12",
      update_available: true,
      notes: null,
      published_at: null,
      last_result: { ok: false, rolled_back: true, completed_at: 123 },
    });
    render(<TopBar />);
    await waitFor(() => {
      expect(
        useEventStore
          .getState()
          .toasts.some(
            (toast) =>
              toast.kind === "warning" &&
              toast.message.includes("rolled back"),
          ),
      ).toBe(true);
    });
  });

  it("warns when the update cannot fully repair desktop registration", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/update/status")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              managed: true,
              current: "1.0.6",
              latest: "1.0.7",
              update_available: true,
              notes: null,
            }),
          };
        }
        if (url === "/api/update/apply") {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              ok: true,
              desktop_integration_warning: "launcher repair failed",
            }),
          };
        }
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }),
    );

    render(<TopBar />);
    fireEvent.click(
      await screen.findByRole("button", { name: /update available/i }),
    );

    await waitFor(() => {
      expect(
        useEventStore
          .getState()
          .toasts.some(
            (toast) =>
              toast.kind === "warning" &&
              toast.message.includes("operating system"),
          ),
      ).toBe(true);
    });
  });
});
