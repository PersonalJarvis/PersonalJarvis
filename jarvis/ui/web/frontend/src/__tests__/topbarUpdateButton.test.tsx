/**
 * The update button (now in the sidebar footer): it renders ONLY when the backend reports a managed
 * install with an available update, and stays hidden on an unmanaged checkout
 * (the dev-tree safety guard surfaced in the UI).
 *
 * At rest it is a quiet icon with an accent dot; the version, the release notes
 * and the one action live in a panel the icon opens. A single click on the icon
 * must never start an update — only the panel's "Update & restart" does.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UpdateButton } from "@/components/layout/TopBar";
import { useEventStore } from "@/store/events";

vi.mock("@/lib/bootStagger", () => ({ bootSettled: () => Promise.resolve() }));

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

/** Open the panel from the title-strip icon, then press its install action. */
async function openAndInstall(): Promise<void> {
  fireEvent.click(await screen.findByRole("button", { name: /update available/i }));
  fireEvent.click(await screen.findByRole("button", { name: "Update & restart" }));
}

describe("TopBar update button", () => {
  beforeEach(() => {
    // The front page hides this bar; these tests need the global chrome.
    useEventStore.setState({
      assistantName: "Assistant",
      toasts: [],
      activeSection: "settings",
    });
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
    render(<UpdateButton placement="sidebar" />);
    const button = await screen.findByRole("button", { name: /update available/i });
    // The version rides in the accessible name; the strip itself shows no text.
    expect(button.getAttribute("aria-label")).toContain("v1.0.2");
    expect(screen.queryByText("Update available")).toBeNull();
    expect(screen.getByTestId("update-dot")).toBeTruthy();
  });

  it("opens a panel with version, current version and notes on click", async () => {
    mockUpdateStatus({
      managed: true,
      current: "1.0.1",
      latest: "1.0.2",
      update_available: true,
      notes: "## What's new\n- **Faster** startup\n- Fixed `voice` lag",
      published_at: null,
    });
    render(<UpdateButton placement="sidebar" />);
    fireEvent.click(await screen.findByRole("button", { name: /update available/i }));

    const panel = await screen.findByRole("dialog", { name: "Update available" });
    expect(panel.textContent).toContain("v1.0.2");
    expect(panel.textContent).toContain("You have v1.0.1");
    // Markdown punctuation is stripped, the words stay.
    expect(panel.textContent).toContain("What's new");
    expect(panel.textContent).toContain("• Faster startup");
    expect(panel.textContent).toContain("Fixed voice lag");
    expect(panel.textContent).not.toContain("##");
    expect(panel.textContent).not.toContain("**");
  });

  it("never starts an update from the icon click alone", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.startsWith("/api/update/status")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            managed: true,
            current: "1.0.1",
            latest: "1.0.2",
            update_available: true,
            notes: null,
          }),
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<UpdateButton placement="sidebar" />);
    fireEvent.click(await screen.findByRole("button", { name: /update available/i }));
    await screen.findByRole("dialog");
    await new Promise((resolve) => setTimeout(resolve, 20));

    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.startsWith("/api/update/apply"))).toBe(false);
    expect(urls.some((url) => url.startsWith("/api/settings/restart-app"))).toBe(false);
  });

  it("closes the panel on Later, on Escape and on an outside click", async () => {
    mockUpdateStatus({
      managed: true,
      current: "1.0.1",
      latest: "1.0.2",
      update_available: true,
      notes: null,
      published_at: null,
    });
    render(<UpdateButton placement="sidebar" />);
    const button = await screen.findByRole("button", { name: /update available/i });

    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: "Later" }));
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(button);
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(button);
    await screen.findByRole("dialog");
    fireEvent.pointerDown(document.body);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(button.getAttribute("aria-expanded")).toBe("false");
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
    render(<UpdateButton placement="sidebar" />);
    // The sidebar keeps a quiet update icon at all times; it must not offer an update.
    await waitFor(() => expect(fetch).toHaveBeenCalled());
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
    render(<UpdateButton placement="sidebar" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
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

    render(<UpdateButton placement="sidebar" />);
    await openAndInstall();

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

    render(<UpdateButton placement="sidebar" />);
    await openAndInstall();

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
    render(<UpdateButton placement="sidebar" />);
    const button = await screen.findByRole("button", { name: /finish update/i });
    expect(button.getAttribute("aria-label")).toContain("v1.0.12");
    fireEvent.click(button);
    const panel = await screen.findByRole("dialog", { name: "Finish update" });
    expect(panel.textContent).toContain("v1.0.12");
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
    render(<UpdateButton placement="sidebar" />);
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

    render(<UpdateButton placement="sidebar" />);
    await openAndInstall();

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

  it("never restarts on top of a native installer that already took over", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        if (url.startsWith("/api/update/status")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              managed: true,
              current: "1.5.3",
              latest: "1.6.0",
              update_available: true,
              notes: null,
            }),
          };
        }
        if (url.startsWith("/api/update/apply")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({ ok: true, kind: "frozen", restart_required: false, quitting: true }),
          };
        }
        return { ok: true, status: 200, json: async () => ({}) };
      }),
    );

    render(<UpdateButton placement="sidebar" />);
    await openAndInstall();

    await waitFor(() => expect(screen.getByText("Restarting…")).toBeTruthy());
    await new Promise((resolve) => setTimeout(resolve, 50));
    // On a frozen build a second restart would race the installer for the
    // program files - the server is already closing the app.
    expect(urls.some((url) => url.startsWith("/api/settings/restart-app"))).toBe(false);
  });

  it("arms the mission override when the install itself is refused", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        if (url.startsWith("/api/update/status")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              managed: true,
              current: "1.5.3",
              latest: "1.6.0",
              update_available: true,
              notes: null,
            }),
          };
        }
        if (url === "/api/update/apply") {
          return {
            ok: false,
            status: 409,
            json: async () => ({
              detail: { error: "missions_running", missions: [{ id: "m-1" }, { id: "m-2" }] },
            }),
          };
        }
        if (url === "/api/update/apply?force=true") {
          return {
            ok: true,
            status: 200,
            json: async () => ({ ok: true, restart_required: false }),
          };
        }
        return { ok: true, status: 200, json: async () => ({}) };
      }),
    );

    render(<UpdateButton placement="sidebar" />);
    await openAndInstall();

    await waitFor(() =>
      expect(
        useEventStore
          .getState()
          .toasts.some((toast) => toast.kind === "warning" && toast.message.startsWith("2 ")),
      ).toBe(true),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Restart anyway?" }));

    await waitFor(() => expect(urls).toContain("/api/update/apply?force=true"));
    expect(urls.some((url) => url.startsWith("/api/settings/restart-app"))).toBe(false);
  });

  it("tells the user to restart when nothing could close the app after an install", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/update/status")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              managed: true,
              current: "1.5.3",
              latest: "1.6.0",
              update_available: true,
              notes: null,
            }),
          };
        }
        if (url.startsWith("/api/update/apply")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              ok: true,
              restart_required: false,
              quitting: false,
              handover: "PersonalJarvis.AppImage was replaced",
            }),
          };
        }
        return { ok: true, status: 200, json: async () => ({}) };
      }),
    );

    render(<UpdateButton placement="sidebar" />);
    await openAndInstall();

    await waitFor(() =>
      expect(
        useEventStore
          .getState()
          .toasts.some(
            (toast) => toast.kind === "warning" && toast.message.includes("AppImage was replaced"),
          ),
      ).toBe(true),
    );
    // Not stuck on "Restarting…": the control is usable again.
    expect(screen.queryByText("Restarting…")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
});
