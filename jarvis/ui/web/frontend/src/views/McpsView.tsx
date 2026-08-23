import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileJson,
  X,
  Copy,
  RefreshCw,
  Search,
  Download,
  MoreHorizontal,
  Trash2,
  Activity,
} from "lucide-react";
import { McpLogo } from "@/components/extensions/McpLogo";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import {
  ActionMenu,
  BackLink,
  Cell,
  ClampedText,
  DetailHeader,
  EmptyRow,
  FactRows,
  IconButton,
  InlineSearch,
  MenuPill,
  Panel,
  PanelHeader,
  StatusDot,
  Table,
  TableHead,
  TableRow,
  type Column,
} from "@/components/extensions/primitives";
import { FileCard, type CardFile } from "@/components/extensions/FileCard";
import { useEventStore } from "@/store/events";
import { robustCopy } from "@/lib/clipboard";
import { fill, useT } from "@/i18n";

// ---------------------------------------------------------------------------
// Wire types — mirror /api/mcps
// ---------------------------------------------------------------------------

interface McpTool {
  name: string;
  description: string;
}

interface McpServer {
  name: string;
  display: string;
  description?: string;
  transport?: string;
  mandatory?: boolean;
  platform_notes?: string;
  install_command?: string[];
  is_bootstrap?: boolean;
  enabled: boolean;
  status: "running" | "stopped" | "not-initialized";
  error: string | null;
  tools: McpTool[];
  credentials_complete: boolean;
  credentials_status?: Record<string, boolean>;
  required_auth: string[];
}

interface McpsResponse {
  servers: McpServer[];
  total: number;
  running: number;
  registry_ready: boolean;
}

