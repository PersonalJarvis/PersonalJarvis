import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { usePermissionsStore } from "@/store/permissions";
import { KeybindsPanel } from "./KeybindsPanel";

const CONFIG = {
  keybinds: { call: "f3+f4", hangup: "f5" },
  defaults: {},
  suggestions: [],
  restart_required: false,
  shortcuts_status: { state: "needs_input_monitoring", detail: "English backend sentence" },
};

beforeEach(() => {
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
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(CONFIG), { status: 200 })));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("KeybindsPanel", () => {
  it("the Settings card says what global shortcuts need from macOS above its rows", async () => {
    render(<KeybindsPanel />);
    expect(await screen.findByTestId("shortcuts-status-note")).toBeTruthy();
    expect(screen.getByTestId("shortcuts-status-sentence").textContent).toContain("Input Monitoring");
  });

  it("the bare variant leaves it to the page that hosts it (one status per page)", async () => {
    render(<KeybindsPanel bare />);
    await screen.findByTestId("combo-field-call");
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
  });
});
