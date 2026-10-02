/**
 * The Privacy group explains the macOS privacy database: Windows and Linux have
 * none, so neither the nav entry nor the page is offered there.
 */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/hooks/useTheme";
import { useI18nStore } from "@/i18n";
import { usePermissionsStore } from "@/store/permissions";
import { SettingsView } from "@/views/SettingsView";

let platform = "darwin";

beforeEach(() => {
  platform = "darwin";
  useI18nStore.getState().setUi("en", { push: false });
  usePermissionsStore.setState({ snapshot: null });
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/permissions/status") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            platform,
            supported: true,
            headless: false,
            app_identity: { app_name: "Personal Jarvis" },
            outside_installed_app: false,
            permissions: [
              { id: "microphone", label: "Microphone", status: platform === "darwin" ? "not_determined" : "not_required", used_for: [], can_request: true, can_open_settings: true, can_reset: false, restart_hint: false, detail: "", settings_path: platform === "darwin" ? "x" : null },
            ],
            needed: [],
          }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}), text: async () => "{}" } as unknown as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <SettingsView />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

describe("Settings > Privacy nav entry", () => {
  it("is listed once the backend says it is macOS", async () => {
    usePermissionsStore.setState({ snapshot: { platform: "darwin" } as never });
    renderSettings();

    const nav = screen.getByTestId("settings-section-nav");
    expect(within(nav).getByRole("button", { name: "Privacy" })).toBeTruthy();
    expect(await screen.findByTestId("permission-row-microphone")).toBeTruthy();
  });

  it("is not listed on Windows or Linux, and no page is rendered for it", async () => {
    platform = "win32";
    usePermissionsStore.setState({ snapshot: { platform: "win32" } as never });
    const { container } = renderSettings();

    const nav = screen.getByTestId("settings-section-nav");
    expect(within(nav).queryByRole("button", { name: "Privacy" })).toBeNull();
    expect(container.querySelector('[data-settings-section="permissions"]')).toBeNull();
  });

  it("disappears when the backend turns out not to be macOS (a remote browser on a Mac)", async () => {
    // jsdom's user agent is not a Mac, so start from a snapshot that says darwin, then learn otherwise.
    usePermissionsStore.setState({ snapshot: { platform: "darwin" } as never });
    platform = "linux";
    renderSettings();

    await waitFor(() => expect(usePermissionsStore.getState().snapshot?.platform).toBe("linux"));
    const nav = screen.getByTestId("settings-section-nav");
    expect(within(nav).queryByRole("button", { name: "Privacy" })).toBeNull();
  });
});
