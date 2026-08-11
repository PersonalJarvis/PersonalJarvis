import { beforeEach, describe, expect, it } from "vitest";

import { useWallpaperStore } from "@/store/wallpaper";

/**
 * The pre-per-theme slot must be migrated, never read live.
 *
 * Read as a live fallback it leaked one mode's picture into the other: a dark
 * pick stored before the per-theme split kept showing behind light chrome for
 * as long as light mode had no pick of its own. `adopt()` runs the same
 * migration the store runs on load, which is what lets a test drive it after
 * the module has long been imported.
 */
describe("legacy single-slot migration", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useWallpaperStore.setState({ selections: { light: null, dark: null } });
  });

  it("files the old pick under the cached theme and retires the slot", () => {
    window.localStorage.setItem("jarvis.theme", "dark");
    window.localStorage.setItem("jarvis.wallpaper.v1", "05-noir-03");

    useWallpaperStore.getState().adopt();

    const { selections } = useWallpaperStore.getState();
    expect(selections.dark).toBe("05-noir-03");
    // The leak this replaces: light mode must NOT inherit the dark pick.
    expect(selections.light).toBeNull();
    expect(window.localStorage.getItem("jarvis.wallpaper.v1")).toBeNull();
    expect(window.localStorage.getItem("jarvis.wallpaper.dark.v1")).toBe(
      "05-noir-03",
    );
  });

  it("never overwrites a mode's own pick", () => {
    window.localStorage.setItem("jarvis.theme", "light");
    window.localStorage.setItem("jarvis.wallpaper.light.v1", "chosen-light");
    window.localStorage.setItem("jarvis.wallpaper.v1", "older-pick");

    useWallpaperStore.getState().adopt();

    expect(useWallpaperStore.getState().selections.light).toBe("chosen-light");
    expect(window.localStorage.getItem("jarvis.wallpaper.v1")).toBeNull();
  });
});
