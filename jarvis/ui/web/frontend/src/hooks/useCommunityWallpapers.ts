import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { WallpaperEntry } from "@/hooks/useWallpaperCatalog";

// ---------------------------------------------------------------------------
// Community wallpapers, in the picker.
//
// They belong here rather than in the Plugins → Community list: a wallpaper is
// not a capability to review and install, it is a picture to look at, and the
// place someone looks at pictures is the wallpaper grid. The store's own list
// stays the canonical catalogue; this is the same feed, shown where it is
// useful.
//
// Nothing is downloaded while browsing. The tiles are the feed's thumbnails,
// served by the registry, and the full picture only arrives when someone adds
// it — at which point it becomes an ordinary upload under "Yours" and is
// indistinguishable from one, except for the provenance marker that stops it
// from being re-published as somebody else's work.
// ---------------------------------------------------------------------------

/** The filter slug and chip label community pictures live under. */
export const COMMUNITY_WALLPAPER_STYLE = "community";

export interface CommunityWallpaper {
  name: string;
  title: string;
  description?: string | null;
  publisher?: string | null;
  theme?: "light" | "dark" | null;
  license?: string | null;
  source_url?: string | null;
  image_url?: string | null;
  thumb_url?: string | null;
  installable: boolean;
  installed: boolean;
  installed_id?: string | null;
}

const COMMUNITY_URL = "/api/marketplace/community";

/**
 * The community feed's wallpaper lane.
 *
 * Shares the query key with nothing else on purpose: the Plugins → Community
 * view fetches the same URL with its own key and its own cadence, and a
 * wallpaper grid that invalidated the plugin store's cache on every import
 * would make one section's action reload another's list.
 */
export function useCommunityWallpapers() {
  return useQuery<CommunityWallpaper[]>({
    queryKey: ["community-wallpapers"],
    // The feed is cached server-side with its own TTL, so a visit to the
    // section costs a local request, not a trip to the registry.
    staleTime: 60_000,
    queryFn: async () => {
      // An unreachable feed means "no community pictures right now", never a
      // broken section — the library and your own uploads are still there.
      const data = await fetch(COMMUNITY_URL)
        .then((response) => (response.ok ? response.json() : null))
        .catch(() => null);
      const items = (data as { wallpapers?: unknown } | null)?.wallpapers;
      if (!Array.isArray(items)) return [];
      return items.filter(
        (item): item is CommunityWallpaper =>
          typeof item?.name === "string" && typeof item?.title === "string",
      );
    },
  });
}

/** Present a community wallpaper the way the browse grid knows every other. */
export function communityAsEntry(item: CommunityWallpaper): WallpaperEntry {
  return {
    // Namespaced so it can never collide with a local upload id.
    id: `community:${item.name}`,
    title: item.title,
    style: COMMUNITY_WALLPAPER_STYLE,
    styleLabel: "Community",
    // The feed may leave the theme unstated; dark is the app's own ground and
    // the safer guess for a picture nobody classified.
    theme: item.theme === "light" ? "light" : "dark",
    isCommunity: true,
    communityName: item.name,
    publisher: item.publisher ?? null,
    remoteThumbUrl: item.thumb_url ?? item.image_url ?? null,
    remoteFullUrl: item.image_url ?? item.thumb_url ?? null,
  };
}

/** What the import route reports back: the local copy it just wrote. */
export interface ImportedWallpaper {
  ok: boolean;
  already_installed: boolean;
  wallpaper: { id: string; title: string; theme: "light" | "dark"; origin?: string | null };
}

/** Add a community wallpaper to this machine's own pictures. */
export function useImportCommunityWallpaper() {
  const client = useQueryClient();
  return useMutation<ImportedWallpaper, Error, string>({
    mutationFn: async (name) => {
      const response = await fetch(
        `${COMMUNITY_URL}/wallpapers/${encodeURIComponent(name)}/install`,
        { method: "POST" },
      );
      if (!response.ok) {
        let message = "That wallpaper could not be added.";
        try {
          const body = (await response.json()) as { detail?: unknown };
          if (typeof body?.detail === "string" && body.detail.trim()) message = body.detail;
        } catch {
          /* a body that is not JSON tells us nothing useful */
        }
        throw new Error(message);
      }
      return (await response.json()) as ImportedWallpaper;
    },
    onSuccess: () => {
      // Both lists move: the picture joins "Yours", and the community entry
      // now reports itself installed.
      client.invalidateQueries({ queryKey: ["wallpapers", "uploads"] });
      client.invalidateQueries({ queryKey: ["community-wallpapers"] });
    },
  });
}
