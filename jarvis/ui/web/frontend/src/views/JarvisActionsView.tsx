import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BrandedSelect, type BrandedSelectOption } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n";
import {
  appActionsApi,
  type ActionMode,
  type AppAction,
  type AppActionCatalog,
} from "@/lib/appActionsApi";
import { cn } from "@/lib/utils";

/**
 * Settings > Jarvis actions. Every action of the app Jarvis can run (its
 * find-app-action / run-app-action tools and the curated voice commands), and
 * the person's choice per action: Default, Allowed, Ask, Blocked. The backend
 * applies the choice as the call's risk tier, so Blocked is enforced there,
 * not here.
 */

const KEYS = {
  catalog: ["app-actions", "catalog"] as const,
  history: ["app-actions", "history"] as const,
};
const PAGE = 120;
const ALL = "__all__";

type Choice = "default" | ActionMode;
const CHOICES: readonly { value: Choice; labelKey: string }[] = [
  { value: "default", labelKey: "jarvis_actions.mode_default" },
  { value: "allow", labelKey: "jarvis_actions.mode_allow" },
  { value: "ask", labelKey: "jarvis_actions.mode_ask" },
  { value: "block", labelKey: "jarvis_actions.mode_block" },
];

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
      className="inline-flex shrink-0 rounded-lg border border-border bg-background p-0.5"
    >
      {CHOICES.map(({ value, labelKey }) => {
        const active = current === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(value === "default" ? null : value)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              active
                ? value === "block"
                  ? "bg-destructive text-destructive-foreground"
                  : "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            )}
          >
            {t(labelKey)}
          </button>
        );
      })}
    </div>
  );
}

function RecentActions() {
  const t = useT();
  const { data } = useQuery({
    queryKey: KEYS.history,
    queryFn: () => appActionsApi.history(30),
  });
  const rows = data?.history ?? [];
  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <h3 className="font-medium text-foreground">{t("jarvis_actions.history_title")}</h3>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">{t("jarvis_actions.history_empty")}</p>
      ) : (
        <ul className="mt-3 divide-y divide-border">
          {rows.map((row) => (
            <li key={`${row.at}-${row.action}`} className="flex items-baseline gap-3 py-2 text-sm">
              <span
                className={cn(
                  "w-24 shrink-0 text-xs font-medium",
                  row.outcome === "ran" && "text-success",
                  row.outcome === "failed" && "text-warning",
                  row.outcome === "blocked" && "text-destructive",
                )}
              >
                {t(`jarvis_actions.outcome_${row.outcome}`)}
              </span>
              <span className="min-w-0 flex-1 truncate text-foreground" title={row.detail}>
                {row.title}
              </span>
              <span className="shrink-0 text-xs text-foreground-faint">
                {new Date(row.at * 1000).toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function JarvisActionsView() {
  const t = useT();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [area, setArea] = useState(ALL);
  const [changedOnly, setChangedOnly] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const catalog = useQuery({ queryKey: KEYS.catalog, queryFn: appActionsApi.list });

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
      ...(catalog.data?.areas ?? []).map((a) => ({ value: a, label: a })),
    ],
    [catalog.data?.areas, t],
  );

  const filtered = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return (catalog.data?.actions ?? []).filter((a) => {
      if (area !== ALL && a.area !== area) return false;
      if (changedOnly && !a.mode) return false;
      const text = `${a.title} ${a.description} ${a.area} ${a.path}`.toLowerCase();
      return words.every((w) => text.includes(w));
    });
  }, [catalog.data?.actions, query, area, changedOnly]);

  const shown = filtered.slice(0, limit);
  const count = t("jarvis_actions.count")
    .replace("{shown}", String(shown.length))
    .replace("{total}", String(filtered.length));

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

        <RecentActions />

        <section className="rounded-xl border border-border bg-card p-5">
          <div className="flex flex-wrap items-center gap-3">
            <Input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setLimit(PAGE);
              }}
              placeholder={t("jarvis_actions.search_placeholder")}
              aria-label={t("jarvis_actions.search_placeholder")}
              className="max-w-sm"
            />
            <BrandedSelect
              value={area}
              options={areaOptions}
              onValueChange={(value) => {
                setArea(value);
                setLimit(PAGE);
              }}
              ariaLabel={t("jarvis_actions.area_all")}
              className="w-56"
            />
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Switch
                checked={changedOnly}
                onCheckedChange={setChangedOnly}
                aria-label={t("jarvis_actions.changed_only")}
              />
              {t("jarvis_actions.changed_only")}
            </label>
            <span className="ml-auto text-xs text-foreground-faint">{count}</span>
          </div>

          {catalog.isError && (
            <p className="mt-4 text-sm text-destructive">{t("jarvis_actions.load_error")}</p>
          )}
          {setMode.isError && (
            <p className="mt-4 text-sm text-destructive">{t("jarvis_actions.save_error")}</p>
          )}

          <ul className="mt-4 divide-y divide-border">
            {shown.map((action) => (
              <li key={action.id} className="flex flex-wrap items-center gap-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-medium text-foreground">{action.title}</span>
                    <span className="text-xs text-foreground-faint">{action.area}</span>
                    {action.dangerous && (
                      <span className="text-xs text-warning">{t("jarvis_actions.dangerous")}</span>
                    )}
                  </div>
                  {action.description && (
                    <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                      {action.description}
                    </p>
                  )}
                  <p className="mt-0.5 text-xs text-foreground-faint">
                    {t(`jarvis_actions.tier_${action.default_tier}`)}
                  </p>
                </div>
                <ModeControl
                  action={action}
                  onChange={(mode) => setMode.mutate({ id: action.id, mode })}
                />
              </li>
            ))}
          </ul>

          {filtered.length > shown.length && (
            <div className="mt-4 flex justify-center">
              <Button variant="outline" size="sm" onClick={() => setLimit((n) => n + PAGE)}>
                {t("jarvis_actions.show_more")}
              </Button>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
