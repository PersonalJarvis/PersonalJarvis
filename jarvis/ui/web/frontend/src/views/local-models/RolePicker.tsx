/**
 * The model picker of one role row.
 *
 * It used to list `row.qualifying` only — the installed downloads that
 * declare every capability the job requires — plus the configured tag when
 * that had gone missing. Two ways that leaves a user stuck with a value they
 * did not choose (BUG-188):
 *
 *   * the sweep behind the payload found no server, so `qualifying` is empty
 *     and the list holds exactly one entry: the tag already configured;
 *   * a download Jarvis could not probe (`probed: false`) reports no
 *     capabilities, so it silently never appears — even though it runs.
 *
 * Both read as "I picked something else and it jumped back". The picker
 * therefore offers EVERY installed download, in two groups: the ones that
 * fit the job first, the rest under "Other installed models" with the
 * consequence spelled out rather than hidden. Choosing one is a real choice
 * a user is allowed to make; the section says what it costs, it does not
 * decide for them.
 */
import { useMemo } from "react";

import { canonicalModelName, type LocalModelRow, type RoleRow } from "@/hooks/useLocalModels";
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

/** `qualifying`, then every other installed tag, each list de-duplicated. */
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
  for (const name of row.qualifying) take(name, fits);
  for (const m of models) take(m.name, others);
  // A configured tag that is gone from disk still belongs in the list, or
  // the row would silently show a value the picker cannot express.
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
      {row.id !== "embedding" && (
        <option value="">{t("local_models.roles.pick_discovery")}</option>
      )}
      {row.id === "embedding" && !row.current && (
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
        <optgroup label={t("local_models.roles.pick_group_others")}>
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
