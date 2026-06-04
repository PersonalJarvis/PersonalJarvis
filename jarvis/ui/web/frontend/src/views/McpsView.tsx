import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plug,
  FileJson,
  X,
  Copy,
  RefreshCw,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

interface McpServer {
  name: string;
  display: string;
  enabled: boolean;
  status: "running" | "stopped" | "not-initialized";
  error: string | null;
  tools: { name: string; description: string }[];
  credentials_complete: boolean;
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

type StatusLabel = string;

function statusOf(server: McpServer, pending: boolean, t: (k: string) => string): {
  label: StatusLabel;
  color: string;
  dotClass: string;
} {
  if (pending) {
    return {
      label: t("mcps_view.status.checking"),
      color: "text-muted-foreground",
      dotClass: "bg-muted-foreground animate-jarvis-pulse",
    };
  }
  if (server.error) {
    return {
      label: t("mcps_view.status.error"),
      color: "text-destructive",
      dotClass: "bg-destructive",
    };
  }
  if (server.status === "running" && server.enabled) {
    return {
      label: t("mcps_view.status.connected"),
      color: "text-primary",
      dotClass: "bg-primary shadow-[0_0_8px_rgba(255,214,10,0.6)]",
    };
  }
  return {
    label: t("mcps_view.status.disconnected"),
    color: "text-muted-foreground",
    dotClass: "bg-muted-foreground/40",
  };
}

export function McpsView() {
  const t = useT();
  const qc = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const [showConfig, setShowConfig] = useState(false);
  const [checkingName, setCheckingName] = useState<string | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
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
          vars.enable ? t("mcps_toast.connected").replace("{0}", vars.name) : `${vars.name} ${t("mcps_view.disconnected").toLowerCase()}`,
        );
      } else if (res.error) {
        pushToast("error", `${vars.name}: ${res.error}`);
      }
    },
    onError: (err, vars) => {
      pushToast("error", `${vars.name}: ${(err as Error).message}`);
    },
  });

  const servers = data?.servers ?? [];

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Plug className="h-4 w-4 text-primary" />}
        title={t("mcps_view.title")}
        subtitle={
          data
            ? `${data.running} ${t("mcps_view.connected")} · ${data.total}`
            : t("common.loading")
        }
        right={
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowConfig(true)}
              title={t("mcps_view.edit_tooltip")}
            >
              <FileJson className="h-3.5 w-3.5" />
              <span className="ml-1.5 text-xs">mcp.json</span>
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => refetch()}
              title={t("mcps_view.reload_tooltip")}
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
          </div>
        }
      />

      <ScrollArea className="flex-1">
        <div className="p-6">
          {isLoading && (
            <div className="text-sm text-muted-foreground">{t("common.loading")}</div>
          )}

          {error && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
              {(error as Error).message}
            </div>
          )}

          {!isLoading && !error && servers.length === 0 && (
            <EmptyState
              onOpenConfig={() => setShowConfig(true)}
              onImportClaude={() => importClaude.mutate()}
              importPending={importClaude.isPending}
            />
          )}

          {servers.length > 0 && (
            <>
              <p className="mb-4 max-w-xl text-xs leading-relaxed text-muted-foreground">
                {t("mcps_view.intro")}
              </p>
              <ul className="space-y-1.5">
                {servers.map((s) => (
                  <ServerRow
                    key={s.name}
                    server={s}
                    pending={checkingName === s.name}
                    onToggle={(enable) => toggle.mutate({ name: s.name, enable })}
                  />
                ))}
              </ul>
            </>
          )}
        </div>
      </ScrollArea>

      {showConfig && <ConfigModal onClose={() => setShowConfig(false)} />}
    </div>
  );
}

function ServerRow({
  server,
  pending,
  onToggle,
}: {
  server: McpServer;
  pending: boolean;
  onToggle: (enable: boolean) => void;
}) {
  const t = useT();
  const status = statusOf(server, pending, t);
  const tooltip = server.error
    ? server.error
    : server.required_auth.length > 0 && !server.credentials_complete
      ? `${t("mcps_view.credentials_incomplete")}: ${server.required_auth.join(", ")}`
      : "";

  return (
    <li
      className={cn(
        "flex items-center gap-4 rounded-lg border border-border bg-card/40 px-4 py-2.5 transition-colors",
        server.status === "running" && server.enabled && "border-primary/30 bg-card/60",
        server.error && "border-destructive/30 bg-destructive/5",
      )}
    >
      <span
        className={cn("h-2 w-2 shrink-0 rounded-full", status.dotClass)}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{server.name}</div>
      </div>
      <span
        className={cn("text-xs tabular-nums", status.color)}
        title={tooltip}
      >
        {status.label}
      </span>
      <Switch
        checked={server.enabled}
        disabled={pending}
        onCheckedChange={(v) => onToggle(Boolean(v))}
      />
    </li>
  );
}

function EmptyState({
  onOpenConfig,
  onImportClaude,
  importPending,
}: {
  onOpenConfig: () => void;
  onImportClaude: () => void;
  importPending: boolean;
}) {
  const t = useT();
  return (
    <div className="flex flex-col items-center justify-center gap-5 py-16 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-border bg-card/60">
        <Plug className="h-7 w-7 text-muted-foreground" />
      </div>
      <div className="max-w-lg space-y-3">
        <h3 className="font-display text-xl font-semibold tracking-tight">
          {t("mcps_view.empty_title")}
        </h3>
        <p className="text-sm leading-relaxed text-muted-foreground">
          {t("mcps_view.empty_description")}
        </p>
        <p className="text-xs italic text-muted-foreground/70">
          {t("mcps_view.empty_tip")}
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button onClick={onOpenConfig} className="btn-primary px-5 py-2">
          <FileJson className="h-4 w-4" />
          <span className="ml-1.5">{t("mcps_view.open_config")}</span>
        </Button>
        <Button
          variant="ghost"
          onClick={onImportClaude}
          disabled={importPending}
          className="px-4 py-2"
        >
          {importPending ? t("mcps_view.importing") : t("mcps_view.import_claude")}
        </Button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Config-Editor-Modal
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
        throw new Error(`JSON-Syntax: ${(err as Error).message}`);
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

  const copyPath = () => {
    const path = info.data?.path;
    if (!path) return;
    navigator.clipboard?.writeText(path).then(
      () => pushToast("info", t("mcps_view.path_copied")),
      () => pushToast("error", t("mcps_view.copy_failed")),
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="flex w-full max-w-3xl flex-col rounded-xl border border-border bg-card shadow-[0_0_60px_rgba(255,214,10,0.1)]">
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
            className="btn-primary"
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
