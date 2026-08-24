/**
 * Catalogue — the public model library, browsed by default.
 *
 * Three ways in: the curated shortlist (Recommended, grouped by role with a
 * fit verdict per row and the date the maintainer last reviewed it), and the
 * live library sorted by Newest or Popular, narrowed by a capability chip and
 * a debounced search. A library row expands to its tag ledger, where every
 * tag carries its quantization, context, size and a plain fit sentence for
 * THIS machine. Pull starts the download and polls the same status route the
 * provider card uses; an installed tag offers "Use for…" over the writable
 * roles. A free-text "Pull exact name" field closes the panel for anything
 * the catalogue does not list.
 *
 * Props: `{ providerId }` — the id of the pull-capable provider card (the
 * view resolves it from `supports_model_pull`, never from a provider name).
 * Every string comes from `local_models.catalogue.*`.
 */
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Check, ChevronDown, Download, Loader2, RefreshCw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import {
  ActionMenu,
  Cell,
  type Column,
  EmptyRow,
  IconButton,
  InlineSearch,
  MenuPill,
  SegmentedFilter,
  SoftButton,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import {
  type CatalogCapability,
  type CatalogRecommendedModel,
  type CatalogSort,
  type CatalogTag,
  type LibraryModel,
  type LocalModelRole,
  type ModelPullProgress,
  type RoleRow,
  modelPullStatus,
  startModelPull,
  useCatalog,
  useCatalogRecommended,
  useCatalogTags,
  useInvalidateLocalModels,
  useRoles,
  useSetRole,
} from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

export type CatalogueMode = "recommended" | CatalogSort;

const MODES: CatalogueMode[] = ["recommended", "newest", "popular"];
const CAPABILITIES: CatalogCapability[] = [
  "tools",
  "vision",
  "embedding",
  "thinking",
];
const ROLE_ORDER: CatalogRecommendedModel["role"][] = [
  "chat",
  "vision",
  "coder",
  "embedding",
];

/** How often the running download is asked for its progress. */
const PULL_POLL_MS = 2_500;
/** Typing pause before the search reaches the backend. */
const SEARCH_DEBOUNCE_MS = 350;

type Translate = (key: string) => string;

interface PullState {
  model: string;
  progress: ModelPullProgress | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
      {children}
    </div>
  );
}

function CapabilityBadge({ label }: { label: string }) {
  return (
    <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
      {label}
    </span>
  );
}

function FitNote({ fit, note }: { fit?: string; note?: string }) {
  if (!note) return null;
  return (
    <p
      className={cn(
        "text-xs",
        fit === "tight"
          ? "text-amber-600 dark:text-amber-400"
          : "text-muted-foreground",
      )}
    >
      {note}
    </p>
  );
}

function formatSize(sizeGb: number | null | undefined, t: Translate): string {
  if (sizeGb === null || sizeGb === undefined)
    return t("local_models.catalogue.no_size");
  return `${sizeGb.toFixed(sizeGb < 10 ? 1 : 0)} GB`;
}

function minutesSince(timestamp: number): number {
  if (!timestamp) return 0;
  return Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
}

/** "Fetched N min ago" + the refresh button; the same line under both modes. */
function FetchedLine({
  updatedAt,
  fetching,
  onRefresh,
  t,
}: {
  updatedAt: number;
  fetching: boolean;
  onRefresh: () => void;
  t: Translate;
}) {
  // Re-render once a minute so the number keeps pace without a refetch.
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => tick((n) => n + 1), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const minutes = minutesSince(updatedAt);
  return (
    <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
      <span className="tabular-nums" data-testid="catalogue-fetched">
        {updatedAt
          ? minutes === 0
            ? t("local_models.catalogue.fetched_just_now")
            : fill(t("local_models.catalogue.fetched_ago"), { minutes })
          : t("local_models.catalogue.loading")}
      </span>
      <IconButton
        label={t("local_models.catalogue.refresh")}
        onClick={onRefresh}
        busy={fetching}
      >
        <RefreshCw className="h-4 w-4" />
      </IconButton>
    </div>
  );
}

