import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { Theme } from "@/hooks/useTheme";
import type { WallpaperEntry } from "@/hooks/useWallpaperCatalog";

/** One wallpaper the owner brought themselves, as the server reports it. */
export interface WallpaperUpload {
  id: string;
  title: string;
  theme: Theme;
  createdAt: number;
}

/** The filter slug and chip label the owner's own pictures live under. */
export const UPLOAD_WALLPAPER_STYLE = "yours";

const UPLOADS_URL = "/api/wallpapers/uploads";
const UPLOADS_KEY = ["wallpapers", "uploads"] as const;

/** Read the sentence the server sent, or fall back to a plain one. */
async function detail(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body?.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    /* a body that is not JSON tells us nothing useful */
  }
  return fallback;
}

/** Present an upload the way the browse grid knows every other wallpaper. */
export function uploadAsEntry(upload: WallpaperUpload): WallpaperEntry {
  return {
    id: upload.id,
    title: upload.title,
    style: UPLOAD_WALLPAPER_STYLE,
    styleLabel: "Yours",
    theme: upload.theme,
    isUpload: true,
  };
}

/**
 * The owner's own wallpapers.
 *
 * A query of its own rather than part of the catalog: the generated library is
 * five hundred fixed entries fetched once and kept forever, while this list
 * changes every time someone adds or removes a picture. Keeping them apart lets
 * one be refetched without discarding the other.
 */
export function useWallpaperUploads() {
  return useQuery<WallpaperUpload[]>({
    queryKey: UPLOADS_KEY,
    queryFn: async () => {
      // A backend that is mid-restart means "no uploads to show right now",
      // not a broken section — the library and the bundled original still are.
      const data = await fetch(UPLOADS_URL)
        .then((response) => (response.ok ? response.json() : null))
        .catch(() => null);
      const items = (data as { items?: unknown } | null)?.items;
      if (!Array.isArray(items)) return [];
      return items.filter(
        (item): item is WallpaperUpload =>
          typeof item?.id === "string" &&
          typeof item?.title === "string" &&
          (item?.theme === "light" || item?.theme === "dark"),
      );
    },
  });
}

/**
 * Add, retheme, and remove the owner's own wallpapers.
 *
 * Every mutation refetches the list rather than patching it in place: the store
 * is a directory on disk that another window may also be writing to, and a
 * refetch is a few hundred bytes.
 */
export function useUploadMutations() {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: UPLOADS_KEY });

  const add = useMutation<WallpaperUpload, Error, File>({
    mutationFn: async (file) => {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(UPLOADS_URL, { method: "POST", body });
      if (!response.ok) {
        throw new Error(await detail(response, "That image could not be added."));
      }
      return (await response.json()) as WallpaperUpload;
    },
    onSuccess: refresh,
  });

  const setTheme = useMutation<WallpaperUpload, Error, { id: string; theme: Theme }>({
    mutationFn: async ({ id, theme }) => {
      const response = await fetch(`${UPLOADS_URL}/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme }),
      });
      if (!response.ok) {
        throw new Error(await detail(response, "That change could not be saved."));
      }
      return (await response.json()) as WallpaperUpload;
    },
    onSuccess: refresh,
  });

  const remove = useMutation<string, Error, string>({
    mutationFn: async (id) => {
      const response = await fetch(`${UPLOADS_URL}/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await detail(response, "That wallpaper could not be removed."));
      }
      return id;
    },
    onSuccess: refresh,
  });

  return { add, setTheme, remove };
}
