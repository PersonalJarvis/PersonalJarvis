/**
 * Helpers for talking to the Obsidian integration: the `obsidian://` URL
 * scheme plus the setup-wizard API (`GET /api/setup/obsidian/vaults`,
 * `POST /api/setup/obsidian/register`).
 *
 * The `obsidian://open?vault=<vault>&file=<path>` URL handler is installed
 * by Obsidian on the user's machine and opens the named file inside the
 * matching vault. We cannot detect from the browser whether the handler
 * is registered — the per-page button in `ObsidianButton.tsx` shows a
 * fallback toast 800 ms after click as a defensive UX measure.
 *
 * The vault name is hard-coded to match the on-disk folder name
 * `wiki/obsidian-vault/`. If the user renames the vault inside Obsidian,
 * they must also rename the folder; we don't try to introspect.
 */

/** Vault name in Obsidian — matches the on-disk folder `wiki/obsidian-vault/`. */
export const VAULT_NAME = "obsidian-vault";

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
 * Build an `obsidian://open` URL for a vault-relative file path.
 *
 * - When `vaultRelPath` is empty, the URL opens to the vault root.
 * - The vault name and the file path are both URI-component-encoded so
 *   spaces, German umlauts, and other special characters round-trip
 *   correctly through Obsidian's URL handler.
 *
 * @param vaultRelPath - Vault-relative POSIX path such as
 *   `"entities/harald.md"`. May be the empty string to open the root.
 */
export function buildObsidianUrl(vaultRelPath: string): string {
  const base = `obsidian://open?vault=${encodeURIComponent(VAULT_NAME)}`;
  if (!vaultRelPath) {
    return base;
  }
  return `${base}&file=${encodeURIComponent(vaultRelPath)}`;
}