/**
 * Pull / Installed / Use for… — the one action cell every list shares.
 * The pull button never gates on the fit verdict: the note is advice.
 */
function ModelActions({
  id,
  installed,
  cloudOnly,
  pull,
  roles,
  onPull,
  onUse,
  t,
}: {
  id: string;
  installed: boolean;
  cloudOnly?: boolean;
  pull: PullState | null;
  roles: RoleRow[];
  onPull: (model: string) => void;
  onUse: (role: LocalModelRole, model: string) => void;
  t: Translate;
}) {
  const busy = pull?.model === id && pull.progress?.state === "running";
  const anotherRunning =
    pull !== null && pull.model !== id && pull.progress?.state === "running";

  if (cloudOnly) {
    return (
      <span className="text-xs text-muted-foreground">
        {t("local_models.catalogue.cloud_only")}
      </span>
    );
  }

  if (installed) {
    const writable = roles.filter((r) => r.writable);
    return (
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
          <Check className="h-3.5 w-3.5" />
          {t("local_models.catalogue.installed")}
        </span>
        {writable.length > 0 && (
          <ActionMenu
            label={t("local_models.catalogue.use_for_menu")}
            actions={writable.map((r) => ({
              id: r.id,
              label: t(r.label_key),
              onSelect: () => onUse(r.id as LocalModelRole, id),
            }))}
            trigger={({ open, toggle }) => (
              <MenuPill open={open} toggle={toggle}>
                {t("local_models.catalogue.use_for")}
              </MenuPill>
            )}
          />
        )}
      </div>
    );
  }

  return (
    <SoftButton
      onClick={() => onPull(id)}
      disabled={busy || anotherRunning}
      ariaLabel={fill(t("local_models.catalogue.pull_aria"), { model: id })}
    >
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Download className="h-3.5 w-3.5" />
      )}
      {t("local_models.catalogue.pull")}
    </SoftButton>
  );
}

