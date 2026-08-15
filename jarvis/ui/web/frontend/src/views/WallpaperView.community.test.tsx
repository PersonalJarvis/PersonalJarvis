import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/hooks/useTheme";
import { WallpaperView } from "@/views/WallpaperView";
import { useWallpaperStore } from "@/store/wallpaper";

// ---------------------------------------------------------------------------
// The wallpaper picker's two marketplace halves: browsing what other people
// published, and publishing your own.
//
// Both are gated on facts the server reports rather than on anything the view
// decides for itself — whether this deployment has the lane at all, whether
// somebody is signed in, and whether the picture is theirs to publish. Those
// gates are what these tests pin: a Share button offered where the server will
// refuse is worse than no button, because it turns a rule into a mystery.
// ---------------------------------------------------------------------------

const CATALOG = {
  available: true,
  count: 1,
  styles: [{ slug: "03-anime-neon", label: "Cinematic Anime Neon", count: 1 }],
  items: [
    {
      id: "03-anime-neon-01",
      title: "Neon Crossing",
      style: "03-anime-neon",
      styleLabel: "Cinematic Anime Neon",
      theme: "dark" as const,
    },
  ],
};

interface Upload {
  id: string;
  title: string;
  theme: "light" | "dark";
  createdAt: number;
  origin?: string | null;
}

const OWN_UPLOAD: Upload = {
  id: "u0000000000000001",
  title: "Harbour At Dawn",
  theme: "light",
  createdAt: 1_700_000_000,
  origin: null,
};

const IMPORTED_UPLOAD: Upload = {
  id: "u0000000000000002",
  title: "Rain Antenna City",
  theme: "dark",
  createdAt: 1_700_000_100,
  origin: "community:rain-antenna-city",
};

const COMMUNITY_WALLPAPER = {
  name: "flooded-observatory",
  title: "Flooded Observatory",
  description: "A drowned observatory under a storm-lit sky.",
  publisher: "octocat",
  theme: "dark" as const,
  license: "CC0-1.0",
  image_url: "https://feed.invalid/wallpapers/flooded-observatory/wallpaper.webp",
  thumb_url: "https://feed.invalid/wallpapers/flooded-observatory/thumb.webp",
  installable: true,
  installed: false,
  installed_id: null,
};

interface ServerOptions {
  uploads?: Upload[];
  wallpapers?: unknown[];
  identity?: Record<string, unknown>;
}

/** A fake of every endpoint this section touches, holding state in memory. */
function stubServer(options: ServerOptions = {}) {
  const state = {
    uploads: [...(options.uploads ?? [])],
    wallpapers: [...(options.wallpapers ?? [])],
    published: [] as unknown[],
    imported: [] as string[],
  };
  const identity = options.identity ?? {
    enabled: true,
    wallpapers_enabled: true,
    signed_in: true,
    login: "octocat",
  };
  const json = (body: unknown, ok = true, status = 200) => ({
    ok,
    status,
    json: async () => body,
  });

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: { method?: string; body?: unknown }) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();

      if (url === "/api/wallpapers") return json(CATALOG);
      if (url === "/api/wallpapers/uploads") return json({ items: state.uploads });
      if (url === "/api/marketplace/publish/identity") return json(identity);
      if (url === "/api/marketplace/community") {
        return json({ status: "ok", plugins: [], skills: [], wallpapers: state.wallpapers });
      }
      if (url === "/api/marketplace/publish/submit-wallpaper" && method === "POST") {
        state.published.push(JSON.parse(String(init?.body ?? "{}")));
        return json({
          ok: true,
          name: "harbour-at-dawn",
          version: "1.0.0",
          install: { cli: "jarvis marketplace install harbour-at-dawn", runner: "uvx …", prompt: "…" },
        });
      }
      const install = url.match(/^\/api\/marketplace\/community\/wallpapers\/([^/]+)\/install$/);
      if (install && method === "POST") {
        state.imported.push(install[1]);
        const added: Upload = {
          id: "u0000000000000009",
          title: "Flooded Observatory",
          theme: "dark",
          createdAt: 1_700_000_500,
          origin: `community:${install[1]}`,
        };
        state.uploads = [added, ...state.uploads];
        state.wallpapers = state.wallpapers.map((w) =>
          (w as { name: string }).name === install[1] ? { ...(w as object), installed: true } : w,
        );
        return json({ ok: true, already_installed: false, wallpaper: added });
      }
      return json({});
    }),
  );
  return state;
}

let server: ReturnType<typeof stubServer>;

