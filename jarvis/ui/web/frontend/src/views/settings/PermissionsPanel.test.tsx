import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PermissionItem, PermissionSnapshot } from "@/hooks/usePermissions";

const request = vi.fn();
const openSettings = vi.fn();
const reset = vi.fn();
const setupAll = vi.fn().mockResolvedValue("complete");
const cancelSetup = vi.fn();

let mockSnapshot: PermissionSnapshot | null = null;
let mockSetupNeeded = false;

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
    setupAll,
    cancelSetup,
    setupProgress: null,
    setupNeeded: mockSetupNeeded,
  }),
}));

import { PermissionRows, SetupAllControl } from "./PermissionsPanel";

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

describe("Set up everything", () => {
  afterEach(() => {
    mockSetupNeeded = false;
  });

  it("offers the one-click flow only while a row still needs setup", () => {
    mockSnapshot = snapshotWith({});
    mockSetupNeeded = true;
    render(<PermissionRows />);

    fireEvent.click(screen.getByTestId("permissions-setup-all"));

    // Settings/banner mode applies the grants itself; onboarding (deferred
    // restart note) leaves the restart to its own final step.
    expect(setupAll).toHaveBeenCalledWith({ autoRestart: true });
  });

  it("leaves the final restart to onboarding", () => {
    mockSnapshot = snapshotWith({});
    mockSetupNeeded = true;
    render(<PermissionRows compact deferRestartNote />);

    fireEvent.click(screen.getByTestId("permissions-setup-all"));

    expect(setupAll).toHaveBeenCalledWith({ autoRestart: false });
  });

  it("hides the flow once everything is granted or the app runs outside its bundle", () => {
    mockSnapshot = snapshotWith({ status: "granted" });
    mockSetupNeeded = false;
    const { unmount } = render(<PermissionRows />);
    expect(screen.queryByTestId("permissions-setup-all")).toBeNull();
    unmount();

    mockSnapshot = { ...snapshotWith({}), app_identity: { stable: false } };
    mockSetupNeeded = true;
    render(<PermissionRows />);
    expect(screen.queryByTestId("permissions-setup-all")).toBeNull();
  });

  it("names the step and the waiting phase while running, with a way out", () => {
    const onCancel = vi.fn();
    render(
      <SetupAllControl
        progress={{ id: "accessibility", index: 2, total: 5, phase: "settings" }}
        onStart={vi.fn()}
        onCancel={onCancel}
      />,
    );

    // The i18n stub echoes keys, so the step line is the bare template key;
    // the phase line tells the user what macOS is waiting for.
    expect(screen.getByTestId("permissions-setup-progress").textContent).toContain(
      "permissions.setup_running",
    );
    expect(screen.getByText("permissions.setup_wait_settings")).toBeDefined();
    expect(screen.queryByTestId("permissions-setup-all")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "permissions.setup_cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
