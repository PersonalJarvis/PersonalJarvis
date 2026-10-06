import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  Check,
  Eye,
  PenLine,
  Search,
  ShieldCheck,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BrandedSelect, type BrandedSelectOption } from "@/components/ui/select";
import { fill, useT, useUiLanguage } from "@/i18n";
import {
  appActionsApi,
  type ActionKind,
  type ActionMode,
  type ActionTier,
  type AppAction,
  type AppActionCatalog,
  type AppActionHistoryRow,
} from "@/lib/appActionsApi";
import { cn } from "@/lib/utils";

/**
 * Settings > Jarvis actions. Every action of the app Jarvis can run (its
 * find-app-action / run-app-action tools and the curated voice commands), and
 * the person's choice per action: Default, Allowed, Ask, Blocked. The backend
 * applies the choice as the call's risk tier, so Blocked is enforced there,
 * not here.
 *
 * The page reads top to bottom as three answers: how much Jarvis may do on
 * its own (the overview, whose groups double as filters), what it actually
 * did (the activity timeline), and where to change it (the permission list).
 */

const KEYS = {
  catalog: ["app-actions", "catalog"] as const,
  history: ["app-actions", "history"] as const,
};
const PAGE = 120;
const HISTORY_LIMIT = 200;
const HISTORY_PAGE = 14;
const STRIP_DAYS = 14;
const ALL = "__all__";
const DAY_MS = 86_400_000;

type Choice = "default" | ActionMode;
const CHOICES: readonly Choice[] = ["default", "allow", "ask", "block"];
const TIERS: readonly ActionTier[] = ["safe", "monitor", "ask", "block"];

/** One status hue per effective tier; reading is neutral, not a signal. */
const TIER_DOT: Record<ActionTier, string> = {
  safe: "bg-foreground/35",
  monitor: "bg-accent",
  ask: "bg-warning",
  block: "bg-destructive",
};
const CHOICE_DOT: Record<Choice, string> = {
  default: "bg-foreground/35",
  allow: "bg-success",
  ask: "bg-warning",
  block: "bg-destructive",
};

const KIND_ICON: Record<ActionKind, LucideIcon> = { read: Eye, change: PenLine, delete: Trash2 };

function kindOf(method: string): ActionKind {
  const m = method.toUpperCase();
  if (m === "GET") return "read";
  return m === "DELETE" ? "delete" : "change";
}

const ACRONYMS: Record<string, string> = {
  ide: "IDE",
  mcp: "MCP",
  mcps: "MCPs",
  cli: "CLI",
  clis: "CLIs",
  ws: "WS",
  pty: "PTY",
  api: "API",
  ui: "UI",
};

/** `agentic-ide-git` → "Agentic IDE git": OpenAPI tags, read as words. */
function humanArea(area: string): string {
  const words = area
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase());
  const text = words.join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-foreground/10", className)} />;
}

// ---------------------------------------------------------------- overview

