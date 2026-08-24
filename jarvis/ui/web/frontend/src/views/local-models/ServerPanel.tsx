/**
 * Server panel of the Local models section (plan step 8).
 *
 * One place for the Ollama server itself: the host address with a probe,
 * install / start / stop of the local process, what is loaded right now, a
 * few facts (version, host kind, models directory, keep-alive default), the
 * per-OS environment guide with copyable commands, and a collapsed log tail.
 *
 * Props: `{ providerId }` — the id of the pull-capable card. The provider
 * descriptor (needed by `BaseUrlField`) is looked up through `useProviders`
 * so the integration view only has to pass a string.
 *
 * A remote host (`host_kind === "remote"`) hides every local-only control —
 * install, start, stop and the log — and says where to manage the server.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Copy,
  RefreshCw,
  Square,
} from "lucide-react";
import {
  Cell,
  type Column,
  EmptyRow,
  FactRows,
  IconButton,
  Panel,
  PanelHeader,
  SegmentedFilter,
  SoftButton,
  StatusDot,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import {
  BaseUrlField,
  OllamaRuntimePanel,
} from "@/components/providers/ProviderTierSection";
import {
  useEnvGuide,
  useServer,
  useServerLog,
  useStopServer,
  useTestServer,
  useUnloadModel,
  type EnvGuideOs,
  type RunningModelRow,
  type ServerProbeResponse,
} from "@/hooks/useLocalModels";
import { useProviders } from "@/hooks/useProviders";
import { robustCopy } from "@/lib/clipboard";
import { useEventStore } from "@/store/events";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/** Ollama's shipped default when OLLAMA_KEEP_ALIVE is not set. */
const KEEP_ALIVE_DEFAULT = "5m";
const LOG_LINES = 40;

const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground";

