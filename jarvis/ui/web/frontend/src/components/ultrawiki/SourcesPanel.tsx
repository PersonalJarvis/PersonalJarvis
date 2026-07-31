/**
 * Connected-sources panel (design doc 04 "Sources view").
 *
 * Consent is THE gate: every source starts as `pending`, and a source without
 * approved consent shows NO sync control at all — the backend would refuse the
 * sync anyway (`service.start_sync`), the UI just does not dangle a dead
 * button. What approval MEANS is now visible: approving imports everything the
 * source holds right away, the card shows that import running (phase, items
 * pulled so far, cancel), and once it is done it says how much is in the store
 * and when it last ran — instead of the ambiguous "Approved / Never synced"
 * pair that told the user nothing about whether anything had happened.
 *
 * Errors and notices are deliberately different things and look different: an
 * error (red) means the sync FAILED, a notice (amber) means it ran fine and had
 * nothing to import — today that is a plugin-bridge integration whose pull
 * adapter is not built yet.
 *
 * "Add source": the curated picker (`ConnectorPicker`) — a tile grid of the
 * built-ins and the curated integrations, each with its real product name and
 * brand mark — plus a path field for the folder-shaped connectors and area
 * assignment. A bridge source is named after the integration the user picked
 * ("GitHub"), never after the generic bridge, so the list stays readable.
 * Picking the dropped-export tile swaps that path field for `ExportPreview`,
 * which can also upload the file and report what is inside it before the
 * source exists — consent semantics are untouched either way.
 */
