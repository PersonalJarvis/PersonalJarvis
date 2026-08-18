import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_WALLPAPER_URLS,
  defaultWallpaperUrl,
  useDesktopWallpaper,
} from "@/hooks/useDesktopWallpaper";
import { useWallpaperStore } from "@/store/wallpaper";

/**
 * jsdom never loads images, so the test drives the outcome by hand: an
 * instance is captured on construction and its `onload`/`onerror` fired when
 * the test wants the swap to succeed or fail.
 */
let pending: HTMLImageElement[] = [];

class TestImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  complete = false;
  naturalWidth = 0;
  #src = "";

  constructor() {
    pending.push(this as unknown as HTMLImageElement);
  }

  get src() {
    return this.#src;
  }

  set src(value: string) {
    this.#src = value;
  }
}

function Probe() {
  return <span data-testid="url">{useDesktopWallpaper()}</span>;
}

beforeEach(() => {
  pending = [];
  window.localStorage.clear();
  // No ThemeProvider in these tests and no `dark` class on <html>, so the
  // hook resolves the light slot; the picks below are filed there.
  useWallpaperStore.setState({ selections: { light: null, dark: null } });
  vi.stubGlobal("Image", TestImage);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.documentElement.classList.remove("dark");
});

describe("useDesktopWallpaper", () => {
  it("starts on the bundled wallpaper of the mode on screen", () => {
    render(<Probe />);

    expect(screen.getByTestId("url").textContent).toBe(defaultWallpaperUrl("light"));
    expect(pending).toHaveLength(0);
  });

  it("paints the night original in dark mode and the daylight one in light", async () => {
    document.documentElement.classList.add("dark");
    render(<Probe />);
    expect(screen.getByTestId("url").textContent).toBe(DEFAULT_WALLPAPER_URLS.dark);

    // Light chrome never lands on the night scene: with no light pick stored,
    // the light mode shows the picture bundled FOR light.
    act(() => document.documentElement.classList.remove("dark"));
    await waitFor(() =>
      expect(screen.getByTestId("url").textContent).toBe(DEFAULT_WALLPAPER_URLS.light),
    );
    expect(DEFAULT_WALLPAPER_URLS.light).not.toBe(DEFAULT_WALLPAPER_URLS.dark);
    expect(pending).toHaveLength(0);
  });

  it("keeps the old picture on screen until the new one has loaded", async () => {
    render(<Probe />);
    act(() => useWallpaperStore.getState().select("03-anime-neon-01", "light"));

    // Requested, but not yet painted — the shell must not go blank meanwhile.
    expect(pending[0]?.src).toBe("/api/wallpapers/03-anime-neon-01/full");
    expect(screen.getByTestId("url").textContent).toBe(defaultWallpaperUrl("light"));

    act(() => pending[0]?.onload?.(new Event("load") as never));

    await waitFor(() =>
      expect(screen.getByTestId("url").textContent).toBe(
        "/api/wallpapers/03-anime-neon-01/full",
      ),
    );
  });

  it("paints a plain ground straight from the bundle, no request to the API", async () => {
    act(() => useWallpaperStore.getState().select("plain-white", "light"));

    render(<Probe />);
    // A drawn colour: the URL is the image, so nothing is fetched from /api/.
    expect(pending[0]?.src.startsWith("data:image/svg+xml,")).toBe(true);
    act(() => pending[0]?.onload?.(new Event("load") as never));

    await waitFor(() =>
      expect(screen.getByTestId("url").textContent?.startsWith("data:image/svg+xml,")).toBe(true),
    );
    expect(screen.getByTestId("url").textContent).toContain(encodeURIComponent("#ffffff"));
  });

  it("falls back to the mode's bundled wallpaper when the library is missing", async () => {
    useWallpaperStore.setState({
      selections: { light: "03-anime-neon-01", dark: null },
    });
    render(<Probe />);

    act(() => pending[0]?.onerror?.(new Event("error") as never));

    await waitFor(() =>
      expect(screen.getByTestId("url").textContent).toBe(defaultWallpaperUrl("light")),
    );
  });
});
