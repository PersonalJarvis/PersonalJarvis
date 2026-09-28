/**
 * Step 1 — "Where is your server?": the provider gallery.
 *
 * Grouped the way people think about it (a cloud, a web host, a box at home,
 * something else), searchable, with the provider's real mark. Picking one is
 * the whole step; the rest of the wizard adapts to it. A virtual machine on
 * this computer is its own entry at the end.
 */
import { useMemo, useState } from "react";
import { Loader2, MonitorSmartphone, Search } from "lucide-react";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { ProviderCategory, ProviderInfo } from "@/lib/computersApi";
import type { Pick } from "./shared";

const ORDER: ProviderCategory[] = ["cloud", "hosting", "home", "other"];

export function ProviderStep({
  providers,
  loading,
  selected,
  onPick,
}: {
  providers: ProviderInfo[];
  loading: boolean;
  selected: Pick | null;
  onPick: (pick: Pick) => void;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();

  const groups = useMemo(
    () =>
      ORDER.map((category) => ({
        category,
        items: providers.filter(
          (p) => p.category === category && (!needle || p.name.toLowerCase().includes(needle)),
        ),
      })).filter((group) => group.items.length > 0),
    [providers, needle],
  );
  const localMatches =
    !needle || t("computers.wz_local_title").toLowerCase().includes(needle) || "vm multipass".includes(needle);

  return (
    <div className="space-y-6">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <input
          type="text"
          role="searchbox"
          value={query}
          autoFocus
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("computers.wz_search")}
          aria-label={t("computers.wz_search")}
          className="h-10 w-full rounded-md border border-border-strong bg-input pl-9 pr-3 text-base text-foreground placeholder:text-foreground-faint focus:border-accent focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {t("computers.loading")}
        </div>
      )}

      {groups.map((group) => (
        <section key={group.category} aria-labelledby={`wz-group-${group.category}`}>
          <h3
            id={`wz-group-${group.category}`}
            className="mb-2 text-xs font-medium uppercase tracking-wide text-foreground-faint"
          >
            {t(`computers.wz_group_${group.category}`)}
          </h3>
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {group.items.map((provider) => {
              const active = selected?.kind === "provider" && selected.provider.id === provider.id;
              return (
                <li key={provider.id}>
                  <button
                    type="button"
                    data-testid={`wz-provider-${provider.id}`}
                    aria-pressed={active}
                    onClick={() => onPick({ kind: "provider", provider })}
                    className={cn(
                      "flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      active ? "border-accent bg-accent-soft" : "border-border hover:border-border-strong hover:bg-secondary/40",
                    )}
                  >
                    <ProviderLogo providerId={provider.id} label={provider.name} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground-strong">{provider.name}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {provider.api ? t("computers.wz_has_api") : t("computers.wz_ssh_only")}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      {localMatches && (
        <section aria-labelledby="wz-group-local">
          <h3 id="wz-group-local" className="mb-2 text-xs font-medium uppercase tracking-wide text-foreground-faint">
            {t("computers.wz_group_local")}
          </h3>
          <button
            type="button"
            data-testid="wz-provider-local"
            aria-pressed={selected?.kind === "local"}
            onClick={() => onPick({ kind: "local" })}
            className={cn(
              "flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors sm:w-1/2 lg:w-1/3",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selected?.kind === "local" ? "border-accent bg-accent-soft" : "border-border hover:border-border-strong hover:bg-secondary/40",
            )}
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground">
              <MonitorSmartphone className="h-4 w-4" aria-hidden />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-foreground-strong">{t("computers.wz_local_title")}</span>
              <span className="block truncate text-xs text-muted-foreground">{t("computers.wz_local_sub")}</span>
            </span>
          </button>
        </section>
      )}

      {!loading && groups.length === 0 && !localMatches && (
        <p className="text-sm text-muted-foreground">{t("computers.wz_no_match")}</p>
      )}
    </div>
  );
}
