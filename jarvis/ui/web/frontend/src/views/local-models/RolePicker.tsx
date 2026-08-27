/**
 * The model picker of one job.
 *
 * Two failures shaped it. The first (BUG-188): the list was `row.qualifying`
 * alone, so an offline sweep left one option and a pick "jumped back". The
 * over-correction then offered EVERY installed download, which put an
 * embedding-only model in the speech list — a model that cannot answer a
 * call is not a choice, it is a trap that looks like one.
 *
 * The list is therefore built from the job's own requirements, on the
 * client, from the inventory it can see:
 *
 *   * `fits`   — every installed download that declares each capability the
 *                job requires AND sits inside the job's size class when it
 *                has one (the voice brain answers within a breath, so it
 *                stays under 6 GB);
 *   * `others` — downloads that have the capabilities but fall outside the
 *                size class, or that Jarvis could not probe (`probed: false`
 *                means "unknown", never "unable"). A real choice with a
 *                stated cost, so it is offered under its own heading.
 *
 * A download that lacks a required capability is not listed at all. The
 * configured tag stays listed even when it is gone from disk, or the row
 * would show a value the picker cannot express.
 */
import { useMemo } from "react";

import {
  canonicalModelName,
  type LocalModelRow,
  type RoleRow,
} from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

export interface RolePickerProps {
  row: RoleRow;
  /** Every download the inventory knows; empty while the server is silent. */
  models: LocalModelRow[];
  /** True while a write for this row is in flight. */
  disabled?: boolean;
  onPick: (model: string) => void;
  className?: string;
}

const GIB = 1024 ** 3;

/** Whether `model` declares every capability `row` requires. */
export function hasRequired(row: RoleRow, model: LocalModelRow): boolean {
  return row.required.every((cap) => model.capabilities.includes(cap));
}

/** Whether `model` sits inside the job's size class (no class = always). */
export function inSizeClass(row: RoleRow, model: LocalModelRow): boolean {
  const cap = row.max_size_gb;
  if (cap == null || cap <= 0) return true;
  return model.size_bytes / GIB <= cap;
}

/**
 * `fits` first, then `others`, both in inventory order, de-duplicated
 * across `:latest`. Nothing that lacks a required capability appears.
 */
export function splitChoices(
  row: RoleRow,
  models: LocalModelRow[],
): { fits: string[]; others: string[] } {
  const seen = new Set<string>();
  const fits: string[] = [];
  const others: string[] = [];
  const take = (name: string, into: string[]) => {
    const key = canonicalModelName(name);
    if (!name || seen.has(key)) return;
    seen.add(key);
    into.push(name);
  };
  for (const m of models) {
    if (m.probed) {
      if (!hasRequired(row, m)) continue;
      take(m.name, inSizeClass(row, m) ? fits : others);
    } else {
      // Unknown capabilities: offered, not hidden, and not called a fit.
      take(m.name, others);
    }
  }
  // The backend's own verdict wins for anything it named that the client
  // could not see (a row the inventory has not caught up with yet).
  for (const name of row.qualifying) take(name, fits);
  if (row.current) take(row.current, others);
  return { fits, others };
}

export function RolePicker({
  row,
  models,
  disabled,
  onPick,
  className,
}: RolePickerProps) {
  const t = useT();
  const { fits, others } = useMemo(() => splitChoices(row, models), [row, models]);
  const installed = useMemo(() => {
    const names = new Set<string>();
    for (const m of models) names.add(canonicalModelName(m.name));
    return names;
  }, [models]);

  const label = (name: string) =>
    installed.has(canonicalModelName(name))
      ? name
      : `${name} ${t("local_models.roles.pick_missing_suffix")}`;

  // The second heading says what "other" costs for THIS job.
  const othersLabel =
    row.max_size_gb != null && row.max_size_gb > 0
      ? fill(t("local_models.roles.pick_group_over_size"), {
          gb: String(row.max_size_gb),
        })
      : t("local_models.roles.pick_group_others");

  return (
    <select
      aria-label={fill(t("local_models.roles.pick_label"), {
        role: t(row.label_key),
      })}
      value={row.current}
      disabled={disabled}
      onChange={(e) => onPick(e.target.value)}
      data-testid={`role-picker-${row.id}`}
      className={cn(
        "h-9 w-full rounded-lg border border-border bg-background/60 px-2.5 text-sm text-foreground",
        "transition-colors hover:border-border/80 focus:outline-none focus:ring-1 focus:ring-primary",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
    >
      {row.id !== "embedding" && row.id !== "voice" && (
        <option value="">{t("local_models.roles.pick_discovery")}</option>
      )}
      {(row.id === "embedding" || row.id === "voice") && !row.current && (
        <option value="">{t("local_models.roles.pick_none")}</option>
      )}
      {fits.length > 0 && (
        <optgroup label={t("local_models.roles.pick_group_fits")}>
          {fits.map((name) => (
            <option key={name} value={name}>
              {label(name)}
            </option>
          ))}
        </optgroup>
      )}
      {others.length > 0 && (
        <optgroup label={othersLabel}>
          {others.map((name) => (
            <option key={name} value={name}>
              {label(name)}
            </option>
          ))}
        </optgroup>
      )}
    </select>
  );
}
