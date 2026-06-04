import { useMemo, useState } from "react";
import {
  Terminal,
  RefreshCw,
  Cloud,
  Database,
  CreditCard,
  Github,
  Container,
  ExternalLink,
  ChevronRight,
  Plus,
  Play,
  LogIn,
  LogOut,
  Clock,
  Trash2,
  X,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
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
import { useEventStore } from "@/store/events";

type StatusLabel = "verbunden" | "getrennt" | "nicht installiert" | "fehler" | "prüfe…";

const STATUS_STYLES: Record<CliStatus, { label: StatusLabel; color: string; dotClass: string }> = {
  connected: {
    label: "verbunden",
    color: "text-primary",
    dotClass: "bg-primary shadow-[0_0_8px_rgba(255,214,10,0.6)]",
  },
  disconnected: {
    label: "getrennt",
    color: "text-muted-foreground",
    dotClass: "bg-muted-foreground/40",
  },
  not_installed: {
    label: "nicht installiert",
    color: "text-muted-foreground/70",
    dotClass: "bg-muted-foreground/20",
  },
  error: { label: "fehler", color: "text-destructive", dotClass: "bg-destructive" },
  checking: {
    label: "prüfe…",
    color: "text-muted-foreground",
    dotClass: "bg-muted-foreground animate-jarvis-pulse",
  },
};

const ICONS_BY_CATEGORY: Record<string, React.ComponentType<{ className?: string }>> = {
  cloud: Cloud,
  paas: Cloud,
  baas: Database,
  git: Github,
  payments: CreditCard,
  container: Container,
  other: Terminal,
};

function iconForCli(cli: CliSummary) {
  const Icon = ICONS_BY_CATEGORY[cli.category] ?? Terminal;
  return <Icon className="h-4 w-4 text-muted-foreground/80 shrink-0" />;
}

function formatRelativeTime(ts: number | null): string {
  if (!ts) return "—";
  const diff = Math.max(0, Date.now() - ts);
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "gerade eben";
  if (mins < 60) return `vor ${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `vor ${hrs}h`;
  return `vor ${Math.floor(hrs / 24)}d`;
}

function formatDateTime(ts: number): string {
  return new Date(ts).toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", year: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

type FilterTab = "all" | "connected" | "installed" | "custom";

export function ClisView() {
  const { data, isLoading, error, refetch } = useClisList();
  const [filter, setFilter] = useState<FilterTab>("all");
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [showWizard, setShowWizard] = useState(false);
  const [usageFor, setUsageFor] = useState<string | null>(null);

  const filtered = useMemo(() => {
    if (!data) return [];
    let list = data.clis;
    if (filter === "connected") list = list.filter((c) => c.status === "connected");
    if (filter === "installed") list = list.filter((c) => c.installed);
    if (filter === "custom") list = list.filter((c) => c.is_custom);
    if (categoryFilter) list = list.filter((c) => c.category === categoryFilter);
    return list;
  }, [data, filter, categoryFilter]);

  return (
    <div className="flex h-full">
      <div className="flex h-full flex-1 flex-col">
        <ViewHeader
          icon={<Terminal className="h-4 w-4 text-primary" />}
          title="CLIs"
          subtitle={
            data
              ? `${data.connected} verbunden · ${data.installed} installiert · ${data.total} im Katalog`
              : "Laden…"
          }
          right={
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setShowWizard(true)}
                title="Custom CLI hinzufügen"
              >
                <Plus className="h-3.5 w-3.5" />
                <span className="ml-1.5 text-xs">Add Custom</span>
              </Button>
              <Button size="sm" variant="ghost" onClick={() => refetch()} title="Neu laden">
                <RefreshCw className="h-3.5 w-3.5" />
              </Button>
            </div>
          }
        />

        <div className="flex flex-wrap items-center gap-2 border-b border-border px-6 py-3">
          {(
            [
              ["all", `Alle (${data?.total ?? 0})`],
              ["connected", `Verbunden (${data?.connected ?? 0})`],
              ["installed", `Installiert (${data?.installed ?? 0})`],
              [
                "custom",
                `Custom (${data?.clis.filter((c) => c.is_custom).length ?? 0})`,
              ],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setFilter(key)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs transition-colors",
                filter === key
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}

          {data && data.categories.length > 0 && (
            <div className="ml-auto flex items-center gap-1.5">
              <span className="text-xs text-muted-foreground/70">Kategorie:</span>
              <button
                type="button"
                onClick={() => setCategoryFilter(null)}
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                  categoryFilter === null
                    ? "border-primary/40 bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                alle
              </button>
              {data.categories.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() =>
                    setCategoryFilter((prev) => (prev === cat ? null : cat))
                  }
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                    categoryFilter === cat
                      ? "border-primary/40 bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  {cat}
                </button>
              ))}
            </div>
          )}
        </div>

        <ScrollArea className="flex-1">
          <div className="p-6">
            {isLoading && <div className="text-sm text-muted-foreground">Lade…</div>}

            {error && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                {(error as Error).message}
              </div>
            )}

            {!isLoading && !error && filtered.length === 0 && (
              <EmptyState hasAny={Boolean(data?.total)} filter={filter} />
            )}

            {filtered.length > 0 && (
              <>
                <p className="mb-4 max-w-2xl text-xs leading-relaxed text-muted-foreground">
                  Jarvis kann diese CLIs als Tools aufrufen. Nur{" "}
                  <span className="text-primary">verbundene</span> CLIs erscheinen
                  im Brain-Tool-Katalog.
                </p>
                <ul className="space-y-1.5">
                  {filtered.map((cli) => (
                    <CliRow
                      key={cli.name}
                      cli={cli}
                      selected={selectedName === cli.name}
                      onSelect={() =>
                        setSelectedName(selectedName === cli.name ? null : cli.name)
                      }
                      onShowUsage={() => setUsageFor(cli.name)}
                    />
                  ))}
                </ul>
              </>
            )}
          </div>
        </ScrollArea>
      </div>

      {selectedName && (
        <DetailPanel
          name={selectedName}
          onClose={() => setSelectedName(null)}
          onShowUsage={() => setUsageFor(selectedName)}
        />
      )}

      {showWizard && <CustomCliWizard onClose={() => setShowWizard(false)} />}
      {usageFor && <UsageDrawer name={usageFor} onClose={() => setUsageFor(null)} />}
    </div>
  );
}

function CliRow({
  cli,
  selected,
  onSelect,
  onShowUsage,
}: {
  cli: CliSummary;
  selected: boolean;
  onSelect: () => void;
  onShowUsage: () => void;
}) {
  const style = STATUS_STYLES[cli.status];
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className={cn(
          "flex w-full items-center gap-4 rounded-lg border border-border bg-card/40 px-4 py-2.5 text-left transition-colors",
          cli.status === "connected" && "border-primary/30 bg-card/60",
          cli.status === "error" && "border-destructive/30 bg-destructive/5",
          selected && "ring-1 ring-primary/40",
        )}
      >
        <span className={cn("h-2 w-2 shrink-0 rounded-full", style.dotClass)} aria-hidden />
        {iconForCli(cli)}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{cli.name}</span>
            <span className="rounded-full border border-border px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground/80">
              {cli.category}
            </span>
            {cli.is_custom && (
              <span className="rounded-full border border-primary/40 px-1.5 py-0.5 text-[10px] font-normal text-primary">
                custom
              </span>
            )}
          </div>
          <div className="truncate text-xs text-muted-foreground/80">
            {cli.display_name} · {cli.description}
          </div>
        </div>
        <span className="hidden w-20 text-right font-mono text-[11px] text-muted-foreground tabular-nums sm:inline">
          {cli.version ?? "—"}
        </span>
        <span className={cn("hidden w-24 text-right text-[11px] tabular-nums sm:inline", style.color)}>
          {style.label}
        </span>
        {cli.usage_count_7d > 0 ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onShowUsage();
            }}
            className="hidden w-24 text-right text-[11px] text-muted-foreground/70 tabular-nums hover:text-primary md:inline"
            title="Usage-History öffnen"
          >
            {cli.usage_count_7d}×/7d
          </button>
        ) : (
          <span className="hidden w-24 text-right text-[11px] text-muted-foreground/40 md:inline">—</span>
        )}
        <span className="hidden w-24 text-right text-[11px] text-muted-foreground/70 md:inline">
          {formatRelativeTime(cli.last_used_at)}
        </span>
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 transition-transform",
            selected && "rotate-90",
            "text-muted-foreground/50",
          )}
        />
      </button>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Detail Panel (mit Install/Connect/Disconnect Actions)
// ---------------------------------------------------------------------------

function DetailPanel({
  name,
  onClose,
  onShowUsage,
}: {
  name: string;
  onClose: () => void;
  onShowUsage: () => void;
}) {
  const { data, isLoading, error } = useCliDetail(name);
  const check = useCheckCli();
  const disconnect = useDisconnectCli();
  const deleteCustom = useDeleteCustomCli();
  const pushToast = useEventStore((s) => s.pushToast);
  const [apiKeyDialog, setApiKeyDialog] = useState(false);
  const [installDialog, setInstallDialog] = useState(false);

  return (
    <div className="flex w-[420px] flex-col border-l border-border bg-card/30">
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">
            {data?.display_name ?? name}
          </div>
          <div className="truncate text-[11px] text-muted-foreground/80">
            {data?.description ?? ""}
          </div>
        </div>
        <Button
          size="sm" variant="ghost"
          onClick={() =>
            check.mutate(name, {
              onError: (err) =>
                pushToast("error", `Status-Check fehlgeschlagen: ${(err as Error).message}`),
            })
          }
          title="Status neu prüfen"
          aria-label="Status neu prüfen"
          disabled={check.isPending}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", check.isPending && "animate-spin")} />
        </Button>
        <button
          type="button" onClick={onClose}
          className="ml-1 text-muted-foreground hover:text-foreground"
          title="Schließen"
          aria-label="Detail-Panel schließen"
        >
          ×
        </button>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-4 p-5 text-xs">
          {isLoading && <div className="text-muted-foreground">Lade Details…</div>}
          {error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-destructive">
              {(error as Error).message}
            </div>
          )}
          {data && (
            <>
              <div className="flex flex-wrap gap-1.5">
                {!data.installed && data.install_methods.length > 0 && (
                  <Button
                    size="sm"
                    className="btn-primary"
                    onClick={() => setInstallDialog(true)}
                  >
                    <Play className="h-3.5 w-3.5" />
                    <span className="ml-1.5">Installieren</span>
                  </Button>
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
                  <Button
                    size="sm"
                    className="btn-primary"
                    onClick={() => setApiKeyDialog(true)}
                  >
                    <LogIn className="h-3.5 w-3.5" />
                    <span className="ml-1.5">API-Key setzen</span>
                  </Button>
                )}
                {data.connected && data.auth_mode !== "none" && data.auth_mode !== "config_file" && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      disconnect.mutate(name, {
                        onSuccess: (res) => {
                          if (res.ok) {
                            pushToast("success", `${name} getrennt`);
                          } else {
                            pushToast("error", res.error || "Trennen fehlgeschlagen");
                          }
                        },
                        onError: (err) =>
                          pushToast("error", `Trennen fehlgeschlagen: ${(err as Error).message}`),
                      })
                    }
                    disabled={disconnect.isPending}
                  >
                    <LogOut className="h-3.5 w-3.5" />
                    <span className="ml-1.5">Trennen</span>
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={onShowUsage}>
                  <Clock className="h-3.5 w-3.5" />
                  <span className="ml-1.5">History</span>
                </Button>
                {data.is_custom && (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Custom-CLI ${name} entfernen`}
                    title="Custom-CLI entfernen"
                    disabled={deleteCustom.isPending}
                    onClick={() => {
                      if (window.confirm(`Custom-CLI "${name}" wirklich entfernen?`)) {
                        deleteCustom.mutate(name, {
                          onSuccess: () => {
                            pushToast("success", `${name} entfernt`);
                            onClose();
                          },
                          onError: (err) =>
                            pushToast("error", `Fehler: ${(err as Error).message}`),
                        });
                      }
                    }}
                    className="text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>

              <Section title="Status">
                <KeyVal k="Binary" v={data.binary_name} />
                <KeyVal k="Pfad" v={data.binary_path ?? "nicht gefunden"} />
                <KeyVal k="Version" v={data.version ?? "—"} />
                <KeyVal k="Auth-Mode" v={data.auth_mode} />
                <KeyVal k="Status" v={STATUS_STYLES[data.status].label} />
                {data.error && <KeyVal k="Fehler" v={data.error} tone="error" />}
              </Section>

              <Section title="Commands">
                <KeyVal k="Check" v={data.check_command} mono />
                {data.login_command && <KeyVal k="Login" v={data.login_command} mono />}
                {data.status_command && <KeyVal k="Auth-Status" v={data.status_command} mono />}
                {data.logout_command && <KeyVal k="Logout" v={data.logout_command} mono />}
              </Section>

              {data.secret_keys.length > 0 && (
                <Section title="Secrets (API-Key-Auth)">
                  {data.secret_keys.map((sk) => (
                    <KeyVal
                      key={sk.name}
                      k={sk.env_var}
                      v={data.secrets_set[sk.name] ? "●●●●● gesetzt" : "(nicht gesetzt)"}
                      tone={data.secrets_set[sk.name] ? "ok" : "muted"}
                    />
                  ))}
                </Section>
              )}

              <Section title="Risk-Tier">
                <KeyVal k="Default-Tier" v={data.risk_tier} />
                {data.deny_patterns.length > 0 && (
                  <div className="mt-1.5">
                    <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
                      Blacklist
                    </div>
                    <ul className="space-y-0.5">
                      {data.deny_patterns.map((p) => (
                        <li key={p} className="font-mono text-[10px] text-destructive/80">
                          {p}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {data.allow_patterns.length > 0 && (
                  <div className="mt-1.5">
                    <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
                      Whitelist
                    </div>
                    <ul className="space-y-0.5">
                      {data.allow_patterns.map((p) => (
                        <li key={p} className="font-mono text-[10px] text-primary/80">
                          {p}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </Section>

              {data.tool_schema_examples.length > 0 && (
                <Section title="Tool-Beispiele (für Brain)">
                  <ul className="space-y-1">
                    {data.tool_schema_examples.map((e) => (
                      <li key={e} className="font-mono text-[10px] text-muted-foreground/90">
                        {e}
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              {data.homepage && (
                <a
                  href={data.homepage}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-[11px] text-primary hover:underline"
                >
                  Dokumentation
                  <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </>
          )}
        </div>
      </ScrollArea>

      {apiKeyDialog && data && (
        <ApiKeyDialog detail={data} onClose={() => setApiKeyDialog(false)} />
      )}
      {installDialog && data && (
        <InstallDialog detail={data} onClose={() => setInstallDialog(false)} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// OAuth-Connect Button (inline — startet und zeigt Toast)
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
  // Spawnt ein **echtes** externes Windows Terminal (wt/pwsh) und tippt
  // den login_command direkt rein — User sieht das Terminal-Fenster
  // aufpoppen, OAuth-Browser-Flow startet, Terminal bleibt offen.
  //
  // Zusaetzlich wird ``cliConnectCoach`` im Store gesetzt — der globale
  // ``CliConnectPoller`` (in App.tsx) checkt dann alle 3s den Auth-Status
  // und setzt den Coach-State zurueck wenn der Login durch ist. Damit
  // erscheint der Toast "X ist verbunden" und die CLIs-Liste refreshed
  // automatisch, egal in welcher Sektion der User gerade ist.
  const spawn = useSpawnExternalTerminal();
  const pushToast = useEventStore((s) => s.pushToast);
  const setCoach = useEventStore((s) => s.setCliConnectCoach);
  return (
    <Button
      size="sm"
      className="btn-primary"
      disabled={spawn.isPending}
      onClick={() =>
        spawn.mutate(
          { name, kind: "login" },
          {
            onSuccess: (res) => {
              if (res.ok) {
                // Coach setzen damit der Headless-Poller los polled.
                setCoach({
                  cliName: name,
                  displayName,
                  authMode: "oauth_cli",
                  loginCommand,
                  statusCommand,
                });
                pushToast(
                  "info",
                  `Terminal geoeffnet (${res.method}) — folge dem Browser-Login. Status wird automatisch geprueft.`,
                );
              } else {
                pushToast("error", res.error || "Terminal-Spawn fehlgeschlagen");
              }
            },
            onError: (err) => pushToast("error", (err as Error).message),
          },
        )
      }
    >
      <LogIn className="h-3.5 w-3.5" />
      <span className="ml-1.5">Browser-Login</span>
    </Button>
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
  const connect = useConnectCli();
  const pushToast = useEventStore((s) => s.pushToast);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="flex w-full max-w-md flex-col rounded-xl border border-border bg-card shadow-lg">
        <div className="flex items-start justify-between gap-4 border-b border-border p-5">
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-base font-semibold">
              {detail.display_name} — API-Key setzen
            </h3>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Keys werden im Windows Credential Manager gespeichert und beim
              Aufruf als ENV-Variable injiziert.
            </p>
          </div>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3 p-5">
          {detail.secret_keys.map((sk) => (
            <label key={sk.name} className="block text-xs">
              <div className="mb-1 flex items-center justify-between">
                <span className="font-medium">{sk.env_var}</span>
                {sk.required && <span className="text-[10px] text-destructive">required</span>}
              </div>
              <input
                type="password"
                autoComplete="new-password"
                value={values[sk.name] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [sk.name]: e.target.value }))}
                className="w-full rounded-md border border-input bg-background px-3 py-1.5 font-mono text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                placeholder={
                  detail.secrets_set[sk.name] ? "●●●●● (bereits gesetzt — überschreiben)" : "API-Key eingeben"
                }
              />
            </label>
          ))}

          {error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-[11px] text-destructive">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border p-4">
          <Button type="button" variant="ghost" onClick={onClose} disabled={connect.isPending}>
            Abbrechen
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
                      pushToast("success", `${detail.name} verbunden`);
                      onClose();
                    } else {
                      setError(res.error || "Validation fehlgeschlagen");
                    }
                  },
                  onError: (err) => setError((err as Error).message),
                },
              );
            }}
          >
            {connect.isPending ? "Validiere…" : "Speichern & Validieren"}
          </Button>
        </div>
      </div>
    </div>
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
  // Wir nutzen Spawn-External (echtes Windows Terminal) statt das interne
  // xterm. ``useInstallCli`` (Background-Subprocess + Output-Streaming)
  // bleibt im Repo erhalten fuer headless/Voice-Pfade — UI-seitig haben
  // wir aber explizit das externe Terminal, weil der User sehen will
  // wie der Install in einer "echten" PowerShell laeuft.
  const spawn = useSpawnExternalTerminal();
  const pushToast = useEventStore((s) => s.pushToast);
  const [selected, setSelected] = useState<string>(
    detail.recommended_install ?? detail.install_methods[0]?.manager ?? "manual",
  );
  const selectedMethod = detail.install_methods.find((m) => m.manager === selected);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="flex w-full max-w-lg flex-col rounded-xl border border-border bg-card shadow-lg">
        <div className="flex items-start justify-between gap-4 border-b border-border p-5">
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-base font-semibold">
              {detail.display_name} installieren
            </h3>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Oeffnet ein externes Windows Terminal mit dem Install-Command.
              Der Status wird automatisch geprueft sobald das Terminal fertig ist.
            </p>
          </div>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3 p-5">
          <div>
            <div className="mb-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/70">
              Methode wählen
            </div>
            <div className="space-y-1">
              {detail.install_methods.map((m) => (
                <label
                  key={m.manager}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 transition-colors",
                    selected === m.manager
                      ? "border-primary/40 bg-primary/10"
                      : "border-border hover:bg-card/60",
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
                  <span className="text-xs font-medium">{m.manager}</span>
                  {m.manager === detail.recommended_install && (
                    <span className="rounded-full border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-[9px] text-primary">
                      empfohlen
                    </span>
                  )}
                </label>
              ))}
            </div>
          </div>

          {selectedMethod && (
            <div>
              <div className="mb-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/70">
                Befehl
              </div>
              <code className="block break-all rounded-md border border-border bg-background px-3 py-2 font-mono text-[10px]">
                {selectedMethod.command}
              </code>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border p-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Abbrechen
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
                      pushToast(
                        "info",
                        `Externes Terminal geoeffnet (${res.method}) — Install laeuft. Status wird automatisch aktualisiert.`,
                      );
                      onClose();
                    } else {
                      pushToast("error", res.error || "Terminal-Spawn fehlgeschlagen");
                    }
                  },
                  onError: (err) => pushToast("error", (err as Error).message),
                },
              );
            }}
          >
            {spawn.isPending ? "Spawne…" : selected === "manual" ? "Öffnen" : "Im Terminal installieren"}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Custom-CLI Wizard (4 Steps)
// ---------------------------------------------------------------------------

function CustomCliWizard({ onClose }: { onClose: () => void }) {
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
        pushToast("success", `${form.name} registriert`);
        onClose();
      },
      onError: (err) => setError((err as Error).message),
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="flex w-full max-w-2xl flex-col rounded-xl border border-border bg-card shadow-lg">
        <div className="flex items-start justify-between gap-4 border-b border-border p-5">
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-base font-semibold">
              Custom CLI hinzufügen · Schritt {step}/4
            </h3>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {step === 1 && "Identität der CLI"}
              {step === 2 && "Check + Version"}
              {step === 3 && "Auth-Konfiguration"}
              {step === 4 && "Risk-Tier + Patterns"}
            </p>
          </div>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid gap-3 p-5 text-xs">
          {step === 1 && (
            <>
              <TextField label="Name (id, lowercase)" val={form.name}
                onChange={(v) => setForm({ ...form, name: v.toLowerCase() })}
                placeholder="z.B. mytool" />
              <TextField label="Display-Name" val={form.display_name}
                onChange={(v) => setForm({ ...form, display_name: v })}
                placeholder="z.B. My Tool CLI" />
              <TextField label="Binary-Name (was im PATH liegt)" val={form.binary_name}
                onChange={(v) => setForm({ ...form, binary_name: v })}
                placeholder="z.B. mytool" />
              <TextField label="Beschreibung" val={form.description}
                onChange={(v) => setForm({ ...form, description: v })}
                placeholder="Was macht die CLI?" />
              <TextField label="Kategorie" val={form.category}
                onChange={(v) => setForm({ ...form, category: v })}
                placeholder="cloud / git / payments / other" />
              <TextField label="Homepage-URL" val={form.homepage}
                onChange={(v) => setForm({ ...form, homepage: v })}
                placeholder="https://..." />
            </>
          )}
          {step === 2 && (
            <>
              <TextField label="Check-Command" val={form.check_command}
                onChange={(v) => setForm({ ...form, check_command: v })}
                placeholder="mytool --version" mono />
              <TextField label="Version-Regex (eine Capture-Group)" val={form.version_parse_regex}
                onChange={(v) => setForm({ ...form, version_parse_regex: v })}
                placeholder="v(\\S+)" mono />
            </>
          )}
          {step === 3 && (
            <>
              <label className="block">
                <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
                  Auth-Mode
                </div>
                <select
                  value={form.auth_mode}
                  onChange={(e) => setForm({ ...form, auth_mode: e.target.value as typeof form.auth_mode })}
                  className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-xs"
                >
                  <option value="none">none (keine Auth)</option>
                  <option value="oauth_cli">oauth_cli (CLI-eigener Login)</option>
                  <option value="api_key">api_key (Keyring + ENV-Injection)</option>
                  <option value="config_file">config_file (fremdverwaltet)</option>
                </select>
              </label>
              {(form.auth_mode === "oauth_cli") && (
                <TextField label="Login-Command" val={form.login_command}
                  onChange={(v) => setForm({ ...form, login_command: v })}
                  placeholder="mytool login" mono />
              )}
              {form.auth_mode !== "none" && (
                <TextField label="Status-Command" val={form.status_command}
                  onChange={(v) => setForm({ ...form, status_command: v })}
                  placeholder="mytool whoami" mono />
              )}
              {form.auth_mode === "api_key" && (
                <>
                  <TextField label="Secret-Keys (comma-separated)" val={form.secret_keys}
                    onChange={(v) => setForm({ ...form, secret_keys: v })}
                    placeholder="mytool_api_key" mono />
                  <TextField label="ENV-Vars (comma-separated, 1:1 Reihenfolge)" val={form.env_vars}
                    onChange={(v) => setForm({ ...form, env_vars: v })}
                    placeholder="MYTOOL_API_KEY" mono />
                </>
              )}
            </>
          )}
          {step === 4 && (
            <>
              <label className="block">
                <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
                  Risk-Tier
                </div>
                <select
                  value={form.risk_tier}
                  onChange={(e) => setForm({ ...form, risk_tier: e.target.value as typeof form.risk_tier })}
                  className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-xs"
                >
                  <option value="safe">safe (läuft ohne Nachfrage)</option>
                  <option value="monitor">monitor (execute + log)</option>
                  <option value="ask">ask (Bestätigung pro Call)</option>
                  <option value="block">block (komplett blockiert)</option>
                </select>
              </label>
              <TextArea label="Allow-Patterns (eins pro Zeile)" val={form.allow_patterns}
                onChange={(v) => setForm({ ...form, allow_patterns: v })}
                placeholder="mytool get *&#10;mytool list*" />
              <TextArea label="Deny-Patterns (eins pro Zeile)" val={form.deny_patterns}
                onChange={(v) => setForm({ ...form, deny_patterns: v })}
                placeholder="mytool delete *&#10;mytool rm *" />
            </>
          )}

          {error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-[11px] text-destructive">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border p-4">
          <div className="text-[10px] text-muted-foreground">
            {step < 4 ? "Schritte können übersprungen werden via 'Speichern'" : "Fertig!"}
          </div>
          <div className="flex items-center gap-2">
            {step > 1 && (
              <Button variant="ghost" onClick={() => setStep(step - 1)}>
                Zurück
              </Button>
            )}
            {step < 4 ? (
              <Button
                className="btn-primary"
                disabled={!canNext()}
                onClick={() => setStep(step + 1)}
              >
                Weiter
              </Button>
            ) : (
              <Button className="btn-primary" onClick={submit} disabled={register.isPending}>
                {register.isPending ? "Speichert…" : "Speichern"}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
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
      <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
        {label}
      </div>
      <input
        type="text" value={val}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={cn(
          "w-full rounded-md border border-input bg-background px-3 py-1.5 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          mono && "font-mono text-[11px]",
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
      <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
        {label}
      </div>
      <textarea
        value={val}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-24 w-full resize-none rounded-md border border-input bg-background px-3 py-1.5 font-mono text-[11px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      />
    </label>
  );
}

// ---------------------------------------------------------------------------
// Usage Drawer
// ---------------------------------------------------------------------------

function UsageDrawer({ name, onClose }: { name: string; onClose: () => void }) {
  const [page, setPage] = useState(1);
  const [successOnly, setSuccessOnly] = useState(false);
  const [search, setSearch] = useState("");
  const { data: usage } = useCliUsage(name, { page, pageSize: 50, successOnly, search });
  const { data: stats } = useCliStats(name);
  const clear = useClearUsage();
  const pushToast = useEventStore((s) => s.pushToast);

  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-end bg-black/60 backdrop-blur-sm">
      <div className="flex w-[520px] flex-col border-l border-border bg-card shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-sm font-semibold">Usage · {name}</h3>
            {stats && (
              <div className="mt-0.5 text-[11px] text-muted-foreground tabular-nums">
                {stats.total_calls} total · {Math.round(stats.success_rate * 100)}% success ·{" "}
                avg {stats.avg_duration_ms}ms
              </div>
            )}
          </div>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex items-center gap-2 border-b border-border px-5 py-2.5">
          <input
            type="text"
            value={search}
            onChange={(e) => {
              setPage(1);
              setSearch(e.target.value);
            }}
            placeholder="Search command…"
            className="flex-1 rounded-md border border-input bg-background px-2 py-1 text-xs"
          />
          <label className="flex items-center gap-1 text-[11px]">
            <input
              type="checkbox"
              checked={successOnly}
              onChange={(e) => {
                setPage(1);
                setSuccessOnly(e.target.checked);
              }}
              className="accent-primary"
            />
            success only
          </label>
        </div>

        <ScrollArea className="flex-1">
          <div className="p-5 text-xs">
            {!usage && <div className="text-muted-foreground">Lade…</div>}
            {usage && usage.entries.length === 0 && (
              <div className="py-8 text-center text-muted-foreground">
                Keine Einträge {search && `(suche: "${search}")`}
              </div>
            )}
            {usage && usage.entries.length > 0 && (
              <ul className="space-y-1.5">
                {usage.entries.map((e) => (
                  <li
                    key={e.id}
                    className={cn(
                      "rounded-md border border-border bg-card/40 px-3 py-2",
                      e.exit_code === 0 && "border-primary/20",
                      e.exit_code !== null && e.exit_code !== 0 && "border-destructive/30",
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <code className="min-w-0 flex-1 break-all font-mono text-[11px]">
                        {e.full_command}
                      </code>
                      <span
                        className={cn(
                          "shrink-0 text-[10px] tabular-nums",
                          e.exit_code === 0 ? "text-primary" : "text-destructive",
                        )}
                      >
                        {e.exit_code === 0 ? "✓" : e.exit_code !== null ? `✗ ${e.exit_code}` : "…"}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground tabular-nums">
                      <span>{formatDateTime(e.started_at)}</span>
                      {e.duration_ms !== null && <span>{e.duration_ms}ms</span>}
                      <span>caller: {e.caller}</span>
                      {e.trace_id && (
                        <span title={e.trace_id}>T:{e.trace_id.slice(0, 8)}</span>
                      )}
                    </div>
                    {e.stderr_preview && (
                      <div className="mt-1.5 rounded border border-destructive/30 bg-destructive/5 px-2 py-1 font-mono text-[10px] text-destructive/90">
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
          <div className="border-t border-border px-5 py-2.5 text-[10px]">
            <div className="mb-1 uppercase tracking-wider text-muted-foreground/70">
              Top-Commands
            </div>
            <ul className="space-y-0.5">
              {stats.top_commands.slice(0, 3).map(([cmd, count]) => (
                <li key={cmd} className="flex items-center justify-between gap-2">
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
              if (window.confirm(`Komplette History von "${name}" löschen?`)) {
                clear.mutate(name, {
                  onSuccess: (res) => pushToast("success", `${res.deleted} Einträge gelöscht`),
                  onError: (err) => pushToast("error", (err as Error).message),
                });
              }
            }}
            className="text-destructive hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span className="ml-1.5">Clear</span>
          </Button>
          <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
            {usage && (
              <>
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
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Atoms
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="mb-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/70">
        {title}
      </h4>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function KeyVal({
  k, v, mono, tone,
}: {
  k: string; v: string; mono?: boolean; tone?: "muted" | "ok" | "error";
}) {
  return (
    <div className="flex items-start gap-2 text-[11px]">
      <span className="w-20 shrink-0 text-muted-foreground/70">{k}</span>
      <span
        className={cn(
          "min-w-0 flex-1 break-all",
          mono && "font-mono text-[10px]",
          tone === "muted" && "text-muted-foreground/60",
          tone === "ok" && "text-primary",
          tone === "error" && "text-destructive",
        )}
      >
        {v}
      </span>
    </div>
  );
}

function EmptyState({ hasAny, filter }: { hasAny: boolean; filter: FilterTab }) {
  if (!hasAny) {
    return (
      <div className="flex flex-col items-center justify-center gap-5 py-16 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-border bg-card/60">
          <Terminal className="h-7 w-7 text-muted-foreground" />
        </div>
        <div className="max-w-lg space-y-3">
          <h3 className="font-display text-xl font-semibold tracking-tight">
            Keine CLIs im Katalog
          </h3>
        </div>
      </div>
    );
  }
  const messages: Record<FilterTab, string> = {
    all: "Kein CLI matched die Filter.",
    connected: "Keine CLI ist gerade verbunden.",
    installed: "Keine CLI ist installiert.",
    custom: "Noch keine Custom-CLI registriert — klick 'Add Custom' oben rechts.",
  };
  return (
    <div className="py-12 text-center text-sm text-muted-foreground">
      {messages[filter]}
    </div>
  );
}