import { useState } from "react";
import {
  AlertCircle,
  FolderOpen,
  Info,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  XCircle,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { formatRelativeTime } from "@/components/ultrawiki/relativeTime";
import { ConnectorBrandMark } from "@/components/ultrawiki/ConnectorBrand";
import {
  ConnectorPicker,
  connectorLabel,
  type ConnectorOption,
} from "@/components/ultrawiki/ConnectorPicker";
import { ExportPreview } from "@/components/ultrawiki/ExportPreview";
import {
  approveUltraWikiSource,
  cancelUltraWikiJob,
  createUltraWikiSource,
  fetchUltraWikiAreas,
  revokeUltraWikiSource,
  startUltraWikiSync,
  updateUltraWikiSource,
  type UltraWikiSource,
} from "@/lib/ultrawikiApi";

/** Connectors that read a user-chosen folder (config key `root`). */
const PATH_CONNECTORS: readonly string[] = ["obsidian-vault", "local-folder"];

/**
 * The dropped-export connector. It also takes a path, but under its own
 * config key (`path`) and through its own control: an export needs to be
 * uploaded and looked inside BEFORE it is registered, which a bare text field
 * cannot do.
 */
const EXPORT_CONNECTOR = "export-import";

/** The generic integration gateway — its cards carry the integration's name. */
const BRIDGE_CONNECTOR = "plugin-bridge";

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
      return true;
    } catch (e) {
      pushToast(
        "error",
        t("ultrawiki.sources.action_failed").replace(
          "{0}",
          (e as Error).message,
        ),
      );
      return false;
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
                void run(source.id, async () => {
                  const result = await approveUltraWikiSource(source.id);
                  // The backend's detail is the honest explanation whenever no
                  // import could start (mode off, connector gone) — a situation
                  // the UI cannot enumerate for itself.
                  pushToast(
                    "success",
                    result.job_id
                      ? t("ultrawiki.sources.import_started")
                      : result.detail,
                  );
                })
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
              onCancelJob={(jobId) =>
                void run(source.id, () => cancelUltraWikiJob(jobId))
              }
              onUpdate={(config) =>
                run(source.id, () =>
                  updateUltraWikiSource(source.id, { config }),
                )
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
  onCancelJob,
  onUpdate,
}: {
  source: UltraWikiSource;
  busy: boolean;
  onApprove: () => void;
  onRevoke: () => void;
  onSync: () => void;
  onCancelJob: (jobId: string) => void;
  onUpdate: (config: Record<string, unknown>) => Promise<boolean>;
}): JSX.Element {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [editRoot, setEditRoot] = useState("");
  const [editExcludes, setEditExcludes] = useState("");
  const consent = String(source.consent);
  const badge = CONSENT_BADGE[consent] ?? CONSENT_BADGE.pending;
  const approved = consent === "approved";
  const activeJob = source.active_job ?? null;
  const storedItems = source.counts?.total ?? 0;
  // The persisted outcome wins over `last_sync_at`: it is the moment a run
  // actually FINISHED, and it survives a restart.
  const lastFinished =
    source.last_outcome?.finished_at ?? source.last_sync_at ?? null;
  const lastFinishedText = formatRelativeTime(lastFinished, t);

  function beginEdit() {
    setEditRoot(source.config?.root ?? "");
    setEditExcludes((source.config?.exclude ?? []).join(", "));
    setEditing(true);
  }

  async function saveEdit(event: React.FormEvent) {
    event.preventDefault();
    const exclude = editExcludes
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean);
    if (await onUpdate({ root: editRoot.trim(), exclude })) {
      setEditing(false);
    }
  }

  return (
    <li
      className="card-outline rounded-xl border border-border bg-card/40 p-3"
      data-testid={`ultrawiki-source-${source.id}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {/* The same mark the picker offered this source under — the backend
              derives it from the connector, so a card added before the roster
              existed gets its logo too. */}
          <ConnectorBrandMark
            brand={source.brand ?? ""}
            label={source.label}
            size="sm"
            testId={`uw-source-brand-${source.id}`}
          />
          <div className="min-w-0">
            <span className="text-sm font-medium text-foreground">
              {source.label}
            </span>
            <span className="ml-2 font-mono text-[10px] text-muted-foreground">
              {source.connector}
            </span>
          </div>
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

      {source.connector === BRIDGE_CONNECTOR && (
        <p
          className="mt-0.5 text-[11px] text-muted-foreground"
          data-testid={`uw-source-bridge-${source.id}`}
        >
          {t("ultrawiki.sources.bridge_via")}
          {source.integration_id
            ? ` · ${t("ultrawiki.sources.integration").replace(
                "{0}",
                source.integration_id,
              )}`
            : ""}
        </p>
      )}

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

      {activeJob ? (
        <div
          className="mt-2 rounded-lg border border-primary/30 bg-primary/5 p-2.5"
          data-testid={`uw-source-progress-${source.id}`}
        >
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
            <Loader2
              className="h-3.5 w-3.5 shrink-0 animate-spin text-primary"
              aria-hidden
            />
            <span
              className="font-medium text-foreground"
              data-phase={activeJob.phase ?? "queued"}
            >
              {t(`ultrawiki.sources.phase_${activeJob.phase ?? "queued"}`)}
            </span>
            <span
              className="text-muted-foreground"
              data-testid={`uw-source-progress-items-${source.id}`}
            >
              {t("ultrawiki.sources.import_items").replace(
                "{0}",
                String(activeJob.items ?? 0),
              )}
            </span>
            <button
              type="button"
              onClick={() => onCancelJob(activeJob.job_id)}
              disabled={busy}
              data-testid={`uw-source-cancel-${source.id}`}
              className="ml-auto inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-foreground hover:bg-muted disabled:opacity-50"
            >
              <XCircle className="h-3 w-3" aria-hidden />
              {t("ultrawiki.sources.cancel_import")}
            </button>
          </div>
          {/* The connector streams — there is no total to divide by, so the
              bar is deliberately indeterminate and the ticking item count
              carries the real information. */}
          <div
            className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-primary/15"
            role="progressbar"
            aria-label={t("ultrawiki.sources.importing")}
          >
            <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
          </div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
            {t("ultrawiki.sources.copy_reassurance")}
          </p>
        </div>
      ) : (
        approved && (
          <p
            className="mt-2 text-[11px] text-foreground"
            data-testid={`uw-source-summary-${source.id}`}
          >
            {storedItems > 0
              ? t("ultrawiki.sources.fully_imported").replace(
                  "{0}",
                  String(storedItems),
                )
              : t("ultrawiki.sources.nothing_imported_yet")}
            {lastFinishedText
              ? ` · ${t("ultrawiki.sources.last_sync_relative").replace(
                  "{0}",
                  lastFinishedText,
                )}`
              : ""}
          </p>
        )
      )}

      {source.areas.length > 0 && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          {t("ultrawiki.sources.areas").replace("{0}", source.areas.join(", "))}
        </p>
      )}

      {/* Errors and notices are big enough to read: the old one-line grey text
          hid both a dead source and a "connected but nothing to import yet"
          integration behind the same tiny sentence. */}
      {source.last_error && (
        <div
          role="alert"
          className="mt-2 flex items-start gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-2.5 text-xs text-destructive"
          data-testid={`ultrawiki-source-error-${source.id}`}
        >
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span className="min-w-0">
            <strong className="block font-medium">
              {t("ultrawiki.sources.error_label")}
            </strong>
            {source.last_error}
          </span>
        </div>
      )}

      {source.last_notice && (
        <div
          className="mt-2 flex items-start gap-2 rounded-lg border border-[#ffb84d]/50 bg-[#ffb84d]/10 p-2.5 text-xs text-[#ffb84d]"
          data-testid={`ultrawiki-source-notice-${source.id}`}
        >
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span className="min-w-0">
            <strong className="block font-medium">
              {t("ultrawiki.sources.notice_label")}
            </strong>
            {source.last_notice}
          </span>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {PATH_CONNECTORS.includes(source.connector) && (
          <Button
            variant="outline"
            size="sm"
            onClick={beginEdit}
            disabled={busy || Boolean(activeJob)}
            data-testid={`uw-source-edit-${source.id}`}
          >
            <Pencil className="mr-1 h-3.5 w-3.5" aria-hidden />
            {t("common.edit")}
          </Button>
        )}
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
            {t("ultrawiki.sources.approve_import")}
          </Button>
        )}
        {approved && (
          <>
            {/* Consent gate: the sync control exists ONLY on approved sources. */}
            <Button
              variant="outline"
              size="sm"
              onClick={onSync}
              disabled={busy || Boolean(activeJob)}
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

      {editing && (
        <form
          onSubmit={(event) => void saveEdit(event)}
          className="mt-3 space-y-2 rounded-lg border border-border bg-background/40 p-2.5"
          data-testid={`uw-source-edit-form-${source.id}`}
        >
          <label className="block text-[11px] text-muted-foreground">
            {t("ultrawiki.sources.path_label")}
            <input
              type="text"
              value={editRoot}
              onChange={(event) => setEditRoot(event.target.value)}
              required
              className="mt-1 w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-foreground"
              data-testid={`uw-source-edit-root-${source.id}`}
            />
          </label>
          <label className="block text-[11px] text-muted-foreground">
            {t("ultrawiki.sources.exclude_label")}
            <input
              type="text"
              value={editExcludes}
              onChange={(event) => setEditExcludes(event.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-foreground"
              data-testid={`uw-source-edit-exclude-${source.id}`}
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setEditing(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button type="submit" size="sm" disabled={busy || !editRoot.trim()}>
              {busy ? t("common.saving") : t("common.save")}
            </Button>
          </div>
        </form>
      )}

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
  const [option, setOption] = useState<ConnectorOption | null>(null);
  const [label, setLabel] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [excludes, setExcludes] = useState("");
  const [exportPath, setExportPath] = useState("");
  const [selectedAreas, setSelectedAreas] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const areasQuery = useQuery({
    queryKey: ["ultrawiki", "areas"],
    queryFn: fetchUltraWikiAreas,
    staleTime: 5_000,
  });

  const needsPath = Boolean(option && PATH_CONNECTORS.includes(option.connector));
  const isExport = option?.connector === EXPORT_CONNECTOR;
  // A bridge card is named after the INTEGRATION the user picked. Falling back
  // to the connector's own name produced a list of identical "Connected
  // integration" rows nobody could tell apart.
  const defaultLabel = option ? connectorLabel(option, t) : "";

  function toggleArea(id: string) {
    setSelectedAreas((current) =>
      current.includes(id)
        ? current.filter((a) => a !== id)
        : [...current, id],
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!option) return;
    setSubmitting(true);
    setError("");
    const config: Record<string, unknown> = {};
    if (needsPath && rootPath.trim()) config.root = rootPath.trim();
    if (needsPath) {
      // A folder the user actually picks is often mostly clutter (build
      // output, working copies). Sent only when non-empty so an untouched
      // field stores nothing at all.
      const skip = excludes
        .split(",")
        .map((name) => name.trim())
        .filter(Boolean);
      if (skip.length > 0) config.exclude = skip;
    }
    if (isExport && exportPath.trim()) config.path = exportPath.trim();
    if (option.connector === BRIDGE_CONNECTOR && option.integrationId) {
      config.integration_id = option.integrationId;
    }
    try {
      await createUltraWikiSource({
        connector: option.connector,
        label: label.trim() || defaultLabel,
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

      <div>
        <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
          {t("ultrawiki.picker.choose")}
        </span>
        <ConnectorPicker
          selectedKey={option?.key ?? ""}
          onSelect={(picked) => {
            setOption(picked);
            // A path belongs to the connector it was typed for; carrying it
            // over to a different source type would silently point the new one
            // at an unrelated folder. The two path kinds are kept apart for
            // the same reason — an export file is not a vault root.
            if (!PATH_CONNECTORS.includes(picked.connector)) setRootPath("");
            if (picked.connector !== EXPORT_CONNECTOR) setExportPath("");
          }}
        />
      </div>

      {option && (
        <label className="block">
          <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
            {t("ultrawiki.sources.label_label")}
          </span>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder={defaultLabel}
            className={inputCls}
          />
        </label>
      )}

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

      {needsPath && (
        <label className="block">
          <span className="mb-1.5 flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            <FolderOpen className="h-3 w-3" aria-hidden />
            {t("ultrawiki.sources.exclude_label")}
          </span>
          <input
            type="text"
            value={excludes}
            onChange={(e) => setExcludes(e.target.value)}
            className={inputCls}
            data-testid="ultrawiki-exclude-input"
          />
          <span className="mt-1 block text-[11px] text-muted-foreground">
            {t("ultrawiki.sources.exclude_hint")}
          </span>
        </label>
      )}

      {isExport && (
        <ExportPreview path={exportPath} onPathChange={setExportPath} />
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
              !option ||
              (needsPath && !rootPath.trim()) ||
              (isExport && !exportPath.trim())
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
