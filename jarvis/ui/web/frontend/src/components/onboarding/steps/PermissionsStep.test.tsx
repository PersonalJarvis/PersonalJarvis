import { expect, it } from "vitest";
import type { PermissionSnapshot } from "@/hooks/usePermissions";
import { permissionSnapshotReady } from "./PermissionsStep";

const IDS = [
  "microphone",
  "screen_recording",
  "accessibility",
  "input_monitoring",
  "event_posting",
] as const;

function macSnapshot(overrides: Partial<PermissionSnapshot> = {}): PermissionSnapshot {
  return {
    platform: "darwin",
    supported: true,
    headless: false,
    app_identity: { stable: true },
    permissions: IDS.map((id) => ({
      id,
      status: "granted",
      required: [],
      can_request: false,
      can_open_settings: true,
      restart_required: false,
    })),
    features: {},
    restart_required: false,
    ...overrides,
  };
}

it("accepts a complete stable macOS permission snapshot", () => {
  expect(permissionSnapshotReady(macSnapshot())).toBe(true);
});

it("fails closed for an unstable macOS app identity", () => {
  expect(
    permissionSnapshotReady(macSnapshot({ app_identity: { stable: false } })),
  ).toBe(false);
});

it("fails closed for an empty or incomplete macOS permission list", () => {
  expect(permissionSnapshotReady(macSnapshot({ permissions: [] }))).toBe(false);
  expect(
    permissionSnapshotReady(macSnapshot({ permissions: macSnapshot().permissions.slice(1) })),
  ).toBe(false);
});

it("accepts supported non-macOS platforms without TCC grants", () => {
  expect(permissionSnapshotReady(macSnapshot({ platform: "linux", permissions: [] }))).toBe(true);
  expect(permissionSnapshotReady(macSnapshot({ platform: "win32", permissions: [] }))).toBe(true);
  expect(permissionSnapshotReady(macSnapshot({ platform: "unknown", permissions: [] }))).toBe(false);
});
