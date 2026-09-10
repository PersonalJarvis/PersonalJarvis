/** Resolve persisted view preferences after retiring the city prototype. */
export type AgentsMode = "world" | "ledger";

export function resolveAgentsMode(
  stored: string | null,
  legacy: string | null,
  webglSupported: boolean,
): AgentsMode {
  if (!webglSupported) return "ledger";
  // A newer city preference takes precedence over an older ledger preference.
  if (stored === "city") return "world";
  if (stored === "world" || stored === "ledger") return stored;
  return legacy === "ledger" ? "ledger" : "world";
}
