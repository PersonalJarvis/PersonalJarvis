import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppSettingsGroup } from "./AppSettingsGroup";

// A supported desktop host with autostart enabled (the default after the
// spec-aligned config change). Mirrors GET /api/settings/autostart.
const SUPPORTED_ON = {
  enabled: true,
  supported: true,
  installed: true,
  matches_spec: true,
  platform: "win32",
  resolved_command: "pythonw.exe -m jarvis.ui.web.launcher",
  entry_path: "C:\\Users\\me\\...\\Startup\\Personal Jarvis.lnk",
  detail: "Autostart entry is current.",
};

// Windows host where only the throttled startup-shortcut fallback is active:
// the UI must offer the one-time "enable instant start" upgrade.
const WIN_SHORTCUT_FALLBACK = {
  ...{
    enabled: true,
    supported: true,
    installed: true,
    matches_spec: true,
    platform: "win32",
    resolved_command: "pythonw.exe -m jarvis.ui.web.launcher",
    entry_path: "C:\\Users\\me\\...\\Startup\\Personal Jarvis.lnk",
    detail: "Autostart via startup shortcut.",
  },
  mechanism: "shortcut",
};

// Windows host already upgraded to the logon scheduled task (instant start).
const WIN_TASK = {
  enabled: true,
  supported: true,
  installed: true,
  matches_spec: true,
  platform: "win32",
  mechanism: "scheduled_task",
  resolved_command: "pythonw.exe -m jarvis.ui.web.launcher",
  entry_path: "Task Scheduler\\Personal Jarvis Autostart",
  detail: "Autostart enabled via scheduled task.",
};

// A headless host (no display): the toggle must persist intent but cannot
// create an OS entry, so the switch is disabled with an honest caption.
const HEADLESS = {
  enabled: true,
  supported: false,
  installed: false,
  matches_spec: false,
  platform: "linux",
  resolved_command: "python3 -m jarvis.ui.web.launcher",
  entry_path: null,
  detail: "Autostart-at-login is not available on this host (no display).",
};

afterEach(() => vi.restoreAllMocks());

describe("AppSettingsGroup", () => {
  it("renders the 'App settings' heading and the 'Launch app at login' toggle (ON)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => SUPPORTED_ON }),
    );
    render(<AppSettingsGroup />);

    // The mockup's two visible strings.
    expect(screen.getByText("App settings")).toBeTruthy();
    expect(screen.getByText("Launch app at login")).toBeTruthy();

    // Once GET resolves, the switch reflects the enabled config and is enabled.
    await waitFor(() => {
      const sw = screen.getByRole("switch");
      expect(sw.getAttribute("aria-checked")).toBe("true");
      expect(sw.hasAttribute("disabled")).toBe(false);
    });
  });

  it("disables the toggle with an honest caption on a headless host", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => HEADLESS }),
    );
    render(<AppSettingsGroup />);

    await waitFor(() => {
      const sw = screen.getByRole("switch");
      expect(sw.hasAttribute("disabled")).toBe(true);
    });
    expect(
      screen.getByText(/not available on this host/i),
    ).toBeTruthy();
  });

  it("offers the 'Enable instant start' upgrade on a Windows shortcut fallback", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => WIN_SHORTCUT_FALLBACK })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ...WIN_TASK, ok: true, applied_live: true }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<AppSettingsGroup />);

    const button = await waitFor(() => screen.getByText("Enable instant start"));
    fireEvent.click(button);

    // Clicking re-applies enabled=true so Windows registers the scheduled task.
    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(putCall).toBeTruthy();
      expect(JSON.parse(putCall?.[1]?.body as string)).toMatchObject({ enabled: true });
    });
  });

  it("shows the active state and no upgrade button once the task is registered", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => WIN_TASK }),
    );
    render(<AppSettingsGroup />);

    await waitFor(() => expect(screen.getByText(/instant start is active/i)).toBeTruthy());
    expect(screen.queryByText("Enable instant start")).toBeNull();
  });

  it("PUTs the new state when the toggle is flipped off", async () => {
    const fetchMock = vi
      .fn()
      // initial GET
      .mockResolvedValueOnce({ ok: true, json: async () => SUPPORTED_ON })
      // PUT after toggle
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ...SUPPORTED_ON,
          ok: true,
          enabled: false,
          installed: false,
          applied_live: true,
          persisted: true,
          restart_required: false,
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<AppSettingsGroup />);

    const sw = await waitFor(() => screen.getByRole("switch"));
    fireEvent.click(sw);

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(
        ([, opts]) => opts?.method === "PUT",
      );
      expect(putCall).toBeTruthy();
      expect(putCall?.[0]).toBe("/api/settings/autostart");
      expect(JSON.parse(putCall?.[1]?.body as string)).toMatchObject({
        enabled: false,
        persist: true,
      });
    });
  });
});
