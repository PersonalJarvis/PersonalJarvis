import { describe, expect, it } from "vitest";

import { privacyRowView } from "./privacyRow";
import type { PermissionRow } from "./permissionSnapshot";

function row(overrides: Partial<PermissionRow> = {}): PermissionRow {
  return {
    id: "screen_recording",
    label: "Screen Recording",
    status: "not_granted",
    used_for: [],
    can_request: true,
    can_open_settings: true,
    can_reset: true,
    restart_hint: false,
    detail: "",
    settings_path: "System Settings > Privacy & Security > Screen & System Audio Recording",
    ...overrides,
  };
}

const fresh = { asked: false, backFromSettings: false };

describe("the pill vocabulary", () => {
  it("not asked yet: not_determined, and a binary permission macOS can still be asked about", () => {
    expect(privacyRowView(row({ status: "not_determined" }), fresh).pill).toBe("not_determined");
    expect(privacyRowView(row({ status: "not_granted" }), fresh).pill).toBe("not_determined");
  });

  it("off: denied, and not granted after an ask (or when macOS cannot be asked)", () => {
    expect(privacyRowView(row({ status: "denied", can_request: false }), fresh).pill).toBe("denied");
    expect(privacyRowView(row({ status: "not_granted" }), { ...fresh, asked: true }).pill).toBe("not_granted");
    expect(privacyRowView(row({ status: "not_granted", can_request: false }), fresh).pill).toBe("not_granted");
  });

  it("the rest keep their own word", () => {
    expect(privacyRowView(row({ status: "granted" }), fresh).pill).toBe("granted");
    expect(privacyRowView(row({ status: "restricted" }), fresh).pill).toBe("restricted");
    expect(privacyRowView(row({ status: "unavailable" }), fresh).pill).toBe("unavailable");
    expect(privacyRowView(row({ status: "not_required" }), fresh).pill).toBe("not_required");
  });

  it("a real failed use wins over the state, even on a row that reads granted", () => {
    expect(privacyRowView(row({ status: "granted", restart_hint: true }), fresh).pill).toBe("restart_pending");
    expect(privacyRowView(row({ status: "not_granted", restart_hint: true }), fresh).pill).toBe("restart_pending");
  });

  it("the Keychain never reads 'not asked yet': declined is off", () => {
    const keychain = row({ id: "credential_store", status: "not_granted", can_request: true });
    expect(privacyRowView(keychain, fresh).pill).toBe("not_granted");
  });
});

describe("at most one action per row", () => {
  it("not asked yet: Ask now, only when macOS can be asked", () => {
    expect(privacyRowView(row({ status: "not_determined" }), fresh).action).toBe("ask");
    expect(privacyRowView(row({ status: "not_determined", can_request: false }), fresh).action).toBeNull();
  });

  it("off: Open System Settings, with the path; a request only when there is no pane", () => {
    const off = privacyRowView(row({ status: "denied", can_request: false }), fresh);
    expect(off.action).toBe("open_settings");
    expect(off.showPath).toBe(true);
    expect(privacyRowView(row({ status: "denied", can_request: true, can_open_settings: false }), fresh).action).toBe("ask");
  });

  it("allowed, restricted, unavailable, not required: no action and no path", () => {
    for (const status of ["granted", "restricted", "unavailable", "not_required"] as const) {
      const view = privacyRowView(row({ status }), fresh);
      expect(view.action, status).toBeNull();
      expect(view.showPath, status).toBe(false);
    }
  });

  it("restart needed: Quit and reopen is the one action", () => {
    const view = privacyRowView(row({ status: "not_granted", restart_hint: true }), fresh);
    expect(view.action).toBe("restart");
    expect(view.showPath).toBe(false);
  });

  it("the Keychain keeps 'Try again' and no pane path", () => {
    const view = privacyRowView(row({ id: "credential_store", status: "not_granted", settings_path: null }), fresh);
    expect(view.action).toBe("try_again");
    expect(view.showPath).toBe(false);
    expect(privacyRowView(row({ id: "credential_store", status: "granted", can_request: false }), fresh).action).toBeNull();
  });

  it("the pane path belongs to the off state only, never to a row not asked yet", () => {
    expect(privacyRowView(row({ status: "not_determined" }), fresh).showPath).toBe(false);
    expect(privacyRowView(row({ status: "not_granted" }), fresh).showPath).toBe(false);
    expect(privacyRowView(row({ status: "not_granted" }), { ...fresh, asked: true }).showPath).toBe(true);
  });
});

describe("Ask again and the stale-grant hint", () => {
  const stranded = row({ status: "denied", can_request: false, can_reset: true });

  it("never on a fresh Mac, whatever the backend allows", () => {
    for (const status of ["not_determined", "not_granted", "denied"] as const) {
      const view = privacyRowView(row({ status, can_reset: true, can_request: status !== "denied" }), fresh);
      expect(view.showReset, status).toBe(false);
      expect(view.showStaleHint, status).toBe(false);
    }
  });

  it("not before the person opened Settings and came back", () => {
    expect(privacyRowView(stranded, fresh).showReset).toBe(false);
    expect(privacyRowView(stranded, { asked: true, backFromSettings: false }).showReset).toBe(false);
  });

  it("after coming back with the row still off: Ask again (can_reset) and the hint (no prompt left)", () => {
    const back = { asked: true, backFromSettings: true };
    const view = privacyRowView(stranded, back);
    expect(view.showReset).toBe(true);
    expect(view.showStaleHint).toBe(true);
    // A prompt is still possible: the checkmark cannot belong to an older build yet.
    expect(privacyRowView({ ...stranded, status: "not_granted", can_request: true }, back).showStaleHint).toBe(false);
    // The backend says a reset would not help: no button.
    expect(privacyRowView({ ...stranded, can_reset: false }, back).showReset).toBe(false);
  });

  it("never on a row that now reads allowed, or on the Keychain", () => {
    const back = { asked: true, backFromSettings: true };
    expect(privacyRowView({ ...stranded, status: "granted" }, back).showReset).toBe(false);
    expect(privacyRowView(row({ id: "credential_store", status: "not_granted", can_reset: true }), back).showReset).toBe(false);
  });
});