/** "18.2 GB" from a byte count; "—" for nothing loaded. */
export function formatGb(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "—";
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/**
 * Minutes until an ISO timestamp, as a short label. Ollama's "forever"
 * (keep_alive -1) arrives as a far-future date; anything past a day reads as
 * "kept". `now` is injectable for tests.
 */
export function formatExpiry(
  iso: string | null | undefined,
  t: (key: string) => string,
  now: number = Date.now(),
): string {
  if (!iso) return "—";
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return "—";
  const minutes = Math.round((at - now) / 60_000);
  if (minutes > 24 * 60) return t("local_models.server.expires_kept");
  if (minutes <= 0) return t("local_models.server.expires_now");
  return fill(t("local_models.server.expires_in_min"), { minutes });
}

function guessOs(): EnvGuideOs | undefined {
  if (typeof navigator === "undefined") return undefined;
  const ua =
    `${navigator.platform ?? ""} ${navigator.userAgent ?? ""}`.toLowerCase();
  if (ua.includes("win")) return "windows";
  if (ua.includes("mac")) return "macos";
  if (ua.includes("linux")) return "linux";
  return undefined;
}

export function ServerPanel({ providerId }: { providerId: string }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const { providers, refetch: refetchProviders } = useProviders();
  const descriptor = useMemo(
    () => providers.find((p) => p.id === providerId) ?? null,
    [providers, providerId],
  );

  const server = useServer(providerId);
  const data = server.data ?? null;
  const remote = data?.host_kind === "remote";

  const refetchServer = server.refetch;
  const onChanged = useCallback(() => {
    void refetchProviders();
    void refetchServer();
  }, [refetchProviders, refetchServer]);

  // --- host probe -----------------------------------------------------------
  const probe = useTestServer(providerId);
  const [probeResult, setProbeResult] = useState<ServerProbeResponse | null>(
    null,
  );
  const [probeError, setProbeError] = useState<string | null>(null);
  const probeTarget = data?.base_url ?? descriptor?.base_url ?? "";

  const runProbe = () => {
    setProbeError(null);
    probe.mutate(probeTarget, {
      onSuccess: (res) => setProbeResult(res),
      onError: (err) => {
        setProbeResult(null);
        setProbeError(err instanceof Error ? err.message : String(err));
      },
    });
  };

  // --- stop -----------------------------------------------------------------
  const stop = useStopServer(providerId);
  const [confirmStop, setConfirmStop] = useState(false);
  // The backend answers ok:false with a sentence when Jarvis did not start the
  // process; remember it and keep the button disabled until the state changes.
  const [stopRefusal, setStopRefusal] = useState<string | null>(null);
  const [stopError, setStopError] = useState<string | null>(null);
  useEffect(() => {
    setStopRefusal(null);
    setConfirmStop(false);
  }, [data?.running]);

  const runStop = () => {
    if (!confirmStop) {
      setConfirmStop(true);
      return;
    }
    setConfirmStop(false);
    setStopError(null);
    stop.mutate(undefined, {
      onSuccess: (res) => {
        if (!res.ok) setStopRefusal(res.message);
        else pushToast("success", res.message);
        onChanged();
      },
      onError: (err) =>
        setStopError(err instanceof Error ? err.message : String(err)),
    });
  };

  // --- running models ---------------------------------------------------------
  const unload = useUnloadModel(providerId);
  const [unloading, setUnloading] = useState<string | null>(null);
  const runUnload = (row: RunningModelRow) => {
    setUnloading(row.name);
    unload.mutate(row.name, {
      onSuccess: (res) => pushToast("success", res.message),
      onError: (err) =>
        pushToast("error", err instanceof Error ? err.message : String(err)),
      onSettled: () => setUnloading(null),
    });
  };

  // --- environment guide ------------------------------------------------------
  const [os, setOs] = useState<EnvGuideOs | undefined>(guessOs);
  const guide = useEnvGuide(providerId, os);
  const guideOs = guide.data?.os ?? os ?? "linux";
  const copyCommand = async (command: string) => {
    const ok = await robustCopy(command);
    pushToast(
      ok ? "success" : "error",
      ok
        ? t("local_models.server.copied")
        : t("local_models.server.copy_failed"),
    );
  };

  // --- log --------------------------------------------------------------------
  const [logOpen, setLogOpen] = useState(false);
  const log = useServerLog(providerId, LOG_LINES, logOpen && !remote);

  const runningRows = data?.running_models ?? [];
  const columns: Column[] = [
    { id: "name", label: t("local_models.server.col_model") },
    {
      id: "vram",
      label: t("local_models.server.col_vram"),
      width: "110px",
      align: "right",
    },
    {
      id: "expires",
      label: t("local_models.server.col_expires"),
      width: "150px",
      align: "right",
    },
    {
      id: "actions",
      label: t("local_models.server.col_actions"),
      width: "100px",
      align: "right",
      srOnly: true,
    },
  ];

  const statusTone = data?.running ? "ok" : data?.installed ? "off" : "warn";
  const statusLabel = data?.running
    ? t("local_models.server.status_running")
    : data?.installed
      ? t("local_models.server.status_stopped")
      : t("local_models.server.status_missing");

  return (
    <div className="space-y-4" data-testid="server-panel">
      {server.isError && (
        <p className="text-sm text-destructive" role="alert">
          {server.error instanceof Error
            ? server.error.message
            : String(server.error)}
        </p>
      )}
      {data?.error && (
        <p className="text-sm text-muted-foreground" data-testid="server-error">
          {data.error}
        </p>
      )}

      {/* Host --------------------------------------------------------------- */}
      <Panel className="p-4">
        <div className="space-y-3">
          <p className={EYEBROW}>{t("local_models.server.host_eyebrow")}</p>
          <PanelHeader
            title={t("local_models.server.host_title")}
            subtitle={t("local_models.server.host_subtitle")}
            actions={
              <SoftButton
                onClick={runProbe}
                disabled={probe.isPending || !probeTarget}
                ariaLabel={t("local_models.server.test")}
              >
                {probe.isPending
                  ? t("local_models.server.testing")
                  : t("local_models.server.test")}
              </SoftButton>
            }
          />
          {descriptor?.supports_base_url && (
            <BaseUrlField descriptor={descriptor} onChanged={onChanged} />
          )}
          {probeResult && (
            <div data-testid="probe-result">
              <StatusDot
                tone={probeResult.ok ? "ok" : "error"}
                label={
                  probeResult.ok
                    ? fill(t("local_models.server.probe_ok"), {
                        version: probeResult.version,
                        ms: probeResult.latency_ms,
                      })
                    : probeResult.detail
                }
              />
            </div>
          )}
          {probeError && (
            <p className="text-sm text-destructive" role="alert">
              {probeError}
            </p>
          )}
        </div>
      </Panel>

      {/* Runtime ------------------------------------------------------------ */}
      <Panel className="p-4">
        <div className="space-y-3">
          <p className={EYEBROW}>{t("local_models.server.runtime_eyebrow")}</p>
          <PanelHeader
            title={t("local_models.server.runtime_title")}
            subtitle={
              data ? (
                <StatusDot tone={statusTone} label={statusLabel} />
              ) : undefined
            }
            actions={
              !remote && data?.running ? (
                <SoftButton
                  onClick={runStop}
                  disabled={stop.isPending || stopRefusal !== null}
                  ariaLabel={t("local_models.server.stop")}
                  className={cn(confirmStop && "text-destructive")}
                >
                  <Square className="h-3.5 w-3.5" />
                  {stop.isPending
                    ? t("local_models.server.stopping")
                    : confirmStop
                      ? t("local_models.server.stop_confirm")
                      : t("local_models.server.stop")}
                </SoftButton>
              ) : undefined
            }
          />
          {remote ? (
            <p
              className="text-sm text-muted-foreground"
              data-testid="remote-note"
            >
              {t("local_models.server.remote_note")}
            </p>
          ) : (
            <OllamaRuntimePanel
              providerId={providerId}
              onChanged={onChanged}
              alwaysVisible
            />
          )}
          {stopRefusal && (
            <p
              className="text-sm text-muted-foreground"
              data-testid="stop-refusal"
            >
              {stopRefusal}
            </p>
          )}
          {stopError && (
            <p className="text-sm text-destructive" role="alert">
              {stopError}
            </p>
          )}
        </div>
      </Panel>

      {/* Loaded now ------------------------------------------------------------ */}
      <Panel>
        <div className="p-4 pb-2">
          <p className={EYEBROW}>{t("local_models.server.loaded_eyebrow")}</p>
          <PanelHeader
            title={t("local_models.server.loaded_title")}
            subtitle={
              runningRows.length > 0
                ? fill(t("local_models.server.loaded_subtitle"), {
                    vram: formatGb(data?.loaded_vram_bytes),
                  })
                : undefined
            }
          />
        </div>
        {runningRows.length === 0 ? (
          <div className="px-4 pb-4">
            <EmptyRow>{t("local_models.server.loaded_empty")}</EmptyRow>
          </div>
        ) : (
          <Table label={t("local_models.server.loaded_title")}>
            <TableHead columns={columns} />
            {runningRows.map((row) => (
              <TableRow key={row.name} columns={columns} ariaLabel={row.name}>
                <Cell>
                  <div className="truncate font-medium text-foreground">
                    {row.name}
                  </div>
                  {row.context_length ? (
                    <div className="text-xs text-muted-foreground tabular-nums">
                      {fill(t("local_models.server.context"), {
                        tokens: row.context_length.toLocaleString(),
                      })}
                    </div>
                  ) : null}
                </Cell>
                <Cell align="right" className="tabular-nums" muted>
                  {formatGb(row.size_vram_bytes)}
                </Cell>
                <Cell align="right" className="tabular-nums" muted>
                  {formatExpiry(row.expires_at, t)}
                </Cell>
                <Cell align="right" stop>
                  <SoftButton
                    onClick={() => runUnload(row)}
                    disabled={unloading !== null}
                    ariaLabel={`${t("local_models.server.unload")} ${row.name}`}
                  >
                    {unloading === row.name
                      ? t("local_models.server.unloading")
                      : t("local_models.server.unload")}
                  </SoftButton>
                </Cell>
              </TableRow>
            ))}
          </Table>
        )}
      </Panel>

      {/* Facts --------------------------------------------------------------- */}
      <Panel className="p-4">
        <div className="space-y-3">
          <p className={EYEBROW}>{t("local_models.server.facts_eyebrow")}</p>
          <FactRows
            rows={[
              {
                label: t("local_models.server.fact_version"),
                value:
                  data?.version ||
                  (data ? t("local_models.server.unknown") : ""),
              },
              {
                label: t("local_models.server.fact_host_kind"),
                value: data
                  ? remote
                    ? t("local_models.server.host_remote")
                    : t("local_models.server.host_local")
                  : "",
              },
              {
                label: t("local_models.server.fact_address"),
                value: data?.base_url ? (
                  <span className="font-mono text-sm">{data.base_url}</span>
                ) : (
                  ""
                ),
              },
              {
                label: t("local_models.server.fact_models_dir"),
                value: data?.models_dir ? (
                  <span className="break-all font-mono text-sm">
                    {data.models_dir}
                  </span>
                ) : (
                  ""
                ),
              },
              {
                label: t("local_models.server.fact_disk"),
                value:
                  data && data.disk_bytes > 0 ? formatGb(data.disk_bytes) : "",
              },
              {
                label: t("local_models.server.fact_keep_alive"),
                value: fill(t("local_models.server.keep_alive_value"), {
                  value: KEEP_ALIVE_DEFAULT,
                }),
              },
            ]}
          />
        </div>
      </Panel>

      {/* Environment guide ------------------------------------------------------- */}
      <Panel className="p-4">
        <div className="space-y-3">
          <p className={EYEBROW}>{t("local_models.server.env_eyebrow")}</p>
          <PanelHeader
            title={t("local_models.server.env_title")}
            subtitle={t("local_models.server.env_subtitle")}
            actions={
              <SegmentedFilter<EnvGuideOs>
                label={t("local_models.server.env_os_label")}
                value={guideOs}
                onChange={setOs}
                options={[
                  { id: "windows", label: t("local_models.server.os_windows") },
                  { id: "macos", label: t("local_models.server.os_macos") },
                  { id: "linux", label: t("local_models.server.os_linux") },
                ]}
              />
            }
          />
          {guide.isError && (
            <p className="text-sm text-destructive" role="alert">
              {guide.error instanceof Error
                ? guide.error.message
                : String(guide.error)}
            </p>
          )}
          {guide.data && guide.data.rows.length > 0 && (
            <ul
              className="divide-y divide-border/70 rounded-xl border border-border bg-card/60"
              data-testid="env-guide"
            >
              {guide.data.rows.map((row) => (
                <li
                  key={row.key}
                  className="flex items-start gap-3 px-3 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-sm text-foreground">
                      {row.key}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {row.purpose}
                    </div>
                    <code className="mt-1 block truncate rounded bg-sheen/[0.06] px-2 py-1 font-mono text-xs text-foreground">
                      {row.command}
                    </code>
                    {row.restart ? (
                      <div className="mt-1 text-[11px] text-muted-foreground">
                        {row.restart}
                      </div>
                    ) : null}
                  </div>
                  <IconButton
                    label={`${t("local_models.server.copy")} ${row.key}`}
                    onClick={() => void copyCommand(row.command)}
                  >
                    <Copy className="h-4 w-4" />
                  </IconButton>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>

      {/* Log --------------------------------------------------------------- */}
      {!remote && (
        <Panel className="p-4">
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => setLogOpen((v) => !v)}
                aria-expanded={logOpen}
                className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground/90 hover:text-foreground"
              >
                {logOpen ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
                {t("local_models.server.log_title")}
              </button>
              {logOpen && (
                <IconButton
                  label={t("local_models.server.log_refresh")}
                  onClick={() => void log.refetch()}
                  busy={log.isFetching}
                >
                  <RefreshCw className="h-4 w-4" />
                </IconButton>
              )}
            </div>
            {logOpen &&
              (log.data && log.data.lines.length > 0 ? (
                <pre
                  className="max-h-72 overflow-auto rounded-lg border border-border bg-sheen/[0.04] p-3 font-mono text-[11px] leading-5 text-foreground"
                  data-testid="server-log"
                >
                  {log.data.lines.join("\n")}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {log.isLoading
                    ? t("local_models.server.log_loading")
                    : t("local_models.server.log_empty")}
                </p>
              ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
