/**
 * Connected-sources panel (design doc 04 "Sources view").
 *
 * Consent is THE gate: every source starts as `pending`, the Approve button
 * is an explicit user action with a short scope description, and a source
 * without approved consent shows NO sync control at all — the backend would
 * refuse the sync anyway (`service.start_sync`), the UI just does not dangle
 * a dead button. Per source: consent badge, per-stage backlog counts, last
 * sync / last error, Approve / Revoke / Sync now.
 *
 * "Add source": connector picker over the five built-ins, a path field for
 * the folder-shaped connectors, area assignment, and — for the
 * plugin-bridge — the connected-integration candidates from
 * `GET /api/ultrawiki/bridge/candidates`, with a "pull adapter pending"
 * candidate marked honestly (registering it works, a sync finds nothing yet).
 */
import { useState } from "react";
import {
  Check,
  FolderOpen,
  Loader2,
  Plus,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  ULTRAWIKI_CONNECTOR_IDS,
  approveUltraWikiSource,
  createUltraWikiSource,
  fetchUltraWikiAreas,
  fetchUltraWikiBridgeCandidates,
  revokeUltraWikiSource,
  startUltraWikiSync,
  type UltraWikiConnectorId,
  type UltraWikiSource,
} from "@/lib/ultrawikiApi";

const CONNECTOR_LABEL_KEY: Record<UltraWikiConnectorId, string> = {
  "obsidian-vault": "ultrawiki.sources.connector_obsidian",
  "local-folder": "ultrawiki.sources.connector_local_folder",
  "jarvis-conversations": "ultrawiki.sources.connector_conversations",
  "normal-wiki": "ultrawiki.sources.connector_normal_wiki",
  "plugin-bridge": "ultrawiki.sources.connector_plugin_bridge",
};

/** Connectors that read a user-chosen folder (config key `root`). */
const PATH_CONNECTORS: readonly string[] = ["obsidian-vault", "local-folder"];

const CONSENT_BADGE: Record<
  string,
  { labelKey: string; className: string }
> = {
  pending: {
    labelKey: "ultrawiki.sources.consent_pending",
    className: "border-[#ffb84d]/40 bg-[#ffb84d]/10 text-[#ffb84d]",
  },
  approved: {
    labelKey: "ultrawiki.sources.consent_approved",
    className: "border-[#5bd4a4]/40 bg-[#5bd4a4]/10 text-[#5bd4a4]",
  },
  revoked: {
    labelKey: "ultrawiki.sources.consent_revoked",
    className: "border-destructive/40 bg-destructive/10 text-destructive",
  },
};

const STAGE_KEYS = [
  ["captured", "ultrawiki.progress.stage_captured"],
  ["keyword_indexed", "ultrawiki.progress.stage_keyword_indexed"],
  ["embedded", "ultrawiki.progress.stage_embedded"],
  ["distilled", "ultrawiki.progress.stage_distilled"],
  ["failed", "ultrawiki.progress.stage_failed"],
] as const;

