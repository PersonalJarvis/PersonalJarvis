/**
 * The model picker of one job: a list that says, per download, whether it
 * can do THIS job — and why not when it cannot.
 *
 * A native <select> could show one string per row, so the old picker could
 * name neither size nor capabilities nor a verdict, and the verdict itself
 * was derived twice (backend `qualifying`, client `splitChoices`) with
 * precedence rules between them. This picker reads the backend's ONE
 * verdict per (job, model) — `row.choices`, from `ollama_roles.fit_for` —
 * and lays it out in four groups:
 *
 *   * fits     — can do the job on this machine;
 *   * slow     — can do it, at a cost the job feels (over the size class,
 *                spilling past the graphics memory);
 *   * unknown  — Jarvis could not read the manifest; offered, not called a fit;
 *   * unfit    — lacks a capability, too small a window, or too big for the
 *                card. Shown greyed WITH the reason, never hidden: a model
 *                that silently vanishes from a list reads as a bug
 *                (BUG-188), a model that says "no tool calls" teaches.
 *
 * A fifth group offers the shortlist's downloads that would do the job here
 * (`row.downloads`), so filling a job never needs another tab. The
 * configured tag stays listed even when it is gone from disk, or the button
 * would show a value the list cannot express.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Download, Search } from "lucide-react";

import {
  canonicalModelName,
  type LocalModelRow,
  type RoleChoice,
  type RoleDownload,
  type RoleRow,
} from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { formatContext, formatGb } from "./localModelsFormat";
import { capabilityChips, findModel, fitTone, modelLabel, type FitTone } from "./modelNames";

export interface RolePickerProps {
  row: RoleRow;
  /** Every download the inventory knows; empty while the server is silent. */
  models: LocalModelRow[];
  /** True while a write for this row is in flight. */
  disabled?: boolean;
  onPick: (model: string) => void;
  /** Downloads a shortlist pick and assigns it; the group hides without it. */
  onInstall?: (model: string) => void;
  className?: string;
}

export type PickerGroupId = "fits" | "slow" | "unknown" | "unfit" | "downloads";

export interface PickerEntry {
  tag: string;
  label: string;
  /** "5.2 GB · Q4_K_M · 256k · tools · thinking" */
  facts: string;
  fit: string;
  reason: string;
  /** Not selectable (unfit); still listed with its reason. */
  disabled: boolean;
  installed: boolean;
  download?: RoleDownload;
}

export interface PickerGroup {
  id: PickerGroupId;
  entries: PickerEntry[];
}

const GROUP_ORDER: readonly PickerGroupId[] = ["fits", "slow", "unknown", "unfit", "downloads"];

