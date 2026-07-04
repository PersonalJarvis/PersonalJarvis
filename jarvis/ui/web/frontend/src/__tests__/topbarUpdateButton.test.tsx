/**
 * The TopBar update button: it renders ONLY when the backend reports a managed
 * install with an available update, shows the new version, and stays hidden on
 * an unmanaged checkout (the dev-tree safety guard surfaced in the UI).
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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
    useEventStore.setState({ assistantName: "Assistant" });
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

  it("opens the What's-new preview modal with the release notes on click", async () => {
    mockUpdateStatus({
      managed: true,
      current: "1.0.1",
      latest: "1.0.2",
      update_available: true,
      notes: "### Fixed\n\n- The stubborn bug is finally gone",
      published_at: null,
      release_url:
        "https://github.com/PersonalJarvis/PersonalJarvis/releases/tag/v1.0.2",
    });
    render(<TopBar />);
    const btn = await screen.findByRole("button", { name: /Update available/ });
    // The button only PREVIEWS — it must not update on its own.
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByText("What's new")).toBeTruthy());
    expect(screen.getByText(/The stubborn bug is finally gone/)).toBeTruthy();
    // The confirm action lives inside the modal, not on the top bar.
    expect(screen.getByText("Update now")).toBeTruthy();
    expect(screen.getByText("Later")).toBeTruthy();
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
});