function renderView(options: ServerOptions = {}) {
  server = stubServer(options);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <WallpaperView />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

/** Open a wallpaper's full-size preview by its tile. */
async function openPreview(title: string) {
  const tile = await screen.findByAltText(title);
  fireEvent.click(tile);
  return screen.findByTestId("wallpaper-preview");
}

beforeEach(() => {
  window.localStorage.clear();
  useWallpaperStore.setState({ selections: { light: null, dark: null }, favorites: [] });
  document.documentElement.classList.add("dark");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("community wallpapers in the picker", () => {
  it("shows what other people published, with its own filter chip", async () => {
    renderView({ wallpapers: [COMMUNITY_WALLPAPER] });

    expect(await screen.findByAltText("Flooded Observatory")).toBeTruthy();
    // The chip, plus the style label the tile itself carries.
    expect(screen.getAllByText("Community").length).toBeGreaterThan(0);
  });

  it("browses a community picture without downloading it", async () => {
    renderView({ wallpapers: [COMMUNITY_WALLPAPER] });

    const tile = await screen.findByAltText("Flooded Observatory");
    // The registry's own thumbnail, not a local file the app does not have.
    expect(tile.getAttribute("src")).toBe(COMMUNITY_WALLPAPER.thumb_url);
    expect(server.imported).toEqual([]);
  });

  it("offers to add it rather than to use it — the bytes are not here yet", async () => {
    renderView({ wallpapers: [COMMUNITY_WALLPAPER] });
    await openPreview("Flooded Observatory");

    expect(screen.getByText("Add to my wallpapers")).toBeTruthy();
    expect(screen.queryByText("Use this wallpaper")).toBeNull();
  });

  it("names the publisher, so a picture is never anonymous", async () => {
    renderView({ wallpapers: [COMMUNITY_WALLPAPER] });
    await openPreview("Flooded Observatory");

    expect(screen.getByText(/by @octocat/)).toBeTruthy();
  });

  it("imports on request and lands the copy under your own pictures", async () => {
    renderView({ wallpapers: [COMMUNITY_WALLPAPER] });
    await openPreview("Flooded Observatory");

    fireEvent.click(screen.getByText("Add to my wallpapers"));

    await waitFor(() => expect(server.imported).toEqual(["flooded-observatory"]));
    // The preview follows the picture to its local copy, which CAN be used.
    await waitFor(() => expect(screen.getByText("Use this wallpaper")).toBeTruthy());
  });

  it("does not list a community picture twice once it is imported", async () => {
    renderView({
      uploads: [IMPORTED_UPLOAD],
      wallpapers: [
        { ...COMMUNITY_WALLPAPER, name: "rain-antenna-city", title: "Rain Antenna City", installed: true },
      ],
    });

    await screen.findByAltText("Rain Antenna City");
    expect(screen.getAllByAltText("Rain Antenna City")).toHaveLength(1);
  });
});

describe("publishing your own wallpaper", () => {
  it("offers Share on your own picture", async () => {
    renderView({ uploads: [OWN_UPLOAD] });
    await openPreview("Harbour At Dawn");

    expect(screen.getByText("Share")).toBeTruthy();
  });

  it("does not offer Share on an imported community picture", async () => {
    // Re-publishing somebody else's work under your own name is the thing the
    // server refuses; the view must not invite it in the first place.
    renderView({ uploads: [IMPORTED_UPLOAD] });
    await openPreview("Rain Antenna City");

    expect(screen.queryByText("Share")).toBeNull();
  });

  it("does not offer Share on a library wallpaper you did not make", async () => {
    renderView();
    await openPreview("Neon Crossing");

    expect(screen.queryByText("Share")).toBeNull();
  });

  it("hides Share entirely where the deployment has no wallpaper lane", async () => {
    renderView({
      uploads: [OWN_UPLOAD],
      identity: { enabled: true, wallpapers_enabled: false, signed_in: true, login: "octocat" },
    });
    await openPreview("Harbour At Dawn");

    expect(screen.queryByText("Share")).toBeNull();
  });

  it("will not publish until the rights statement is ticked", async () => {
    renderView({ uploads: [OWN_UPLOAD] });
    await openPreview("Harbour At Dawn");
    fireEvent.click(screen.getByText("Share"));

    const submit = (await screen.findByTestId("wallpaper-publish-submit")) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.click(screen.getByTestId("wallpaper-rights"));
    await waitFor(() => expect(submit.disabled).toBe(false));
  });

  it("sends the picture's id and the statement, never the pixels", async () => {
    renderView({ uploads: [OWN_UPLOAD] });
    await openPreview("Harbour At Dawn");
    fireEvent.click(screen.getByText("Share"));
    fireEvent.click(await screen.findByTestId("wallpaper-rights"));
    fireEvent.click(screen.getByTestId("wallpaper-publish-submit"));

    await waitFor(() => expect(server.published).toHaveLength(1));
    expect(server.published[0]).toMatchObject({
      upload_id: OWN_UPLOAD.id,
      title: "Harbour At Dawn",
      license: "CC0-1.0",
      rights: true,
      // The picker's own light/dark filing travels with it.
      theme: "light",
    });
  });

  it("asks for a sign-in before it will publish anything", async () => {
    renderView({
      uploads: [OWN_UPLOAD],
      identity: { enabled: true, wallpapers_enabled: true, signed_in: false },
    });
    await openPreview("Harbour At Dawn");
    fireEvent.click(screen.getByText("Share"));

    const submit = (await screen.findByTestId("wallpaper-publish-submit")) as HTMLButtonElement;
    fireEvent.click(screen.getByTestId("wallpaper-rights"));
    // Ticking the box is not enough — identity is the other half of the gate.
    await waitFor(() => expect(submit.disabled).toBe(true));
    expect(screen.getByText("Sign in with GitHub")).toBeTruthy();
  });

  it("reports the published name and how anyone else installs it", async () => {
    renderView({ uploads: [OWN_UPLOAD] });
    await openPreview("Harbour At Dawn");
    fireEvent.click(screen.getByText("Share"));
    fireEvent.click(await screen.findByTestId("wallpaper-rights"));
    fireEvent.click(screen.getByTestId("wallpaper-publish-submit"));

    // The published name, and the install line other people will run.
    await waitFor(() =>
      expect(screen.getAllByText(/harbour-at-dawn/).length).toBeGreaterThan(1),
    );
  });
});
