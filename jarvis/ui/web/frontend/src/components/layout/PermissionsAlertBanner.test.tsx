import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PermissionSnapshot } from "@/hooks/usePermissions";

const request = vi.fn();
const openSettings = vi.fn();

let mockSnapshot: PermissionSnapshot | null = null;

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (state: { pushToast: ReturnType<typeof vi.fn> }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    snapshot: mockSnapshot,
    loading: false,
    error: null,
    pendingId: null,
    refetch: vi.fn(),
    request,
    openSettings,
  }),
}));

import { PermissionsAlertBanner } from "./PermissionsAlertBanner";

function darwinSnapshot(overrides: Partial<PermissionSnapshot> = {}): PermissionSnapshot {
  return {
    platform: "darwin",
    supported: true,
    headless: false,
    app_identity: { stable: true },
    permissions: [
      {
        id: "microphone",
        status: "denied",
        required: ["voice"],
        can_request: false,
        can_open_settings: true,
        restart_required: false,
      },
    ],
    features: { voice: { ready: false, missing: ["microphone"] } },
    restart_required: false,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockSnapshot = null;
});

describe("PermissionsAlertBanner", () => {
  it("shows a blocked permission with its System Settings deep link", () => {
    mockSnapshot = darwinSnapshot();
    render(<PermissionsAlertBanner />);

    expect(screen.getByTestId("permissions-alert-banner")).toBeDefined();
    expect(screen.getByText("permissions.banner.title")).toBeDefined();
    // The broken-feature summary line is rendered from snapshot.features.
    expect(screen.getByText("permissions.banner.impact")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "permissions.open_settings" }));
    expect(openSettings).toHaveBeenCalledWith("microphone");
  });

  it("offers the native prompt when the backend says a request can run", () => {
    mockSnapshot = darwinSnapshot({
      permissions: [
        {
          id: "microphone",
          status: "not_determined",
          required: ["voice"],
          can_request: true,
          can_open_settings: true,
          restart_required: false,
        },
      ],
    });
    render(<PermissionsAlertBanner />);

    fireEvent.click(screen.getByRole("button", { name: "permissions.request" }));
    expect(request).toHaveBeenCalledWith("microphone");
  });

  it("collapses to the headline but never disappears while something is missing", () => {
    mockSnapshot = darwinSnapshot();
    render(<PermissionsAlertBanner />);

    fireEvent.click(screen.getByRole("button", { name: /permissions.banner.collapse/ }));

    expect(screen.getByText("permissions.banner.title")).toBeDefined();
    expect(screen.queryByText("permissions.items.microphone.title")).toBeNull();
  });

  it("shows only the restart call-to-action once everything is granted", () => {
    mockSnapshot = darwinSnapshot({
      permissions: [
        {
          id: "screen_recording",
          status: "granted",
          required: ["computer_use"],
          can_request: false,
          can_open_settings: true,
          restart_required: true,
        },
      ],
      features: {
        computer_use: { ready: false, missing: [] },
      },
      restart_required: true,
    });
    render(<PermissionsAlertBanner />);

    const banner = screen.getByTestId("permissions-alert-banner");
    expect(banner.getAttribute("data-state")).toBe("restart");
    expect(screen.getByRole("button", { name: "permissions.restart_now" })).toBeDefined();
  });

  it("renders nothing on other platforms", () => {
    mockSnapshot = darwinSnapshot({ platform: "win32" });
    render(<PermissionsAlertBanner />);
    expect(screen.queryByTestId("permissions-alert-banner")).toBeNull();
  });

  it("renders nothing while the snapshot has not loaded", () => {
    mockSnapshot = null;
    render(<PermissionsAlertBanner />);
    expect(screen.queryByTestId("permissions-alert-banner")).toBeNull();
  });

  it("renders nothing when every required permission is settled", () => {
    mockSnapshot = darwinSnapshot({
      permissions: [
        {
          id: "microphone",
          status: "granted",
          required: ["voice"],
          can_request: false,
          can_open_settings: true,
          restart_required: false,
        },
      ],
      features: { voice: { ready: true, missing: [] } },
    });
    render(<PermissionsAlertBanner />);
    expect(screen.queryByTestId("permissions-alert-banner")).toBeNull();
  });

  it("renders nothing on a headless install (no desktop session to grant from)", () => {
    mockSnapshot = darwinSnapshot({ headless: true });
    render(<PermissionsAlertBanner />);
    expect(screen.queryByTestId("permissions-alert-banner")).toBeNull();
  });
});