export function SourcesPanel({
  sources,
  onChanged,
}: {
  sources: UltraWikiSource[];
  onChanged: () => void;
}): JSX.Element {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [busySource, setBusySource] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  async function run(sourceId: string, action: () => Promise<unknown>) {
    setBusySource(sourceId);
    try {
      await action();
      onChanged();
    } catch (e) {
      pushToast(
        "error",
        t("ultrawiki.sources.action_failed").replace(
          "{0}",
          (e as Error).message,
        ),
      );
    } finally {
      setBusySource(null);
    }
  }

  return (
    <div className="space-y-3 p-4" data-testid="ultrawiki-sources-panel">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-foreground">
          {t("ultrawiki.sources.title")}
        </h3>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setAddOpen((open) => !open)}
          data-testid="ultrawiki-add-source-toggle"
        >
          <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />
          {t("ultrawiki.sources.add_source")}
        </Button>
      </div>

      {addOpen && (
        <AddSourceForm
          onClose={() => setAddOpen(false)}
          onCreated={() => {
            setAddOpen(false);
            onChanged();
          }}
        />
      )}

      {sources.length === 0 ? (
        <p
          className="rounded-lg border border-dashed border-border/70 bg-card/30 p-4 text-xs text-muted-foreground"
          data-testid="ultrawiki-sources-empty"
        >
          {t("ultrawiki.sources.empty")}
        </p>
      ) : (
        <ul className="space-y-2">
          {sources.map((source) => (
            <SourceCard
              key={source.id}
              source={source}
              busy={busySource === source.id}
              onApprove={() =>
                void run(source.id, () => approveUltraWikiSource(source.id))
              }
              onRevoke={() =>
                void run(source.id, () => revokeUltraWikiSource(source.id))
              }
              onSync={() =>
                void run(source.id, async () => {
                  await startUltraWikiSync(source.id);
                  pushToast("success", t("ultrawiki.sources.sync_started"));
                })
              }
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function SourceCard({
  source,
  busy,
  onApprove,
  onRevoke,
  onSync,
}: {
  source: UltraWikiSource;
  busy: boolean;
  onApprove: () => void;
  onRevoke: () => void;
  onSync: () => void;
}): JSX.Element {
  const t = useT();
  const consent = String(source.consent);
  const badge = CONSENT_BADGE[consent] ?? CONSENT_BADGE.pending;
  const approved = consent === "approved";

  return (
    <li
      className="card-outline rounded-xl border border-border bg-card/40 p-3"
      data-testid={`ultrawiki-source-${source.id}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <span className="text-sm font-medium text-foreground">
            {source.label}
          </span>
          <span className="ml-2 font-mono text-[10px] text-muted-foreground">
            {source.connector}
          </span>
        </div>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-[10px]",
            badge.className,
          )}
          data-testid={`ultrawiki-consent-${source.id}`}
          data-consent={consent}
        >
          {t(badge.labelKey)}
        </span>
      </div>

      {source.counts && (
        <dl className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          {STAGE_KEYS.map(([key, labelKey]) => {
            const value = source.counts?.[key] ?? 0;
            return (
              <div className="flex items-baseline gap-1" key={key}>
                <dt>{t(labelKey)}</dt>
                <dd
                  className={cn(
                    "font-medium",
                    key === "failed" && value > 0
                      ? "text-destructive"
                      : "text-foreground",
                  )}
                >
                  {value}
                </dd>
              </div>
            );
          })}
        </dl>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
        <span>
          {source.last_sync_at
            ? t("ultrawiki.sources.last_sync").replace(
                "{0}",
                source.last_sync_at,
              )
            : t("ultrawiki.sources.never_synced")}
        </span>
        {source.areas.length > 0 && (
          <span>
            {t("ultrawiki.sources.areas").replace(
              "{0}",
              source.areas.join(", "),
            )}
          </span>
        )}
        {source.last_error && (
          <span
            className="text-destructive"
            data-testid={`ultrawiki-source-error-${source.id}`}
          >
            {t("ultrawiki.sources.last_error").replace(
              "{0}",
              source.last_error,
            )}
          </span>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {!approved && (
          <Button
            size="sm"
            onClick={onApprove}
            disabled={busy}
            data-testid={`uw-source-approve-${source.id}`}
          >
            {busy ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <ShieldCheck className="mr-1 h-3.5 w-3.5" aria-hidden />
            )}
            {t("ultrawiki.sources.approve")}
          </Button>
        )}
        {approved && (
          <>
            {/* Consent gate: the sync control exists ONLY on approved sources. */}
            <Button
              variant="outline"
              size="sm"
              onClick={onSync}
              disabled={busy}
              data-testid={`uw-source-sync-${source.id}`}
            >
              <RefreshCw
                className={cn("mr-1 h-3.5 w-3.5", busy && "animate-spin")}
                aria-hidden
              />
              {t("ultrawiki.sources.sync_now")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onRevoke}
              disabled={busy}
              data-testid={`uw-source-revoke-${source.id}`}
            >
              <ShieldOff className="mr-1 h-3.5 w-3.5" aria-hidden />
              {t("ultrawiki.sources.revoke")}
            </Button>
          </>
        )}
      </div>

      {!approved && (
        <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
          {t("ultrawiki.sources.approve_scope")}
        </p>
      )}
    </li>
  );
}

function AddSourceForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}): JSX.Element {
  const t = useT();
  const [connector, setConnector] =
    useState<UltraWikiConnectorId>("obsidian-vault");
  const [label, setLabel] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [integrationId, setIntegrationId] = useState("");
  const [selectedAreas, setSelectedAreas] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const areasQuery = useQuery({
    queryKey: ["ultrawiki", "areas"],
    queryFn: fetchUltraWikiAreas,
    staleTime: 5_000,
  });
  const bridgeQuery = useQuery({
    queryKey: ["ultrawiki", "bridge-candidates"],
    queryFn: fetchUltraWikiBridgeCandidates,
    staleTime: 5_000,
    enabled: connector === "plugin-bridge",
  });

  const needsPath = PATH_CONNECTORS.includes(connector);

  function toggleArea(id: string) {
    setSelectedAreas((current) =>
      current.includes(id)
        ? current.filter((a) => a !== id)
        : [...current, id],
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    const config: Record<string, unknown> = {};
    if (needsPath && rootPath.trim()) config.root = rootPath.trim();
    if (connector === "plugin-bridge" && integrationId) {
      config.integration_id = integrationId;
    }
    try {
      await createUltraWikiSource({
        connector,
        label: label.trim() || t(CONNECTOR_LABEL_KEY[connector]),
        config,
        areas: selectedAreas,
      });
      onCreated();
    } catch (err) {
      setError(
        t("ultrawiki.sources.create_failed").replace(
          "{0}",
          (err as Error).message,
        ),
      );
    } finally {
      setSubmitting(false);
    }
  }

  const inputCls =
    "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/40";

  return (
    <form
      onSubmit={(e) => void handleSubmit(e)}
      className="space-y-3 rounded-xl border border-border bg-card/40 p-3"
      data-testid="ultrawiki-add-source-form"
    >
      <h4 className="text-xs font-medium text-foreground">
        {t("ultrawiki.sources.add_title")}
      </h4>

      <label className="block">
        <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
          {t("ultrawiki.sources.connector_label")}
        </span>
        <select
          value={connector}
          onChange={(e) =>
            setConnector(e.target.value as UltraWikiConnectorId)
          }
          className={inputCls}
          data-testid="ultrawiki-connector-select"
        >
          {ULTRAWIKI_CONNECTOR_IDS.map((id) => (
            <option key={id} value={id}>
              {t(CONNECTOR_LABEL_KEY[id])}
            </option>
          ))}
        </select>
      </label>

      <label className="block">
        <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
          {t("ultrawiki.sources.label_label")}
        </span>
        <input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder={t(CONNECTOR_LABEL_KEY[connector])}
          className={inputCls}
        />
      </label>

      {needsPath && (
        <label className="block">
          <span className="mb-1.5 flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            <FolderOpen className="h-3 w-3" aria-hidden />
            {t("ultrawiki.sources.path_label")}
          </span>
          <input
            type="text"
            value={rootPath}
            onChange={(e) => setRootPath(e.target.value)}
            className={inputCls}
            data-testid="ultrawiki-path-input"
          />
          <span className="mt-1 block text-[11px] text-muted-foreground">
            {t("ultrawiki.sources.path_hint")}
          </span>
        </label>
      )}

      {connector === "plugin-bridge" && (
        <div>
          <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
            {t("ultrawiki.sources.bridge_label")}
          </span>
          {bridgeQuery.isLoading ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              {t("ultrawiki.panel.loading")}
            </div>
          ) : bridgeQuery.isError ? (
            <p className="text-xs text-destructive">
              {t("ultrawiki.sources.bridge_load_failed")}
            </p>
          ) : (bridgeQuery.data?.candidates ?? []).length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {t("ultrawiki.sources.bridge_empty")}
            </p>
          ) : (
            <ul className="space-y-1.5">
              {(bridgeQuery.data?.candidates ?? []).map((candidate) => {
                const selected = integrationId === candidate.id;
                // The backend states adapter readiness inside `detail`; mark
                // "pull adapter pending" honestly instead of hiding it.
                const adapterPending = candidate.detail.includes(
                  "pull adapter pending",
                );
                return (
                  <li key={candidate.id}>
                    <button
                      type="button"
                      onClick={() => setIntegrationId(candidate.id)}
                      aria-pressed={selected}
                      data-testid={`ultrawiki-bridge-${candidate.id}`}
                      className={cn(
                        "w-full rounded-lg border p-2 text-left text-xs transition-colors",
                        selected
                          ? "border-primary/60 bg-primary/5 ring-1 ring-primary/40"
                          : "border-border hover:bg-secondary/30",
                      )}
                    >
                      <span className="flex items-center gap-2">
                        {selected && (
                          <Check className="h-3 w-3 text-primary" aria-hidden />
                        )}
                        <span className="font-medium text-foreground">
                          {candidate.label}
                        </span>
                        <span className="font-mono text-[10px] text-muted-foreground">
                          {candidate.kind}
                        </span>
                        {adapterPending && (
                          <span className="rounded-full border border-[#ffb84d]/40 bg-[#ffb84d]/10 px-1.5 py-0.5 text-[10px] text-[#ffb84d]">
                            {t("ultrawiki.sources.adapter_pending")}
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 block text-muted-foreground">
                        {candidate.detail}
                      </span>
                      {adapterPending && selected && (
                        <span className="mt-1 block text-[#ffb84d]">
                          {t("ultrawiki.sources.adapter_pending_hint")}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      <div>
        <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
          {t("ultrawiki.sources.areas_label")}
        </span>
        {(areasQuery.data?.areas ?? []).length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            {t("ultrawiki.sources.no_areas")}
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {(areasQuery.data?.areas ?? []).map((area) => (
              <label
                key={area.id}
                className="flex cursor-pointer items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[11px] text-muted-foreground has-[:checked]:border-primary/50 has-[:checked]:text-foreground"
              >
                <input
                  type="checkbox"
                  className="h-3 w-3 accent-[#FFD60A]"
                  checked={selectedAreas.includes(area.id)}
                  onChange={() => toggleArea(area.id)}
                />
                {area.name}
              </label>
            ))}
          </div>
        )}
      </div>

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}

      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] text-muted-foreground">
          {t("ultrawiki.sources.create_hint")}
        </p>
        <div className="flex shrink-0 gap-2">
          <Button variant="outline" size="sm" type="button" onClick={onClose}>
            {t("ultrawiki.sources.cancel")}
          </Button>
          <Button
            size="sm"
            type="submit"
            disabled={
              submitting ||
              (connector === "plugin-bridge" && !integrationId) ||
              (needsPath && !rootPath.trim())
            }
            data-testid="ultrawiki-create-source"
          >
            {submitting && (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
            )}
            {t("ultrawiki.sources.create")}
          </Button>
        </div>
      </div>
    </form>
  );
}
