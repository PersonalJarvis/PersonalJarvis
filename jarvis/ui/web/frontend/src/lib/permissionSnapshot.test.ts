import { describe, expect, it } from "vitest";
import { isReadyState, parsePermissionRow, parsePermissionSnapshot } from "./permissionSnapshot";

describe("parsePermissionSnapshot", () => {
  const valid = {
    platform: "darwin",
    supported: true,
    headless: false,
    app_identity: { app_name: "Personal Jarvis", bundle_id: "ai.example", bundle_path: "/Applications/x.app", launched_as_bundle: true, stable: true },
    outside_installed_app: false,
    permissions: [
      { id: "microphone", label: "Microphone", status: "granted", used_for: ["voice"], can_request: false, can_open_settings: true, can_reset: false, restart_hint: false, detail: "", settings_path: "p" },
    ],
    needed: [{ feature: "voice" }],
  };

  it("reads a v2 snapshot", () => {
    const snapshot = parsePermissionSnapshot(valid)!;

    expect(snapshot.app_identity.app_name).toBe("Personal Jarvis");
    expect(snapshot.permissions[0]).toMatchObject({ id: "microphone", status: "granted", used_for: ["voice"] });
    expect(snapshot.needed).toHaveLength(1);
  });

  it("is null for anything that is not a v2 snapshot, so callers keep what they had", () => {
    expect(parsePermissionSnapshot(null)).toBeNull();
    expect(parsePermissionSnapshot({})).toBeNull();
    expect(parsePermissionSnapshot({ ...valid, permissions: "no" })).toBeNull();
    const { needed: _needed, ...v1 } = valid;
    expect(parsePermissionSnapshot(v1)).toBeNull();
  });

  it("survives missing optional pieces", () => {
    const snapshot = parsePermissionSnapshot({ platform: "linux", permissions: [{ id: "microphone" }], needed: [] })!;

    expect(snapshot.app_identity.app_name).toBe("");
    expect(snapshot.permissions[0]).toMatchObject({ status: "unavailable", can_request: false, settings_path: null });
  });

  it("drops rows without an id", () => {
    expect(parsePermissionSnapshot({ ...valid, permissions: [{ status: "granted" }] })!.permissions).toEqual([]);
    expect(parsePermissionRow({})).toBeNull();
  });
});

describe("isReadyState", () => {
  it("is true for granted and not_required only", () => {
    expect(isReadyState("granted")).toBe(true);
    expect(isReadyState("not_required")).toBe(true);
    for (const state of ["not_determined", "denied", "restricted", "not_granted", "unavailable"]) {
      expect(isReadyState(state)).toBe(false);
    }
  });
});