function Overview({
  catalog,
  history,
  tierFilter,
  onTierFilter,
}: {
  catalog: AppActionCatalog | undefined;
  history: AppActionHistoryRow[] | undefined;
  tierFilter: ActionTier | null;
  onTierFilter: (tier: ActionTier | null) => void;
}) {
  const t = useT();
  const counts = useMemo(() => {
    const out: Record<ActionTier, number> = { safe: 0, monitor: 0, ask: 0, block: 0 };
    for (const a of catalog?.actions ?? []) out[a.tier] += 1;
    return out;
  }, [catalog?.actions]);
  const total = catalog?.count ?? 0;

  return (
    <section
      data-testid="jarvis-actions-overview"
      className="grid gap-px overflow-hidden rounded-lg border border-border bg-border lg:grid-cols-[minmax(0,1fr)_340px]"
    >
      <div className="bg-card p-5">
        {catalog ? (
          <>
            <p className="font-display text-xl font-semibold text-foreground-strong">
              {fill(t("jarvis_actions.overview_headline"), { count: total.toLocaleString() })}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">{t("jarvis_actions.overview_hint")}</p>
            <div className="mt-5 flex h-2 w-full gap-0.5 overflow-hidden rounded-full" aria-hidden>
              {TIERS.filter((tier) => counts[tier] > 0).map((tier) => (
                <span
                  key={tier}
                  className={cn(
                    "h-full transition-opacity",
                    TIER_DOT[tier],
                    tierFilter && tierFilter !== tier && "opacity-25",
                  )}
                  style={{ width: `${(counts[tier] / Math.max(total, 1)) * 100}%`, minWidth: 6 }}
                />
              ))}
            </div>
          </>
        ) : (
          <>
            <Skeleton className="h-7 w-80 max-w-full" />
            <Skeleton className="mt-2 h-4 w-96 max-w-full" />
            <Skeleton className="mt-5 h-2 w-full rounded-full" />
          </>
        )}
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {TIERS.map((tier) => {
            const active = tierFilter === tier;
            return (
              <button
                key={tier}
                type="button"
                aria-pressed={active}
                disabled={!catalog}
                onClick={() => onTierFilter(active ? null : tier)}
                className={cn(
                  "rounded-md border px-3 py-2.5 text-left transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  active
                    ? "border-accent bg-accent-soft"
                    : "border-border hover:border-border-strong hover:bg-secondary",
                )}
              >
                <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                  <span className={cn("h-2 w-2 shrink-0 rounded-full", TIER_DOT[tier])} />
                  <span className="truncate">{t(`jarvis_actions.group_${tier}`)}</span>
                </span>
                {catalog ? (
                  <span className="mt-1 block text-xl font-semibold tabular-nums text-foreground-strong">
                    {counts[tier].toLocaleString()}
                  </span>
                ) : (
                  <Skeleton className="mt-1.5 h-6 w-12" />
                )}
                <span className="mt-0.5 block text-xs text-foreground-faint">
                  {t(`jarvis_actions.group_${tier}_hint`)}
                </span>
              </button>
            );
          })}
        </div>
      </div>
      <ActivityStrip history={history} />
    </section>
  );
}

/**
 * Fourteen day columns, each stacked ran / failed / blocked. The history keeps
 * a bounded number of entries, so days older than the oldest kept entry are
 * drawn as "unknown", never as an invented quiet day.
 */
