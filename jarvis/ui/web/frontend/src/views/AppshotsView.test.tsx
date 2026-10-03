import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatAppshotHotkey } from "@/lib/appshotApi";
import { useAppshotEditor } from "@/store/appshotEditor";
import { useEventStore } from "@/store/events";
import { AppshotsView } from "@/views/AppshotsView";

const SETTINGS = {
  enabled: true,
  hotkey: "alt+alt",
  region_hotkey: "alt+win+a",
  target: "auto",
  sound: true,
  effect: true,
  sound_effects_master: true,
  shortcut: { hotkey: "alt+alt", armed: true, detail: "" },
  region_shortcut: { hotkey: "alt+win+a", armed: true, detail: "" },
  readiness: {
    capture: true,
    capture_detail: "",
    effect: true,
    effect_detail: "",
    region: true,
    region_detail: "",
  },
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

  it("shows why unsupported capture is unavailable and disables both capture actions", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url === "/api/appshot/settings"
        ? json({ ...SETTINGS, readiness: {
            ...SETTINGS.readiness, capture: false, capture_detail: "Wayland capture is unavailable.",
          } })
        : json({ appshot: null }),
    );
    render(<AppshotsView />);
    const button = await screen.findByTestId("appshots-try");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("appshots-try-region") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Wayland capture is unavailable.")).toBeDefined();
    expect(fetchMock.mock.calls.some(([url]) => url.startsWith("/api/permissions"))).toBe(false);
  });

  it("keeps supported first-use capture available without requesting permissions on mount", async () => {
    render(<AppshotsView />);
    const button = await screen.findByTestId("appshots-try-region");
    expect((button as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByTestId("appshots-try") as HTMLButtonElement).disabled).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => url.startsWith("/api/permissions"))).toBe(false);
  });
});

describe("AppshotsView area appshots", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    useEventStore.setState({ events: [], toasts: [], assistantName: "Jarvis" });
    fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url === "/api/appshot/settings") return json(SETTINGS);
      if (url === "/api/appshot/latest") return json({ appshot: null });
      if (url === "/api/appshot/take") return json({ ok: false, reason: "cancelled", message: "" });
      return json({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the area shortcut with its own hint", async () => {
    render(<AppshotsView />);
    await screen.findByTestId("appshots-region-hotkey");
    expect(screen.getByText(/Alt \+ Win \+ A/, { selector: "p" })).toBeDefined();
  });

  it("asks the backend for an area appshot without a delay", async () => {
    render(<AppshotsView />);
    fireEvent.click(await screen.findByTestId("appshots-try-region"));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => url === "/api/appshot/take")).toBe(true),
    );
    const take = fetchMock.mock.calls.find(([url]) => url === "/api/appshot/take")!;
    expect(JSON.parse(String(take[1].body))).toEqual({ delay_s: 0, scope: "region" });
    // Esc on the picker is not an error worth a toast.
    await waitFor(() =>
      expect((screen.getByTestId("appshots-try-region") as HTMLButtonElement).disabled).toBe(false),
    );
    expect(useEventStore.getState().toasts).toEqual([]);
  });

  it("hides the area controls while an older backend is still running", async () => {
    const { region_hotkey: _hotkey, region_shortcut: _status, ...older } = SETTINGS;
    fetchMock.mockImplementation(async (url: string) =>
      url === "/api/appshot/settings"
        ? json({ ...older, readiness: { capture: true, capture_detail: "", effect: true, effect_detail: "" } })
        : json({ appshot: null }),
    );
    render(<AppshotsView />);
    await screen.findByTestId("appshots-try");
    expect(screen.queryByTestId("appshots-region-hotkey")).toBeNull();
    expect(screen.queryByTestId("appshots-try-region")).toBeNull();
    expect(screen.queryByText(/undefined/)).toBeNull();
  });

  it("disables the area button where no picker can run", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url === "/api/appshot/settings"
        ? json({
            ...SETTINGS,
            readiness: { ...SETTINGS.readiness, region: false, region_detail: "no screen" },
          })
        : json({ appshot: null }),
    );
    render(<AppshotsView />);
    const button = await screen.findByTestId("appshots-try-region");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/no screen/)).toBeDefined();
  });
});

