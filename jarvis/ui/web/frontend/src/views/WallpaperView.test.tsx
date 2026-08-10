import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  return render(
    <QueryClientProvider client={client}>
      <WallpaperView />
    </QueryClientProvider>,
  );
}

/** Every `<img>` the grid and preview have actually asked the browser for. */
function requestedSources(): string[] {
  return Array.from(document.querySelectorAll("img")).map((img) =>
    img.getAttribute("src") ?? "",
  );
}

beforeEach(() => {
  window.localStorage.clear();
  useWallpaperStore.setState({ selectedId: null });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("WallpaperView", () => {
  it("browses on thumbnails alone — no full-size image is requested", async () => {
    renderView();

    await screen.findByAltText("Flooded Observatory");
    const sources = requestedSources();

    expect(sources).toHaveLength(3);
    expect(sources.every((src) => src.endsWith("/thumb"))).toBe(true);
    expect(sources.some((src) => src.endsWith("/full"))).toBe(false);
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
      expect(useWallpaperStore.getState().selectedId).toBe(
        "01-cinematic-photoreal-02",
      );
    });
    expect(window.localStorage.getItem("jarvis.wallpaper.v1")).toBe(
      "01-cinematic-photoreal-02",
    );
  });

  it("returns to the bundled default", async () => {
    renderView();
    act(() => useWallpaperStore.getState().select("03-anime-neon-01"));

    fireEvent.click(await screen.findByRole("button", { name: /Default/ }));

    expect(useWallpaperStore.getState().selectedId).toBeNull();
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

  it("explains itself instead of breaking when the library is absent", async () => {
    renderView({ available: false, count: 0, styles: [], items: [] });

    expect(await screen.findByText("No wallpaper library installed")).toBeTruthy();
  });
});
