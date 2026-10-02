import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/i18n";
import { resetConnectBudgetForTests } from "@/lib/connectBudget";
import { useEventStore } from "@/store/events";
import { usePermissionsStore } from "@/store/permissions";
import { PermissionsPanel } from "./PermissionsPanel";

interface Call {
  url: string;
  method: string;
}

let calls: Call[] = [];
let permissionRows: Array<Record<string, unknown>> = [];
let platform = "darwin";
let outside = false;
let resetStatus = 200;
let headless = false;

function row(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    label: id,
    status: "not_determined",
    used_for: [],
    can_request: true,
    can_open_settings: true,
    can_reset: true,
    restart_hint: false,
    detail: "English backend text that must never be shown",
    settings_path: "System Settings > Privacy & Security > X",
    ...overrides,
  };
}

function snapshotBody() {
  return {
    platform,
    supported: true,
    headless,
    app_identity: { app_name: "Personal Jarvis", bundle_id: "x", bundle_path: null, launched_as_bundle: true, stable: !outside },
    outside_installed_app: outside,
    permissions: permissionRows,
    needed: [],
  };
}

beforeEach(() => {
  calls = [];
  platform = "darwin";
  outside = false;
  resetStatus = 200;
  permissionRows = [
    row("microphone", { status: "granted", can_request: false, can_reset: false }),
    row("screen_recording", { status: "not_determined" }),
    row("accessibility", { status: "denied", can_request: false }),
    row("input_monitoring", { status: "restricted", can_request: false, can_open_settings: false, can_reset: false, settings_path: "System Settings > Privacy & Security > Input Monitoring" }),
    row("credential_store", { status: "granted", can_request: false, can_open_settings: false, can_reset: false, settings_path: null }),
  ];
  resetConnectBudgetForTests();
  useI18nStore.getState().setUi("en", { push: false });
  useEventStore.setState({ toasts: [] });
  usePermissionsStore.setState({ snapshot: null });
  headless = false;
  // The desktop shell's flag: this window sits at the machine the permissions belong to.
  (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (url === "/api/settings/restart-app") return { ok: true, status: 200, json: async () => ({}) } as Response;
      if (method === "POST" && url.includes("/reset")) {
        return {
          ok: resetStatus < 400,
          status: resetStatus,
          json: async () => ({ ok: resetStatus < 400, permission_id: "x", action: "reset", performed: true, dry_run: false, message: "x", permission: null }),
        } as Response;
      }
      if (method === "POST") {
        return { ok: true, status: 200, json: async () => ({ ok: true, permission: "x", outcome: "pending", asked: true }) } as Response;
      }
      return { ok: true, status: 200, json: async () => snapshotBody() } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP;
});

const posts = (suffix: string) => calls.filter((call) => call.method === "POST" && call.url.includes(suffix));

async function renderPanel() {
  render(<PermissionsPanel />);
  await screen.findByTestId("permission-row-microphone");
}

describe("Settings > Privacy (passive)", () => {
  it("says the app asks only when a feature needs it, with the app name from the snapshot", async () => {
    await renderPanel();

    expect(screen.getByRole("heading", { name: "Privacy" })).toBeTruthy();
    expect(screen.getByText(/Personal Jarvis asks only when a feature needs it\./)).toBeTruthy();
  });

  it("shows Granted / Off or not asked / Denied / Restricted, and never the backend's English detail", async () => {
    await renderPanel();

    expect(screen.getByTestId("permission-status-microphone").textContent).toBe("Granted");
    expect(screen.getByTestId("permission-status-screen_recording").textContent).toBe("Off or not asked");
    expect(screen.getByTestId("permission-status-accessibility").textContent).toBe("Denied");
    expect(screen.getByTestId("permission-status-input_monitoring").textContent).toBe("Restricted");
    expect(screen.queryByText(/English backend text/)).toBeNull();
  });

  it("puts the textual System Settings path next to 'Open System Settings'", async () => {
    await renderPanel();
    const accessibility = screen.getByTestId("permission-row-accessibility");

    expect(within(accessibility).getByRole("button", { name: "Open System Settings" })).toBeTruthy();
    expect(screen.getByTestId("permission-path-accessibility").textContent).toBe(
      "System Settings > Privacy & Security > Accessibility",
    );
  });

  it("offers Allow only where macOS can still be asked, and Ask again only where the backend says so", async () => {
    await renderPanel();

    const screenRow = within(screen.getByTestId("permission-row-screen_recording"));
    expect(screenRow.getByRole("button", { name: "Allow" })).toBeTruthy();
    expect(screenRow.getByRole("button", { name: "Ask again" })).toBeTruthy();

    const granted = within(screen.getByTestId("permission-row-microphone"));
    expect(granted.queryByRole("button", { name: "Allow" })).toBeNull();
    expect(granted.queryByRole("button", { name: "Ask again" })).toBeNull();

    const restricted = within(screen.getByTestId("permission-row-input_monitoring"));
    expect(restricted.queryByRole("button")).toBeNull();
  });

  it("has no wizard, no scoring, no banner and no 'Optional' marks", async () => {
    await renderPanel();

    expect(screen.queryByText(/Set up everything/i)).toBeNull();
    expect(screen.queryByText(/Optional/i)).toBeNull();
    expect(screen.queryByText(/Action needed/i)).toBeNull();
    expect(screen.queryByTestId("permissions-setup-all")).toBeNull();
  });

  it("Allow asks, then reads the page again", async () => {
    await renderPanel();
    const before = calls.filter((call) => call.method === "GET").length;

    fireEvent.click(within(screen.getByTestId("permission-row-screen_recording")).getByRole("button", { name: "Allow" }));

    await waitFor(() => expect(posts("/screen_recording/request")).toHaveLength(1));
    await waitFor(() => expect(calls.filter((call) => call.method === "GET").length).toBe(before + 1));
  });

  it("Open System Settings opens the right pane", async () => {
    await renderPanel();

    fireEvent.click(within(screen.getByTestId("permission-row-accessibility")).getByRole("button", { name: "Open System Settings" }));

    await waitFor(() => expect(posts("/accessibility/open-settings")).toHaveLength(1));
  });

  it("Ask again resets, says what to do next, and a refused reset (already allowed) is a calm note", async () => {
    await renderPanel();
    fireEvent.click(within(screen.getByTestId("permission-row-screen_recording")).getByRole("button", { name: "Ask again" }));
    await waitFor(() => expect(posts("/screen_recording/reset")).toHaveLength(1));
    await waitFor(() =>
      expect(useEventStore.getState().toasts.map((toast) => toast.message)).toContain("Reset. Press Allow to be asked again."),
    );

    resetStatus = 409;
    fireEvent.click(within(screen.getByTestId("permission-row-accessibility")).getByRole("button", { name: "Ask again" }));
    await waitFor(() =>
      expect(useEventStore.getState().toasts.map((toast) => toast.message)).toContain(
        "This is already allowed, so there is nothing to reset.",
      ),
    );
  });

  it("a Keychain that was declined offers 'Try again', and says where the keys are for now", async () => {
    permissionRows = [row("credential_store", { status: "not_granted", can_request: true, can_open_settings: false, can_reset: false, settings_path: null })];
    await (async () => {
      render(<PermissionsPanel />);
      await screen.findByTestId("permission-row-credential_store");
    })();

    const keychain = within(screen.getByTestId("permission-row-credential_store"));
    expect(keychain.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(keychain.getByText(/kept in a local file for now/)).toBeTruthy();
    expect(screen.queryByTestId("permission-path-credential_store")).toBeNull();
  });

  it("a row that only applies after a restart offers 'Quit and reopen' through the shared restart guard", async () => {
    permissionRows = [row("screen_recording", { status: "not_granted", restart_hint: true, can_request: false })];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-screen_recording");

    expect(screen.getByTestId("permission-status-screen_recording").textContent).toBe("Restart needed");
    fireEvent.click(screen.getByRole("button", { name: "Quit and reopen" }));

    await waitFor(() => expect(posts("/api/settings/restart-app")).toHaveLength(1));
  });

  it("shows the restart hint on a granted row after a real failed use", async () => {
    permissionRows = [row("screen_recording", { status: "granted", restart_hint: true, can_request: false })];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-screen_recording");

    expect(screen.getByTestId("permission-status-screen_recording").textContent).toBe("Restart needed");
    fireEvent.click(screen.getByRole("button", { name: "Quit and reopen" }));

    await waitFor(() => expect(posts("/api/settings/restart-app")).toHaveLength(1));
  });

  it("explains a run outside the installed app", async () => {
    outside = true;
    await renderPanel();

    expect(screen.getByText(/is not running as an installed app/)).toBeTruthy();
  });

  it("shows the rows read-only, with no host action button, in a remote browser", async () => {
    delete (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP;
    permissionRows = [
      row("screen_recording", { status: "not_determined" }),
      row("accessibility", { status: "denied", can_request: false, restart_hint: true }),
    ];
    await (async () => {
      render(<PermissionsPanel />);
      await screen.findByTestId("permission-row-screen_recording");
    })();

    expect(screen.getByTestId("permission-status-screen_recording")).toBeTruthy();
    expect(screen.getByTestId("permission-path-screen_recording")).toBeTruthy();
    for (const name of ["Allow", "Ask again", "Open System Settings", "Quit and reopen"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("shows no host action button on a headless backend either", async () => {
    headless = true;
    permissionRows = [row("screen_recording", { status: "not_determined" })];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-screen_recording");

    expect(screen.queryByRole("button", { name: "Open System Settings" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Allow" })).toBeNull();
  });

  it("is hidden on a non-macOS backend", async () => {
    platform = "win32";
    permissionRows = [row("microphone", { status: "not_required", can_request: false, can_open_settings: false, can_reset: false, settings_path: null })];

    const { container } = render(<PermissionsPanel />);
    await waitFor(() => expect(usePermissionsStore.getState().snapshot?.platform).toBe("win32"));

    expect(container.textContent).toBe("");
  });

  it("reads on mount and after the person returns, never on a timer", async () => {
    vi.useFakeTimers();
    try {
      render(<PermissionsPanel />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      const reads = () => calls.filter((call) => call.method === "GET").length;
      expect(reads()).toBe(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5 * 60_000);
      });
      expect(reads()).toBe(1);

      act(() => {
        window.dispatchEvent(new Event("focus"));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      expect(reads()).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says so, with a way to retry, when the backend cannot be read", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<PermissionsPanel />);

    expect(await screen.findByText("Could not read the current permission status.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Check again" })).toBeTruthy();
  });
});
