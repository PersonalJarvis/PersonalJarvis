/**
 * Helpers for talking to the Obsidian integration: the `obsidian://` URL
 * scheme plus the setup-wizard API (`GET /api/setup/obsidian/vaults`,
 * `POST /api/setup/obsidian/register`).
 *
 * `obsidian://open?path=<absolute-path>` asks Obsidian to find the most
 * specific registered vault containing the path. This works for both a
 * separate Jarvis vault and a `Jarvis/` directory inside an existing vault.
 */


/** One entry in the "use my existing vault" picker (spec A6). */
export interface ObsidianVaultInfo {
  path: string;
  name: string;
}

/**
 * List the user's already-registered Obsidian vaults, for the vault-choice
 * picker in `ObsidianSetupDialog`. Degrades to an empty list on any
 * non-OK response or `ok: false` body — the "use my existing vault" option
 * simply stays disabled rather than the dialog surfacing an error, since
 * this list is a nice-to-have, not load-bearing for the default setup path.
 *
 * Accepts an optional `fetchImpl` (mirrors the dialog's own `fetchImpl`
 * prop) so callers can inject a stable fetch reference for tests instead
 * of always hitting `window.fetch`.
 */
export async function fetchObsidianVaults(
  fetchImpl: typeof fetch = fetch,
): Promise<ObsidianVaultInfo[]> {
  const res = await fetchImpl("/api/setup/obsidian/vaults");
  if (!res.ok) return [];
  const body = (await res.json()) as {
    ok: boolean;
    vaults?: ObsidianVaultInfo[];
  };
  return body.ok ? (body.vaults ?? []) : [];
}

/** `"separate"` (default) creates a Jarvis-owned vault; `"existing"` repoints
 * Jarvis's vault root into a `Jarvis` subfolder of a vault the user already
 * has registered in Obsidian. */
export type RegisterMode = "separate" | "existing";

/** Flat result shape returned by `POST /api/setup/obsidian/register`. */
export interface ObsidianRegisterResult {
  status: string;
  active_vault_root?: string;
  restart_required?: boolean;
  error?: string;
}

/**
 * Register (or repoint) the Jarvis wiki vault.
 *
 * Unlike `mode="separate"`, which still answers HTTP 409/500 with a
 * FastAPI `{"detail": {...}}` error envelope for its failure branches (see
 * `ObsidianSetupDialog`'s own `handleRegister`, which parses those by
 * status code), `mode="existing"` always answers HTTP 200 with a flat
 * body — an unknown/missing `existingVaultPath` comes back inline as
 * `status: "config_missing"` rather than as an HTTP error (see
 * `jarvis/ui/web/setup_routes.py::obsidian_register`). This helper parses
 * the body unconditionally and is therefore only safe to use for
 * `mode="existing"`; the `separate` flow keeps its existing status-code-aware
 * handling in the dialog.
 */
export async function registerObsidianVault(
  mode: RegisterMode,
  existingVaultPath?: string | null,
  fetchImpl: typeof fetch = fetch,
): Promise<ObsidianRegisterResult> {
  const res = await fetchImpl("/api/setup/obsidian/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode,
      existing_vault_path: existingVaultPath ?? null,
    }),
  });
  return (await res.json()) as ObsidianRegisterResult;
}

/**
 * Build an `obsidian://open` URL for an absolute vault or page path.
 *
 * - When `vaultRelPath` is empty, the URL targets the configured vault root.
 * - Windows and POSIX separators stay consistent.
 * - The absolute path is URI-component-encoded for a reliable round trip.
 *
 * @param vaultRoot - Absolute configured Jarvis vault root.
 * @param vaultRelPath - Vault-relative POSIX path such as
 *   `"entities/sam.md"`. May be the empty string to open the root.
 */
export function buildObsidianUrl(
  vaultRoot: string,
  vaultRelPath = "",
): string {
  const root = vaultRoot.replace(/[\\/]+$/, "");
  const separator = root.includes("\\") ? "\\" : "/";
  const relative = vaultRelPath
    .replace(/^[\\/]+/, "")
    .replace(/[\\/]+/g, separator);
  const absolutePath = relative ? `${root}${separator}${relative}` : root;
  return `obsidian://open?path=${encodeURIComponent(absolutePath)}`;
}
