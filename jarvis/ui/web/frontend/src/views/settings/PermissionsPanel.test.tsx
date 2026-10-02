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
  body?: unknown;
}

let calls: Call[] = [];
let permissionRows: Array<Record<string, unknown>> = [];
let platform = "darwin";
let outside = false;
let resetStatus = 200;
let headless = false;
let neededEpisodes: Array<Record<string, unknown>> = [];

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
    needed: neededEpisodes,
  };
}

beforeEach(() => {
  calls = [];
  platform = "darwin";
  outside = false;
  resetStatus = 200;
  neededEpisodes = [];
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
      calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
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

    // ONE title: the Settings nav already says "Privacy", so there is no second heading under it.
    expect(screen.getAllByRole("heading").map((heading) => heading.textContent)).toEqual(["Privacy"]);
    expect(screen.queryByText("macOS privacy permissions")).toBeNull();
    expect(screen.getByText(/Personal Jarvis asks only when a feature needs it\./)).toBeTruthy();
  });

  it("is one list of rows: icon, title, description, a pill", async () => {
    await renderPanel();

    const list = screen.getByRole("list");
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    const mic = within(screen.getByTestId("permission-row-microphone"));
    expect(mic.getByText("Microphone")).toBeTruthy();
    expect(mic.getByText("Used for voice conversations, dictation and the wake word.")).toBeTruthy();
  });

  it("uses ONE pill vocabulary: Allowed / Not asked yet / Off / Restricted, and never the backend's English detail", async () => {
    await renderPanel();

    expect(screen.getByTestId("permission-status-microphone").textContent).toBe("Allowed");
    expect(screen.getByTestId("permission-status-screen_recording").textContent).toBe("Not asked yet");
    expect(screen.getByTestId("permission-status-accessibility").textContent).toBe("Off");
    expect(screen.getByTestId("permission-status-input_monitoring").textContent).toBe("Restricted");
    expect(screen.queryByText(/English backend text/)).toBeNull();
  });

  it("reads 'Not available' and 'Not required' for the rest of the vocabulary", async () => {
    permissionRows = [
      row("automation", { status: "unavailable", can_request: false, can_reset: false }),
      row("credential_store", { status: "not_required", can_request: false, can_open_settings: false, can_reset: false, settings_path: null }),
    ];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-automation");

    expect(screen.getByTestId("permission-status-automation").textContent).toBe("Not available");
    expect(screen.getByTestId("permission-status-credential_store").textContent).toBe("Not required");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("a fresh Mac is calm: every row not asked yet has ONE quiet 'Ask now', and no path, no reset, no hint", async () => {
    permissionRows = [
      row("microphone", { status: "not_determined" }),
      row("screen_recording", { status: "not_granted" }),
      row("accessibility", { status: "not_granted" }),
      row("input_monitoring", { status: "not_granted" }),
      row("automation", { status: "not_determined" }),
    ];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-microphone");

    for (const id of ["microphone", "screen_recording", "accessibility", "input_monitoring", "automation"]) {
      const view = within(screen.getByTestId(`permission-row-${id}`));
      expect(screen.getByTestId(`permission-status-${id}`).textContent, id).toBe("Not asked yet");
      expect(view.getAllByRole("button").map((button) => button.textContent), id).toEqual(["Ask now"]);
      expect(screen.queryByTestId(`permission-path-${id}`), id).toBeNull();
    }
    expect(screen.queryByText(/macOS will not ask for this access again/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Open System Settings" })).toBeNull();
    // The old "Allow" is gone: a row is asked with "Ask now" only.
    expect(screen.queryByRole("button", { name: "Allow" })).toBeNull();
  });

  it("an off row has ONE action, 'Open System Settings', with the textual path as its secondary line", async () => {
    await renderPanel();
    const accessibility = screen.getByTestId("permission-row-accessibility");

    expect(within(accessibility).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Open System Settings",
    ]);
    expect(screen.getByTestId("permission-path-accessibility").textContent).toBe(
      "System Settings > Privacy & Security > Accessibility",
    );
  });

  it("offers nothing on a row that is allowed, restricted or not required", async () => {
    await renderPanel();

    for (const id of ["microphone", "input_monitoring", "credential_store"]) {
      expect(within(screen.getByTestId(`permission-row-${id}`)).queryByRole("button"), id).toBeNull();
      expect(screen.queryByTestId(`permission-path-${id}`), id).toBeNull();
    }
  });

  it("a binary permission reads 'Not asked yet' until it was asked, then 'Off' with the Settings action", async () => {
    permissionRows = [row("screen_recording", { status: "not_granted", can_request: true, can_reset: false })];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-screen_recording");
    expect(screen.getByTestId("permission-status-screen_recording").textContent).toBe("Not asked yet");

    fireEvent.click(screen.getByRole("button", { name: "Ask now" }));

    await waitFor(() => expect(screen.getByTestId("permission-status-screen_recording").textContent).toBe("Off"));
    const view = within(screen.getByTestId("permission-row-screen_recording"));
    expect(view.getAllByRole("button").map((button) => button.textContent)).toEqual(["Open System Settings"]);
    expect(screen.getByTestId("permission-path-screen_recording")).toBeTruthy();
  });

  it("reads 'Off' at once when an open episode already says the person was asked (needs_settings)", async () => {
    permissionRows = [row("screen_recording", { status: "not_granted", can_request: true, can_reset: false })];
    neededEpisodes = [{ permissions: ["screen_recording"], feature: "computer_use", reason: "needs_settings", phase: "blocked", origin: "user", target: "", can_prompt: false, can_open_settings: true, outside_app: false, detail: "", trace_id: "t", opened_at_ns: 1 }];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-screen_recording");

    expect(screen.getByTestId("permission-status-screen_recording").textContent).toBe("Off");
  });

  it("'Ask again' and the stale-grant hint appear only after coming back from Settings with the row still off", async () => {
    permissionRows = [row("accessibility", { status: "denied", can_request: false, can_reset: true })];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-accessibility");
    const rowView = () => within(screen.getByTestId("permission-row-accessibility"));
    expect(rowView().queryByRole("button", { name: "Ask again" })).toBeNull();
    expect(screen.queryByText(/macOS will not ask for this access again/)).toBeNull();

    fireEvent.click(rowView().getByRole("button", { name: "Open System Settings" }));
    await waitFor(() => expect(posts("/accessibility/open-settings")).toHaveLength(1));
    // Opening Settings alone does not make it the stranded case.
    expect(rowView().queryByRole("button", { name: "Ask again" })).toBeNull();

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(await rowView().findByRole("button", { name: "Ask again" }, { timeout: 2_000 })).toBeTruthy();
    expect(screen.getByText(/macOS will not ask for this access again/)).toBeTruthy();
  });

  it("does not show 'Ask again' after coming back when the row now reads allowed", async () => {
    permissionRows = [row("accessibility", { status: "denied", can_request: false, can_reset: true })];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-accessibility");
    fireEvent.click(screen.getByRole("button", { name: "Open System Settings" }));
    await waitFor(() => expect(posts("/accessibility/open-settings")).toHaveLength(1));

    permissionRows = [row("accessibility", { status: "granted", can_request: false, can_reset: false })];
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    await waitFor(() => expect(screen.getByTestId("permission-status-accessibility").textContent).toBe("Allowed"), {
      timeout: 2_000,
    });
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
  });

  it("has no wizard, no scoring, no banner and no 'Optional' marks", async () => {
    await renderPanel();

    expect(screen.queryByText(/Set up everything/i)).toBeNull();
    expect(screen.queryByText(/Optional/i)).toBeNull();
    expect(screen.queryByText(/Action needed/i)).toBeNull();
    expect(screen.queryByTestId("permissions-setup-all")).toBeNull();
  });

  it("Ask now asks, then reads the page again", async () => {
    await renderPanel();
    const before = calls.filter((call) => call.method === "GET").length;

    fireEvent.click(within(screen.getByTestId("permission-row-screen_recording")).getByRole("button", { name: "Ask now" }));

    await waitFor(() => expect(posts("/screen_recording/request")).toHaveLength(1));
    await waitFor(() => expect(calls.filter((call) => call.method === "GET").length).toBe(before + 1));
  });

  it("Open System Settings opens the right pane", async () => {
    await renderPanel();

    fireEvent.click(within(screen.getByTestId("permission-row-accessibility")).getByRole("button", { name: "Open System Settings" }));

    await waitFor(() => expect(posts("/accessibility/open-settings")).toHaveLength(1));
  });

  it("Ask again resets, says what to do next, and a refused reset (already allowed) is a calm note", async () => {
    // The stranded case: both rows were sent to Settings, the person came back, they still read off.
    permissionRows = [
      row("screen_recording", { status: "not_granted", can_request: false, can_reset: true }),
      row("accessibility", { status: "denied", can_request: false, can_reset: true }),
    ];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-screen_recording");
    for (const id of ["screen_recording", "accessibility"]) {
      fireEvent.click(within(screen.getByTestId(`permission-row-${id}`)).getByRole("button", { name: "Open System Settings" }));
      await waitFor(() => expect(posts(`/${id}/open-settings`)).toHaveLength(1));
    }
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    const again = (id: string) =>
      within(screen.getByTestId(`permission-row-${id}`)).findByRole("button", { name: "Ask again" }, { timeout: 2_000 });

    fireEvent.click(await again("screen_recording"));
    await waitFor(() => expect(posts("/screen_recording/reset")).toHaveLength(1));
    await waitFor(() =>
      expect(useEventStore.getState().toasts.map((toast) => toast.message)).toContain(
        "Reset. Press Ask now to be asked again.",
      ),
    );

    resetStatus = 409;
    fireEvent.click(await again("accessibility"));
    await waitFor(() =>
      expect(useEventStore.getState().toasts.map((toast) => toast.message)).toContain(
        "This is already allowed, so there is nothing to reset.",
      ),
    );
  });

  it("a Keychain that was declined offers 'Try again' (its one action), and says where the keys are for now", async () => {
    permissionRows = [row("credential_store", { status: "not_granted", can_request: true, can_open_settings: false, can_reset: false, settings_path: null })];
    await (async () => {
      render(<PermissionsPanel />);
      await screen.findByTestId("permission-row-credential_store");
    })();

    const keychain = within(screen.getByTestId("permission-row-credential_store"));
    expect(keychain.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(keychain.getByText(/kept in a local file for now/)).toBeTruthy();
    expect(screen.getByTestId("permission-status-credential_store").textContent).toBe("Off");
    expect(keychain.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByTestId("permission-path-credential_store")).toBeNull();
  });

  it("a row that only applies after a restart offers 'Quit and reopen' (its one action) through the shared restart guard", async () => {
    permissionRows = [row("screen_recording", { status: "not_granted", restart_hint: true, can_request: false })];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-screen_recording");

    expect(screen.getByTestId("permission-status-screen_recording").textContent).toBe("Restart needed");
    expect(within(screen.getByTestId("permission-row-screen_recording")).getAllByRole("button")).toHaveLength(1);
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

    expect(screen.getByText(/running from outside your Applications folder/)).toBeTruthy();
  });

  it("outside the installed app 'Ask now' is the confirmation and sends the consent flag", async () => {
    outside = true;
    permissionRows = [row("microphone", { status: "not_determined", can_request: true })];
    await renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Ask macOS now" }));

    await waitFor(() => expect(posts("/api/permissions/microphone/request")).toHaveLength(1));
    expect(posts("/api/permissions/microphone/request")[0].body).toEqual({ allow_outside_app: true });
  });

  it("inside the installed app 'Ask now' asks without the consent flag", async () => {
    permissionRows = [row("microphone", { status: "not_determined", can_request: true })];
    await renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Ask now" }));

    await waitFor(() => expect(posts("/api/permissions/microphone/request")).toHaveLength(1));
    expect(posts("/api/permissions/microphone/request")[0].body).toBeUndefined();
  });

  it("shows the rows read-only, with no host action button, in a remote browser", async () => {
    delete (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP;
    permissionRows = [
      row("screen_recording", { status: "denied", can_request: false }),
      row("accessibility", { status: "denied", can_request: false, restart_hint: true }),
    ];
    await (async () => {
      render(<PermissionsPanel />);
      await screen.findByTestId("permission-row-screen_recording");
    })();

    expect(screen.getByTestId("permission-status-screen_recording")).toBeTruthy();
    // The textual path is information, not an action: a remote viewer still reads where to go.
    expect(screen.getByTestId("permission-path-screen_recording")).toBeTruthy();
    for (const name of ["Ask now", "Ask again", "Open System Settings", "Quit and reopen"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("shows no host action button on a headless backend either", async () => {
    headless = true;
    permissionRows = [row("screen_recording", { status: "not_determined" })];
    render(<PermissionsPanel />);
    await screen.findByTestId("permission-row-screen_recording");

    expect(screen.queryByRole("button", { name: "Open System Settings" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask now" })).toBeNull();
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
