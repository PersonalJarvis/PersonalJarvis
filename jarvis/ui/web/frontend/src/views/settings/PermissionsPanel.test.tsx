import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PermissionItem, PermissionSnapshot } from "@/hooks/usePermissions";

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
  }),
}));

import { PermissionRows } from "./PermissionsPanel";

function snapshotWith(row: Partial<PermissionItem>): PermissionSnapshot {
  return {
    platform: "darwin",
    supported: true,
    headless: false,
    app_identity: { stable: true },
    permissions: [
      {
        id: "screen_recording",
        status: "not_granted",
        required: ["computer_use"],
        can_request: true,
        can_open_settings: true,
        can_reset: false,
        restart_required: false,
        ...row,
      } as PermissionItem,
    ],
    features: { computer_use: { ready: false, missing: ["screen_recording"] } },
    restart_required: false,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockSnapshot = null;
});

describe("PermissionRows", () => {
  it("keeps System Settings available when a native request can also run", () => {
    mockSnapshot = snapshotWith({});
    render(<PermissionRows />);

    expect(screen.getByRole("button", { name: "permissions.request" })).toBeDefined();
    expect(
      screen.getByRole("button", { name: "permissions.open_settings" }),
    ).toBeDefined();
  });

  it("offers the reset on a stranded grant that never reads 'denied' (BUG-159)", () => {
    // The old rule was `status === "denied"`, which the Screen Recording and
    // Accessibility preflights never produce — the recovery button was
    // unreachable on exactly the rows a signature change strands.
    mockSnapshot = snapshotWith({ can_request: false, can_reset: true });
    render(<PermissionRows />);

    fireEvent.click(screen.getByRole("button", { name: "permissions.ask_again" }));
    expect(reset).toHaveBeenCalledWith("screen_recording");
    expect(screen.getByText("permissions.stale_grant_hint")).toBeDefined();
  });

  it("hides the reset while a grant is in place", () => {
    mockSnapshot = snapshotWith({ status: "granted", can_request: false, can_reset: false });
    render(<PermissionRows />);

    expect(screen.queryByRole("button", { name: "permissions.ask_again" })).toBeNull();
  });
});