function ActivityStrip({ history }: { history: AppActionHistoryRow[] | undefined }) {
  const t = useT();
  const lang = useUiLanguage();
  const days = useMemo(() => {
    const today = startOfDay(Date.now());
    const rows = history ?? [];
    const capped = rows.length >= HISTORY_LIMIT;
    const knownFrom = capped ? startOfDay(rows[rows.length - 1].at * 1000) : -Infinity;
    return Array.from({ length: STRIP_DAYS }, (_, i) => {
      const start = today - (STRIP_DAYS - 1 - i) * DAY_MS;
      const tally = { ran: 0, failed: 0, blocked: 0 };
      for (const r of rows) {
        const at = r.at * 1000;
        if (at >= start && at < start + DAY_MS) tally[r.outcome] += 1;
      }
      return { start, known: start >= knownFrom, ...tally };
    });
  }, [history]);
  const totals = days.reduce(
    (acc, d) => ({ ran: acc.ran + d.ran, failed: acc.failed + d.failed, blocked: acc.blocked + d.blocked }),
    { ran: 0, failed: 0, blocked: 0 },
  );
  const peak = Math.max(1, ...days.map((d) => d.ran + d.failed + d.blocked));
  const dateLabel = (ms: number) =>
    new Date(ms).toLocaleDateString(lang, { day: "numeric", month: "short" });

  return (
    <div className="flex flex-col bg-card p-5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium text-foreground">{t("jarvis_actions.activity_days")}</span>
        {history ? (
          <span className="text-xl font-semibold tabular-nums text-foreground-strong">
            {(totals.ran + totals.failed + totals.blocked).toLocaleString()}
          </span>
        ) : (
          <Skeleton className="h-6 w-10" />
        )}
      </div>
      <div className="mt-4 flex h-24 items-end gap-1" role="img" aria-label={t("jarvis_actions.activity_days")}>
        {days.map((d) => {
          const sum = d.ran + d.failed + d.blocked;
          const label = `${dateLabel(d.start)} · ${
            d.known
              ? fill(t("jarvis_actions.activity_day_tooltip"), {
                  ran: d.ran,
                  failed: d.failed,
                  blocked: d.blocked,
                })
              : t("jarvis_actions.activity_unknown")
          }`;
          return (
            <div key={d.start} title={label} className="flex h-full flex-1 flex-col justify-end">
              {!history ? (
                <Skeleton className="h-1/3 w-full rounded-sm" />
              ) : !d.known ? (
                <div className="h-1 w-full rounded-full border border-dashed border-border-strong" />
              ) : sum === 0 ? (
                <div className="h-1 w-full rounded-full bg-foreground/10" />
              ) : (
                <div
                  className="flex w-full flex-col-reverse overflow-hidden rounded-sm"
                  style={{ height: `${Math.max(8, (sum / peak) * 100)}%` }}
                >
                  <span className="bg-success/80" style={{ flexGrow: d.ran }} />
                  <span className="bg-warning" style={{ flexGrow: d.failed }} />
                  <span className="bg-destructive" style={{ flexGrow: d.blocked }} />
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex justify-between text-xs text-foreground-faint">
        <span>{dateLabel(days[0].start)}</span>
        <span>{dateLabel(days[days.length - 1].start)}</span>
      </div>
      <div className="mt-auto flex flex-wrap gap-x-4 gap-y-1 pt-4 text-xs text-muted-foreground">
        {(["ran", "failed", "blocked"] as const).map((o) => (
          <span key={o} className="inline-flex items-center gap-1.5">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                o === "ran" && "bg-success/80",
                o === "failed" && "bg-warning",
                o === "blocked" && "bg-destructive",
              )}
            />
            <span className="tabular-nums text-foreground">{totals[o]}</span>
            {t(`jarvis_actions.outcome_${o}`)}
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- activity

const OUTCOME_STYLE: Record<
  AppActionHistoryRow["outcome"],
  { icon: LucideIcon; badge: string; text: string }
> = {
  ran: { icon: Check, badge: "bg-success/15 text-success", text: "text-success" },
  failed: { icon: AlertTriangle, badge: "bg-warning/15 text-warning", text: "text-warning" },
  blocked: { icon: Ban, badge: "bg-destructive/15 text-destructive", text: "text-destructive" },
};

interface ActivityItem extends AppActionHistoryRow {
  repeat: number;
}

/** Consecutive identical runs on one day read as one line with a count. */
function collapse(rows: AppActionHistoryRow[]): ActivityItem[] {
  const out: ActivityItem[] = [];
  for (const row of rows) {
    const last = out[out.length - 1];
    if (
      last &&
      last.action === row.action &&
      last.outcome === row.outcome &&
      startOfDay(last.at * 1000) === startOfDay(row.at * 1000)
    ) {
      last.repeat += 1;
      continue;
    }
    out.push({ ...row, repeat: 1 });
  }
  return out;
}

function ActivityTimeline({
  history,
  isLoading,
  onOpen,
}: {
  history: AppActionHistoryRow[] | undefined;
  isLoading: boolean;
  onOpen: (catalogId: string) => void;
}) {
  const t = useT();
  const lang = useUiLanguage();
  const [limit, setLimit] = useState(HISTORY_PAGE);
  const items = useMemo(() => collapse(history ?? []), [history]);
  const shown = items.slice(0, limit);

  const days = useMemo(() => {
    const groups: { day: number; items: ActivityItem[] }[] = [];
    for (const item of shown) {
      const day = startOfDay(item.at * 1000);
      const group = groups[groups.length - 1];
      if (group && group.day === day) group.items.push(item);
      else groups.push({ day, items: [item] });
    }
    return groups;
  }, [shown]);

  const dayLabel = (day: number) => {
    const diff = Math.round((startOfDay(Date.now()) - day) / DAY_MS);
    if (diff <= 1) {
      const text = new Intl.RelativeTimeFormat(lang, { numeric: "auto" }).format(-diff, "day");
      return text.charAt(0).toUpperCase() + text.slice(1);
    }
    return new Date(day).toLocaleDateString(lang, { weekday: "long", day: "numeric", month: "long" });
  };
  const time = (at: number) =>
    new Date(at * 1000).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });

  return (
    <section
      data-testid="jarvis-actions-history"
      className="flex min-h-0 flex-col rounded-lg border border-border bg-card"
    >
      <header className="border-b border-border px-5 py-4">
        <h2 className="text-base font-semibold text-foreground-strong">
          {t("jarvis_actions.history_title")}
        </h2>
        <p className="mt-0.5 text-sm text-muted-foreground">{t("jarvis_actions.history_hint")}</p>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 scrollbar-jarvis">
        {isLoading ? (
          <div className="space-y-4">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="flex gap-3">
                <Skeleton className="h-7 w-7 rounded-full" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-1/3" />
                </div>
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center py-10 text-center">
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-secondary text-muted-foreground">
              <ShieldCheck className="h-5 w-5" />
            </span>
            <p className="mt-3 max-w-xs text-sm text-muted-foreground">
              {t("jarvis_actions.history_empty")}
            </p>
          </div>
        ) : (
          <div className="space-y-5">
            {days.map((group) => (
              <div key={group.day}>
                <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-foreground-faint">
                  {dayLabel(group.day)}
                </h3>
                <ol className="relative">
                  {group.items.map((item, index) => {
                    const style = OUTCOME_STYLE[item.outcome];
                    const Icon = style.icon;
                    const last = index === group.items.length - 1;
                    const clickable = Boolean(item.catalog_id);
                    const meta = [
                      item.area ? humanArea(item.area) : null,
                      item.kind ? t(`jarvis_actions.did_${item.kind}`) : null,
                    ].filter(Boolean);
                    const body = (
                      <>
                        <span className="relative flex w-7 shrink-0 justify-center">
                          {!last && (
                            <span
                              aria-hidden
                              className="absolute left-1/2 top-7 h-[calc(100%-4px)] w-px -translate-x-1/2 bg-border"
                            />
                          )}
                          <span
                            className={cn(
                              "relative flex h-7 w-7 items-center justify-center rounded-full",
                              style.badge,
                            )}
                          >
                            <Icon className="h-3.5 w-3.5" strokeWidth={2.25} />
                          </span>
                        </span>
                        <span className="min-w-0 flex-1 pb-4">
                          <span className="flex items-baseline gap-2">
                            <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                              {item.title}
                            </span>
                            {item.repeat > 1 && (
                              <span className="shrink-0 rounded-sm bg-secondary px-1.5 text-xs tabular-nums text-muted-foreground">
                                ×{item.repeat}
                              </span>
                            )}
                            <span className="shrink-0 text-xs tabular-nums text-foreground-faint">
                              {time(item.at)}
                            </span>
                          </span>
                          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                            <span className={style.text}>{t(`jarvis_actions.outcome_${item.outcome}`)}</span>
                            {meta.map((m) => (
                              <span key={m}> · {m}</span>
                            ))}
                          </span>
                          {item.outcome !== "ran" && item.detail && (
                            <span
                              className="mt-1.5 block line-clamp-2 rounded-md bg-secondary px-2 py-1 text-xs text-foreground-secondary"
                              title={item.detail}
                            >
                              {item.detail}
                            </span>
                          )}
                        </span>
                      </>
                    );
                    return (
                      <li key={`${item.at}-${item.action}`}>
                        {clickable ? (
                          <button
                            type="button"
                            title={t("jarvis_actions.open_permission")}
                            onClick={() => onOpen(item.catalog_id as string)}
                            className="-mx-2 flex w-[calc(100%+1rem)] gap-3 rounded-md px-2 pt-1 text-left transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            {body}
                          </button>
                        ) : (
                          <div className="flex gap-3 pt-1">{body}</div>
                        )}
                      </li>
                    );
                  })}
                </ol>
              </div>
            ))}
            {items.length > shown.length && (
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => setLimit((n) => n + HISTORY_PAGE * 2)}
              >
                {t("jarvis_actions.show_older")}
              </Button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- permissions

function ModeControl({
  action,
  onChange,
}: {
  action: AppAction;
  onChange: (mode: ActionMode | null) => void;
}) {
  const t = useT();
  const current: Choice = action.mode ?? "default";
  return (
    <div
      role="radiogroup"
      aria-label={action.title}
      className="inline-flex shrink-0 rounded-md bg-secondary p-0.5"
    >
      {CHOICES.map((value) => {
        const active = current === value;
        const hint =
          value === "default"
            ? t(`jarvis_actions.tier_${action.default_tier}`)
            : t(`jarvis_actions.mode_${value}_hint`);
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            title={hint}
            onClick={() => onChange(value === "default" ? null : value)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active
                ? "bg-card text-foreground-strong ring-1 ring-border-strong"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "h-1.5 w-1.5 rounded-full transition-opacity",
                CHOICE_DOT[value],
                !active && "opacity-40",
              )}
            />
            {t(`jarvis_actions.mode_${value}`)}
          </button>
        );
      })}
    </div>
  );
}

function ActionRow({
  action,
  onChange,
}: {
  action: AppAction;
  onChange: (mode: ActionMode | null) => void;
}) {
  const t = useT();
  const kind = kindOf(action.method);
  const Icon = KIND_ICON[kind];
  return (
    <li
      data-testid="jarvis-action-row"
      className="relative flex flex-wrap items-center gap-x-4 gap-y-3 px-5 py-3.5"
    >
      {action.mode && (
        <span aria-hidden className="absolute inset-y-3 left-0 w-0.5 rounded-full bg-accent" />
      )}
      <span
        aria-hidden
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
          action.dangerous ? "bg-warning/15 text-warning" : "bg-secondary text-muted-foreground",
        )}
      >
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1 basis-64">
        <div className="text-sm font-medium text-foreground">{action.title}</div>
        {action.description && action.description !== action.title && (
          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{action.description}</p>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-foreground-faint">
          <span className="inline-flex items-center gap-1.5">
            <span className={cn("h-1.5 w-1.5 rounded-full", TIER_DOT[action.default_tier])} />
            {t(`jarvis_actions.tier_${action.default_tier}`)}
          </span>
          {action.dangerous && (
            <span className="inline-flex items-center gap-1 text-warning">
              <AlertTriangle className="h-3 w-3" />
              {t("jarvis_actions.dangerous")}
            </span>
          )}
          {action.mode && (
            <span className="font-medium text-accent">{t("jarvis_actions.set_by_you")}</span>
          )}
        </div>
      </div>
      <ModeControl action={action} onChange={onChange} />
    </li>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 items-center gap-2 rounded-md border px-3 text-sm transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-accent bg-accent-soft text-foreground-strong"
          : "border-border text-muted-foreground hover:bg-secondary hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

export function JarvisActionsView() {
  const t = useT();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [area, setArea] = useState(ALL);
  const [changedOnly, setChangedOnly] = useState(false);
  const [tierFilter, setTierFilter] = useState<ActionTier | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const catalog = useQuery({ queryKey: KEYS.catalog, queryFn: appActionsApi.list });
  const history = useQuery({
    queryKey: KEYS.history,
    queryFn: () => appActionsApi.history(HISTORY_LIMIT),
  });

  const setMode = useMutation({
    mutationFn: ({ id, mode }: { id: string; mode: ActionMode | null }) =>
      appActionsApi.setMode(id, mode),
    onSuccess: (saved) => {
      queryClient.setQueryData<AppActionCatalog>(KEYS.catalog, (old) =>
        old
          ? {
              ...old,
              actions: old.actions.map((a) =>
                a.id === saved.id ? { ...a, mode: saved.mode, tier: saved.tier } : a,
              ),
            }
          : old,
      );
    },
  });

  const areaOptions = useMemo<BrandedSelectOption[]>(
    () => [
      { value: ALL, label: t("jarvis_actions.area_all") },
      ...(catalog.data?.areas ?? []).map((a) => ({ value: a, label: humanArea(a) })),
    ],
    [catalog.data?.areas, t],
  );

  const changedCount = useMemo(
    () => (catalog.data?.actions ?? []).filter((a) => a.mode).length,
    [catalog.data?.actions],
  );

  const filtered = useMemo(() => {
    const actions = catalog.data?.actions ?? [];
    if (focusId) return actions.filter((a) => a.id === focusId);
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return actions.filter((a) => {
      if (area !== ALL && a.area !== area) return false;
      if (changedOnly && !a.mode) return false;
      if (tierFilter && a.tier !== tierFilter) return false;
      const text =
        `${a.title} ${a.description} ${a.area} ${humanArea(a.area)} ${a.path}`.toLowerCase();
      return words.every((w) => text.includes(w));
    });
  }, [catalog.data?.actions, query, area, changedOnly, tierFilter, focusId]);

  const shown = filtered.slice(0, limit);
  const groups = useMemo(() => {
    const out: { area: string; actions: AppAction[] }[] = [];
    for (const action of shown) {
      const group = out[out.length - 1];
      if (group && group.area === action.area) group.actions.push(action);
      else out.push({ area: action.area, actions: [action] });
    }
    return out;
  }, [shown]);
  const focused = focusId ? catalog.data?.actions.find((a) => a.id === focusId) : undefined;
  const resetPaging = () => setLimit(PAGE);

  return (
    <div
      data-testid="jarvis-actions-view"
      className="flex h-full flex-col overflow-y-auto bg-background px-8 pb-10 scrollbar-jarvis"
    >
      <div className="w-full max-w-[1400px] space-y-5">
        <PageHeader
          icon={<ShieldCheck className="h-5 w-5" />}
          title={t("jarvis_actions.title")}
          description={t("jarvis_actions.subtitle")}
        />

        <Overview
          catalog={catalog.data}
          history={history.data?.history}
          tierFilter={tierFilter}
          onTierFilter={(tier) => {
            setTierFilter(tier);
            setFocusId(null);
            resetPaging();
          }}
        />

        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
          <section className="min-w-0 rounded-lg border border-border bg-card">
            <header className="space-y-3 border-b border-border px-5 py-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div>
                  <h2 className="text-base font-semibold text-foreground-strong">
                    {t("jarvis_actions.permissions_title")}
                  </h2>
                  <p className="mt-0.5 text-sm text-muted-foreground">
                    {t("jarvis_actions.permissions_hint")}
                  </p>
                </div>
                {catalog.data && (
                  <span className="text-xs tabular-nums text-foreground-faint">
                    {fill(t("jarvis_actions.count"), {
                      shown: shown.length,
                      total: filtered.length,
                    })}
                  </span>
                )}
              </div>
              {focused ? (
                <div className="flex items-center gap-2 rounded-md bg-accent-soft px-3 py-2 text-sm text-foreground">
                  <span className="min-w-0 flex-1 truncate">
                    {fill(t("jarvis_actions.focus_label"), { title: focused.title })}
                  </span>
                  <Button variant="ghost" size="sm" onClick={() => setFocusId(null)}>
                    <X className="h-3.5 w-3.5" />
                    {t("jarvis_actions.focus_clear")}
                  </Button>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative min-w-[200px] flex-1 basis-64">
                    <Search
                      aria-hidden
                      className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-faint"
                    />
                    <Input
                      value={query}
                      onChange={(e) => {
                        setQuery(e.target.value);
                        resetPaging();
                      }}
                      placeholder={t("jarvis_actions.search_placeholder")}
                      aria-label={t("jarvis_actions.search_placeholder")}
                      className="pl-9"
                    />
                  </div>
                  <BrandedSelect
                    value={area}
                    options={areaOptions}
                    onValueChange={(value) => {
                      setArea(value);
                      resetPaging();
                    }}
                    ariaLabel={t("jarvis_actions.area_all")}
                    className="w-52"
                  />
                  <FilterChip
                    active={changedOnly}
                    onClick={() => {
                      setChangedOnly((v) => !v);
                      resetPaging();
                    }}
                  >
                    {t("jarvis_actions.changed_only")}
                    <span className="tabular-nums text-foreground-faint">{changedCount}</span>
                  </FilterChip>
                  {tierFilter && (
                    <FilterChip active onClick={() => setTierFilter(null)}>
                      <span className={cn("h-2 w-2 rounded-full", TIER_DOT[tierFilter])} />
                      {t(`jarvis_actions.group_${tierFilter}`)}
                      <X className="h-3.5 w-3.5" />
                    </FilterChip>
                  )}
                </div>
              )}
              {catalog.isError && (
                <p className="text-sm text-destructive">{t("jarvis_actions.load_error")}</p>
              )}
              {setMode.isError && (
                <p className="text-sm text-destructive">{t("jarvis_actions.save_error")}</p>
              )}
            </header>

            {catalog.isLoading ? (
              <div className="divide-y divide-border">
                {Array.from({ length: 6 }, (_, i) => (
                  <div key={i} className="flex items-center gap-4 px-5 py-4">
                    <Skeleton className="h-8 w-8" />
                    <div className="flex-1 space-y-1.5">
                      <Skeleton className="h-4 w-1/3" />
                      <Skeleton className="h-3 w-2/3" />
                    </div>
                    <Skeleton className="h-7 w-64" />
                  </div>
                ))}
              </div>
            ) : catalog.data && filtered.length === 0 ? (
              <p className="px-5 py-12 text-center text-sm text-muted-foreground">
                {t("jarvis_actions.no_results")}
              </p>
            ) : (
              groups.map((group) => (
                <div key={group.area}>
                  <h3 className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-card/95 px-5 py-2 text-xs font-medium uppercase tracking-wide text-foreground-faint backdrop-blur">
                    <span>{group.area ? humanArea(group.area) : t("jarvis_actions.area_other")}</span>
                    <span className="tabular-nums">{group.actions.length}</span>
                  </h3>
                  <ul className="divide-y divide-border">
                    {group.actions.map((action) => (
                      <ActionRow
                        key={action.id}
                        action={action}
                        onChange={(mode) => setMode.mutate({ id: action.id, mode })}
                      />
                    ))}
                  </ul>
                </div>
              ))
            )}

            {filtered.length > shown.length && (
              <div className="flex justify-center border-t border-border p-4">
                <Button variant="outline" size="sm" onClick={() => setLimit((n) => n + PAGE)}>
                  {t("jarvis_actions.show_more")}
                </Button>
              </div>
            )}
          </section>

          <div className="xl:sticky xl:top-6 xl:flex xl:max-h-[calc(100vh-3rem)] xl:flex-col">
            <ActivityTimeline
              history={history.data?.history}
              isLoading={history.isLoading}
              onOpen={(id) => {
                setFocusId(id);
                setTierFilter(null);
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