/** The inline progress line under a list while a download runs. */
function PullLine({ pull, t }: { pull: PullState | null; t: Translate }) {
  if (!pull) return null;
  const p = pull.progress;
  if (pull.error) {
    return (
      <p
        className="text-xs text-destructive"
        role="alert"
        data-testid="catalogue-pull-line"
      >
        {fill(t("local_models.catalogue.pull_failed"), {
          model: pull.model,
          message: pull.error,
        })}
      </p>
    );
  }
  if (!p || p.state === "running" || p.state === "idle") {
    const percent = Math.round(p?.percent ?? 0);
    return (
      <div className="space-y-1" data-testid="catalogue-pull-line">
        <p className="text-xs text-muted-foreground tabular-nums">
          {fill(t("local_models.catalogue.pulling"), {
            model: pull.model,
            percent,
          })}
          {p?.message ? ` — ${p.message}` : ""}
        </p>
        <div className="h-1 w-full overflow-hidden rounded-full bg-sheen/[0.08]">
          <div
            className="h-full bg-primary transition-[width]"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>
    );
  }
  if (p.state === "done") {
    return (
      <p
        className="text-xs text-emerald-600 dark:text-emerald-400"
        data-testid="catalogue-pull-line"
      >
        {fill(t("local_models.catalogue.pull_done"), { model: pull.model })}
      </p>
    );
  }
  return (
    <p
      className="text-xs text-destructive"
      role="alert"
      data-testid="catalogue-pull-line"
    >
      {fill(t("local_models.catalogue.pull_failed"), {
        model: pull.model,
        message: p.message,
      })}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Recommended — the curated shortlist grouped by role
// ---------------------------------------------------------------------------

function RecommendedList({
  providerId,
  filter,
  pull,
  roles,
  onPull,
  onUse,
  t,
}: {
  providerId: string;
  filter: string;
  pull: PullState | null;
  roles: RoleRow[];
  onPull: (model: string) => void;
  onUse: (role: LocalModelRole, model: string) => void;
  t: Translate;
}) {
  const query = useCatalogRecommended(providerId);
  const data = query.data;

  const groups = useMemo(() => {
    const rows = (data?.models ?? []).filter((m) => {
      if (!filter) return true;
      const needle = filter.toLowerCase();
      return (
        m.id.toLowerCase().includes(needle) ||
        m.label.toLowerCase().includes(needle)
      );
    });
    const order = (
      data?.roles?.length ? data.roles : ROLE_ORDER
    ) as CatalogRecommendedModel["role"][];
    return order
      .map((role) => ({ role, rows: rows.filter((m) => m.role === role) }))
      .filter((g) => g.rows.length > 0);
  }, [data, filter]);

  if (query.isError) {
    return <EmptyRow>{(query.error as Error).message}</EmptyRow>;
  }
  if (!data) {
    return <EmptyRow>{t("local_models.catalogue.loading")}</EmptyRow>;
  }

  return (
    <div className="space-y-4" data-testid="catalogue-recommended">
      <FetchedLine
        updatedAt={query.dataUpdatedAt}
        fetching={query.isFetching}
        onRefresh={() => void query.refetch()}
        t={t}
      />
      {!data.server_reachable && data.message && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          {data.message}
        </p>
      )}
      {groups.length === 0 && (
        <EmptyRow>{t("local_models.catalogue.empty")}</EmptyRow>
      )}
      {groups.map((g) => (
        <section key={g.role} className="space-y-2">
          <Eyebrow>{t(`local_models.catalogue.group_${g.role}`)}</Eyebrow>
          <div className="divide-y divide-border/70 overflow-hidden rounded-xl border border-border bg-card/60">
            {g.rows.map((m) => (
              <div
                key={m.id}
                className="flex items-start gap-4 px-3.5 py-3"
                data-testid={`catalogue-pick-${m.id}`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-display text-sm font-semibold tracking-tight text-foreground">
                      {m.label}
                    </span>
                    <span className="font-mono text-xs text-muted-foreground">
                      {m.id}
                    </span>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {formatSize(m.size_gb, t)}
                    </span>
                    {m.tools && (
                      <CapabilityBadge
                        label={t("local_models.catalogue.cap_tools")}
                      />
                    )}
                    {m.vision && (
                      <CapabilityBadge
                        label={t("local_models.catalogue.cap_vision")}
                      />
                    )}
                    {m.recommended_for.length > 0 && (
                      <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                        {fill(t("local_models.catalogue.recommended_badge"), {
                          roles: m.recommended_for
                            .map((r) => t(`local_models.catalogue.group_${r}`))
                            .join(", "),
                        })}
                      </span>
                    )}
                  </div>
                  {m.purpose && (
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {m.purpose}
                    </p>
                  )}
                  <FitNote fit={m.fit} note={m.fit_note} />
                </div>
                <div className="shrink-0">
                  <ModelActions
                    id={m.id}
                    installed={m.installed}
                    pull={pull}
                    roles={roles}
                    onPull={onPull}
                    onUse={onUse}
                    t={t}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}
      <PullLine pull={pull} t={t} />
      {data.curated_reviewed_on && (
        <p
          className="text-xs text-muted-foreground"
          data-testid="catalogue-reviewed"
        >
          {fill(t("local_models.catalogue.reviewed"), {
            date: data.curated_reviewed_on,
          })}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Library — the live table, one row expands to its tag ledger
// ---------------------------------------------------------------------------

function TagLedger({
  providerId,
  model,
  pull,
  roles,
  onPull,
  onUse,
  t,
}: {
  providerId: string;
  model: string;
  pull: PullState | null;
  roles: RoleRow[];
  onPull: (model: string) => void;
  onUse: (role: LocalModelRole, model: string) => void;
  t: Translate;
}) {
  const query = useCatalogTags(providerId, model);
  const columns: Column[] = [
    {
      id: "tag",
      label: t("local_models.catalogue.col_tag"),
      width: "minmax(0, 1.4fr)",
    },
    {
      id: "quant",
      label: t("local_models.catalogue.col_quant"),
      width: "minmax(0, 0.8fr)",
    },
    {
      id: "context",
      label: t("local_models.catalogue.col_context"),
      width: "minmax(0, 0.7fr)",
    },
    {
      id: "size",
      label: t("local_models.catalogue.col_size"),
      width: "minmax(0, 0.6fr)",
      align: "right",
    },
    {
      id: "fit",
      label: t("local_models.catalogue.col_fit"),
      width: "minmax(0, 2fr)",
    },
    {
      id: "action",
      label: t("local_models.catalogue.col_action"),
      width: "max-content",
      srOnly: true,
    },
  ];

  if (query.isError) {
    return <EmptyRow>{(query.error as Error).message}</EmptyRow>;
  }
  if (!query.data) {
    return (
      <p className="px-3 py-2 text-xs text-muted-foreground">
        {t("local_models.catalogue.tags_loading")}
      </p>
    );
  }
  if (query.data.error && query.data.tags.length === 0) {
    return <EmptyRow>{query.data.error}</EmptyRow>;
  }
  if (query.data.tags.length === 0) {
    return <EmptyRow>{t("local_models.catalogue.tags_empty")}</EmptyRow>;
  }

  return (
    <div
      className="rounded-lg border border-border/70 bg-background/40"
      data-testid={`catalogue-tags-${model}`}
    >
      <Table label={fill(t("local_models.catalogue.tags_label"), { model })}>
        <TableHead columns={columns} />
        {query.data.tags.map((tag: CatalogTag) => (
          <TableRow key={tag.id} columns={columns} className="min-h-[44px]">
            <Cell>
              <span className="font-mono text-xs">{tag.id}</span>
            </Cell>
            <Cell muted>
              <span className="font-mono text-xs">
                {tag.quantization || "—"}
              </span>
            </Cell>
            <Cell muted>
              <span className="tabular-nums">{tag.context || "—"}</span>
            </Cell>
            <Cell muted align="right">
              <span className="tabular-nums">
                {tag.cloud ? "—" : formatSize(tag.size_gb, t)}
              </span>
            </Cell>
            <Cell>
              <FitNote fit={tag.fit} note={tag.fit_note} />
            </Cell>
            <Cell stop align="right">
              <ModelActions
                id={tag.id}
                installed={Boolean(tag.installed)}
                cloudOnly={tag.cloud}
                pull={pull}
                roles={roles}
                onPull={onPull}
                onUse={onUse}
                t={t}
              />
            </Cell>
          </TableRow>
        ))}
      </Table>
    </div>
  );
}

function LibraryList({
  providerId,
  sort,
  capability,
  q,
  pull,
  roles,
  onPull,
  onUse,
  t,
}: {
  providerId: string;
  sort: CatalogSort;
  capability: CatalogCapability | null;
  q: string;
  pull: PullState | null;
  roles: RoleRow[];
  onPull: (model: string) => void;
  onUse: (role: LocalModelRole, model: string) => void;
  t: Translate;
}) {
  const query = useCatalog(providerId, { q, sort, capability });
  const [expanded, setExpanded] = useState<string | null>(null);

  // A new page means a new list; an open ledger from the old one would sit
  // under the wrong row.
  useEffect(() => {
    setExpanded(null);
  }, [sort, capability, q]);

  const columns: Column[] = [
    {
      id: "name",
      label: t("local_models.catalogue.col_name"),
      width: "minmax(0, 2.2fr)",
    },
    {
      id: "caps",
      label: t("local_models.catalogue.col_capabilities"),
      width: "minmax(0, 1.4fr)",
    },
    {
      id: "sizes",
      label: t("local_models.catalogue.col_sizes"),
      width: "minmax(0, 1.2fr)",
    },
    {
      id: "pulls",
      label: t("local_models.catalogue.col_pulls"),
      width: "minmax(0, 0.7fr)",
      align: "right",
    },
    {
      id: "updated",
      label: t("local_models.catalogue.col_updated"),
      width: "minmax(0, 0.9fr)",
      align: "right",
    },
    {
      id: "toggle",
      label: t("local_models.catalogue.col_expand"),
      width: "max-content",
      srOnly: true,
    },
  ];

  const data = query.data;
  const models: LibraryModel[] = data?.models ?? [];

  return (
    <div className="space-y-3" data-testid="catalogue-library">
      <FetchedLine
        updatedAt={query.dataUpdatedAt}
        fetching={query.isFetching}
        onRefresh={() => void query.refetch()}
        t={t}
      />
      {query.isError && <EmptyRow>{(query.error as Error).message}</EmptyRow>}
      {!query.isError && data && data.error && models.length === 0 && (
        <EmptyRow>{data.error}</EmptyRow>
      )}
      {!query.isError && data && !data.error && models.length === 0 && (
        <EmptyRow>{t("local_models.catalogue.empty")}</EmptyRow>
      )}
      {!query.isError && !data && (
        <EmptyRow>{t("local_models.catalogue.loading")}</EmptyRow>
      )}
      {models.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-border bg-card/60">
          <Table label={t("local_models.catalogue.library_label")}>
            <TableHead columns={columns} />
            {models.map((m) => {
              const open = expanded === m.name;
              return (
                <div key={m.name}>
                  <TableRow
                    columns={columns}
                    onClick={() => setExpanded(open ? null : m.name)}
                    selected={open}
                    ariaLabel={m.name}
                  >
                    <Cell>
                      <div className="flex items-center gap-2">
                        <span className="truncate font-display text-sm font-semibold tracking-tight text-foreground">
                          {m.name}
                        </span>
                        {m.installed && (
                          <span className="inline-flex items-center gap-1 text-[10px] text-emerald-600 dark:text-emerald-400">
                            <Check className="h-3 w-3" />
                            {t("local_models.catalogue.installed")}
                          </span>
                        )}
                      </div>
                      {m.description && (
                        <p className="truncate text-xs text-muted-foreground">
                          {m.description}
                        </p>
                      )}
                    </Cell>
                    <Cell>
                      <div className="flex flex-wrap gap-1">
                        {m.capabilities.map((c) => (
                          <CapabilityBadge key={c} label={c} />
                        ))}
                      </div>
                    </Cell>
                    <Cell muted>
                      <span className="truncate font-mono text-xs">
                        {m.sizes.join(" · ")}
                      </span>
                    </Cell>
                    <Cell muted align="right">
                      <span className="tabular-nums">{m.pulls}</span>
                    </Cell>
                    <Cell muted align="right">
                      <span className="tabular-nums">{m.updated}</span>
                    </Cell>
                    <Cell align="right">
                      <ChevronDown
                        className={cn(
                          "h-4 w-4 text-muted-foreground transition-transform",
                          open && "rotate-180",
                        )}
                      />
                    </Cell>
                  </TableRow>
                  {open && (
                    <div className="border-b border-border/70 px-3 pb-3 pt-1 last:border-b-0">
                      <TagLedger
                        providerId={providerId}
                        model={m.name}
                        pull={pull}
                        roles={roles}
                        onPull={onPull}
                        onUse={onUse}
                        t={t}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </Table>
        </div>
      )}
      <PullLine pull={pull} t={t} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------

export function CataloguePanel({ providerId }: { providerId: string }) {
  const t = useT();
  const qc = useQueryClient();
  const invalidate = useInvalidateLocalModels(providerId);
  const rolesQuery = useRoles(providerId);
  const setRole = useSetRole(providerId);

  const [mode, setMode] = useState<CatalogueMode>("recommended");
  const [capability, setCapability] = useState<CatalogCapability | null>(null);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [exact, setExact] = useState("");
  const [pull, setPull] = useState<PullState | null>(null);
  const [useNote, setUseNote] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebounced(search.trim()),
      SEARCH_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [search]);

  // One poll loop per download; a finished pull refreshes every list that
  // shows an installed mark (inventory, recommended, tags, roles).
  const pollRef = useRef<number | null>(null);
  useEffect(() => {
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, []);

  const onPull = useCallback(
    (model: string) => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
      setPull({ model, progress: null, error: null });
      void (async () => {
        try {
          const first = await startModelPull(providerId, model);
          setPull({ model, progress: first, error: null });
          if (first.state === "done" || first.state === "error") {
            invalidate();
            return;
          }
          pollRef.current = window.setInterval(async () => {
            try {
              const next = await modelPullStatus(providerId, model);
              setPull({ model, progress: next, error: null });
              if (next.state === "done" || next.state === "error") {
                if (pollRef.current !== null)
                  window.clearInterval(pollRef.current);
                pollRef.current = null;
                invalidate();
              }
            } catch (err) {
              if (pollRef.current !== null)
                window.clearInterval(pollRef.current);
              pollRef.current = null;
              setPull({
                model,
                progress: null,
                error: err instanceof Error ? err.message : String(err),
              });
            }
          }, PULL_POLL_MS);
        } catch (err) {
          setPull({
            model,
            progress: null,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      })();
    },
    [providerId, invalidate],
  );

  const onUse = useCallback(
    (role: LocalModelRole, model: string) => {
      setUseNote(null);
      setRole.mutate(
        { role, model },
        {
          onSuccess: (res) => {
            const spec = rolesQuery.data?.roles.find((r) => r.id === role);
            setUseNote(
              res.message ||
                fill(t("local_models.catalogue.use_for_done"), {
                  model,
                  role: spec ? t(spec.label_key) : role,
                }),
            );
            void qc.invalidateQueries({
              queryKey: ["local-models", providerId],
            });
          },
          onError: (err) =>
            setUseNote(err instanceof Error ? err.message : String(err)),
        },
      );
    },
    [setRole, rolesQuery.data, t, qc, providerId],
  );

  const roles = rolesQuery.data?.roles ?? [];
  const exactName = exact.trim();

  return (
    <div className="space-y-4" data-testid="catalogue-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SegmentedFilter<CatalogueMode>
          label={t("local_models.catalogue.mode_label")}
          value={mode}
          onChange={setMode}
          options={MODES.map((id) => ({
            id,
            label: t(`local_models.catalogue.mode_${id}`),
          }))}
        />
        <div
          className="flex items-center gap-1"
          role="group"
          aria-label={t("local_models.catalogue.capability_label")}
        >
          {CAPABILITIES.map((c) => {
            const active = capability === c;
            return (
              <button
                key={c}
                type="button"
                aria-pressed={active}
                onClick={() => {
                  setCapability(active ? null : c);
                  if (mode === "recommended") setMode("popular");
                }}
                className={cn(
                  "inline-flex h-7 items-center rounded-md border px-2.5 text-xs transition-colors",
                  active
                    ? "border-primary/40 bg-primary/10 text-foreground"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {t(`local_models.catalogue.cap_${c}`)}
              </button>
            );
          })}
        </div>
      </div>

      <InlineSearch
        value={search}
        onChange={setSearch}
        placeholder={t("local_models.catalogue.search_placeholder")}
      />

      {mode === "recommended" ? (
        <RecommendedList
          providerId={providerId}
          filter={debounced}
          pull={pull}
          roles={roles}
          onPull={onPull}
          onUse={onUse}
          t={t}
        />
      ) : (
        <LibraryList
          providerId={providerId}
          sort={mode}
          capability={capability}
          q={debounced}
          pull={pull}
          roles={roles}
          onPull={onPull}
          onUse={onUse}
          t={t}
        />
      )}

      {useNote && (
        <p
          className="text-xs text-muted-foreground"
          role="status"
          data-testid="catalogue-use-note"
        >
          {useNote}
        </p>
      )}

      <section className="space-y-2 border-t border-border/70 pt-4">
        <Eyebrow>{t("local_models.catalogue.exact_title")}</Eyebrow>
        <div className="flex items-center gap-2">
          <InlineSearch
            value={exact}
            onChange={setExact}
            placeholder={t("local_models.catalogue.exact_placeholder")}
          />
          <SoftButton
            primary
            disabled={!exactName || pull?.progress?.state === "running"}
            onClick={() => {
              onPull(exactName);
              setExact("");
            }}
            ariaLabel={t("local_models.catalogue.exact_pull")}
          >
            <Download className="h-3.5 w-3.5" />
            {t("local_models.catalogue.exact_pull")}
          </SoftButton>
        </div>
        <p className="text-xs text-muted-foreground">
          {t("local_models.catalogue.exact_hint")}
        </p>
      </section>
    </div>
  );
}
