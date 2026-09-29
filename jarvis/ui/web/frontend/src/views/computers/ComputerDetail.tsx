/**
 * One computer, full page: what is wrong (and the one button that fixes it),
 * its vitals, the facts, a console for one-off commands, and how Jarvis logs
 * in. Replaces the list in place, with "← All computers" on top — the same
 * detail pattern as the rest of Settings.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Activity,
  KeyRound,
  Loader2,
  Pencil,
  Play,
  Power,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  SquareTerminal,
  Trash2,
} from "lucide-react";
import { BackLink, FactRows, Panel, SegmentedFilter } from "@/components/extensions/primitives";
import { Button } from "@/components/ui/button";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import {
  useCheckComputer,
  useIdentity,
  useRemoveComputer,
  useUpsertComputer,
} from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { computersApi, type CommandResult, type Computer } from "@/lib/computersApi";
import { AccessPanel } from "./AccessPanel";
import { AgentReadiness } from "./AgentReadiness";
import { statusLabel } from "./ComputerRow";
import {
  CopyField,
  Meter,
  StatusLight,
  formatAgo,
  formatMemory,
  formatUptime,
  inputClass,
  loadPct,
  statusTone,
} from "./parts";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function Card({
  title,
  icon,
  actions,
  children,
  className,
}: {
  title: string;
  icon: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Panel className={cn("p-5", className)}>
      <div className="mb-4 flex items-center gap-2.5">
        <span className="text-muted-foreground [&>svg]:h-4 [&>svg]:w-4">{icon}</span>
        <h3 className="flex-1 text-base font-semibold text-foreground-strong">{title}</h3>
        {actions}
      </div>
      {children}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// The attention banner: what is wrong, and the fix right there
// ---------------------------------------------------------------------------

function AttentionBanner({ computer }: { computer: Computer }) {
  const t = useT();
  const upsert = useUpsertComputer();
  const check = useCheckComputer();
  const identity = useIdentity();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = computer.health.status;

  async function act(fn: () => Promise<Computer>) {
    setBusy(true);
    setError(null);
    try {
      upsert(await fn());
      setPassword("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (status === "provisioning") {
    return (
      <div
        className="relative overflow-hidden rounded-lg border border-border bg-card p-5"
        data-testid="computer-provisioning"
      >
        <div className="flex items-start gap-3">
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-info" aria-hidden />
          <div>
            <div className="text-base font-medium text-foreground-strong">{t("computers.provisioning_title")}</div>
            <p className="mt-0.5 text-sm text-muted-foreground">{computer.health.message}</p>
          </div>
        </div>
        <div className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-secondary">
          <div className="h-full w-1/3 animate-[computers-indeterminate_1.6s_ease-in-out_infinite] bg-info" />
        </div>
      </div>
    );
  }

  if (status === "auth_failed") {
    return (
      <div className="space-y-4 rounded-lg border border-warning/50 bg-warning/10 p-5">
        <div className="flex items-start gap-3">
          <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
          <div>
            <div className="text-base font-medium text-foreground-strong">{t("computers.fix_auth_title")}</div>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {computer.health.message ? `${computer.health.message} ` : ""}
              {t("computers.fix_auth_body")}
            </p>
          </div>
        </div>
        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            if (password) void act(() => computersApi.installKey(computer.id, password));
          }}
        >
          <input
            type="password"
            className={inputClass}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t("computers.password_for").replace("{user}", computer.username)}
            aria-label={t("computers.field_password")}
            autoComplete="off"
          />
          <Button type="submit" disabled={!password || busy}>
            {busy ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
            {t("computers.install_key")}
          </Button>
        </form>
        {identity.data && (
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
              {t("computers.fix_auth_manual")}
            </summary>
            <div className="mt-3">
              <CopyField
                multiline
                label={t("computers.key_paste_hint")}
                value={identity.data.public_key}
                copyLabel={t("computers.copy")}
                copiedLabel={t("computers.copied")}
              />
            </div>
          </details>
        )}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </div>
    );
  }

  if (status === "host_key_changed") {
    return (
      <div className="space-y-3 rounded-lg border border-destructive/50 bg-destructive/10 p-5">
        <div className="flex items-start gap-3">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden />
          <div>
            <div className="text-base font-medium text-foreground-strong">{t("computers.fix_hostkey_title")}</div>
            <p className="mt-0.5 text-sm text-muted-foreground">{t("computers.fix_hostkey_body")}</p>
            {computer.host_fingerprint && (
              <p className="mt-2 font-mono text-xs text-foreground-secondary">
                {t("computers.pinned_identity")}: {computer.host_fingerprint}
              </p>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void act(() => computersApi.trustHostKey(computer.id))}
          >
            {busy ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
            {t("computers.trust_new_identity")}
          </Button>
        </div>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </div>
    );
  }

  if (status === "offline" || status === "error") {
    return (
      <div className="flex flex-col gap-3 rounded-lg border border-destructive/40 bg-destructive/[0.07] p-5 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1">
          <div className="text-base font-medium text-foreground-strong">
            {status === "offline" ? t("computers.fix_offline_title") : t("computers.fix_error_title")}
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">{computer.health.message}</p>
        </div>
        <Button
          type="button"
          variant="outline"
          disabled={check.isPending}
          onClick={() => check.mutate(computer.id)}
        >
          {check.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          {t("computers.check_again")}
        </Button>
      </div>
    );
  }

  return null;
}

// ---------------------------------------------------------------------------
// The console: one command at a time, output kept for the visit
// ---------------------------------------------------------------------------

interface ConsoleEntry {
  id: number;
  command: string;
  result: CommandResult | null;
  error: string | null;
}

const QUICK_COMMANDS = ["uptime", "df -h /", "free -h", "docker ps", "systemctl --failed"];

function Console({ computer }: { computer: Computer }) {
  const t = useT();
  const [command, setCommand] = useState("");
  const [entries, setEntries] = useState<ConsoleEntry[]>([]);
  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const counter = useRef(0);
  const online = computer.health.status === "online";

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries]);

  async function run(text: string) {
    const cmd = text.trim();
    if (!cmd || running) return;
    counter.current += 1;
    const id = counter.current;
    setEntries((rows) => [...rows, { id, command: cmd, result: null, error: null }]);
    setHistory((rows) => [...rows.filter((r) => r !== cmd), cmd].slice(-50));
    setCursor(null);
    setCommand("");
    setRunning(true);
    try {
      const result = await computersApi.run(computer.id, cmd);
      setEntries((rows) => rows.map((r) => (r.id === id ? { ...r, result } : r)));
    } catch (e) {
      setEntries((rows) => rows.map((r) => (r.id === id ? { ...r, error: errorText(e) } : r)));
    } finally {
      setRunning(false);
    }
  }

  const prompt = `${computer.username}@${computer.facts?.hostname ?? computer.name}`;

  return (
    <Card
      title={t("computers.console_title")}
      icon={<SquareTerminal />}
      actions={
        entries.length > 0 ? (
          <Button type="button" variant="ghost" size="sm" onClick={() => setEntries([])}>
            {t("computers.console_clear")}
          </Button>
        ) : null
      }
    >
      <div className="overflow-hidden rounded-lg border border-border bg-surface-raised">
        <div
          ref={scroller}
          className="h-72 overflow-y-auto px-4 py-3 font-mono text-xs leading-5 scrollbar-jarvis"
          data-testid="computer-console-output"
        >
          {entries.length === 0 && (
            <p className="text-muted-foreground">{t("computers.console_empty")}</p>
          )}
          {entries.map((entry) => (
            <div key={entry.id} className="mb-3">
              <div className="text-foreground-strong">
                <span className="text-success">{prompt}</span>
                <span className="text-muted-foreground"> $ </span>
                {entry.command}
              </div>
              {entry.result === null && entry.error === null && (
                <Loader2 className="mt-1 h-3 w-3 animate-spin text-muted-foreground" />
              )}
              {entry.result && (
                <>
                  {entry.result.stdout && (
                    <pre className="whitespace-pre-wrap break-words text-foreground-secondary">
                      {entry.result.stdout}
                    </pre>
                  )}
                  {entry.result.stderr && (
                    <pre className="whitespace-pre-wrap break-words text-warning">{entry.result.stderr}</pre>
                  )}
                  <div className="text-[11px] text-foreground-faint">
                    {t("computers.console_exit")} {entry.result.exit_status ?? "?"} · {entry.result.duration_ms} ms
                    {entry.result.truncated ? ` · ${t("computers.console_truncated")}` : ""}
                  </div>
                </>
              )}
              {entry.error && <div className="text-destructive">{entry.error}</div>}
            </div>
          ))}
        </div>
        <form
          className="flex items-center gap-2 border-t border-border px-4 py-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            void run(command);
          }}
        >
          <span className="shrink-0 font-mono text-xs text-muted-foreground">$</span>
          <input
            className="min-w-0 flex-1 bg-transparent font-mono text-xs text-foreground placeholder:text-foreground-faint focus:outline-none"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowUp" && history.length > 0) {
                e.preventDefault();
                const next = cursor === null ? history.length - 1 : Math.max(0, cursor - 1);
                setCursor(next);
                setCommand(history[next]);
              } else if (e.key === "ArrowDown" && cursor !== null) {
                e.preventDefault();
                const next = cursor + 1;
                if (next >= history.length) {
                  setCursor(null);
                  setCommand("");
                } else {
                  setCursor(next);
                  setCommand(history[next]);
                }
              }
            }}
            placeholder={online ? t("computers.console_placeholder") : t("computers.console_offline")}
            aria-label={t("computers.console_title")}
            disabled={!online}
            spellCheck={false}
            autoComplete="off"
          />
          {running && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        </form>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {QUICK_COMMANDS.map((q) => (
          <button
            key={q}
            type="button"
            disabled={!online || running}
            onClick={() => void run(q)}
            className="rounded-md border border-border px-2 py-1 font-mono text-xs text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground disabled:opacity-50"
          >
            {q}
          </button>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Remove: a small confirm in place, with the "delete the VM too" choice
// ---------------------------------------------------------------------------

function RemoveConfirm({
  computer,
  onCancel,
  onRemoved,
}: {
  computer: Computer;
  onCancel: () => void;
  onRemoved: () => void;
}) {
  const t = useT();
  const remove = useRemoveComputer();
  const [destroyVm, setDestroyVm] = useState(false);
  return (
    <div className="space-y-3 rounded-lg border border-destructive/40 bg-destructive/[0.07] p-4">
      <p className="text-sm text-foreground">
        {t("computers.remove_confirm").replace("{computer}", computer.name)}
      </p>
      {computer.kind === "local_vm" && (
        <label className="flex items-center gap-2 text-sm text-foreground-secondary">
          <input
            type="checkbox"
            checked={destroyVm}
            onChange={(e) => setDestroyVm(e.target.checked)}
            className="h-4 w-4 accent-[hsl(var(--destructive))]"
          />
          {t("computers.remove_destroy_vm")}
        </label>
      )}
      {remove.isError && <p role="alert" className="text-sm text-destructive">{errorText(remove.error)}</p>}
      <div className="flex gap-2">
        <Button
          type="button"
          variant="destructive"
          disabled={remove.isPending}
          onClick={() => remove.mutate({ id: computer.id, destroyVm }, { onSuccess: onRemoved })}
        >
          {remove.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
          {t("computers.remove")}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t("computers.cancel")}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

export type DetailTab = "overview" | "console" | "access" | "agents";

export function ComputerDetail({
  computer,
  onBack,
  initialTab = "overview",
}: {
  computer: Computer;
  onBack: () => void;
  initialTab?: DetailTab;
}) {
  const t = useT();
  const check = useCheckComputer();
  const upsert = useUpsertComputer();
  const [tab, setTab] = useState<DetailTab>(initialTab);
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState(computer.name);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [powerBusy, setPowerBusy] = useState(false);
  const [powerError, setPowerError] = useState<string | null>(null);

  const { facts, health } = computer;
  const online = health.status === "online";
  const tone = statusTone(health.status);
  const isVm = computer.kind === "local_vm";

  async function saveName() {
    const name = draftName.trim();
    setRenaming(false);
    if (!name || name === computer.name) return;
    try {
      upsert(await computersApi.update(computer.id, { name }));
    } catch (e) {
      setDraftName(computer.name);
      setPowerError(errorText(e));
    }
  }

  async function power(action: "start" | "stop") {
    setPowerBusy(true);
    setPowerError(null);
    try {
      upsert(await computersApi.power(computer.id, action));
    } catch (e) {
      setPowerError(errorText(e));
    } finally {
      setPowerBusy(false);
    }
  }

  const providerName =
    computer.provider_name ||
    (computer.provider === "generic" ? t("computers.provider_generic") : t(`computers.provider_${computer.provider}`));
  const providerLine = [providerName, computer.region, computer.plan].filter(Boolean);
  const tabs: { id: DetailTab; label: string }[] = [
    { id: "overview", label: t("computers.tab_overview") },
    { id: "console", label: t("computers.tab_console") },
    { id: "access", label: t("computers.tab_access") },
    { id: "agents", label: t("computers.tab_agents") },
  ];

  return (
    <div className="flex w-full flex-col gap-5" data-testid="computer-detail">
      <BackLink label={t("computers.all_computers")} onClick={onBack} />

      <header className="flex flex-col gap-4 md:flex-row md:items-start">
        <div className="flex min-w-0 flex-1 items-start gap-4">
          <ProviderLogo providerId={computer.provider} label={computer.name} className="h-12 w-12" />
          <div className="min-w-0 flex-1">
            {renaming ? (
              <input
                className={cn(inputClass, "h-10 max-w-md text-xl font-semibold")}
                value={draftName}
                autoFocus
                onChange={(e) => setDraftName(e.target.value)}
                onBlur={() => void saveName()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void saveName();
                  if (e.key === "Escape") {
                    setDraftName(computer.name);
                    setRenaming(false);
                  }
                }}
                aria-label={t("computers.field_name")}
              />
            ) : (
              <button
                type="button"
                onClick={() => {
                  setDraftName(computer.name);
                  setRenaming(true);
                }}
                className="group flex max-w-full items-center gap-2 text-left"
                title={t("computers.rename")}
              >
                <h2 className="truncate text-xl font-semibold text-foreground-strong">{computer.name}</h2>
                <Pencil className="h-3.5 w-3.5 shrink-0 text-foreground-faint opacity-0 transition-opacity group-hover:opacity-100" />
              </button>
            )}
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <StatusLight tone={tone} />
                <span className="text-foreground-secondary">{statusLabel(computer, t)}</span>
              </span>
              <span className="font-mono text-xs">
                {computer.username}@{computer.host === "0.0.0.0" ? "…" : computer.host}
                {computer.port !== 22 ? `:${computer.port}` : ""}
              </span>
              {providerLine.length > 0 && <span>{providerLine.join(" · ")}</span>}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {isVm && health.status === "stopped" && (
            <Button type="button" variant="outline" disabled={powerBusy} onClick={() => void power("start")}>
              {powerBusy ? <Loader2 className="animate-spin" /> : <Play />}
              {t("computers.start_vm")}
            </Button>
          )}
          {isVm && online && (
            <Button type="button" variant="outline" disabled={powerBusy} onClick={() => void power("stop")}>
              {powerBusy ? <Loader2 className="animate-spin" /> : <Power />}
              {t("computers.stop_vm")}
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            disabled={check.isPending || health.status === "provisioning"}
            onClick={() => check.mutate(computer.id)}
          >
            {check.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            {t("computers.check_now")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={t("computers.remove")}
            title={t("computers.remove")}
            onClick={() => setConfirmRemove((v) => !v)}
          >
            <Trash2 />
          </Button>
        </div>
      </header>

      {confirmRemove && (
        <RemoveConfirm computer={computer} onCancel={() => setConfirmRemove(false)} onRemoved={onBack} />
      )}
      {powerError && <p role="alert" className="text-sm text-destructive">{powerError}</p>}

      <AttentionBanner computer={computer} />

      <SegmentedFilter<DetailTab> label={t("computers.tabs_label")} value={tab} onChange={setTab} options={tabs} />

      {tab === "overview" && (
        <div className="grid gap-5 xl:grid-cols-2">
          <Card
            title={t("computers.vitals_title")}
            icon={<Activity />}
            actions={
              <span className="text-xs text-muted-foreground">
                {t("computers.checked")} {formatAgo(health.checked_at, t)}
              </span>
            }
          >
            <div className="space-y-4">
              <Meter
                label={t("computers.meter_cpu_long")}
                pct={online ? loadPct(computer) : null}
                value={online && health.load_1m !== null ? `${health.load_1m.toFixed(2)}` : undefined}
              />
              <Meter label={t("computers.meter_memory")} pct={online ? health.mem_used_pct : null} />
              <Meter label={t("computers.meter_disk")} pct={online ? health.disk_used_pct : null} />
            </div>
            <div className="mt-5 grid grid-cols-2 gap-3 border-t border-border pt-4">
              <div>
                <div className="text-xs text-muted-foreground">{t("computers.uptime")}</div>
                <div className="mt-0.5 text-base tabular-nums text-foreground-strong">
                  {online ? formatUptime(health.uptime_s, t) : "—"}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">{t("computers.latency")}</div>
                <div className="mt-0.5 text-base tabular-nums text-foreground-strong">
                  {online && health.latency_ms !== null ? `${health.latency_ms} ms` : "—"}
                </div>
              </div>
            </div>
          </Card>

          <Card title={t("computers.facts_title")} icon={<SquareTerminal />}>
            {facts ? (
              <FactRows
                className="text-base"
                rows={[
                  { label: t("computers.fact_os"), value: facts.os_name },
                  { label: t("computers.fact_kernel"), value: facts.kernel },
                  { label: t("computers.fact_arch"), value: facts.arch },
                  {
                    label: t("computers.fact_cpu"),
                    value: facts.cpu_count ? `${facts.cpu_count} ${t("computers.unit_cpu")}` : null,
                  },
                  { label: t("computers.fact_memory"), value: formatMemory(facts.mem_total_mb) },
                  {
                    label: t("computers.fact_disk"),
                    value: facts.disk_total_gb ? `${facts.disk_total_gb} GB` : null,
                  },
                  { label: t("computers.fact_hostname"), value: facts.hostname },
                ]}
              />
            ) : (
              <p className="text-sm text-muted-foreground">{t("computers.facts_empty")}</p>
            )}
          </Card>
        </div>
      )}

      {tab === "console" && <Console computer={computer} />}
      {tab === "access" && <AccessPanel computer={computer} />}
      {tab === "agents" && <AgentReadiness computer={computer} />}
    </div>
  );
}
