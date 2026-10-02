import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatAppshotHotkey } from "@/lib/appshotApi";
import { useEventStore } from "@/store/events";
import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { usePermissionsStore } from "@/store/permissions";
import { AppshotsView } from "@/views/AppshotsView";

const SETTINGS = {
  enabled: true,
  hotkey: "alt+alt",
  target: "auto",
  sound: true,
  effect: true,
  sound_effects_master: true,
  shortcut: { hotkey: "alt+alt", armed: true, detail: "" },
  readiness: { capture: true, capture_detail: "", effect: true, effect_detail: "" },
};

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

describe("AppshotsView", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    useEventStore.setState({ events: [], toasts: [], assistantName: "Jarvis" });
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/appshot/settings" && init?.method === "PUT") {
        return json({ ...SETTINGS, ...JSON.parse(String(init.body)) });
      }
      if (url === "/api/appshot/settings") return json(SETTINGS);
      if (url === "/api/appshot/latest") return json({ appshot: null });
      return json({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the switches once the settings arrive", async () => {
    render(<AppshotsView />);
    await waitFor(() => expect(screen.getByTestId("appshots-enabled")).toBeDefined());
    expect(screen.getByTestId("appshots-sound").getAttribute("data-state")).toBe("checked");
    expect(screen.getByTestId("appshots-effect").getAttribute("data-state")).toBe("checked");
    expect(screen.getByTestId("appshots-try")).toBeDefined();
  });

  it("saves one switch with a PUT of just that key", async () => {
    render(<AppshotsView />);
    const sound = await screen.findByTestId("appshots-sound");
    fireEvent.click(sound);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            url === "/api/appshot/settings" &&
            init?.method === "PUT" &&
            JSON.parse(String(init.body)).sound === false,
        ),
      ).toBe(true),
    );
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")!;
    expect(Object.keys(JSON.parse(String(put[1].body)))).toEqual(["sound"]);
  });

  it("disables every other control while appshots are switched off", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url === "/api/appshot/settings"
        ? json({ ...SETTINGS, enabled: false })
        : json({ appshot: null }),
    );
    render(<AppshotsView />);
    const tryButton = await screen.findByTestId("appshots-try");
    expect((tryButton as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("appshots-sound").hasAttribute("disabled")).toBe(true);
  });
});

describe("AppshotsView permissions, said where the feature lives", () => {
  function darwin() {
    (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = true;
    usePermissionsStore.setState({
      ...EMPTY_PROMPTS,
      inline: {},
      snapshot: {
        platform: "darwin",
        supported: true,
        headless: false,
        app_identity: { app_name: "Personal Jarvis", bundle_id: null, bundle_path: null, launched_as_bundle: true, stable: true },
        outside_installed_app: false,
        permissions: [],
        needed: [],
      },
    });
  }

  function stub(settings: unknown, shortcutsState: string) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/appshot/settings") return json(settings);
        if (url === "/api/appshot/latest") return json({ appshot: null });
        if (url === "/api/settings/keybinds") {
          return json({
            keybinds: {},
            defaults: {},
            suggestions: [],
            restart_required: false,
            shortcuts_status: { state: shortcutsState, detail: "" },
          });
        }
        return json({}, 404);
      }),
    );
  }

  beforeEach(() => {
    useEventStore.setState({ events: [], toasts: [], assistantName: "Jarvis" });
    window.localStorage.clear();
    darwin();
  });
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    vi.unstubAllGlobals();
    delete (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP;
  });

  it("explains a combination shortcut macOS has not allowed in the person's language, not the backend's English", async () => {
    stub(
      {
        ...SETTINGS,
        hotkey: "ctrl+alt+a",
        shortcut: { hotkey: "ctrl+alt+a", armed: false, detail: "ENGLISH tap is not running" },
      },
      "needs_input_monitoring",
    );
    render(<AppshotsView />);

    // A compact, one-line note: this page is about appshots, not about shortcuts.
    const note = await screen.findByTestId("shortcuts-status-note");
    expect(note.getAttribute("data-variant")).toBe("compact");
    expect(note.textContent).toContain("Shortcuts need Input Monitoring to work in other apps");
    expect(note.textContent).not.toContain("Buttons and voice work without it");
    expect(screen.getByRole("button", { name: "Enable global shortcuts" })).toBeTruthy();
    expect(document.body.textContent).not.toContain("ENGLISH tap is not running");
  });

  it("lets the person close the note, and keeps it closed on the next visit", async () => {
    const withHotkey = {
      ...SETTINGS,
      hotkey: "ctrl+alt+a",
      shortcut: { hotkey: "ctrl+alt+a", armed: false, detail: "" },
    };
    stub(withHotkey, "needs_input_monitoring");
    const { unmount } = render(<AppshotsView />);
    await screen.findByTestId("shortcuts-status-note");

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
    unmount();

    render(<AppshotsView />);
    await screen.findByTestId("appshots-hotkey");
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
  });

  it("does not tie the both-Option gesture to Input Monitoring (its permission need is unverified)", async () => {
    stub(SETTINGS, "needs_input_monitoring");
    render(<AppshotsView />);
    await screen.findByTestId("appshots-hotkey");
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
  });

  it("keeps the backend's reason for a shortcut that is off for another cause", async () => {
    stub(
      {
        ...SETTINGS,
        hotkey: "ctrl+alt+a",
        shortcut: { hotkey: "ctrl+alt+a", armed: false, detail: "The hotkey backend is unavailable." },
      },
      "ready",
    );
    render(<AppshotsView />);
    await waitFor(() => expect(document.body.textContent).toContain("Not active: The hotkey backend is unavailable."));
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
  });

  it("says a refused appshot's Screen Recording problem on the page, and the card stays quiet", async () => {
    stub(SETTINGS, "ready");
    render(<AppshotsView />);
    await screen.findByTestId("appshots-hotkey");

    act(() => {
      usePermissionsStore.getState().ingest(
        "PermissionNeeded",
        "t1",
        {
          permissions: ["screen_recording"],
          feature: "appshot",
          reason: "needs_settings",
          phase: "blocked",
          origin: "user",
          target: "",
          can_prompt: false,
          can_open_settings: true,
          outside_app: false,
          detail: "ENGLISH backend detail",
        },
        Date.now(),
      );
    });

    const note = screen.getByTestId("appshots-permission-note");
    expect(note.textContent).toContain("Appshots need access to \u201cScreen Recording\u201d.");
    expect(note.textContent).not.toContain("ENGLISH backend detail");
    expect(usePermissionsStore.getState().inline).toEqual({ appshot: 1 });
    expect(screen.getByRole("button", { name: "Open System Settings" })).toBeTruthy();
  });
});

describe("formatAppshotHotkey", () => {
  it("names the both-Alt gesture per platform", () => {
    expect(formatAppshotHotkey("alt+alt", false)).toBe("Alt + Alt");
    expect(formatAppshotHotkey("alt+alt", true)).toBe("⌥ + ⌥");
  });

  it("title-cases an ordinary combo", () => {
    expect(formatAppshotHotkey("ctrl+alt+a", false)).toBe("Ctrl + Alt + A");
    expect(formatAppshotHotkey("", false)).toBe("");
  });
});
