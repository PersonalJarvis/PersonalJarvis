/**
 * The Share button in the wallpaper preview: offered for the owner's own
 * pictures only, when the wallpaper lane is on, and it opens the publish
 * dialog over the picker.
 *
 * A file of its own because it needs the publish identity endpoint to answer
 * something real, which the picker's main harness deliberately leaves blank.
 *
 * No jest-dom in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/hooks/useTheme";
import { WallpaperView } from "@/views/WallpaperView";

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

const OWN = { id: "u000000000000000a", title: "Kitchen Window", theme: "light", createdAt: 1 };
const INSTALLED = {
  id: "u000000000000000b",
  title: "Rain Antenna City",
  theme: "dark",
  createdAt: 2,
  source: "marketplace",
  publisher: "someone",
};

function stubServer(wallpapersEnabled: boolean) {
  const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url === "/api/wallpapers/uploads") return json({ items: [OWN, INSTALLED] });
      if (url === "/api/wallpapers") return json(CATALOG);
      if (url === "/api/marketplace/publish/identity") {
        return json({
          enabled: true,
          wallpapers_enabled: wallpapersEnabled,
          signed_in: true,
          login: "octocat",
        });
      }
      return json({});
    }),
  );
}

function renderView(wallpapersEnabled = true) {
  stubServer(wallpapersEnabled);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <WallpaperView />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("WallpaperView — share", () => {
  it("offers Share on the owner's own picture and opens the publish dialog", async () => {
    renderView();
    fireEvent.click(await screen.findByAltText("Kitchen Window"));
    const preview = await screen.findByTestId("wallpaper-preview");
    const share = await within(preview).findByTestId("wallpaper-share");
    fireEvent.click(share);
    const dialog = await screen.findByTestId("publish-wallpaper-dialog");
    expect(dialog.textContent).toContain("Share with the community");
    // The title is prefilled from the picker's own name for the picture.
    expect((within(dialog).getByTestId("wallpaper-title") as HTMLInputElement).value).toBe(
      "Kitchen Window",
    );
  });

  it("does not offer Share on a picture installed from the community", async () => {
    renderView();
    fireEvent.click(await screen.findByAltText("Rain Antenna City"));
    const preview = await screen.findByTestId("wallpaper-preview");
    // Give the identity query a tick to settle, then assert absence.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(within(preview).queryByTestId("wallpaper-share")).toBeNull();
  });

  it("does not offer Share when the wallpaper lane is off", async () => {
    renderView(false);
    fireEvent.click(await screen.findByAltText("Kitchen Window"));
    const preview = await screen.findByTestId("wallpaper-preview");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(within(preview).queryByTestId("wallpaper-share")).toBeNull();
  });
});
