import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  BACKGROUND_MODE_KEY,
  WALLPAPER_CLASS,
  applyBackgroundClass,
  readBackgroundMode,
  writeBackgroundMode,
} from "@/lib/backgroundMode";

describe("backgroundMode", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.classList.remove(WALLPAPER_CLASS);
  });
  afterEach(() => {
    document.documentElement.classList.remove(WALLPAPER_CLASS);
  });

  it("defaults to the solid ground", () => {
    expect(readBackgroundMode()).toBe("solid");
  });

  it("round-trips the wallpaper choice and ignores garbage", () => {
    writeBackgroundMode("wallpaper");
    expect(readBackgroundMode()).toBe("wallpaper");
    window.localStorage.setItem(BACKGROUND_MODE_KEY, "nonsense");
    expect(readBackgroundMode()).toBe("solid");
  });

  it("stamps the root class only for the wallpaper", () => {
    applyBackgroundClass("wallpaper");
    expect(document.documentElement.classList.contains(WALLPAPER_CLASS)).toBe(true);
    applyBackgroundClass("solid");
    expect(document.documentElement.classList.contains(WALLPAPER_CLASS)).toBe(false);
  });
});
