/**
 * Installed models — the compact ledger on the Simple overview: every
 * download on the server as one line, so "what do I have?" is answered on
 * the page the user lands on, not behind the Advanced switch.
 *
 * One line per model: name, parameters and size, the capabilities a role
 * cares about (tools / vision / thinking / embedding / audio — "completion"
 * is every model's and is not shown), which roles use it, which role it is
 * the recommendation for, and whether it sits in memory right now. Nothing
 * here writes; "Manage" opens the full ledger (Advanced → Models) with its
 * Tune, Use-for, Unload and Delete.
 */
import { useMemo, type ReactNode } from "react";
import { Database } from "lucide-react";

import {
  Panel,
  PanelHeader,
  SoftButton,
  StatusDot,
} from "@/components/extensions/primitives";
import type { LocalModelRow, RoleRow } from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { formatGb } from "./localModelsFormat";
import { canonical } from "./localSetup";

export interface InstalledPanelProps {
  models: LocalModelRow[];
  roles: RoleRow[];
  diskBytes: number;
  loading?: boolean;
  /** The server's sentence when it did not answer; the list is empty then. */
  error?: string | null;
  /** Opens the full ledger (Advanced → Models); the button hides without it. */
  onManage?: () => void;
  /**
   * Drop the card frame and the title row: the caller already draws both
   * (the overview's `StepCard`), and a card inside a card reads as clutter.
   */
  bare?: boolean;
}

/** The capabilities a role is gated on; the rest is noise on a one-liner. */
const ROLE_CAPABILITIES: ReadonlySet<string> = new Set([
  "tools",
  "vision",
  "thinking",
  "embedding",
  "audio",
]);

function Chip({
  children,
  tone = "muted",
}: {
  children: ReactNode;
  tone?: "muted" | "primary";
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium",
        tone === "primary"
          ? "bg-primary/10 text-primary"
          : "bg-sheen/[0.08] text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}

export function InstalledPanel({
  models,
  roles,
  diskBytes,
  loading = false,
  error = null,
  onManage,
  bare = false,
}: InstalledPanelProps) {
  const t = useT();
  const k = (key: string) => t(`local_models.installed.${key}`);

  const roleLabel = (roleId: string) => {
    const row = roles.find((r) => r.id === roleId);
    return t(row?.label_key ?? `local_models.role_${roleId}`);
  };

  // Which writable role recommends which download (the same pick the roles
  // ledger's "Use recommended" would assign).
  const recommendedFor = useMemo(() => {
    const out = new Map<string, string[]>();
    for (const row of roles) {
      if (!row.writable || row.advanced || !row.recommended) continue;
      const key = canonical(row.recommended);
      out.set(key, [...(out.get(key) ?? []), row.id]);
    }
    return out;
  }, [roles]);

  // The models in use first, then the biggest — the order a user scans in.
  const sorted = useMemo(
    () =>
      [...models].sort(
        (a, b) =>
          b.used_by.length - a.used_by.length || b.size_bytes - a.size_bytes,
      ),
    [models],
  );

  const subtitle =
    models.length === 1
      ? fill(k("subtitle_one"), { size: formatGb(diskBytes) })
      : fill(k("subtitle"), {
          count: models.length,
          size: formatGb(diskBytes),
        });

  const body = (
    <div className="space-y-3" data-testid="local-models-installed">
        {!bare && (
          <div className="flex flex-wrap items-start justify-between gap-3">
            <PanelHeader title={k("title")} subtitle={subtitle} />
            {onManage && models.length > 0 && (
              <SoftButton onClick={onManage} ariaLabel={k("manage")}>
                <Database className="h-3.5 w-3.5" />
                {k("manage")}
              </SoftButton>
            )}
          </div>
        )}

        {error && (
          <p
            className="text-sm text-amber-700 dark:text-amber-400"
            data-testid="installed-error"
          >
            {error}
          </p>
        )}
        {loading && models.length === 0 && !error && (
          <p className="text-sm text-muted-foreground">{k("loading")}</p>
        )}
        {!loading && models.length === 0 && !error && (
          <p className="text-sm text-muted-foreground" data-testid="installed-empty">
            {k("empty")}
          </p>
        )}

        {sorted.length > 0 && (
          <ul
            aria-label={k("list_label")}
            className="divide-y divide-border/70 overflow-hidden rounded-xl border border-border bg-card/60"
          >
            {sorted.map((row) => {
              const caps = row.probed
                ? row.capabilities.filter((c) => ROLE_CAPABILITIES.has(c))
                : [];
              const picks = (recommendedFor.get(canonical(row.name)) ?? []).filter(
                (roleId) => !row.used_by.includes(roleId as LocalModelRow["used_by"][number]),
              );
              return (
                <li
                  key={row.name}
                  className="grid gap-2 px-3.5 py-2.5 sm:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1.4fr)] sm:items-center"
                  data-testid={`installed-${row.name}`}
                >
                  <div className="min-w-0">
                    <StatusDot
                      tone={row.loaded ? "ok" : "off"}
                      label={
                        <span className="font-mono text-xs text-foreground/85">
                          {row.name}
                        </span>
                      }
                    />
                    <div className="ml-4 mt-0.5 text-[11px] text-muted-foreground">
                      {[row.parameter_size, formatGb(row.size_bytes)]
                        .filter(Boolean)
                        .join(" · ")}
                      {row.loaded ? ` · ${k("loaded")}` : ""}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {row.probed ? (
                      caps.map((cap) => <Chip key={cap}>{cap}</Chip>)
                    ) : (
                      <Chip>{k("unknown_caps")}</Chip>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1 sm:justify-end">
                    {row.used_by.map((roleId) => (
                      <Chip key={roleId} tone="primary">
                        {roleLabel(roleId)}
                      </Chip>
                    ))}
                    {picks.length > 0 && (
                      <span
                        className="text-[11px] text-muted-foreground"
                        data-testid={`installed-recommended-${row.name}`}
                      >
                        {fill(k("recommended_for"), {
                          role: picks.map(roleLabel).join(", "),
                        })}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
    </div>
  );
  if (bare) return body;
  return <Panel className="p-4">{body}</Panel>;
}