async function fetchMcps(): Promise<McpsResponse> {
  const res = await fetch("/api/mcps");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function postJson<T>(
  url: string,
  body?: unknown,
  method: "POST" | "DELETE" | "PUT" = "POST",
): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${txt || res.statusText}`);
  }
  return res.json();
}

type Tone = "ok" | "off" | "warn" | "error" | "busy";

function statusOf(
  server: McpServer,
  pending: boolean,
  t: (k: string) => string,
): { label: string; tone: Tone } {
  if (pending) return { label: t("mcps_view.status.checking"), tone: "busy" };
  if (server.error) return { label: t("common.error"), tone: "error" };
  if (server.status === "running" && server.enabled) {
    return { label: t("mcps_view.connected"), tone: "ok" };
  }
  if (!server.credentials_complete) {
    return { label: t("mcps_view.credentials_incomplete"), tone: "warn" };
  }
  return { label: t("mcps_view.disconnected"), tone: "off" };
}

function toolCountLabel(n: number, t: (k: string) => string): string {
  return n === 1 ? t("mcps_view.tool_one") : fill(t("mcps_view.tools"), { n });
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

export function McpsView() {
  const t = useT();
  const qc = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const [showConfig, setShowConfig] = useState(false);
  const [checkingName, setCheckingName] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [confirmRemove, setConfirmRemove] = useState<McpServer | null>(null);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["mcps"],
    queryFn: fetchMcps,
    refetchInterval: 5_000,
  });

  const importClaude = useMutation({
    mutationFn: () =>
      postJson<{ ok: boolean; count: number; added: string[]; note: string }>(
        "/api/mcps/import-claude-desktop",
      ),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["mcps"] });
      pushToast(res.count > 0 ? "success" : "info", res.note);
    },
    onError: (err) => {
      pushToast("error", `${t("mcps_view.import_failed")}: ${(err as Error).message}`);
    },
  });

  const toggle = useMutation({
    mutationFn: async ({ name, enable }: { name: string; enable: boolean }) => {
      setCheckingName(name);
      const action = enable ? "enable" : "disable";
      return postJson<{ ok: boolean; error?: string; enabled: boolean }>(
        `/api/mcps/${name}/${action}`,
      );
    },
    onSettled: () => setCheckingName(null),
    onSuccess: (res, vars) => {
      qc.invalidateQueries({ queryKey: ["mcps"] });
      if (res.ok) {
        pushToast(
          "success",
          vars.enable
            ? t("mcps_toast.connected").replace("{0}", vars.name)
            : `${vars.name} ${t("mcps_view.disconnected").toLowerCase()}`,
        );
      } else if (res.error) {
        pushToast("error", `${vars.name}: ${res.error}`);
      }
    },
    onError: (err, vars) => {
      pushToast("error", `${vars.name}: ${(err as Error).message}`);
    },
  });

  const check = useMutation({
    mutationFn: async (name: string) => {
      setCheckingName(name);
      return postJson<{ ok: boolean; tools_count: number; error: string | null }>(
        `/api/mcps/${name}/check`,
      );
    },
    onSettled: () => setCheckingName(null),
    onSuccess: (res, name) => {
      qc.invalidateQueries({ queryKey: ["mcps"] });
      if (res.ok) {
        pushToast("success", `${name}: ${fill(t("mcps_view.check_ok"), { n: res.tools_count })}`);
      } else {
        pushToast("error", `${name}: ${res.error ?? t("mcps_view.check_failed")}`);
      }
    },
    onError: (err, name) => {
      pushToast("error", `${name}: ${(err as Error).message}`);
    },
  });

  const remove = useMutation({
    mutationFn: async (name: string) =>
      postJson<{ ok: boolean; removed?: boolean }>(`/api/mcps/${name}`, undefined, "DELETE"),
    onSuccess: (_res, name) => {
      qc.invalidateQueries({ queryKey: ["mcps"] });
      setConfirmRemove(null);
      if (selected === name) setSelected(null);
      pushToast("success", fill(t("mcps_view.removed"), { name }));
    },
    onError: (err, name) => {
      pushToast("error", `${name}: ${(err as Error).message}`);
    },
  });

  const servers = data?.servers ?? [];
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return servers;
    return servers.filter((s) =>
      [s.name, s.display, s.description ?? "", s.transport ?? ""].join(" ").toLowerCase().includes(q),
    );
  }, [servers, query]);

  const closeSearch = () => {
    setQuery("");
    setSearchOpen(false);
  };

  const dialogs = (
    <>
      {showConfig && <ConfigModal onClose={() => setShowConfig(false)} />}
      {confirmRemove && (
        <RemoveConfirmDialog
          server={confirmRemove}
          pending={remove.isPending}
          onCancel={() => setConfirmRemove(null)}
          onConfirm={() => remove.mutate(confirmRemove.name)}
        />
      )}
    </>
  );

  // ---- Detail page -------------------------------------------------------
  const current = selected ? servers.find((s) => s.name === selected) ?? null : null;
  if (selected) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <ScrollArea className="flex-1">
          <div className="mx-auto w-full max-w-4xl px-8 py-6">
            <BackLink label={t("mcps_view.title")} onClick={() => setSelected(null)} />
            {current ? (
              <McpDetail
                server={current}
                pending={checkingName === current.name}
                onToggle={(enable) => toggle.mutate({ name: current.name, enable })}
                onCheck={() => check.mutate(current.name)}
                onRemove={() => setConfirmRemove(current)}
                onEditConfig={() => setShowConfig(true)}
              />
            ) : (
              <div className="mt-6 text-sm text-muted-foreground">
                {isLoading ? t("common.loading") : t("mcps_view.not_found")}
              </div>
            )}
          </div>
        </ScrollArea>
        {dialogs}
      </div>
    );
  }

  // ---- List page ---------------------------------------------------------
  const columns: Column[] = [
    { id: "name", label: t("mcps_view.col_server") },
    { id: "transport", label: t("mcps_view.col_transport"), width: "110px" },
    { id: "status", label: t("mcps_view.col_status"), width: "170px" },
    { id: "enabled", label: t("mcps_view.col_enabled"), width: "44px", srOnly: true, align: "right" },
  ];

  const subtitle =
    !isLoading && !error
      ? fill(t("mcps_view.count"), { running: data?.running ?? 0, total: data?.total ?? 0 })
      : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1">
        <div className="mx-auto w-full max-w-4xl px-8 py-6">
          <PanelHeader
            title={t("mcps_view.title")}
            subtitle={subtitle}
            actions={
              <>
                <IconButton
                  label={t("mcps_view.search_placeholder")}
                  active={searchOpen}
                  onClick={() => (searchOpen ? closeSearch() : setSearchOpen(true))}
                >
                  <Search className="h-4 w-4" />
                </IconButton>
                <IconButton
                  label={t("mcps_view.reload_tooltip")}
                  onClick={() => void refetch()}
                  busy={isFetching && !data}
                >
                  <RefreshCw className="h-4 w-4" />
                </IconButton>
                <IconButton label={t("mcps_view.edit_tooltip")} onClick={() => setShowConfig(true)} className="ml-1">
                  <FileJson className="h-4 w-4" />
                </IconButton>
                <ActionMenu
                  label={t("mcps_view.add")}
                  actions={[
                    {
                      id: "import",
                      label: t("mcps_view.import_claude"),
                      icon: <Download className="h-3.5 w-3.5" />,
                      disabled: importClaude.isPending,
                      onSelect: () => importClaude.mutate(),
                    },
                    {
                      id: "edit",
                      label: t("mcps_view.add_edit"),
                      icon: <FileJson className="h-3.5 w-3.5" />,
                      onSelect: () => setShowConfig(true),
                    },
                  ]}
                  trigger={({ open, toggle: toggleMenu }) => (
                    <MenuPill open={open} toggle={toggleMenu}>
                      {t("mcps_view.add")}
                    </MenuPill>
                  )}
                />
              </>
            }
          />

          {searchOpen && (
            <div className="mt-4 flex items-center gap-2">
              <div className="flex-1">
                <InlineSearch
                  value={query}
                  onChange={setQuery}
                  placeholder={t("mcps_view.search_placeholder")}
                  autoFocus
                />
              </div>
              <IconButton label={t("common.close")} onClick={closeSearch}>
                <X className="h-4 w-4" />
              </IconButton>
            </div>
          )}

          <div className="mt-4">
            {isLoading && (
              <div className="py-8 text-center text-sm text-muted-foreground">{t("common.loading")}</div>
            )}
            {error && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                {t("common.error")}: {(error as Error).message}
              </div>
            )}
            {!isLoading && !error && servers.length === 0 && (
              <EmptyState
                onOpenConfig={() => setShowConfig(true)}
                onImport={() => importClaude.mutate()}
                importing={importClaude.isPending}
              />
            )}
            {!isLoading && !error && servers.length > 0 && (
              <Table label={t("mcps_view.title")}>
                <TableHead columns={columns} />
                {visible.map((s) => {
                  const pending = checkingName === s.name;
                  const st = statusOf(s, pending, t);
                  return (
                    <TableRow
                      key={s.name}
                      columns={columns}
                      onClick={() => setSelected(s.name)}
                      ariaLabel={s.name}
                    >
                      <Cell>
                        <div className="flex items-center gap-2" title={s.description}>
                          <span className="truncate text-[15px] font-medium">{s.name}</span>
                          {s.tools.length > 0 && (
                            <span className="shrink-0 text-xs text-muted-foreground">
                              {toolCountLabel(s.tools.length, t)}
                            </span>
                          )}
                        </div>
                      </Cell>
                      <Cell muted>
                        <span className="font-mono text-xs">{s.transport ?? "—"}</span>
                      </Cell>
                      <Cell>
                        <span title={s.error ?? undefined}>
                          <StatusDot tone={st.tone} label={st.label} pulse={pending} />
                        </span>
                      </Cell>
                      <Cell align="right" stop>
                        <Switch
                          checked={s.enabled}
                          disabled={pending}
                          onCheckedChange={(next) => toggle.mutate({ name: s.name, enable: next })}
                          aria-label={`${s.name}: ${s.enabled ? t("mcps_view.status.connected") : t("mcps_view.status.disconnected")}`}
                        />
                      </Cell>
                    </TableRow>
                  );
                })}
                {visible.length === 0 && <EmptyRow>{t("mcps_view.search_no_hits")}</EmptyRow>}
              </Table>
            )}
          </div>
        </div>
      </ScrollArea>
      {dialogs}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail page
// ---------------------------------------------------------------------------

async function fetchMcpFiles(name: string): Promise<{ files: CardFile[]; config_path: string }> {
  const res = await fetch(`/api/mcps/${encodeURIComponent(name)}/files`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function McpDetail({
  server,
  pending,
  onToggle,
  onCheck,
  onRemove,
  onEditConfig,
}: {
  server: McpServer;
  pending: boolean;
  onToggle: (enable: boolean) => void;
  onCheck: () => void;
  onRemove: () => void;
  onEditConfig: () => void;
}) {
  const t = useT();
  const filesQuery = useQuery({
    queryKey: ["mcp-files", server.name],
    queryFn: () => fetchMcpFiles(server.name),
  });
  const st = statusOf(server, pending, t);
  const missing = (server.required_auth ?? []).filter(
    (k) => server.credentials_status && server.credentials_status[k] === false,
  );
  const command = (server.install_command ?? []).join(" ");

  return (
    <div className="mt-5">
      <DetailHeader
        leading={
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-border bg-sheen/[0.05]">
            <McpLogo className="h-5 w-5 text-muted-foreground" />
          </span>
        }
        title={server.name}
        byline={
          <span className="inline-flex items-center gap-2">
            <StatusDot tone={st.tone} label={st.label} pulse={pending} />
            {server.tools.length > 0 && <span>· {toolCountLabel(server.tools.length, t)}</span>}
          </span>
        }
        actions={
          <>
            <Switch
              checked={server.enabled}
              disabled={pending}
              onCheckedChange={onToggle}
              aria-label={`${server.name}: ${server.enabled ? t("mcps_view.status.connected") : t("mcps_view.status.disconnected")}`}
            />
            <ActionMenu
              label={t("mcps_view.more_actions")}
              actions={[
                {
                  id: "check",
                  label: t("mcps_view.check"),
                  icon: <Activity className="h-3.5 w-3.5" />,
                  disabled: pending,
                  onSelect: onCheck,
                },
                {
                  id: "edit",
                  label: t("mcps_view.add_edit"),
                  icon: <FileJson className="h-3.5 w-3.5" />,
                  onSelect: onEditConfig,
                },
                {
                  id: "remove",
                  label: t("mcps_view.remove"),
                  icon: <Trash2 className="h-3.5 w-3.5" />,
                  destructive: true,
                  separatorAbove: true,
                  disabled: Boolean(server.mandatory),
                  onSelect: onRemove,
                },
              ]}
              trigger={({ open, toggle }) => (
                <IconButton label={t("mcps_view.more_actions")} onClick={toggle} active={open}>
                  <MoreHorizontal className="h-4 w-4" />
                </IconButton>
              )}
            />
          </>
        }
      />

      {server.description && (
        <ClampedText
          className="mt-4"
          text={server.description}
          moreLabel={t("common.see_more")}
          lessLabel={t("common.see_less")}
        />
      )}

      {server.error && (
        <div className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
          {server.error}
        </div>
      )}

      <Panel className="mt-4">
        <div className="px-5 py-4">
          <FactRows
            rows={[
              { label: t("mcps_view.col_status"), value: st.label },
              { label: t("mcps_view.col_transport"), value: server.transport ?? null },
              {
                label: t("mcps_view.command"),
                value: command ? <code className="font-mono text-[13px]">{command}</code> : null,
              },
              {
                label: t("mcps_view.credentials"),
                value:
                  (server.required_auth ?? []).length === 0
                    ? null
                    : missing.length > 0
                      ? fill(t("mcps_view.credentials_missing"), { list: missing.join(", ") })
                      : t("mcps_view.credentials_ok"),
              },
              { label: t("mcps_view.platform_notes"), value: server.platform_notes || null },
              {
                label: t("mcps_view.origin"),
                value: server.is_bootstrap ? t("mcps_view.origin_bootstrap") : t("mcps_view.origin_config"),
              },
            ]}
          />
        </div>
      </Panel>

      <FileCard
        className="mt-4"
        files={filesQuery.data?.files ?? []}
        loading={filesQuery.isPending}
        error={filesQuery.error ? (filesQuery.error as Error).message : null}
      />

      <Panel className="mt-4">
        <div className="flex items-center gap-2 border-b border-border/70 px-5 py-2.5">
          <span className="text-sm font-medium">{t("mcps_view.tools_heading")}</span>
          <span className="text-sm text-muted-foreground">{server.tools.length}</span>
        </div>
        {server.tools.length === 0 ? (
          <p className="px-5 py-4 text-xs text-muted-foreground">{t("mcps_view.no_tools")}</p>
        ) : (
          <ul className="divide-y divide-border/70">
            {server.tools.map((tool) => (
              <li key={tool.name} className="px-5 py-3">
                <p className="font-mono text-[13px]">{tool.name}</p>
                {tool.description && (
                  <p className="mt-0.5 line-clamp-2 text-[13px] text-muted-foreground" title={tool.description}>
                    {tool.description}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function RemoveConfirmDialog({
  server,
  pending,
  onCancel,
  onConfirm,
}: {
  server: McpServer;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60"
      role="dialog"
      aria-label={t("mcps_view.remove_title")}
    >
      <div className="w-[420px] rounded-lg border border-border bg-card p-6 shadow-xl">
        <h3 className="flex items-center gap-2 text-base font-semibold">
          <Trash2 className="h-4 w-4 text-destructive" />
          {t("mcps_view.remove_title")}
        </h3>
        <p className="mt-2 text-sm text-muted-foreground">{t("mcps_view.remove_body")}</p>
        <p className="mt-1 font-mono text-sm font-medium">{server.name}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
            {t("common.cancel")}
          </Button>
          <Button size="sm" variant="destructive" onClick={onConfirm} disabled={pending}>
            {t("mcps_view.remove_confirm")}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Empty state
// ------------------------------------------------------------------

function EmptyState({
  onOpenConfig,
  onImport,
  importing,
}: {
  onOpenConfig: () => void;
  onImport: () => void;
  importing: boolean;
}) {
  const t = useT();
  return (
    <EmptyRow>
      <p className="font-medium text-foreground">{t("mcps_view.empty_title")}</p>
      <p className="mx-auto mt-2 max-w-md text-xs">{t("mcps_view.empty_description")}</p>
      <div className="mt-4 flex justify-center gap-2">
        <Button size="sm" variant="outline" onClick={onOpenConfig} className="gap-1.5">
          <FileJson className="h-3.5 w-3.5" />
          {t("mcps_view.open_config")}
        </Button>
        <Button size="sm" onClick={onImport} disabled={importing} className="gap-1.5">
          <Download className="h-3.5 w-3.5" />
          {importing ? t("mcps_view.importing") : t("mcps_view.import_claude")}
        </Button>
      </div>
      <p className="mt-4 text-[11px] text-muted-foreground/80">{t("mcps_view.empty_tip")}</p>
    </EmptyRow>
  );
}

// ------------------------------------------------------------------
// Config editor modal
// ------------------------------------------------------------------

function ConfigModal({ onClose }: { onClose: () => void }) {
  const t = useT();
  const qc = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const [editing, setEditing] = useState<string>("");

  const info = useQuery({
    queryKey: ["mcp-config-info"],
    queryFn: async () => {
      const res = await fetch("/api/mcps/config/info");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: { path: string; exists: boolean; content: string | null } =
        await res.json();
      if (data.content !== null) setEditing(data.content);
      else setEditing('{\n  "mcpServers": {}\n}\n');
      return data;
    },
  });

  const save = useMutation({
    mutationFn: async () => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(editing);
      } catch (err) {
        throw new Error(`JSON syntax: ${(err as Error).message}`);
      }
      return postJson<{ ok: boolean; servers: number }>(
        "/api/mcps/config/raw",
        parsed,
        "PUT",
      );
    },
    onSuccess: (res) => {
      pushToast("success", t("mcps_view.saved_servers").replace("{0}", String(res.servers)));
      qc.invalidateQueries({ queryKey: ["mcps"] });
      qc.invalidateQueries({ queryKey: ["mcp-config-info"] });
      onClose();
    },
    onError: (err) => {
      pushToast("error", (err as Error).message);
    },
  });

  const copyPath = async () => {
    const path = info.data?.path;
    if (!path) return;
    const copied = await robustCopy(path);
    pushToast(
      copied ? "info" : "error",
      t(copied ? "mcps_view.path_copied" : "mcps_view.copy_failed"),
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 backdrop-blur-sm">
      <div className="flex w-full max-w-3xl flex-col rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-start justify-between gap-4 border-b border-border p-6">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <FileJson className="h-4 w-4 text-primary" />
              <h3 className="font-display text-lg font-semibold tracking-tight">
                mcp.json
              </h3>
            </div>
            {info.data?.path && (
              <button
                type="button"
                onClick={copyPath}
                className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
                title={t("mcps_view.copy_path")}
              >
                <code className="font-mono">{info.data.path}</code>
                <Copy className="h-3 w-3" />
              </button>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            className="shrink-0 text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-6">
          <textarea
            value={editing}
            onChange={(e) => setEditing(e.target.value)}
            spellCheck={false}
            className="h-[420px] w-full resize-none rounded-md border border-input bg-background px-3 py-2 font-mono text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            placeholder='{"mcpServers": {...}}'
          />
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            {t("mcps_view.config_format_hint")}
          </p>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border p-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("mcps_view.cancel")}
          </Button>
          <Button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending}
          >
            {save.isPending ? t("mcps_view.saving") : t("mcps_view.save")}
          </Button>
        </div>
      </div>
    </div>
  );
}
