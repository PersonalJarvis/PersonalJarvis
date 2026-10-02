import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatAppshotHotkey } from "@/lib/appshotApi";
import { useEventStore } from "@/store/events";
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
