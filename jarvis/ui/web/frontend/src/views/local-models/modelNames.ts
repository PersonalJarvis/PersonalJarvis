/**
 * The readable side of a model row, for every panel that names one.
 *
 * The backend describes each download once (`ollama_names.describe`): the
 * model line, its size, its quantisation. The panels read those parts here
 * instead of showing the tag — and fall back to the tag when a row from an
 * older payload has none, so nothing ever renders blank.
 */
import {
  canonicalModelName,
  type LocalModelRow,
  type RoleFit,
} from "@/hooks/useLocalModels";

/** "Qwen 3.5 4B" — or the tag itself when the row carries no label. */
export function modelLabel(
  row: LocalModelRow | null | undefined,
  fallback = "",
): string {
  if (!row) return fallback;
  return row.display_label || row.display_name || row.name || fallback;
}

/** The inventory row for a tag (`:latest` tolerant), or null. */
export function findModel(
  models: readonly LocalModelRow[],
  name: string,
): LocalModelRow | null {
  if (!name) return null;
  const key = canonicalModelName(name);
  return models.find((m) => canonicalModelName(m.name) === key) ?? null;
}

/** The label a tag gets: the row's readable one, else the tag. */
export function labelFor(models: readonly LocalModelRow[], name: string): string {
  return modelLabel(findModel(models, name), name);
}

/** The capabilities a job is gated on; "completion" is every model's. */
export const ROLE_CAPABILITIES: ReadonlySet<string> = new Set([
  "tools",
  "vision",
  "thinking",
  "embedding",
  "audio",
]);

export function capabilityChips(row: LocalModelRow | null | undefined): string[] {
  if (!row || !row.probed) return [];
  return row.capabilities.filter((c) => ROLE_CAPABILITIES.has(c));
}

export type FitTone = "ok" | "warn" | "bad" | "muted";

/** The colour a verdict carries: semantic, never the accent. */
export function fitTone(fit: RoleFit | "absent" | "" | string | undefined): FitTone {
  switch (fit) {
    case "fits":
      return "ok";
    case "slow":
      return "warn";
    case "unfit":
    case "absent":
      return "bad";
    default:
      return "muted";
  }
}
