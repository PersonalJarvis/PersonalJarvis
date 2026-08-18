import { beforeEach, describe, expect, it } from "vitest";

import { DEFAULT_WALLPAPER_URLS } from "@/lib/bundledWallpapers";
import {
  useWallpaperStore,
  wallpaperFullUrl,
  wallpaperThumbUrl,
} from "@/store/wallpaper";

/**
 * Three id families, three sources — and the id alone tells them apart, so
 * whoever holds only an id (the shell, a restored pick) lands on the right
 * picture without a flag beside it.
 */
describe("wallpaper URLs by id", () => {
  it("serves a library id from the catalog endpoint", () => {
    expect(wallpaperFullUrl("03-anime-neon-01")).toBe("/api/wallpapers/03-anime-neon-01/full");
    expect(wallpaperThumbUrl("03-anime-neon-01")).toBe("/api/wallpapers/03-anime-neon-01/thumb");
  });

  it("serves an upload id from the uploads endpoint", () => {
    expect(wallpaperFullUrl("u0123456789abcdef")).toBe(
      "/api/wallpapers/uploads/u0123456789abcdef/full",
    );
  });

  it("answers a bundled id from the bundle, never from the API", () => {
    expect(wallpaperFullUrl("original")).toBe(DEFAULT_WALLPAPER_URLS.dark);
    expect(wallpaperFullUrl("original-light")).toBe(DEFAULT_WALLPAPER_URLS.light);
    expect(wallpaperThumbUrl("original")).toBe(DEFAULT_WALLPAPER_URLS.dark);
  });

  it("draws the plain grounds as data URIs — pure black, pure white", () => {
    const black = wallpaperFullUrl("plain-black");
    const white = wallpaperFullUrl("plain-white");
    expect(black.startsWith("data:image/svg+xml,")).toBe(true);
    expect(white.startsWith("data:image/svg+xml,")).toBe(true);
    expect(decodeURIComponent(black)).toContain('fill="#000000"');
    expect(decodeURIComponent(white)).toContain('fill="#ffffff"');
    // The shell puts the URL into an unquoted CSS url(...): no parentheses or
    // quotes may survive encoding, or the declaration would end early.
    for (const url of [black, white]) {
      expect(url).not.toMatch(/[()'"\s]/);
    }
    expect(wallpaperThumbUrl("plain-black")).toBe(black);
  });
});

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

describe("live mascot on the wallpaper", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useWallpaperStore.setState({ mascotOn: true });
  });

  it("defaults to on when nothing is stored", () => {
    useWallpaperStore.getState().adopt();
    expect(useWallpaperStore.getState().mascotOn).toBe(true);
  });

  it("persists off and comes back in another adopt", () => {
    useWallpaperStore.getState().setMascotOn(false);
    expect(window.localStorage.getItem("jarvis.wallpaper.mascot.v1")).toBe("0");
    useWallpaperStore.setState({ mascotOn: true });
    useWallpaperStore.getState().adopt();
    expect(useWallpaperStore.getState().mascotOn).toBe(false);
  });
});
