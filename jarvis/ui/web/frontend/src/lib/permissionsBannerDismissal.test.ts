import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PERMISSIONS_BANNER_DISMISSED_KEY,
  PERMISSIONS_BANNER_DISMISS_TTL_MS,
  dismissPermissionIds,
  readDismissedPermissionIds,
} from "@/lib/permissionsBannerDismissal";

const T0 = 1_700_000_000_000;

describe("permissionsBannerDismissal", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("starts with nothing dismissed", () => {
    expect([...readDismissedPermissionIds(T0)]).toEqual([]);
  });

  it("remembers the rows that were put off", () => {
    dismissPermissionIds(["screen_recording", "automation"], T0);

    expect([...readDismissedPermissionIds(T0 + 1000)].sort()).toEqual([
      "automation",
      "screen_recording",
    ]);
  });

  it("keeps earlier rows when more are put off later", () => {
    dismissPermissionIds(["automation"], T0);
    const after = dismissPermissionIds(["accessibility"], T0 + 1000);

    expect([...after].sort()).toEqual(["accessibility", "automation"]);
    expect([...readDismissedPermissionIds(T0 + 2000)].sort()).toEqual([
      "accessibility",
      "automation",
    ]);
  });

  it("forgets everything after a week so a declined microphone is raised again", () => {
    dismissPermissionIds(["microphone"], T0);

    const justBefore = readDismissedPermissionIds(T0 + PERMISSIONS_BANNER_DISMISS_TTL_MS - 1);
    const atExpiry = readDismissedPermissionIds(T0 + PERMISSIONS_BANNER_DISMISS_TTL_MS);

    expect([...justBefore]).toEqual(["microphone"]);
    expect([...atExpiry]).toEqual([]);
  });

  it("restarts the week when more rows are put off", () => {
    dismissPermissionIds(["microphone"], T0);
    dismissPermissionIds(["automation"], T0 + PERMISSIONS_BANNER_DISMISS_TTL_MS - 1);

    const later = readDismissedPermissionIds(T0 + PERMISSIONS_BANNER_DISMISS_TTL_MS + 1000);

    expect([...later].sort()).toEqual(["automation", "microphone"]);
  });

  it("ignores a record from the future instead of hiding the banner forever", () => {
    localStorage.setItem(
      PERMISSIONS_BANNER_DISMISSED_KEY,
      JSON.stringify({ at: T0 + 10 * PERMISSIONS_BANNER_DISMISS_TTL_MS, ids: ["microphone"] }),
    );

    expect([...readDismissedPermissionIds(T0)]).toEqual([]);
  });

  it("treats a damaged record as nothing dismissed", () => {
    for (const raw of ["not json", "null", "{}", JSON.stringify({ at: "x", ids: [] })]) {
      localStorage.setItem(PERMISSIONS_BANNER_DISMISSED_KEY, raw);
      expect([...readDismissedPermissionIds(T0)]).toEqual([]);
    }
  });

  it("drops non-string ids from a hand-edited record", () => {
    localStorage.setItem(
      PERMISSIONS_BANNER_DISMISSED_KEY,
      JSON.stringify({ at: T0, ids: ["automation", 7, null] }),
    );

    expect([...readDismissedPermissionIds(T0)]).toEqual(["automation"]);
  });

  it("still honours the click for this session when storage refuses the write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    const dismissed = dismissPermissionIds(["automation"], T0);

    expect([...dismissed]).toEqual(["automation"]);
    expect([...readDismissedPermissionIds(T0)]).toEqual([]);
  });

  it("reads as nothing dismissed when storage itself throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });

    expect([...readDismissedPermissionIds(T0)]).toEqual([]);
  });
});
