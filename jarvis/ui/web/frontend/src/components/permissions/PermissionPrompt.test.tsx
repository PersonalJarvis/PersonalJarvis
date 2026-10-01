import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PermissionItem, PermissionSnapshot } from "@/hooks/usePermissions";
import { askPermission, usePermissionPrompt } from "@/store/permissionPrompt";

const request = vi.fn();
const openSettings = vi.fn();
const reset = vi.fn();
const pushToast = vi.fn();
let mockSnapshot: PermissionSnapshot | null = null;

vi.mock("@/i18n", () => ({
  // Echoes keys like the other permission tests, except that the known "why"
  // sentences resolve (an unknown feature must fall back to the description).
  useT: () => (key: string) =>
    key === "permissions.prompt.reason.voice" ? "reason: voice" : key,
  fill: (template: string) => template,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (state: { pushToast: typeof pushToast }) => unknown) =>
    selector({ pushToast }),
}));

vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    snapshot: mockSnapshot,
    pendingId: null,
    request,
    openSettings,
    reset,
  }),
}));

import { PermissionPrompt } from "./PermissionPrompt";

function snapshotWith(row: Partial<PermissionItem>, platform = "darwin"): PermissionSnapshot {
  return {
    platform,
    supported: platform === "darwin",
    headless: false,
    app_identity: { stable: true },
    permissions: [
      {
        id: "microphone",
        status: "not_determined",
        required: ["voice"],
        can_request: true,
        can_open_settings: true,
        can_reset: false,
        restart_required: false,
        ...row,
      } as PermissionItem,
    ],
    features: {},
    restart_required: false,
  };
}

beforeEach(() => {
  usePermissionPrompt.setState({ request: null });
  vi.clearAllMocks();
});

afterEach(cleanup);

describe("PermissionPrompt", () => {
  it("renders nothing until a feature asks", () => {
    mockSnapshot = snapshotWith({});
    render(<PermissionPrompt />);

    expect(screen.queryByTestId("permission-prompt")).toBeNull();
  });

  it("explains the feature and fires the system dialog on Continue", () => {
    mockSnapshot = snapshotWith({});
    render(<PermissionPrompt />);
    act(() => askPermission("microphone", "voice"));

    expect(screen.getByTestId("permission-prompt").getAttribute("data-permission")).toBe("microphone");
    expect(screen.getByText("reason: voice")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "permissions.prompt.allow" }));
    expect(request).toHaveBeenCalledWith("microphone");
  });

  it("falls back to the access's own description for an unknown feature", () => {
    mockSnapshot = snapshotWith({});
    render(<PermissionPrompt />);
    act(() => askPermission("microphone", "something_new"));

    expect(screen.getByText("permissions.items.microphone.description")).toBeDefined();
  });

  it("leads to System Settings when macOS has no dialog left", () => {
    mockSnapshot = snapshotWith({ status: "denied", can_request: false });
    render(<PermissionPrompt />);
    act(() => askPermission("microphone", "voice"));

    expect(screen.queryByRole("button", { name: "permissions.prompt.allow" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "permissions.open_settings" }));
    expect(openSettings).toHaveBeenCalledWith("microphone");
  });

  it("offers a restart once the grant only applies to a fresh process", () => {
    mockSnapshot = snapshotWith({ status: "not_granted", can_request: false, restart_required: true });
    render(<PermissionPrompt />);
    act(() => askPermission("microphone", "voice"));

    expect(screen.getByRole("button", { name: "permissions.restart_now" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "permissions.open_settings" })).toBeNull();
  });

  it("closes without ever showing when the access is already granted", () => {
    mockSnapshot = snapshotWith({ status: "granted", can_request: false });
    render(<PermissionPrompt />);
    act(() => askPermission("microphone", "voice"));

    expect(screen.queryByTestId("permission-prompt")).toBeNull();
    expect(usePermissionPrompt.getState().request).toBeNull();
  });

  it("stays away on other operating systems", () => {
    mockSnapshot = snapshotWith({}, "win32");
    render(<PermissionPrompt />);
    act(() => askPermission("microphone", "voice"));

    expect(screen.queryByTestId("permission-prompt")).toBeNull();
    expect(usePermissionPrompt.getState().request).toBeNull();
  });

  it("says it worked and closes once the grant lands", () => {
    mockSnapshot = snapshotWith({});
    const { rerender } = render(<PermissionPrompt />);
    act(() => askPermission("microphone", "voice"));
    expect(screen.getByTestId("permission-prompt")).toBeDefined();

    mockSnapshot = snapshotWith({ status: "granted", can_request: false });
    rerender(<PermissionPrompt />);

    expect(pushToast).toHaveBeenCalledWith("success", "permissions.prompt.granted");
    expect(usePermissionPrompt.getState().request).toBeNull();
  });

  it("Not now dismisses the card", () => {
    mockSnapshot = snapshotWith({});
    render(<PermissionPrompt />);
    act(() => askPermission("microphone", "voice"));

    fireEvent.click(screen.getByTestId("permission-prompt-later"));
    expect(usePermissionPrompt.getState().request).toBeNull();
  });
});

describe("PermissionPrompt outside the installed app", () => {
  it("explains why there is nothing to click instead of offering a wrong-identity grant", () => {
    mockSnapshot = {
      ...snapshotWith({ can_request: false, can_open_settings: false }),
      app_identity: { stable: false },
    };
    render(<PermissionPrompt />);
    act(() => askPermission("microphone", "voice"));

    expect(screen.getByTestId("permission-prompt-hint").textContent).toBe(
      "permissions.identity_warning",
    );
    expect(screen.queryByRole("button", { name: "permissions.prompt.allow" })).toBeNull();
    expect(screen.queryByRole("button", { name: "permissions.open_settings" })).toBeNull();
  });
});
