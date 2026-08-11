import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/hooks/useTheme";
import { WallpaperView } from "@/views/WallpaperView";
import { useWallpaperStore } from "@/store/wallpaper";

const CATALOG = {
  available: true,
  count: 3,
  styles: [
    { slug: "01-cinematic-photoreal", label: "Cinematic Photorealistic", count: 2 },
    { slug: "03-anime-neon", label: "Cinematic Anime Neon", count: 1 },
  ],
  items: [
    {
      id: "01-cinematic-photoreal-01",
      title: "Flooded Observatory",
      style: "01-cinematic-photoreal",
      styleLabel: "Cinematic Photorealistic",
      theme: "dark" as const,
    },
    {
      id: "01-cinematic-photoreal-02",
      title: "Morning Atrium",
      style: "01-cinematic-photoreal",
      styleLabel: "Cinematic Photorealistic",
      theme: "light" as const,
    },
    {
      id: "03-anime-neon-01",
      title: "Neon Crossing",
      style: "03-anime-neon",
      styleLabel: "Cinematic Anime Neon",
      theme: "dark" as const,
    },
  ],
};

function renderView(catalog: unknown = CATALOG) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => catalog }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // A real ThemeProvider, not a stub: applying a wallpaper is supposed to move
  // the theme, and the assertion for that is the class on <html>.
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <WallpaperView />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

/** True while the app is painted dark. */
function isDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

/** Every `<img>` the grid and preview have actually asked the browser for. */
function requestedSources(): string[] {
  return Array.from(document.querySelectorAll("img")).map((img) =>
    img.getAttribute("src") ?? "",
  );
}

