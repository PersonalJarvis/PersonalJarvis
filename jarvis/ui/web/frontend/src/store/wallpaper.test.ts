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

/**
 * The catalog-backed correction pass.
 *
 * The boot migration can only guess a mode from the theme cache; `reconcile`
 * is handed the real answer per picture and moves anything the guess misfiled.
 */
describe("reconcile", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useWallpaperStore.setState({ selections: { light: null, dark: null } });
  });

  const themes: Record<string, "light" | "dark"> = {
    "05-noir-03": "dark",
    "07-terrace-01": "light",
  };
  const themeOf = (id: string) => themes[id] ?? null;

  it("moves a dark picture out of the light slot and into its own", () => {
    window.localStorage.setItem("jarvis.wallpaper.light.v1", "05-noir-03");

    useWallpaperStore.getState().reconcile(themeOf);

    const { selections } = useWallpaperStore.getState();
    expect(selections.light).toBeNull();
    expect(selections.dark).toBe("05-noir-03");
  });

  it("drops a misfiled pick when its own mode has already chosen", () => {
    window.localStorage.setItem("jarvis.wallpaper.light.v1", "05-noir-03");
    window.localStorage.setItem("jarvis.wallpaper.dark.v1", "already-dark");

    useWallpaperStore.getState().reconcile(themeOf);

    const { selections } = useWallpaperStore.getState();
    // A mode showing its default is right; one wearing the other mode's
    // picture is the bug — so the misfiled pick goes, not the chosen one.
    expect(selections.light).toBeNull();
    expect(selections.dark).toBe("already-dark");
  });

  it("leaves ids the catalog cannot answer for exactly where they are", () => {
    window.localStorage.setItem("jarvis.wallpaper.light.v1", "u0123456789abcdef");

    useWallpaperStore.getState().reconcile(themeOf);

    // An upload still loading must not be thrown away for being unknown.
    expect(useWallpaperStore.getState().selections.light).toBe(
      "u0123456789abcdef",
    );
  });

  it("keeps a correctly filed pick untouched", () => {
    window.localStorage.setItem("jarvis.wallpaper.light.v1", "07-terrace-01");
    window.localStorage.setItem("jarvis.wallpaper.dark.v1", "05-noir-03");

    useWallpaperStore.getState().reconcile(themeOf);

    const { selections } = useWallpaperStore.getState();
    expect(selections.light).toBe("07-terrace-01");
    expect(selections.dark).toBe("05-noir-03");
  });
});