describe("AppshotsView shortcut recorder", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    useEventStore.setState({ events: [], toasts: [], assistantName: "Jarvis" });
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/appshot/settings" && init?.method === "PUT") {
        return json({ ...SETTINGS, ...JSON.parse(String(init.body)) });
      }
      if (url === "/api/appshot/settings") return json(SETTINGS);
      return json({ appshot: null });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const puts = () =>
    fetchMock.mock.calls
      .filter(([url, init]) => url === "/api/appshot/settings" && init?.method === "PUT")
      .map(([, init]) => JSON.parse(String(init.body)));

  function press(codes: string[]) {
    for (const code of codes) fireEvent.keyDown(window, { code, key: code });
    for (const code of [...codes].reverse()) fireEvent.keyUp(window, { code, key: code });
  }

  it("records both Shift keys as the area shortcut", async () => {
    render(<AppshotsView />);
    fireEvent.click(await screen.findByTestId("appshots-region-hotkey-change"));
    press(["ShiftLeft", "ShiftRight"]);
    await waitFor(() => expect(puts()).toEqual([{ region_hotkey: "shift+shift" }]));
  });

  it("records an ordinary combo for the window shortcut", async () => {
    render(<AppshotsView />);
    fireEvent.click(await screen.findByTestId("appshots-hotkey-change"));
    press(["ControlLeft", "ShiftLeft", "KeyS"]);
    await waitFor(() => expect(puts()).toEqual([{ hotkey: "ctrl+shift+s" }]));
  });

  it.each([
    ["AltLeft", "ControlLeft", "AltRight"],
    ["ControlLeft", "AltRight", "AltLeft"],
  ])("records both Alt keys from Off with Windows AltGr events (%s first)", async (...codes) => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/appshot/settings" && init?.method === "PUT") {
        return json({ ...SETTINGS, ...JSON.parse(String(init.body)) });
      }
      if (url === "/api/appshot/settings") return json({ ...SETTINGS, hotkey: "" });
      return json({ appshot: null });
    });
    render(<AppshotsView />);
    const change = await screen.findByTestId("appshots-hotkey-change");
    expect(change.textContent).toBe("Off");
    fireEvent.click(change);
    const keys: Record<string, string> = {
      AltLeft: "Alt", ControlLeft: "Control", AltRight: "AltGraph",
    };
    for (const code of codes) fireEvent.keyDown(window, { code, key: keys[code] });
    for (const code of [...codes].reverse()) fireEvent.keyUp(window, { code, key: keys[code] });
    await waitFor(() => expect(puts()).toEqual([{ hotkey: "alt+alt" }]));
    expect(change.textContent).toBe("Alt+Alt");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("Esc cancels without saving, and a lone modifier explains itself", async () => {
    render(<AppshotsView />);
    const change = await screen.findByTestId("appshots-hotkey-change");
    fireEvent.click(change);
    press(["Escape"]);
    fireEvent.click(change);
    press(["ShiftLeft"]);
    await screen.findByRole("alert");
    expect(puts()).toEqual([]);
  });

  it("the X turns a shortcut off", async () => {
    render(<AppshotsView />);
    fireEvent.click(await screen.findByTestId("appshots-region-hotkey-clear"));
    await waitFor(() => expect(puts()).toEqual([{ region_hotkey: "" }]));
  });
});

describe("AppshotsView editor", () => {
  const SHOT = {
    id: "shot-1",
    width: 800,
    height: 500,
    label: "selected area",
    app_name: "Editor",
    trigger: "hotkey",
    taken_at: 1_700_000_000,
    delivered_to: "message",
  };

  beforeEach(() => {
    useEventStore.setState({ events: [], toasts: [], assistantName: "Jarvis" });
    useAppshotEditor.setState({ openId: null });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/appshot/settings") return json(SETTINGS);
        if (url === "/api/appshot/latest") return json({ appshot: SHOT });
        return json({}, 404);
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("opens from the last appshot and closes on Escape", async () => {
    render(<AppshotsView />);
    fireEvent.click(await screen.findByTestId("appshots-preview-edit"));
    expect(await screen.findByTestId("appshot-editor")).toBeDefined();
    expect(useAppshotEditor.getState().openId).toBe("shot-1");

    fireEvent.keyDown(window, { key: "r" });
    expect(
      screen.getByTestId("appshot-editor-tool-rect").getAttribute("aria-pressed"),
    ).toBe("true");

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("appshot-editor")).toBeNull());
    expect(useAppshotEditor.getState().openId).toBeNull();
  });

  it("opens when the card in the screen corner asked for it", async () => {
    useAppshotEditor.getState().open("shot-1");
    render(<AppshotsView />);
    expect(await screen.findByTestId("appshot-editor")).toBeDefined();
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