beforeEach(() => {
  window.localStorage.clear();
  useWallpaperStore.setState({
    selections: { light: null, dark: null },
    favorites: [],
  });
  document.documentElement.classList.add("dark");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("WallpaperView", () => {
  it("browses on thumbnails alone — no full-size image is requested", async () => {
    renderView();

    await screen.findByAltText("Flooded Observatory");
    // The bundled original rides along as a fourth tile, served from the app
    // bundle rather than from /api/wallpapers.
    const sources = requestedSources().filter((src) => src.includes("/api/"));

    expect(sources).toHaveLength(3);
    expect(sources.every((src) => src.endsWith("/thumb"))).toBe(true);
    expect(sources.some((src) => src.endsWith("/full"))).toBe(false);
  });

  it("pins the original to the very first tile", async () => {
    renderView();

    await screen.findByAltText("Flooded Observatory");
    const tiles = [...document.querySelectorAll('[data-testid="wallpaper-grid"] img')];

    expect(tiles).toHaveLength(4);
    expect(tiles[0].getAttribute("alt")).toBe("The Original");
    expect(tiles[0].getAttribute("src")).not.toContain("/api/");
  });

  it("keeps the original first when sorting by style", async () => {
    renderView();

    fireEvent.click(await screen.findByRole("button", { name: "By style" }));

    await waitFor(() => {
      const first = document.querySelector('[data-testid="wallpaper-grid"] img');
      expect(first?.getAttribute("alt")).toBe("The Original");
    });
  });

  it("marks the original as in use while no choice is stored", async () => {
    renderView();

    fireEvent.click(await screen.findByAltText("The Original"));

    expect(await screen.findByRole("button", { name: /In use/ })).toBeTruthy();
  });

  it("adopting the original clears the stored choice rather than storing an id", async () => {
    renderView();
    act(() => useWallpaperStore.getState().select("03-anime-neon-01", "dark"));

    fireEvent.click(await screen.findByAltText("The Original"));
    fireEvent.click(await screen.findByRole("button", { name: "Use this wallpaper" }));

    await waitFor(() => {
      expect(useWallpaperStore.getState().selections.dark).toBeNull();
    });
    expect(window.localStorage.getItem("jarvis.wallpaper.v1")).toBeNull();
    expect(isDark()).toBe(true);
  });

  it("downloads the full-size image only once a wallpaper is previewed", async () => {
    renderView();

    fireEvent.click(await screen.findByAltText("Neon Crossing"));

    await screen.findByTestId("wallpaper-preview");
    expect(requestedSources()).toContain("/api/wallpapers/03-anime-neon-01/full");
  });

  it("applies the previewed wallpaper and remembers it", async () => {
    renderView();

    fireEvent.click(await screen.findByAltText("Morning Atrium"));
    fireEvent.click(await screen.findByRole("button", { name: "Use this wallpaper" }));

    await waitFor(() => {
      expect(useWallpaperStore.getState().selections.light).toBe(
        "01-cinematic-photoreal-02",
      );
    });
    expect(window.localStorage.getItem("jarvis.wallpaper.light.v1")).toBe(
      "01-cinematic-photoreal-02",
    );
  });

  it("switches the interface to light with a light wallpaper", async () => {
    renderView();

    fireEvent.click(await screen.findByAltText("Morning Atrium"));
    fireEvent.click(await screen.findByRole("button", { name: "Use this wallpaper" }));

    await waitFor(() => expect(isDark()).toBe(false));
  });

  it("switches back to dark when a dark wallpaper is picked next", async () => {
    renderView();

    // Go light first, so the second pick is a real transition rather than a
    // no-op against the app's dark default.
    fireEvent.click(await screen.findByAltText("Morning Atrium"));
    fireEvent.click(await screen.findByRole("button", { name: "Use this wallpaper" }));
    await waitFor(() => expect(isDark()).toBe(false));

    fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
    fireEvent.click(await screen.findByAltText("Neon Crossing"));
    fireEvent.click(await screen.findByRole("button", { name: "Use this wallpaper" }));

    await waitFor(() => expect(isDark()).toBe(true));
  });

  it("returns to dark along with the bundled default", async () => {
    renderView();

    fireEvent.click(await screen.findByAltText("Morning Atrium"));
    fireEvent.click(await screen.findByRole("button", { name: "Use this wallpaper" }));
    await waitFor(() => expect(isDark()).toBe(false));

    fireEvent.click(await screen.findByRole("button", { name: /Default/ }));

    await waitFor(() => expect(isDark()).toBe(true));
    expect(useWallpaperStore.getState().selections.dark).toBeNull();
  });

  it("returns to the bundled default", async () => {
    renderView();
    act(() => useWallpaperStore.getState().select("03-anime-neon-01", "dark"));

    fireEvent.click(await screen.findByRole("button", { name: /Default/ }));

    expect(useWallpaperStore.getState().selections.dark).toBeNull();
    expect(window.localStorage.getItem("jarvis.wallpaper.v1")).toBeNull();
  });

  it("filters by style and by theme", async () => {
    renderView();

    fireEvent.click(await screen.findByRole("button", { name: "Cinematic Anime Neon" }));
    await waitFor(() => expect(requestedSources()).toHaveLength(1));
    expect(screen.getByAltText("Neon Crossing")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "All styles" }));
    fireEvent.click(screen.getByRole("button", { name: /Light/ }));
    await waitFor(() => expect(requestedSources()).toHaveLength(1));
    expect(screen.getByAltText("Morning Atrium")).toBeTruthy();
  });

  it("keeps the same shuffled order across renders", async () => {
    const first = renderView();
    await screen.findByAltText("Flooded Observatory");
    const before = requestedSources();
    first.unmount();

    renderView();
    await screen.findByAltText("Flooded Observatory");

    expect(requestedSources()).toEqual(before);
  });

  it("stars a wallpaper from its tile and remembers it", async () => {
    renderView();

    await screen.findByAltText("Neon Crossing");
    fireEvent.click(
      screen.getByRole("button", { name: "Add Neon Crossing to favorites" }),
    );

    await waitFor(() => {
      expect(useWallpaperStore.getState().favorites).toEqual(["03-anime-neon-01"]);
    });
    expect(
      JSON.parse(window.localStorage.getItem("jarvis.wallpaper.favorites.v1") ?? "[]"),
    ).toEqual(["03-anime-neon-01"]);
  });

  it("un-stars a wallpaper and clears the storage slot when none are left", async () => {
    renderView();

    await screen.findByAltText("Neon Crossing");
    fireEvent.click(
      screen.getByRole("button", { name: "Add Neon Crossing to favorites" }),
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Remove Neon Crossing from favorites",
      }),
    );

    await waitFor(() => {
      expect(useWallpaperStore.getState().favorites).toEqual([]);
    });
    expect(window.localStorage.getItem("jarvis.wallpaper.favorites.v1")).toBeNull();
  });

  it("shows only the starred wallpapers under the Favorites filter", async () => {
    renderView();

    await screen.findByAltText("Morning Atrium");
    fireEvent.click(
      screen.getByRole("button", { name: "Add Morning Atrium to favorites" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Favorites/ }));

    await waitFor(() => {
      const tiles = [...document.querySelectorAll('[data-testid="wallpaper-grid"] img')];
      expect(tiles.map((tile) => tile.getAttribute("alt"))).toEqual([
        "Morning Atrium",
      ]);
    });
  });

  it("stars the bundled original like any other wallpaper", async () => {
    renderView();

    fireEvent.click(
      await screen.findByRole("button", { name: "Add The Original to favorites" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Favorites/ }));

    await waitFor(() => {
      const tiles = [...document.querySelectorAll('[data-testid="wallpaper-grid"] img')];
      expect(tiles.map((tile) => tile.getAttribute("alt"))).toEqual(["The Original"]);
    });
  });

  it("explains the empty Favorites filter instead of showing a bare no-match line", async () => {
    renderView();

    fireEvent.click(await screen.findByRole("button", { name: /Favorites/ }));

    expect(await screen.findByText(/No favorites yet/)).toBeTruthy();
  });

  it("ignores stored favorites the catalog no longer knows", async () => {
    // A library that was uninstalled leaves ids behind; they must not be
    // counted, and the filter must read as empty rather than as "1 starred".
    act(() => useWallpaperStore.setState({ favorites: ["99-vanished-01"] }));
    renderView();

    fireEvent.click(await screen.findByRole("button", { name: /Favorites/ }));

    expect(await screen.findByText(/No favorites yet/)).toBeTruthy();
  });

  it("keeps the preview open when its wallpaper is un-starred under the filter", async () => {
    renderView();

    await screen.findByAltText("Neon Crossing");
    fireEvent.click(
      screen.getByRole("button", { name: "Add Neon Crossing to favorites" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Favorites/ }));
    fireEvent.click(await screen.findByAltText("Neon Crossing"));
    const preview = await screen.findByTestId("wallpaper-preview");

    fireEvent.click(
      within(preview).getByRole("button", {
        name: "Remove Neon Crossing from favorites",
      }),
    );

    expect(screen.getByTestId("wallpaper-preview")).toBeTruthy();
  });

  it("stars a wallpaper from the preview", async () => {
    renderView();

    fireEvent.click(await screen.findByAltText("Flooded Observatory"));
    await screen.findByTestId("wallpaper-preview");
    const preview = screen.getByTestId("wallpaper-preview");
    fireEvent.click(
      within(preview).getByRole("button", {
        name: "Add Flooded Observatory to favorites",
      }),
    );

    await waitFor(() => {
      expect(useWallpaperStore.getState().favorites).toEqual([
        "01-cinematic-photoreal-01",
      ]);
    });
  });

  it("still offers the original when the library is absent", async () => {
    renderView({ available: false, count: 0, styles: [], items: [] });

    expect(await screen.findByAltText("The Original")).toBeTruthy();
    expect(screen.getByText(/No wallpaper library installed/)).toBeTruthy();
  });

  it("still offers the original when the catalog request fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("backend restarting")));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ThemeProvider>
          <WallpaperView />
        </ThemeProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByAltText("The Original")).toBeTruthy();
  });
});
