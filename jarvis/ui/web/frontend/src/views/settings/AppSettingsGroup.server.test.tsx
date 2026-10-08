import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/hooks/useTheme", () => ({
  useTheme: () => ({ preference: "dark", setPreference: vi.fn() }),
}));
vi.mock("@/hooks/useAutostart", () => ({
  useAutostart: () => ({ config: null, loading: true, save: vi.fn() }),
}));

import { AppSettingsGroup } from "./AppSettingsGroup";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("enables an independent server and saves its destination without stopping live work", async () => {
  let config = {
    supported: true, keep_agents_running: true, persistent_server: false, server_url: "",
    running_as_server: false, autostart_enabled: false, running_as_service: false,
    background_only_at_login: false, work: { routines: 1, running: 1, channels: [] },
  };
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
    const body = options?.body ? JSON.parse(String(options.body)) : null;
    requests.push({ path, method: options?.method ?? "GET", body });
    if (options?.method === "PUT") config = { ...config, ...body };
    return { ok: true, json: async () => config };
  }));
  render(<AppSettingsGroup />);
  const toggle = await screen.findByRole("switch", { name: "Independent agent server" });
  await waitFor(() => expect(toggle.getAttribute("disabled")).toBeNull());
  fireEvent.click(toggle);
  const field = await screen.findByLabelText("Server address (leave empty to run on this computer)");
  fireEvent.change(field, { target: { value: "https://jarvis.example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Save address" }));
  await waitFor(() => expect(config.server_url).toBe("https://jarvis.example.com"));
  expect(config.persistent_server).toBe(true);
  expect(requests.filter((r) => r.method === "PUT")).toEqual([
    { path: "/api/settings/background", method: "PUT", body: { persistent_server: true } },
    { path: "/api/settings/background", method: "PUT", body: { server_url: "https://jarvis.example.com" } },
  ]);
  expect(requests.every((r) => !r.path.includes("stop") && !r.path.includes("restart"))).toBe(true);
});
