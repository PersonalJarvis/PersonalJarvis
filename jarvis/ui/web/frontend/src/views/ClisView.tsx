/**
 * The CLI catalog — every command-line tool Jarvis can drive, as one table.
 *
 * Same settings surface as Skills, Plugins & MCPs: a quiet table with a row per
 * CLI, and a detail page that replaces the table in place when a row is opened.
 * It deliberately reuses `components/extensions/primitives` rather than owning
 * a second look, so the two sections cannot drift apart.
 *
 * Every row leads with the **vendor's own mark** (see `components/clis/CliLogo`)
 * — a list of 22 command-line tools is unscannable by name alone, and people
 * recognise the Cloudflare cloud long before they recognise "wrangler".
 */
import { useMemo, useState } from "react";
import {
  Check,
  Clock,
  ExternalLink,
  ListFilter,
  LogIn,
  LogOut,
  Plus,
  RefreshCw,
  Search,
  Terminal,
  Trash2,
  X,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { CliLogo } from "@/components/clis/CliLogo";
import {
  ActionMenu,
  BackLink,
  Cell,
  DetailHeader,
  EmptyRow,
  FactRows,
  IconButton,
  InlineSearch,
  Panel,
  PanelHeader,
  SegmentedFilter,
  SoftButton,
  StatusDot,
  Table,
  TableHead,
  TableRow,
  type Column,
} from "@/components/extensions/primitives";
import { cn } from "@/lib/utils";
import {
  useCheckCli,
  useCliDetail,
  useClisList,
  useCliStats,
  useCliUsage,
  useClearUsage,
  useConnectCli,
  useDisconnectCli,
  useRegisterCustomCli,
  useDeleteCustomCli,
  useSpawnExternalTerminal,
  type CliDetail,
  type CliStatus,
  type CliSummary,
} from "@/hooks/useClis";
import { fill, translate, useT } from "@/i18n";
import { useEventStore } from "@/store/events";

// ---------------------------------------------------------------------------
// Status vocabulary
// ---------------------------------------------------------------------------

type StatusTone = "ok" | "off" | "warn" | "error" | "busy";

// One place decides how a status reads and how it is coloured. The tones come
// from the shared `StatusDot`, so a connected CLI looks exactly like a running
// MCP server one section over.
const STATUS_TONES: Record<CliStatus, { labelKey: string; tone: StatusTone }> = {
  connected: { labelKey: "clis_view.status_connected", tone: "ok" },
  disconnected: { labelKey: "clis_view.status_disconnected", tone: "off" },
  not_installed: { labelKey: "clis_view.status_not_installed", tone: "off" },
  error: { labelKey: "clis_view.status_error", tone: "error" },
  checking: { labelKey: "clis_view.status_checking", tone: "busy" },
};

function statusLabel(status: CliStatus): string {
  return translate(STATUS_TONES[status].labelKey);
}

// Wrap a compact "5m"/"3h"/"2d" delta with the localized "ago" marker. The
// unit letters are locale-neutral; only the surrounding word differs (de "vor X",
// en "X ago", es "hace X"). Empty parts are dropped so word order stays correct.
function ago(value: string): string {
  const prefix = translate("clis_view.ago_prefix");
  const suffix = translate("clis_view.ago_suffix");
  return [prefix, value, suffix].filter(Boolean).join(" ");
}

function formatRelativeTime(ts: number | null): string {
  if (!ts) return "—";
  const diff = Math.max(0, Date.now() - ts);
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return translate("clis_view.just_now");
  if (mins < 60) return ago(`${mins}m`);
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return ago(`${hrs}h`);
  return ago(`${Math.floor(hrs / 24)}d`);
}

function formatDateTime(ts: number): string {
  return new Date(ts).toLocaleString(undefined, {
    day: "2-digit", month: "2-digit", year: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

type FilterTab = "all" | "connected" | "installed" | "custom";

// ---------------------------------------------------------------------------
// Catalog
// ---------------------------------------------------------------------------

export function ClisView() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const { data, isLoading, error, refetch, isFetching } = useClisList();
  const [filter, setFilter] = useState<FilterTab>("all");
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [showWizard, setShowWizard] = useState(false);
  const [usageFor, setUsageFor] = useState<string | null>(null);

  const customCount = data?.clis.filter((c) => c.is_custom).length ?? 0;

  const filtered = useMemo(() => {
    if (!data) return [];
    let list = data.clis;
    if (filter === "connected") list = list.filter((c) => c.status === "connected");
    if (filter === "installed") list = list.filter((c) => c.installed);
    if (filter === "custom") list = list.filter((c) => c.is_custom);
    if (categoryFilter) list = list.filter((c) => c.category === categoryFilter);
    const needle = query.trim().toLowerCase();
    if (needle) {
      // Name, slug and description all searchable: people look for "postgres"
      // as readily as for "neonctl".
      list = list.filter((c) =>
        [c.display_name, c.name, c.description, c.category]
          .join(" ")
          .toLowerCase()
          .includes(needle),
      );
    }
    return list;
  }, [data, filter, categoryFilter, query]);

  const columns: Column[] = [
    { id: "name", label: t("clis_view.col_name") },
    { id: "version", label: "Version", width: "96px" },
    { id: "status", label: t("clis_view.col_status"), width: "150px" },
    { id: "activity", label: t("clis_view.col_activity"), width: "136px" },
  ];

  const closeSearch = () => {
    setSearchOpen(false);
    setQuery("");
  };

  const dialogs = (
    <>
      {showWizard && <CustomCliWizard onClose={() => setShowWizard(false)} />}
      {usageFor && <UsageDrawer name={usageFor} onClose={() => setUsageFor(null)} />}
    </>
  );

  if (selectedName) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <CliDetailPage
          name={selectedName}
          onBack={() => setSelectedName(null)}
          onShowUsage={() => setUsageFor(selectedName)}
        />
        {dialogs}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1">
        <div className="mx-auto w-full max-w-5xl px-8 py-6">
          <PanelHeader
            title="CLIs"
            subtitle={
              data
                ? `${data.connected} ${t("clis_view.subtitle_connected")} · ${data.installed} ${t("clis_view.subtitle_installed")} · ${data.total} ${t("clis_view.subtitle_in_catalog")}`
                : t("common.loading")
            }
            actions={
              <>
                <IconButton
                  label={t("clis_view.search_placeholder")}
                  active={searchOpen}
                  onClick={() => (searchOpen ? closeSearch() : setSearchOpen(true))}
                >
                  <Search className="h-4 w-4" />
                </IconButton>
                <IconButton
                  label={t("clis_view.reload")}
                  onClick={() => void refetch()}
                  busy={isFetching && !data}
                >
                  <RefreshCw className="h-4 w-4" />
                </IconButton>
                <SoftButton onClick={() => setShowWizard(true)} className="ml-1">
                  <Plus className="h-3.5 w-3.5" />
                  {t("clis_view.add_custom")}
                </SoftButton>
              </>
            }
          />

          {searchOpen && (
            <div className="mt-4 flex items-center gap-2">
              <div className="flex-1">
                <InlineSearch
                  value={query}
                  onChange={setQuery}
                  placeholder={t("clis_view.search_placeholder")}
                  autoFocus
                />
              </div>
              <IconButton label={t("common.close")} onClick={closeSearch}>
                <X className="h-4 w-4" />
              </IconButton>
            </div>
          )}

          <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
            <SegmentedFilter
              label={t("clis_view.filter_label")}
              value={filter}
              onChange={setFilter}
              options={[
                { id: "all", label: t("clis_view.filter_all"), count: data?.total ?? 0 },
                { id: "connected", label: t("clis_view.filter_connected"), count: data?.connected ?? 0 },
                { id: "installed", label: t("clis_view.filter_installed"), count: data?.installed ?? 0 },
                { id: "custom", label: t("clis_view.filter_custom"), count: customCount },
              ]}
            />
            {data && data.categories.length > 0 && (
              <CategoryMenu
                categories={data.categories}
                value={categoryFilter}
                onChange={setCategoryFilter}
              />
            )}
          </div>

          <p className="mt-4 max-w-2xl text-xs leading-relaxed text-muted-foreground">
            {assistantName} {t("clis_view.intro_can_call")}{" "}
            <span className="text-foreground">{t("clis_view.intro_connected_word")}</span>{" "}
            {t("clis_view.intro_appear")}
          </p>

          <div className="mt-4">
            {isLoading && (
              <div className="py-8 text-center text-sm text-muted-foreground">
                {t("common.loading")}
              </div>
            )}

            {error && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                {t("common.error")}: {(error as Error).message}
              </div>
            )}

            {!isLoading && !error && !data?.total && <EmptyCatalog />}

            {!isLoading && !error && Boolean(data?.total) && (
              <Table label="CLIs">
                <TableHead columns={columns} />
                {filtered.map((cli) => (
                  <CliRow
                    key={cli.name}
                    cli={cli}
                    columns={columns}
                    onSelect={() => setSelectedName(cli.name)}
                    onShowUsage={() => setUsageFor(cli.name)}
                  />
                ))}
                {filtered.length === 0 && (
                  <EmptyRow>
                    {query.trim()
                      ? t("clis_view.search_no_hits")
                      : t(`clis_view.empty_${filter}`)}
                  </EmptyRow>
                )}
              </Table>
            )}
          </div>
        </div>
      </ScrollArea>
      {dialogs}
    </div>
  );
}

/**
 * The category filter as a menu rather than a row of nine pills.
 *
 * The pills used to sit permanently in the header and were the loudest thing on
 * the screen while being the least-used control. A menu costs one click and
 * gives the table back its width.
 */
function CategoryMenu({
  categories,
  value,
  onChange,
}: {
  categories: string[];
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  const t = useT();
  return (
    <ActionMenu
      label={t("clis_view.category_label")}
      actions={[
        {
          id: "__all",
          label: t("clis_view.category_all"),
          icon: value === null ? <Check className="h-3.5 w-3.5" /> : undefined,
          onSelect: () => onChange(null),
        },
        ...categories.map((cat, index) => ({
          id: cat,
          label: cat,
          icon: value === cat ? <Check className="h-3.5 w-3.5" /> : undefined,
          separatorAbove: index === 0,
          onSelect: () => onChange(value === cat ? null : cat),
        })),
      ]}
      trigger={({ open, toggle }) => (
        <SoftButton onClick={toggle} ariaExpanded={open} ariaHasPopup="menu">
          <ListFilter className="h-3.5 w-3.5" />
          {value ?? t("clis_view.category_all_categories")}
        </SoftButton>
      )}
    />
  );
}

function CliRow({
  cli,
  columns,
  onSelect,
  onShowUsage,
}: {
  cli: CliSummary;
  columns: Column[];
  onSelect: () => void;
  onShowUsage: () => void;
}) {
  const t = useT();
  const status = STATUS_TONES[cli.status];
  return (
    <TableRow columns={columns} onClick={onSelect} ariaLabel={cli.display_name}>
      <Cell>
        <div className="flex min-w-0 items-center gap-3">
          <CliLogo cliName={cli.name} category={cli.category} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-[15px] font-medium">{cli.display_name}</span>
              <span className="shrink-0 font-mono text-xs text-muted-foreground">
                {cli.name}
              </span>
              {cli.is_custom && (
                <span className="shrink-0 text-xs text-muted-foreground">
                  · {t("clis_view.custom_word")}
                </span>
              )}
            </div>
            <div className="truncate text-xs text-muted-foreground">{cli.description}</div>
          </div>
        </div>
      </Cell>
      <Cell muted>
        <span className="font-mono text-xs tabular-nums">{cli.version ?? "—"}</span>
      </Cell>
      <Cell>
        <span title={cli.error ?? undefined}>
          <StatusDot
            tone={status.tone}
            label={statusLabel(cli.status)}
            pulse={cli.status === "checking"}
          />
        </span>
      </Cell>
      <Cell stop>
        {cli.usage_count_7d > 0 ? (
          <button
            type="button"
            onClick={onShowUsage}
            title={t("clis_view.open_usage_history")}
            className="text-left text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <span className="tabular-nums">
              {fill(t("clis_view.usage_7d"), { count: cli.usage_count_7d })}
            </span>
            <span className="block text-[11px] text-muted-foreground/70">
              {formatRelativeTime(cli.last_used_at)}
            </span>
          </button>
        ) : (
          <span className="text-xs text-muted-foreground/60">
            {t("clis_view.never_used")}
          </span>
        )}
      </Cell>
    </TableRow>
  );
}

function EmptyCatalog() {
  const t = useT();
  return (
    <div className="flex flex-col items-center justify-center gap-5 py-16 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-border bg-card/60">
        <Terminal className="h-7 w-7 text-muted-foreground" />
      </div>
      <h3 className="font-display text-xl font-semibold tracking-tight">
        {t("clis_view.empty_no_clis")}
      </h3>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail page — replaces the table in place
// ---------------------------------------------------------------------------

function CliDetailPage({
  name,
  onBack,
  onShowUsage,
}: {
  name: string;
  onBack: () => void;
  onShowUsage: () => void;
}) {
  const t = useT();
  const { data, isLoading, error } = useCliDetail(name);
  const check = useCheckCli();
  const disconnect = useDisconnectCli();
  const deleteCustom = useDeleteCustomCli();
  const pushToast = useEventStore((s) => s.pushToast);
  const [apiKeyDialog, setApiKeyDialog] = useState(false);
  const [installDialog, setInstallDialog] = useState(false);

  const status = data ? STATUS_TONES[data.status] : null;

  return (
    <ScrollArea className="flex-1">
      <div className="mx-auto w-full max-w-5xl space-y-6 px-8 py-6">
        <BackLink label={t("clis_view.back_to_list")} onClick={onBack} />

        {isLoading && (
          <div className="py-8 text-sm text-muted-foreground">
            {t("clis_view.loading_details")}
          </div>
        )}
        {error && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            {(error as Error).message}
          </div>
        )}

        {data && (
          <>
            <DetailHeader
              leading={
                <CliLogo cliName={data.name} category={data.category} size="lg" />
              }
              title={data.display_name}
              titleAccessory={
                <span className="rounded-md bg-sheen/[0.08] px-2 py-0.5 font-mono text-xs text-muted-foreground">
                  {data.name}
                </span>
              }
              byline={data.description}
              actions={
                <IconButton
                  label={t("clis_view.recheck_status")}
                  busy={check.isPending}
                  onClick={() =>
                    check.mutate(name, {
                      onError: (err) =>
                        pushToast(
                          "error",
                          `${t("clis_view.status_check_failed")}: ${(err as Error).message}`,
                        ),
                    })
                  }
                >
                  <RefreshCw className="h-4 w-4" />
                </IconButton>
              }
            />

            <div className="flex flex-wrap items-center gap-2">
              {status && (
                <StatusDot
                  tone={status.tone}
                  label={statusLabel(data.status)}
                  pulse={data.status === "checking"}
                />
              )}
              <div className="ml-auto flex flex-wrap items-center gap-2">
                {!data.installed && data.install_methods.length > 0 && (
                  <SoftButton primary onClick={() => setInstallDialog(true)}>
                    {t("clis_view.install")}
                  </SoftButton>
                )}
                {data.installed && !data.connected && data.auth_mode === "oauth_cli" && (
                  <ConnectOAuthButton
                    name={name}
                    displayName={data.display_name}
                    loginCommand={data.login_command ?? ""}
                    statusCommand={data.status_command ?? null}
                  />
                )}
                {data.installed && !data.connected && data.auth_mode === "api_key" && (
                  <SoftButton primary onClick={() => setApiKeyDialog(true)}>
                    <LogIn className="h-3.5 w-3.5" />
                    {t("clis_view.set_api_key")}
                  </SoftButton>
                )}
                {data.connected &&
                  data.auth_mode !== "none" &&
                  data.auth_mode !== "config_file" && (
                    <SoftButton
                      disabled={disconnect.isPending}
                      onClick={() =>
                        disconnect.mutate(name, {
                          onSuccess: (res) => {
                            if (res.ok) {
                              pushToast(
                                "success",
                                `${name} ${t("clis_view.disconnected_suffix")}`,
                              );
                            } else {
                              pushToast("error", res.error || t("clis_view.disconnect_failed"));
                            }
                          },
                          onError: (err) =>
                            pushToast(
                              "error",
                              `${t("clis_view.disconnect_failed")}: ${(err as Error).message}`,
                            ),
                        })
                      }
                    >
                      <LogOut className="h-3.5 w-3.5" />
                      {t("clis_view.disconnect")}
                    </SoftButton>
                  )}
                <SoftButton onClick={onShowUsage}>
                  <Clock className="h-3.5 w-3.5" />
                  {t("clis_view.history")}
                </SoftButton>
                {data.homepage && (
                  <SoftButton
                    onClick={() =>
                      window.open(data.homepage, "_blank", "noopener,noreferrer")
                    }
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    {t("clis_view.documentation")}
                  </SoftButton>
                )}
                {data.is_custom && (
                  <IconButton
                    label={t("clis_view.remove_custom_cli")}
                    disabled={deleteCustom.isPending}
                    className="text-destructive hover:text-destructive"
                    onClick={() => {
                      if (
                        window.confirm(
                          `${t("clis_view.confirm_remove_custom_prefix")} "${name}" ${t("clis_view.confirm_remove_custom_suffix")}`,
                        )
                      ) {
                        deleteCustom.mutate(name, {
                          onSuccess: () => {
                            pushToast("success", `${name} ${t("clis_view.removed_suffix")}`);
                            onBack();
                          },
                          onError: (err) =>
                            pushToast("error", `${t("common.error")}: ${(err as Error).message}`),
                        });
                      }
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </IconButton>
                )}
              </div>
            </div>

            {data.error && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                {data.error}
              </div>
            )}

            <div className="grid gap-4 lg:grid-cols-2">
              <DetailPanel title={t("clis_view.section_binary")}>
                <FactRows
                  rows={[
                    { label: "Binary", value: <Mono>{data.binary_name}</Mono> },
                    {
                      label: t("clis_view.kv_path"),
                      value: data.binary_path ? (
                        <Mono>{data.binary_path}</Mono>
                      ) : (
                        <span className="text-muted-foreground">{t("clis_view.not_found")}</span>
                      ),
                    },
                    { label: "Version", value: data.version ?? "—" },
                    { label: t("clis_view.auth_mode_short"), value: <Mono>{data.auth_mode}</Mono> },
                    { label: t("clis_view.field_category"), value: data.category },
                  ]}
                />
              </DetailPanel>

              <DetailPanel title={t("clis_view.section_commands")}>
                <FactRows
                  rows={[
                    { label: "Check", value: <Mono>{data.check_command}</Mono> },
                    {
                      label: "Login",
                      value: data.login_command ? <Mono>{data.login_command}</Mono> : "",
                    },
                    {
                      label: t("clis_view.auth_status_label"),
                      value: data.status_command ? <Mono>{data.status_command}</Mono> : "",
                    },
                    {
                      label: "Logout",
                      value: data.logout_command ? <Mono>{data.logout_command}</Mono> : "",
                    },
                  ]}
                />
              </DetailPanel>

              {data.secret_keys.length > 0 && (
                <DetailPanel title={t("clis_view.section_secrets")}>
                  <FactRows
                    rows={data.secret_keys.map((sk) => ({
                      label: sk.env_var,
                      value: data.secrets_set[sk.name] ? (
                        <span className="text-muted-foreground">
                          ●●●●● {t("clis_view.secret_set")}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">
                          {t("clis_view.secret_unset")}
                        </span>
                      ),
                    }))}
                  />
                </DetailPanel>
              )}

              {/* The two pattern lists are long and read as a pair — side by
                  side across the full width rather than stacked in one column
                  with a column-height of empty space beside them. */}
              <DetailPanel
                title={t("clis_view.risk_tier_label")}
                className="lg:col-span-2"
              >
                <FactRows
                  rows={[{ label: t("clis_view.default_tier"), value: <Mono>{data.risk_tier}</Mono> }]}
                />
                <div className="grid gap-4 sm:grid-cols-2">
                  {data.deny_patterns.length > 0 && (
                    <PatternList
                      title={t("clis_view.blocked_patterns")}
                      patterns={data.deny_patterns}
                      tone="deny"
                    />
                  )}
                  {data.allow_patterns.length > 0 && (
                    <PatternList
                      title={t("clis_view.allowed_patterns")}
                      patterns={data.allow_patterns}
                      tone="allow"
                    />
                  )}
                </div>
              </DetailPanel>

              {data.tool_schema_examples.length > 0 && (
                <DetailPanel
                  title={t("clis_view.section_tool_examples")}
                  className="lg:col-span-2"
                >
                  <ul className="space-y-1">
                    {data.tool_schema_examples.map((example) => (
                      <li key={example}>
                        <Mono>{example}</Mono>
                      </li>
                    ))}
                  </ul>
                </DetailPanel>
              )}
            </div>
          </>
        )}
      </div>

      {apiKeyDialog && data && (
        <ApiKeyDialog detail={data} onClose={() => setApiKeyDialog(false)} />
      )}
      {installDialog && data && (
        <InstallDialog detail={data} onClose={() => setInstallDialog(false)} />
      )}
    </ScrollArea>
  );
}

function DetailPanel({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Panel className={cn("p-5", className)}>
      <h4 className="mb-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {title}
      </h4>
      <div className="space-y-3">{children}</div>
    </Panel>
  );
}

function Mono({ children }: { children: React.ReactNode }) {
  return <span className="break-all font-mono text-xs">{children}</span>;
}

function PatternList({
  title,
  patterns,
  tone,
}: {
  title: string;
  patterns: string[];
  tone: "allow" | "deny";
}) {
  return (
    <div>
      <div className="mb-1.5 text-xs text-muted-foreground">{title}</div>
      <ul className="space-y-0.5">
        {patterns.map((pattern) => (
          <li
            key={pattern}
            className={cn(
              "break-all font-mono text-xs",
              tone === "deny" ? "text-destructive/80" : "text-muted-foreground/90",
            )}
          >
            {pattern}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// OAuth connect button (inline — starts the flow and shows a toast)
// ---------------------------------------------------------------------------

function ConnectOAuthButton({
  name,
  displayName,
  loginCommand,
  statusCommand,
}: {
  name: string;
  displayName: string;
  loginCommand: string;
  statusCommand: string | null;
}) {
  // Spawns a **real** external terminal (wt/pwsh) and types the login_command
  // straight in — the user sees the terminal window pop up, the OAuth browser
  // flow starts, and the terminal stays open.
  //
  // It also sets ``cliConnectCoach`` in the store — the global
  // ``CliConnectPoller`` (in App.tsx) then checks the auth status every 3s and
  // resets the coach state once the login completes. That's what makes the
  // "X is connected" toast appear and the CLIs list refresh automatically, no
  // matter which section the user is currently in.
  const t = useT();
  const spawn = useSpawnExternalTerminal();
  const pushToast = useEventStore((s) => s.pushToast);
  const setCoach = useEventStore((s) => s.setCliConnectCoach);
  return (
    <SoftButton
      primary
      disabled={spawn.isPending}
      onClick={() =>
        spawn.mutate(
          { name, kind: "login" },
          {
            onSuccess: (res) => {
              if (res.ok) {
                // Set the coach so the headless poller starts polling.
                setCoach({
                  cliName: name,
                  displayName,
                  authMode: "oauth_cli",
                  loginCommand,
                  statusCommand,
                });
                pushToast(
                  "info",
                  `${t("clis_view.terminal_opened")} (${res.method}) — ${t("clis_view.follow_browser_login")}`,
                );
              } else {
                pushToast("error", res.error || t("clis_view.terminal_spawn_failed"));
              }
            },
            onError: (err) => pushToast("error", (err as Error).message),
          },
        )
      }
    >
      <LogIn className="h-3.5 w-3.5" />
      {t("clis_view.browser_login")}
    </SoftButton>
  );
}

// ---------------------------------------------------------------------------
// Dialog shell — one frame for the API-key, install and wizard dialogs
// ---------------------------------------------------------------------------

function DialogShell({
  title,
  help,
  onClose,
  width = "max-w-md",
  footer,
  children,
}: {
  title: React.ReactNode;
  help?: string;
  onClose: () => void;
  width?: string;
  footer: React.ReactNode;
  children: React.ReactNode;
}) {
  const t = useT();
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 backdrop-blur-sm">
      <div
        className={cn(
          "flex w-full flex-col overflow-hidden rounded-xl border border-border bg-card",
          width,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-border p-5">
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-base font-semibold">{title}</h3>
            {help && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{help}</p>}
          </div>
          <IconButton label={t("common.close")} onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        </div>
        <div className="max-h-[60vh] overflow-y-auto p-5">{children}</div>
        <div className="flex items-center justify-end gap-2 border-t border-border p-4">
          {footer}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// API-Key Dialog
// ---------------------------------------------------------------------------

function ApiKeyDialog({
  detail,
  onClose,
}: {
  detail: CliDetail;
  onClose: () => void;
}) {
  const t = useT();
  const connect = useConnectCli();
  const pushToast = useEventStore((s) => s.pushToast);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  return (
    <DialogShell
      title={`${detail.display_name} — ${t("clis_view.set_api_key")}`}
      help={t("clis_view.api_key_help")}
      onClose={onClose}
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose} disabled={connect.isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            type="button"
            className="btn-primary"
            disabled={connect.isPending}
            onClick={() => {
              setError(null);
              connect.mutate(
                { name: detail.name, mode: "api_key", secrets: values },
                {
                  onSuccess: (res) => {
                    if (res.ok) {
                      pushToast("success", `${detail.name} ${t("clis_view.connected_suffix")}`);
                      onClose();
                    } else {
                      setError(res.error || t("clis_view.validation_failed"));
                    }
                  },
                  onError: (err) => setError((err as Error).message),
                },
              );
            }}
          >
            {connect.isPending ? t("clis_view.validating") : t("clis_view.save_and_validate")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {detail.secret_keys.map((sk) => (
          <label key={sk.name} className="block text-xs">
            <div className="mb-1 flex items-center justify-between">
              <span className="font-medium">{sk.env_var}</span>
              {sk.required && (
                <span className="text-[11px] text-muted-foreground">
                  {t("clis_view.required_word")}
                </span>
              )}
            </div>
            <input
              type="password"
              autoComplete="new-password"
              value={values[sk.name] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [sk.name]: e.target.value }))}
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 font-mono text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              placeholder={
                detail.secrets_set[sk.name]
                  ? `●●●●● ${t("clis_view.api_key_already_set")}`
                  : t("clis_view.api_key_enter")
              }
            />
          </label>
        ))}

        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
            {error}
          </div>
        )}
      </div>
    </DialogShell>
  );
}

// ---------------------------------------------------------------------------
// Install Dialog
// ---------------------------------------------------------------------------

function InstallDialog({
  detail,
  onClose,
}: {
  detail: CliDetail;
  onClose: () => void;
}) {
  // We use spawn-external (a real terminal window) instead of the internal
  // xterm. ``useInstallCli`` (background subprocess + output streaming) stays
  // in the repo for headless/voice paths — but on the UI side we deliberately
  // use the external terminal, because the user wants to see the install
  // running in a "real" shell.
  const t = useT();
  const spawn = useSpawnExternalTerminal();
  const pushToast = useEventStore((s) => s.pushToast);
  const [selected, setSelected] = useState<string>(
    detail.recommended_install ?? detail.install_methods[0]?.manager ?? "manual",
  );
  const selectedMethod = detail.install_methods.find((m) => m.manager === selected);

  return (
    <DialogShell
      title={`${detail.display_name}${t("clis_view.install_lowercase")}`}
      help={t("clis_view.install_help")}
      onClose={onClose}
      width="max-w-lg"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            type="button"
            className="btn-primary"
            disabled={spawn.isPending}
            onClick={() => {
              if (selected === "manual") {
                if (selectedMethod?.command) {
                  window.open(selectedMethod.command, "_blank", "noopener,noreferrer");
                }
                onClose();
                return;
              }
              spawn.mutate(
                { name: detail.name, kind: "install", method: selected },
                {
                  onSuccess: (res) => {
                    if (res.ok) {
                      // A machine with no screen has no terminal window to open;
                      // the backend then installs in-app and streams the output,
                      // so the toast must not claim a window appeared somewhere.
                      pushToast(
                        "info",
                        res.method === "in-app"
                          ? t("clis_view.install_running_in_app")
                          : `${t("clis_view.external_terminal_opened")} (${res.method}) — ${t("clis_view.install_running")}`,
                      );
                      onClose();
                    } else {
                      pushToast("error", res.error || t("clis_view.terminal_spawn_failed"));
                    }
                  },
                  onError: (err) => pushToast("error", (err as Error).message),
                },
              );
            }}
          >
            {spawn.isPending
              ? t("clis_view.spawning")
              : selected === "manual"
                ? t("clis_view.open")
                : t("clis_view.install_in_terminal")}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <div className="mb-2 text-xs text-muted-foreground">
            {t("clis_view.choose_method")}
          </div>
          <div className="space-y-1">
            {detail.install_methods.map((m) => (
              <label
                key={m.manager}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 transition-colors",
                  selected === m.manager
                    ? "border-primary/40 bg-primary/10"
                    : "border-border hover:bg-sheen/[0.05]",
                )}
              >
                <input
                  type="radio"
                  name="install-method"
                  value={m.manager}
                  checked={selected === m.manager}
                  onChange={() => setSelected(m.manager)}
                  className="accent-primary"
                />
                <span className="text-sm font-medium">{m.manager}</span>
                {m.manager === detail.recommended_install && (
                  <span className="text-xs text-muted-foreground">
                    · {t("clis_view.recommended")}
                  </span>
                )}
              </label>
            ))}
          </div>
        </div>

        {selectedMethod && (
          <div>
            <div className="mb-1.5 text-xs text-muted-foreground">
              {t("clis_view.command_label")}
            </div>
            <code className="block break-all rounded-md border border-border bg-background px-3 py-2 font-mono text-xs">
              {selectedMethod.command}
            </code>
          </div>
        )}
      </div>
    </DialogShell>
  );
}

// ---------------------------------------------------------------------------
// Custom-CLI Wizard (4 Steps)
// ---------------------------------------------------------------------------

function CustomCliWizard({ onClose }: { onClose: () => void }) {
  const t = useT();
  const register = useRegisterCustomCli();
  const pushToast = useEventStore((s) => s.pushToast);
  const [step, setStep] = useState(1);
  const [form, setForm] = useState({
    name: "",
    display_name: "",
    description: "",
    binary_name: "",
    check_command: "",
    version_command: "",
    version_parse_regex: "v?(\\S+)",
    auth_mode: "none" as "none" | "oauth_cli" | "api_key" | "config_file",
    login_command: "",
    status_command: "",
    secret_keys: "",
    env_vars: "",
    risk_tier: "monitor" as "safe" | "monitor" | "ask" | "block",
    allow_patterns: "",
    deny_patterns: "",
    category: "other",
    homepage: "",
  });
  const [error, setError] = useState<string | null>(null);

  const canNext = () => {
    if (step === 1)
      return form.name.length >= 2 && form.display_name.length >= 1 && form.binary_name.length >= 1;
    if (step === 2) return form.check_command.length >= 1;
    return true;
  };

  const submit = () => {
    setError(null);
    const payload = {
      name: form.name,
      display_name: form.display_name,
      description: form.description,
      homepage: form.homepage,
      binary_name: form.binary_name,
      check_command: form.check_command.split(/\s+/).filter(Boolean),
      version_parse_regex: form.version_parse_regex || "(\\S+)",
      install_manual_url: form.homepage,
      auth_mode: form.auth_mode,
      login_command: form.login_command
        ? form.login_command.split(/\s+/).filter(Boolean)
        : null,
      status_command: form.status_command.split(/\s+/).filter(Boolean),
      status_parse: "text_nonempty",
      secret_keys: form.secret_keys.split(",").map((s) => s.trim()).filter(Boolean),
      env_vars: form.env_vars.split(",").map((s) => s.trim()).filter(Boolean),
      risk_tier: form.risk_tier,
      allow_patterns: form.allow_patterns.split("\n").map((s) => s.trim()).filter(Boolean),
      deny_patterns: form.deny_patterns.split("\n").map((s) => s.trim()).filter(Boolean),
      category: form.category,
      icon: "",
    };
    register.mutate(payload, {
      onSuccess: () => {
        pushToast("success", `${form.name} ${t("clis_view.registered_suffix")}`);
        onClose();
      },
      onError: (err) => setError((err as Error).message),
    });
  };

  const stepHelp = [
    t("clis_view.wizard_step1_identity"),
    t("clis_view.wizard_step2_check"),
    t("clis_view.wizard_step3_auth"),
    t("clis_view.wizard_step4_risk"),
  ][step - 1];

  return (
    <DialogShell
      title={`${t("clis_view.add_custom_cli_title")} · ${t("clis_view.step")} ${step}/4`}
      help={stepHelp}
      onClose={onClose}
      width="max-w-2xl"
      footer={
        <>
          <div className="mr-auto text-xs text-muted-foreground">
            {step < 4 ? t("clis_view.steps_skippable") : t("clis_view.done")}
          </div>
          {step > 1 && (
            <Button variant="ghost" onClick={() => setStep(step - 1)}>
              {t("common.back")}
            </Button>
          )}
          {step < 4 ? (
            <Button className="btn-primary" disabled={!canNext()} onClick={() => setStep(step + 1)}>
              {t("clis_view.next")}
            </Button>
          ) : (
            <Button className="btn-primary" onClick={submit} disabled={register.isPending}>
              {register.isPending ? t("common.saving") : t("common.save")}
            </Button>
          )}
        </>
      }
    >
      <div className="grid gap-3 text-xs">
        {step === 1 && (
          <>
            <TextField label={t("clis_view.field_name_id")} val={form.name}
              onChange={(v) => setForm({ ...form, name: v.toLowerCase() })}
              placeholder={t("clis_view.eg_mytool")} />
            <TextField label={t("clis_view.field_display_name")} val={form.display_name}
              onChange={(v) => setForm({ ...form, display_name: v })}
              placeholder={t("clis_view.eg_my_tool_cli")} />
            <TextField label={t("clis_view.field_binary_name")} val={form.binary_name}
              onChange={(v) => setForm({ ...form, binary_name: v })}
              placeholder={t("clis_view.eg_mytool")} />
            <TextField label={t("clis_view.field_description")} val={form.description}
              onChange={(v) => setForm({ ...form, description: v })}
              placeholder={t("clis_view.what_does_cli_do")} />
            <TextField label={t("clis_view.field_category")} val={form.category}
              onChange={(v) => setForm({ ...form, category: v })}
              placeholder="cloud / git / payments / other" />
            <TextField label={t("clis_view.field_homepage_url")} val={form.homepage}
              onChange={(v) => setForm({ ...form, homepage: v })}
              placeholder="https://..." />
          </>
        )}
        {step === 2 && (
          <>
            <TextField label={t("clis_view.field_check_command")} val={form.check_command}
              onChange={(v) => setForm({ ...form, check_command: v })}
              placeholder="mytool --version" mono />
            <TextField label={t("clis_view.field_version_regex")} val={form.version_parse_regex}
              onChange={(v) => setForm({ ...form, version_parse_regex: v })}
              placeholder="v(\\S+)" mono />
          </>
        )}
        {step === 3 && (
          <>
            <label className="block">
              <div className="mb-1 text-xs text-muted-foreground">
                {t("clis_view.auth_mode_label")}
              </div>
              <BrandedSelect
                value={form.auth_mode}
                onValueChange={(value) =>
                  setForm({ ...form, auth_mode: value as typeof form.auth_mode })
                }
                ariaLabel={t("clis_view.auth_mode_label")}
                className="py-1.5 text-xs"
                options={[
                  { value: "none", label: t("clis_view.auth_none") },
                  { value: "oauth_cli", label: t("clis_view.auth_oauth_cli") },
                  { value: "api_key", label: t("clis_view.auth_api_key") },
                  { value: "config_file", label: t("clis_view.auth_config_file") },
                ]}
              />
            </label>
            {form.auth_mode === "oauth_cli" && (
              <TextField label={t("clis_view.field_login_command")} val={form.login_command}
                onChange={(v) => setForm({ ...form, login_command: v })}
                placeholder="mytool login" mono />
            )}
            {form.auth_mode !== "none" && (
              <TextField label={t("clis_view.field_status_command")} val={form.status_command}
                onChange={(v) => setForm({ ...form, status_command: v })}
                placeholder="mytool whoami" mono />
            )}
            {form.auth_mode === "api_key" && (
              <>
                <TextField label={t("clis_view.field_secret_keys")} val={form.secret_keys}
                  onChange={(v) => setForm({ ...form, secret_keys: v })}
                  placeholder="mytool_api_key" mono />
                <TextField label={t("clis_view.field_env_vars")} val={form.env_vars}
                  onChange={(v) => setForm({ ...form, env_vars: v })}
                  placeholder="MYTOOL_API_KEY" mono />
              </>
            )}
          </>
        )}
        {step === 4 && (
          <>
            <label className="block">
              <div className="mb-1 text-xs text-muted-foreground">
                {t("clis_view.risk_tier_label")}
              </div>
              <BrandedSelect
                value={form.risk_tier}
                onValueChange={(value) =>
                  setForm({ ...form, risk_tier: value as typeof form.risk_tier })
                }
                ariaLabel={t("clis_view.risk_tier_label")}
                className="py-1.5 text-xs"
                options={[
                  { value: "safe", label: t("clis_view.risk_safe") },
                  { value: "monitor", label: t("clis_view.risk_monitor") },
                  { value: "ask", label: t("clis_view.risk_ask") },
                  { value: "block", label: t("clis_view.risk_block") },
                ]}
              />
            </label>
            <TextArea label={t("clis_view.field_allow_patterns")} val={form.allow_patterns}
              onChange={(v) => setForm({ ...form, allow_patterns: v })}
              placeholder="mytool get *&#10;mytool list*" />
            <TextArea label={t("clis_view.field_deny_patterns")} val={form.deny_patterns}
              onChange={(v) => setForm({ ...form, deny_patterns: v })}
              placeholder="mytool delete *&#10;mytool rm *" />
          </>
        )}

        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
            {error}
          </div>
        )}
      </div>
    </DialogShell>
  );
}

function TextField({
  label, val, onChange, placeholder, mono,
}: {
  label: string; val: string; onChange: (v: string) => void;
  placeholder?: string; mono?: boolean;
}) {
  return (
    <label className="block">
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <input
        type="text" value={val}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={cn(
          "w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          mono && "font-mono text-xs",
        )}
      />
    </label>
  );
}

function TextArea({
  label, val, onChange, placeholder,
}: {
  label: string; val: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <label className="block">
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <textarea
        value={val}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-24 w-full resize-none rounded-md border border-input bg-background px-3 py-1.5 font-mono text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      />
    </label>
  );
}

// ---------------------------------------------------------------------------
// Usage Drawer
// ---------------------------------------------------------------------------

function UsageDrawer({ name, onClose }: { name: string; onClose: () => void }) {
  const t = useT();
  const [page, setPage] = useState(1);
  const [successOnly, setSuccessOnly] = useState(false);
  const [search, setSearch] = useState("");
  const { data: usage } = useCliUsage(name, { page, pageSize: 50, successOnly, search });
  const { data: stats } = useCliStats(name);
  const clear = useClearUsage();
  const pushToast = useEventStore((s) => s.pushToast);

  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-end bg-scrim/60 backdrop-blur-sm">
      <div className="flex w-[560px] max-w-full flex-col border-l border-border bg-card">
        <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-sm font-semibold">
              {t("clis_view.history")} · {name}
            </h3>
            {stats && (
              <div className="mt-0.5 text-xs text-muted-foreground tabular-nums">
                {fill(t("clis_view.usage_stats"), {
                  total: stats.total_calls,
                  rate: Math.round(stats.success_rate * 100),
                  avg: stats.avg_duration_ms,
                })}
              </div>
            )}
          </div>
          <IconButton label={t("common.close")} onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        </div>

        <div className="flex items-center gap-3 border-b border-border px-5 py-2.5">
          <div className="flex-1">
            <InlineSearch
              value={search}
              onChange={(v) => {
                setPage(1);
                setSearch(v);
              }}
              placeholder={t("clis_view.search_command")}
            />
          </div>
          <label className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={successOnly}
              onChange={(e) => {
                setPage(1);
                setSuccessOnly(e.target.checked);
              }}
              className="accent-primary"
            />
            {t("clis_view.success_only")}
          </label>
        </div>

        <ScrollArea className="flex-1">
          <div className="p-5">
            {!usage && (
              <div className="text-sm text-muted-foreground">{t("common.loading")}</div>
            )}
            {usage && usage.entries.length === 0 && (
              <div className="py-10 text-center text-sm text-muted-foreground">
                {t("clis_view.no_entries")}
              </div>
            )}
            {usage && usage.entries.length > 0 && (
              <ul className="space-y-1.5">
                {usage.entries.map((e) => (
                  <li key={e.id} className="rounded-lg border border-border bg-background/40 px-3 py-2">
                    <div className="flex items-start justify-between gap-2">
                      <code className="min-w-0 flex-1 break-all font-mono text-xs">
                        {e.full_command}
                      </code>
                      <span
                        className={cn(
                          "shrink-0 font-mono text-xs tabular-nums",
                          e.exit_code === 0 ? "text-muted-foreground" : "text-destructive",
                        )}
                      >
                        {e.exit_code === 0 ? "✓" : e.exit_code !== null ? `✗ ${e.exit_code}` : "…"}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground tabular-nums">
                      <span>{formatDateTime(e.started_at)}</span>
                      {e.duration_ms !== null && <span>{e.duration_ms} ms</span>}
                      <span>{e.caller}</span>
                      {e.trace_id && <span title={e.trace_id}>T:{e.trace_id.slice(0, 8)}</span>}
                    </div>
                    {e.stderr_preview && (
                      <div className="mt-1.5 rounded border border-destructive/30 bg-destructive/5 px-2 py-1 font-mono text-[11px] text-destructive/90">
                        {e.stderr_preview}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </ScrollArea>

        {stats && stats.top_commands.length > 0 && (
          <div className="border-t border-border px-5 py-3">
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              {t("clis_view.top_commands")}
            </div>
            <ul className="space-y-0.5">
              {stats.top_commands.slice(0, 3).map(([cmd, count]) => (
                <li key={cmd} className="flex items-center justify-between gap-2 text-xs">
                  <code className="truncate font-mono">{cmd}</code>
                  <span className="tabular-nums text-muted-foreground">{count}×</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex items-center justify-between gap-2 border-t border-border p-3">
          <Button
            variant="ghost" size="sm"
            disabled={clear.isPending}
            onClick={() => {
              if (
                window.confirm(
                  `${t("clis_view.confirm_clear_history_prefix")} "${name}" ${t("clis_view.confirm_clear_history_suffix")}`,
                )
              ) {
                clear.mutate(name, {
                  onSuccess: (res) =>
                    pushToast("success", `${res.deleted} ${t("clis_view.entries_deleted")}`),
                  onError: (err) => pushToast("error", (err as Error).message),
                });
              }
            }}
            className="text-destructive hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span className="ml-1.5">{t("clis_view.clear")}</span>
          </Button>
          {usage && (
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <Button
                variant="ghost" size="sm"
                disabled={page === 1}
                onClick={() => setPage(Math.max(1, page - 1))}
              >
                ‹
              </Button>
              <span className="tabular-nums">
                {(page - 1) * 50 + 1}–{Math.min(page * 50, usage.total)} / {usage.total}
              </span>
              <Button
                variant="ghost" size="sm"
                disabled={page * 50 >= usage.total}
                onClick={() => setPage(page + 1)}
              >
                ›
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
