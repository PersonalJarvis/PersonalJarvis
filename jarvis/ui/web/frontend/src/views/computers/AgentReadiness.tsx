/**
 * "Ready for coding agents" — what this computer needs so IDE sessions and
 * Jarvis agents can run on it (tmux, git, Node, the claude / codex CLIs and
 * their logins), with the one action per missing piece.
 *
 * Nothing is installed unasked: the install button names exactly what it
 * installs, runs as a job on the server and streams its log here. Copying
 * this computer's CLI login to the server is its own explicit button.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Download, KeyRound, Loader2, RefreshCw, X } from "lucide-react";
import { Panel } from "@/components/extensions/primitives";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { readinessApi, type Computer, type InstallJob, type ToolId } from "@/lib/computersApi";

const TOOL_LABELS: Record<ToolId, string> = {
  tmux: "tmux",
  git: "git",
  node: "Node.js",
  claude: "Claude Code",
  codex: "Codex",
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function AgentReadiness({ computer }: { computer: Computer }) {
  const t = useT();
  const qc = useQueryClient();
  const online = computer.health.status === "online";
  const key = ["computers", computer.id, "readiness"] as const;
  const readiness = useQuery({
    queryKey: key,
    queryFn: () => readinessApi.get(computer.id),
    enabled: online,
    retry: false,
    staleTime: 60_000,
  });
  const [job, setJob] = useState<InstallJob | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (readiness.data?.install) setJob(readiness.data.install);
  }, [readiness.data]);

  // Poll the install job while it runs; refresh the checklist when it ends.
  useEffect(() => {
    if (job?.state !== "running") return;
    const timer = window.setInterval(() => {
      void readinessApi.job(computer.id).then((next) => {
        setJob(next);
        if (next && next.state !== "running") void qc.invalidateQueries({ queryKey: key });
      }).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.state, computer.id]);

  const missing = useMemo(
    () => (readiness.data?.tools ?? []).filter((tool) => !tool.installed).map((tool) => tool.id),
    [readiness.data],
  );

  async function install() {
    setBusy("install");
    setError(null);
    try {
      setJob(await readinessApi.install(computer.id, missing));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function copyLogin(agent: "claude" | "codex") {
    setBusy(agent);
    setError(null);
    setNotice(null);
    try {
      await readinessApi.copyLogin(computer.id, agent);
      setNotice(t("computers.ready_login_copied").replace("{agent}", TOOL_LABELS[agent]));
      await qc.invalidateQueries({ queryKey: key });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const data = readiness.data;
  // A Windows computer needs Git for Windows instead of tmux, and an admin, not root.
  const windows = data?.os === "Windows" || computer.facts?.os_id === "windows";
  return (
    <div data-testid="computer-readiness">
    <Panel className="p-5">
      <div className="mb-4 flex items-center gap-2.5">
        <Bot className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h3 className="flex-1 text-base font-semibold text-foreground-strong">{t("computers.ready_title")}</h3>
        {data && (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-xs font-medium",
              data.ready ? "bg-success/15 text-success" : "bg-warning/15 text-warning",
            )}
          >
            {data.ready ? t("computers.ready_yes") : t("computers.ready_no")}
          </span>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t("computers.refresh")}
          disabled={!online || readiness.isFetching}
          onClick={() => void readiness.refetch()}
        >
          {readiness.isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
        </Button>
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        {t(windows ? "computers.ready_body_windows" : "computers.ready_body")}
      </p>

      {!online && <p className="text-sm text-muted-foreground">{t("computers.ready_offline")}</p>}
      {readiness.isError && <p role="alert" className="text-sm text-destructive">{errorText(readiness.error)}</p>}

      {data && (
        <>
          <ul className="grid gap-2 sm:grid-cols-2">
            {data.tools.map((tool) => {
              const loginNeeded = (tool.id === "claude" || tool.id === "codex") && tool.installed;
              const loggedIn = loginNeeded ? data.logins[tool.id as "claude" | "codex"] : true;
              return (
                <li
                  key={tool.id}
                  className="flex items-center gap-2.5 rounded-md border border-border px-3 py-2"
                  data-testid={`readiness-${tool.id}`}
                >
                  {tool.installed && loggedIn ? (
                    <Check className="h-4 w-4 shrink-0 text-success" aria-hidden />
                  ) : (
                    <X className="h-4 w-4 shrink-0 text-foreground-faint" aria-hidden />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-foreground-strong">{TOOL_LABELS[tool.id]}</span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {!tool.installed
                        ? t("computers.ready_missing")
                        : !loggedIn
                          ? t("computers.ready_not_logged_in")
                          : tool.version ?? t("computers.ready_installed")}
                    </span>
                  </span>
                  {loginNeeded && !loggedIn && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busy !== null}
                      onClick={() => void copyLogin(tool.id as "claude" | "codex")}
                      title={t("computers.ready_copy_login_hint")}
                    >
                      {busy === tool.id ? <Loader2 className="animate-spin" /> : <KeyRound />}
                      {t("computers.ready_copy_login")}
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>

          {missing.length > 0 && job?.state !== "running" && (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Button type="button" disabled={busy !== null} onClick={() => void install()} data-testid="readiness-install">
                {busy === "install" ? <Loader2 className="animate-spin" /> : <Download />}
                {t("computers.ready_install").replace(
                  "{items}",
                  missing.map((id) => TOOL_LABELS[id]).join(", "),
                )}
              </Button>
              {!data.root && !data.sudo && (
                <span className="text-xs text-muted-foreground">
                  {t(windows ? "computers.ready_needs_admin" : "computers.ready_needs_root")}
                </span>
              )}
            </div>
          )}
        </>
      )}

      {job && (
        <div className="mt-4 overflow-hidden rounded-lg border border-border bg-surface-raised">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs text-muted-foreground">
            {job.state === "running" && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
            {job.state === "done" && <Check className="h-3.5 w-3.5 text-success" aria-hidden />}
            {job.state === "failed" && <X className="h-3.5 w-3.5 text-destructive" aria-hidden />}
            <span>
              {job.state === "running"
                ? t("computers.ready_installing")
                : job.state === "done"
                  ? t("computers.ready_install_done")
                  : job.message ?? t("computers.ready_install_failed")}
            </span>
          </div>
          <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap px-3 py-2 font-mono text-[11px] leading-4 text-foreground-secondary scrollbar-jarvis">
            {job.log.slice(-40).join("\n") || "…"}
          </pre>
        </div>
      )}

      {notice && <p className="mt-3 text-sm text-success">{notice}</p>}
      {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
    </Panel>
    </div>
  );
}
