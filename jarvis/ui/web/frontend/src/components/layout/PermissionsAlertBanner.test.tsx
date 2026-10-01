import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PermissionItem, PermissionSnapshot } from "@/hooks/usePermissions";
import {
  PERMISSIONS_BANNER_DISMISSED_KEY,
  PERMISSIONS_BANNER_DISMISS_TTL_MS,
} from "@/lib/permissionsBannerDismissal";

const request = vi.fn();
const openSettings = vi.fn();
const reset = vi.fn();

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
    reset,
    setupAll: vi.fn().mockResolvedValue("complete"),
    cancelSetup: vi.fn(),
    setupProgress: null,
    setupNeeded: false,
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
        can_reset: false,
        restart_required: false,
      },
    ],
    features: { voice: { ready: false, missing: ["microphone"] } },
    restart_required: false,
    ...overrides,
  };
}

beforeEach(() => localStorage.clear());

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockSnapshot = null;
  localStorage.clear();
});

function row(overrides: Partial<PermissionItem> & Pick<PermissionItem, "id">): PermissionItem {
  return {
    status: "not_determined",
    required: ["computer_use"],
    can_request: true,
    can_open_settings: true,
    can_reset: false,
    restart_required: false,
    ...overrides,
  };
}

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
          can_reset: false,
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
          can_reset: false,
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

  it("labels a restart-pending screen recording honestly instead of the frozen state", () => {
    // CGPreflightScreenCaptureAccess is frozen per process: after granting in
    // System Settings the probe still reports not_granted until relaunch. The
    // stale label plus a dead Allow button was the live 2026-07-18 Mac finding.
    mockSnapshot = darwinSnapshot({
      permissions: [
        {
          id: "screen_recording",
          status: "not_granted",
          required: ["computer_use"],
          can_request: false,
          can_open_settings: true,
          can_reset: false,
          restart_required: true,
        },
      ],
      features: { computer_use: { ready: false, missing: ["screen_recording"] } },
      restart_required: true,
    });
    render(<PermissionsAlertBanner />);

    expect(screen.getByText("permissions.status.restart_pending")).toBeDefined();
    expect(screen.queryByText("permissions.status.not_granted")).toBeNull();
    expect(screen.queryByRole("button", { name: "permissions.request" })).toBeNull();
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
          can_reset: false,
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

  it("offers the reset for a stranded grant that never reads 'denied' (BUG-159)", () => {
    // Screen Recording's preflight reports an orphaned TCC row as plain
    // "not_granted" while System Settings still shows the checkmark. The
    // banner used to carry no reset at all, so this row was a dead end.
    mockSnapshot = darwinSnapshot({
      permissions: [
        {
          id: "screen_recording",
          status: "not_granted",
          required: ["computer_use"],
          can_request: false,
          can_open_settings: true,
          can_reset: true,
          restart_required: false,
        },
      ],
      features: { computer_use: { ready: false, missing: ["screen_recording"] } },
    });
    render(<PermissionsAlertBanner />);

    expect(screen.getByText("permissions.stale_grant_hint")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "permissions.ask_again" }));
    expect(reset).toHaveBeenCalledWith("screen_recording");
  });

  it("explains a signature change instead of looking amnesic", () => {
    mockSnapshot = darwinSnapshot({
      identity_reset: { reason: "signature-change", services: ["ScreenCapture"] },
    });
    render(<PermissionsAlertBanner />);

    expect(screen.getByText("permissions.identity_reset")).toBeDefined();
  });

  it("says nothing about a permission no feature the user turned on needs", () => {
    // Automation is only for "mute music while dictating", which is off.
    mockSnapshot = darwinSnapshot({
      permissions: [
        row({ id: "automation", required: ["audio_ducking"], wanted: false }),
      ],
      features: { audio_ducking: { ready: false, missing: ["automation"], active: false } },
    });
    render(<PermissionsAlertBanner />);

    expect(screen.queryByTestId("permissions-alert-banner")).toBeNull();
  });

  it("still asks about the wanted rows when an optional one is missing beside them", () => {
    mockSnapshot = darwinSnapshot({
      permissions: [
        row({ id: "microphone", required: ["voice"], wanted: true }),
        row({ id: "automation", required: ["audio_ducking"], wanted: false }),
      ],
      features: {
        voice: { ready: false, missing: ["microphone"], active: true },
        audio_ducking: { ready: false, missing: ["automation"], active: false },
      },
    });
    render(<PermissionsAlertBanner />);

    expect(screen.getByText("permissions.items.microphone.title")).toBeDefined();
    expect(screen.queryByText("permissions.items.automation.title")).toBeNull();
    // Only the feature that is on is named as "not working right now".
    expect(screen.getByText("permissions.banner.impact")).toBeDefined();
  });

  it("puts a row off with Not now, and keeps it off across a reload", () => {
    mockSnapshot = darwinSnapshot({
      permissions: [row({ id: "screen_recording", required: ["computer_use"] })],
      features: { computer_use: { ready: false, missing: ["screen_recording"], active: true } },
    });
    const { unmount } = render(<PermissionsAlertBanner />);

    fireEvent.click(screen.getByTestId("permissions-banner-dismiss"));

    expect(screen.queryByTestId("permissions-alert-banner")).toBeNull();
    unmount();
    // A fresh mount (a restart) reads the stored decision.
    render(<PermissionsAlertBanner />);
    expect(screen.queryByTestId("permissions-alert-banner")).toBeNull();
  });

  it("brings the banner back for a row that turns up after Not now", () => {
    mockSnapshot = darwinSnapshot({
      permissions: [row({ id: "screen_recording", required: ["computer_use"] })],
      features: { computer_use: { ready: false, missing: ["screen_recording"], active: true } },
    });
    const { unmount } = render(<PermissionsAlertBanner />);
    fireEvent.click(screen.getByTestId("permissions-banner-dismiss"));
    unmount();

    // The user switches "mute music" on: its Automation row is now wanted.
    mockSnapshot = darwinSnapshot({
      permissions: [
        row({ id: "screen_recording", required: ["computer_use"] }),
        row({ id: "automation", required: ["audio_ducking"], wanted: true }),
      ],
      features: {
        computer_use: { ready: false, missing: ["screen_recording"], active: true },
        audio_ducking: { ready: false, missing: ["automation"], active: true },
      },
    });
    render(<PermissionsAlertBanner />);

    expect(screen.getByText("permissions.items.automation.title")).toBeDefined();
    expect(screen.queryByText("permissions.items.screen_recording.title")).toBeNull();
  });

  it("raises a dismissed row again after a week", () => {
    mockSnapshot = darwinSnapshot();
    localStorage.setItem(
      PERMISSIONS_BANNER_DISMISSED_KEY,
      JSON.stringify({
        at: Date.now() - PERMISSIONS_BANNER_DISMISS_TTL_MS - 1000,
        ids: ["microphone"],
      }),
    );

    render(<PermissionsAlertBanner />);

    expect(screen.getByTestId("permissions-alert-banner")).toBeDefined();
  });

  it("never hides the restart call-to-action behind Not now", () => {
    mockSnapshot = darwinSnapshot({
      permissions: [row({ id: "microphone", required: ["voice"], status: "granted" })],
      features: { voice: { ready: true, missing: [], active: true } },
      restart_required: true,
    });
    localStorage.setItem(
      PERMISSIONS_BANNER_DISMISSED_KEY,
      JSON.stringify({ at: Date.now(), ids: ["microphone"] }),
    );

    render(<PermissionsAlertBanner />);

    expect(screen.getByTestId("permissions-alert-banner").getAttribute("data-state")).toBe(
      "restart",
    );
    expect(screen.queryByTestId("permissions-banner-dismiss")).toBeNull();
  });
});

