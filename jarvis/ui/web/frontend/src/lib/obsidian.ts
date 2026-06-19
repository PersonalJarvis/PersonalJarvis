/**
 * Helpers for the Obsidian URL scheme.
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