function factsOf(model: LocalModelRow | null): string {
  if (!model) return "";
  return [
    formatGb(model.size_bytes),
    model.quant_label || model.quantization_level,
    model.context_length ? formatContext(model.context_length) : "",
    ...capabilityChips(model),
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * The groups the list shows, from the backend's verdicts. A payload without
 * `choices` (an older snapshot, a silent server) falls back to every
 * installed download under "unknown", so the list is never empty while
 * something is on disk — and never a single option (BUG-188).
 */
export function buildGroups(
  row: RoleRow,
  models: readonly LocalModelRow[],
  query = "",
): PickerGroup[] {
  const seen = new Set<string>();
  const byGroup: Record<PickerGroupId, PickerEntry[]> = {
    fits: [],
    slow: [],
    unknown: [],
    unfit: [],
    downloads: [],
  };
  const choices: RoleChoice[] =
    row.choices && row.choices.length > 0
      ? row.choices
      : models.map((m) => ({ tag: m.name, fit: m.probed ? "unknown" : "unknown", reason: "" }));
  for (const choice of choices) {
    const key = canonicalModelName(choice.tag);
    if (!choice.tag || seen.has(key)) continue;
    seen.add(key);
    const model = findModel(models, choice.tag);
    const fit = choice.fit;
    const group: PickerGroupId =
      fit === "fits" ? "fits" : fit === "slow" ? "slow" : fit === "unfit" ? "unfit" : "unknown";
    byGroup[group].push({
      tag: choice.tag,
      label: modelLabel(model, choice.tag),
      facts: factsOf(model),
      fit,
      reason: choice.reason,
      disabled: fit === "unfit",
      installed: model !== null,
    });
  }
  if (row.current && !seen.has(canonicalModelName(row.current))) {
    seen.add(canonicalModelName(row.current));
    const model = findModel(models, row.current);
    byGroup.unknown.push({
      tag: row.current,
      label: modelLabel(model, row.current),
      facts: factsOf(model),
      fit: model ? "unknown" : "absent",
      reason: "",
      disabled: false,
      installed: model !== null,
    });
  }
  for (const download of row.downloads ?? []) {
    const key = canonicalModelName(download.tag);
    if (seen.has(key)) continue;
    seen.add(key);
    byGroup.downloads.push({
      tag: download.tag,
      label: download.label || download.tag,
      facts: [download.size_gb > 0 ? `${download.size_gb.toFixed(1)} GB` : "", download.note]
        .filter(Boolean)
        .join(" · "),
      fit: download.fit,
      reason: "",
      disabled: false,
      installed: false,
      download,
    });
  }
  const needle = query.trim().toLowerCase();
  const matches = (e: PickerEntry) =>
    !needle || e.tag.toLowerCase().includes(needle) || e.label.toLowerCase().includes(needle);
  return GROUP_ORDER.map((id) => ({ id, entries: byGroup[id].filter(matches) })).filter(
    (g) => g.entries.length > 0,
  );
}

const TONE_TEXT: Record<FitTone, string> = {
  ok: "text-muted-foreground",
  warn: "text-foreground",
  bad: "text-destructive",
  muted: "text-muted-foreground",
};

export function RolePicker({
  row,
  models,
  disabled,
  onPick,
  onInstall,
  className,
}: RolePickerProps) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);

  const groups = useMemo(() => buildGroups(row, models, query), [row, models, query]);
  const current = findModel(models, row.current);
  // The voice server and the wiki cannot discover a model; the brain roles can.
  const discovers = row.id !== "embedding" && row.id !== "voice";
  const roleName = t(row.label_key);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);

  useEffect(() => {
    if (!open) return;
    search.current?.focus();
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  const choose = (tag: string) => {
    close();
    if (canonicalModelName(tag) !== canonicalModelName(row.current)) onPick(tag);
  };

  const buttonLabel = row.current
    ? modelLabel(current, row.current)
    : discovers
      ? t("local_models.roles.pick_discovery")
      : t("local_models.roles.pick_none");

  const groupLabel = (id: PickerGroupId) => {
    switch (id) {
      case "fits":
        return t("local_models.roles.pick_group_fits");
      case "slow":
        return row.max_size_gb != null && row.max_size_gb > 0
          ? fill(t("local_models.roles.pick_group_over_size"), { gb: String(row.max_size_gb) })
          : t("local_models.roles.pick_group_slow");
      case "unknown":
        return t("local_models.roles.pick_group_unknown");
      case "unfit":
        return t("local_models.roles.pick_group_unfit");
      case "downloads":
        return t("local_models.roles.pick_group_downloads");
    }
  };

  const listId = `role-picker-list-${row.id}`;

  return (
    <div ref={root} className={cn("relative", className)}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={fill(t("local_models.roles.pick_label"), { role: roleName })}
        disabled={disabled}
        onClick={() => (open ? close() : setOpen(true))}
        data-testid={`role-picker-${row.id}`}
        data-value={row.current}
        className={cn(
          "flex h-9 w-full items-center justify-between gap-2 rounded-lg border border-border bg-background/60 px-2.5 text-left text-sm text-foreground",
          "transition-colors hover:border-border/80 focus:outline-none focus:ring-1 focus:ring-primary",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
      >
        <span className="truncate">{buttonLabel}</span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      </button>

      {open && (
        <div
          className="absolute left-0 right-0 top-full z-30 mt-1 overflow-hidden rounded-xl border border-border bg-card"
          data-testid={`role-picker-popover-${row.id}`}
        >
          <label className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs text-muted-foreground">
            <Search className="h-3.5 w-3.5" />
            <input
              ref={search}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("local_models.roles.pick_search")}
              aria-label={t("local_models.roles.pick_search")}
              className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
              data-testid={`role-picker-search-${row.id}`}
            />
          </label>
          <ul
            id={listId}
            role="listbox"
            aria-label={fill(t("local_models.roles.pick_label"), { role: roleName })}
            className="max-h-80 overflow-y-auto py-1"
          >
            {discovers && !query && (
              <Option
                selected={row.current === ""}
                onSelect={() => choose("")}
                testId="role-option-discovery"
              >
                <span className="text-sm text-foreground">
                  {t("local_models.roles.pick_discovery")}
                </span>
                <span className="text-[11px] text-muted-foreground">
                  {t("local_models.roles.discovery")}
                </span>
              </Option>
            )}
            {groups.length === 0 && (
              <li className="px-3 py-2 text-xs text-muted-foreground" role="presentation">
                {t("local_models.roles.pick_empty")}
              </li>
            )}
            {groups.map((group) => (
              <li key={group.id} role="presentation" data-testid={`role-picker-group-${group.id}`}>
                <p className="px-3 pb-0.5 pt-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  {groupLabel(group.id)}
                </p>
                <ul role="group" aria-label={groupLabel(group.id)}>
                  {group.entries.map((entry) => {
                    const selected =
                      !!row.current &&
                      canonicalModelName(entry.tag) === canonicalModelName(row.current);
                    const tone = fitTone(entry.fit);
                    const isDownload = group.id === "downloads";
                    return (
                      <Option
                        key={entry.tag}
                        selected={selected}
                        disabled={entry.disabled || (isDownload && !onInstall)}
                        onSelect={() => {
                          if (isDownload) {
                            close();
                            onInstall?.(entry.tag);
                          } else {
                            choose(entry.tag);
                          }
                        }}
                        testId={`role-option-${entry.tag}`}
                      >
                        <span className="flex min-w-0 items-baseline gap-2">
                          <span className="truncate text-sm text-foreground">{entry.label}</span>
                          <span className="truncate font-mono text-[10px] text-muted-foreground/80">
                            {entry.tag}
                          </span>
                          {!entry.installed && !isDownload && (
                            <span className="text-[10px] text-muted-foreground">
                              {t("local_models.roles.pick_missing_suffix")}
                            </span>
                          )}
                        </span>
                        <span className="flex items-center justify-between gap-2">
                          <span className="truncate text-[11px] text-muted-foreground">
                            {entry.facts}
                          </span>
                          <span
                            className={cn(
                              "shrink-0 text-[11px]",
                              isDownload ? "text-primary" : TONE_TEXT[tone],
                            )}
                            data-testid={`role-option-verdict-${entry.tag}`}
                          >
                            {isDownload ? (
                              <span className="inline-flex items-center gap-1">
                                <Download className="h-3 w-3" />
                                {t("local_models.roles.pick_download")}
                              </span>
                            ) : selected ? (
                              <span className="inline-flex items-center gap-1">
                                <Check className="h-3 w-3" />
                                {t("local_models.roles.pick_current")}
                              </span>
                            ) : entry.reason ? (
                              entry.reason
                            ) : entry.fit === "fits" ? (
                              t("local_models.roles.pick_fits")
                            ) : (
                              ""
                            )}
                          </span>
                        </span>
                      </Option>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Option({
  children,
  selected,
  disabled = false,
  onSelect,
  testId,
}: {
  children: React.ReactNode;
  selected: boolean;
  disabled?: boolean;
  onSelect: () => void;
  testId: string;
}) {
  return (
    <li
      role="option"
      aria-selected={selected}
      aria-disabled={disabled || undefined}
      tabIndex={disabled ? -1 : 0}
      data-testid={testId}
      onClick={() => {
        if (!disabled) onSelect();
      }}
      onKeyDown={(event) => {
        if (disabled) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "grid gap-0.5 px-3 py-1.5 outline-none",
        disabled
          ? "cursor-not-allowed opacity-50"
          : "cursor-pointer hover:bg-sheen/[0.05] focus-visible:bg-sheen/[0.07]",
        selected && "bg-primary/[0.06]",
      )}
    >
      {children}
    </li>
  );
}
