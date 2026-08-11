import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

/**
 * The library installer, as the server reports it.
 *
 * `state` is the machine the download banner renders from: `idle` before
 * anything happened, `downloading`/`unpacking` while the daemon thread works,
 * `done` once the catalog is on disk, `error` with a sentence for the owner.
 */
export interface LibraryInstallStatus {
  installed: boolean;
  state: "idle" | "downloading" | "unpacking" | "done" | "error";
  receivedBytes: number;
  totalBytes: number;
  error: string | null;
}

const STATUS_URL = "/api/wallpapers/library";
const STATUS_KEY = ["wallpapers", "library"] as const;

/** True while the server is actively working on the install. */
export function installRunning(status: LibraryInstallStatus | undefined): boolean {
  return status?.state === "downloading" || status?.state === "unpacking";
}

function parse(data: unknown): LibraryInstallStatus | null {
  const status = data as Partial<LibraryInstallStatus> | null;
  if (!status || typeof status.state !== "string") return null;
  return {
    installed: Boolean(status.installed),
    state: status.state as LibraryInstallStatus["state"],
    receivedBytes: typeof status.receivedBytes === "number" ? status.receivedBytes : 0,
    totalBytes: typeof status.totalBytes === "number" ? status.totalBytes : 0,
    error: typeof status.error === "string" ? status.error : null,
  };
}

/**
 * The install status, polled only while an install is actually running.
 *
 * The 500-piece library is optional content a fresh machine does not have, so
 * this is what turns the "download it" button into a progress bar. Once the
 * server reports `done`, the catalog query is refetched and the grid fills in
 * without a reload.
 */
export function useWallpaperLibraryInstall() {
  const client = useQueryClient();

  const status = useQuery<LibraryInstallStatus | null>({
    queryKey: STATUS_KEY,
    queryFn: async () => {
      const data = await fetch(STATUS_URL)
        .then((response) => (response.ok ? response.json() : null))
        .catch(() => null);
      const parsed = parse(data);
      // The catalog was fetched once and kept (see useWallpaperCatalog); the
      // install finishing is the one event that makes it stale.
      if (parsed?.state === "done") {
        void client.invalidateQueries({ queryKey: ["wallpapers"], exact: true });
      }
      return parsed;
    },
    refetchInterval: (query) =>
      installRunning(query.state.data ?? undefined) ? 500 : false,
  });

  const start = useMutation<LibraryInstallStatus | null, Error>({
    mutationFn: async () => {
      const response = await fetch(`${STATUS_URL}/install`, { method: "POST" });
      if (!response.ok) {
        throw new Error("The download could not be started.");
      }
      return parse(await response.json());
    },
    onSuccess: (data) => {
      client.setQueryData(STATUS_KEY, data);
    },
  });

  return { status, start };
}
