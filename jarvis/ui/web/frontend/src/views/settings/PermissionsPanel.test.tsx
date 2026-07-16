import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const request = vi.fn();
const openSettings = vi.fn();

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (state: { pushToast: ReturnType<typeof vi.fn> }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    snapshot: {
      platform: "darwin",
      app_identity: { stable: true },
      permissions: [
        {
          id: "screen_recording",
          status: "not_granted",
          required: ["computer_use"],
          can_request: true,
          can_open_settings: true,
          restart_required: false,
        },
      ],
      restart_required: false,
    },
    loading: false,
    error: null,
    pendingId: null,
    refetch: vi.fn(),
    request,
    openSettings,
  }),
}));

import { PermissionRows } from "./PermissionsPanel";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PermissionRows", () => {
  it("keeps System Settings available when a native request can also run", () => {
    render(<PermissionRows />);

    expect(screen.getByRole("button", { name: "permissions.request" })).toBeDefined();
    expect(
      screen.getByRole("button", { name: "permissions.open_settings" }),
    ).toBeDefined();
  });
});
